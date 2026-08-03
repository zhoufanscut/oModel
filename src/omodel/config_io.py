"""Clean JSONC read / serialize / backups / restore / scaffold.  DESIGN.md §config_io.py.

FROZEN CONTRACT — owned by the Config-I/O specialist. Implement the bodies; keep these
signatures and the SaveResult/BackupInfo shapes (cli.py + app.py depend on them).

⚠ REAL-CONFIG SAFETY: never default-write the user's live config in tests. Every test
passes an explicit temp `path`; the live ~/.config/opencode/oh-my-openagent.jsonc is off-limits.
"""
from __future__ import annotations

import difflib
import glob
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class ConfigParseError(ValueError):
    """Raised by load_config() when the on-disk JSONC fails to parse (malformed syntax).
    The message includes the config path and the underlying json5 error, so callers (cli.py)
    can print a friendly one-liner instead of letting a raw json5 traceback escape."""


class BackupScopeMismatch(ValueError):
    """Raised by restore() when a snapshot's config format doesn't match the live config's.

    `restore` is a VERBATIM copy, so writing a pre-4.19.3 snapshot over a unified
    `~/.omo/omo.jsonc` would leave legacy keys (`claude_code`, `experimental`, `team_mode`, …)
    at the document root. omo's root schema is `.strict()` and rejects them as
    `unrecognized_keys`, and its loader answers a failed validation with the ALL-DEFAULT config
    plus one diagnostic — so the restore would silently reset the user's entire omo setup, not
    just their model assignments. There is deliberately no `--force`: `--restore` is the
    recover-from-a-mistake path, and it must not be the one that makes a bigger one."""


# --- config location + scope ------------------------------------------------------------
# omo 4.19.3+ keeps ONE config at ~/.omo/omo.jsonc and nests the whole OpenCode plugin config
# — agents/categories included — under `"[opencode]"`. The legacy
# ~/.config/opencode/oh-my-openagent.jsonc is read by nothing but omo's migration engine, which
# MOVES it aside on first launch. omodel therefore edits `"[opencode]"` on a unified document and
# only falls back to the document root for a legacy one. See DESIGN §config scope.

OPENCODE_BLOCK = "[opencode]"


def _home() -> Path:
    """omo's home resolution (`loader/paths.ts` resolveHomeDir): $HOME, else $USERPROFILE.
    Deliberately NOT `Path.home()` — that flips the precedence on Windows — and deliberately
    NOT $XDG_CONFIG_HOME: omo puts `.omo` under the home dir on every platform, so honoring XDG
    here would point omodel at a directory omo never reads."""
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    return Path(home) if home else Path.home()


def unified_config_path() -> str:
    """`~/.omo/omo.jsonc` — omo's unified config. Does NOT require the file to exist; this is
    also the scaffold target when nothing exists anywhere."""
    return str(_home() / ".omo" / "omo.jsonc")


def legacy_config_path() -> str:
    """The pre-4.19.3 path: $XDG_CONFIG_HOME/opencode/oh-my-openagent.jsonc, else
    ~/.config/opencode/oh-my-openagent.jsonc. Kept for users still on an omo that reads it;
    omodel never CREATES this file (see load_config)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return str(Path(xdg) / "opencode" / "oh-my-openagent.jsonc")
    return str(_home() / ".config" / "opencode" / "oh-my-openagent.jsonc")


def config_path(cli_override: str | None = None) -> str:
    """Resolve the config path: cli_override, else the first EXISTING of
    `~/.omo/omo.jsonc` → `~/.omo/omo.json` → the legacy path; else `~/.omo/omo.jsonc` (so a
    from-scratch scaffold lands in the new format and the legacy file is never recreated).
    Does NOT require the file to exist."""
    if cli_override is not None:
        return str(cli_override)
    omo_dir = _home() / ".omo"
    for name in ("omo.jsonc", "omo.json"):
        candidate = omo_dir / name
        if candidate.exists():
            return str(candidate)
    legacy = legacy_config_path()
    if os.path.exists(legacy):
        return str(legacy)
    return str(omo_dir / "omo.jsonc")


def scope_of(cfg) -> str:
    """Which node of `cfg` holds the agents/categories omodel manages: `"opencode"` (nested under
    `"[opencode]"`) or `"root"` (legacy, top level).

    Detection is CONTENT-based, never filename-based, so `--config <anywhere>` behaves correctly
    and a unified document opened by an explicit path is still edited in the right place. A
    document counts as unified when it has an `"[opencode]"` block, an engine-managed migration
    key, or omo's unified `$schema` — the last two catch a migrated document whose `[opencode]`
    block has yet to be created."""
    if not isinstance(cfg, dict):
        return "root"
    # PRESENCE of the key, not its type: a legacy document never has it at all, so a hand-edited
    # `"[opencode]": null` is still a unified document with a broken block. Treating it as legacy
    # would send writes to the document root, where they are outranked the moment the block comes
    # back (`managed_root_for_write` coerces it to `{}` instead).
    if OPENCODE_BLOCK in cfg:
        return "opencode"
    if "_migrations" in cfg or "legacy_migrations" in cfg:
        return "opencode"
    schema = cfg.get("$schema")
    if isinstance(schema, str) and "omo.schema.json" in schema:
        return "opencode"
    return "root"


def managed_root(cfg) -> dict:
    """The node holding `agents`/`categories` for READS — `cfg["[opencode]"]` on a unified
    document, `cfg` on a legacy one. Never creates anything; returns `{}` when the block is
    absent or not a dict."""
    if scope_of(cfg) != "opencode":
        return cfg if isinstance(cfg, dict) else {}
    block = cfg.get(OPENCODE_BLOCK)
    return block if isinstance(block, dict) else {}


def managed_root_for_write(cfg: dict) -> dict:
    """The node holding `agents`/`categories` for WRITES, created on demand. On a unified
    document this is `cfg["[opencode]"]`, coerced back to `{}` if a hand edit left a non-dict
    there — writing agents at the document root instead would be accepted, saved, and then
    silently outranked by the `[opencode]` block (omo folds base → `[opencode]`, last wins)."""
    if scope_of(cfg) != "opencode":
        return cfg
    block = cfg.get(OPENCODE_BLOCK)
    if not isinstance(block, dict):
        block = {}
        cfg[OPENCODE_BLOCK] = block
    return block


def load_config(path: str | None = None):
    """Resolve via config_path(path); if missing, scaffold a bundled default to that location;
    json5.load → ordered dict. Returns (cfg: dict, resolved_path: str). `agents`/`categories`
    (inside `"[opencode]"` on a unified document) are editable; every other key — the rest of
    the `[opencode]` block, `_migrations`, `profiles`, other harness blocks, `$schema` — passes
    through by value. Raises ConfigParseError if the on-disk JSONC is malformed.

    The scaffold is the UNIFIED shape (`data/default-omo-config.jsonc`) everywhere except an
    explicit legacy path: omo moves the legacy file aside on migration, and recreating it there
    would hand the user an empty config that omo no longer reads."""
    import importlib.resources

    import json5

    resolved = config_path(path)

    if not os.path.exists(resolved):
        default_name = (
            "default-config.jsonc"
            if os.path.abspath(resolved) == os.path.abspath(legacy_config_path())
            else "default-omo-config.jsonc"
        )
        default_ref = importlib.resources.files("omodel.data") / default_name
        default_text = default_ref.read_text(encoding="utf-8")
        # dirname() of a bare relative filename (no directory component) is "" — resolve via
        # abspath first so a relative `--config foo.jsonc` doesn't crash makedirs(exist_ok=True).
        os.makedirs(os.path.dirname(os.path.abspath(resolved)), exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(default_text)

    with open(resolved, encoding="utf-8") as f:
        try:
            cfg = json5.load(f)
        except ValueError as exc:
            raise ConfigParseError(f"could not parse config at {resolved}: {exc}") from exc

    if not isinstance(cfg, dict):
        # Valid JSON, wrong shape (a top-level array, string, number…). Every caller assumes a
        # mapping, so without this the first `.items()`/`.get()` escapes as a raw traceback
        # instead of the friendly one-liner a malformed file already gets.
        raise ConfigParseError(
            f"could not parse config at {resolved}: expected a JSON object at the top level, "
            f"found {type(cfg).__name__}"
        )

    return cfg, resolved


def _clean_agents(agents: dict) -> dict:
    """Return a COPY of the agents map with empty ultrawork/compaction sub-objects removed
    (an added-but-unfilled sub-target must not persist — clean active-only). Never mutates
    the input; preserves key order."""
    out: dict = {}
    for name, data in agents.items():
        if not isinstance(data, dict):
            out[name] = data
            continue
        cleaned = {}
        for k, v in data.items():
            if k in ("ultrawork", "compaction") and isinstance(v, dict) and not v.get("model"):
                continue  # drop empty / model-less sub-object
            cleaned[k] = v
        out[name] = cleaned
    return out


def serialize(cfg: dict) -> str:
    """EXACT format (DESIGN §config_io serialize):
      (1) ordered dict preserving on-disk key order, but FORCE `$schema` to position 0 if present;
      (2) within agents/categories, freshly-added sub-keys (ultrawork/compaction) APPENDED,
          cleared fields DELETED;
      (3) body = json.dumps(cfg, indent=2, ensure_ascii=False);  json.dumps cannot emit comments;
      (4) return "// Generated by oModel — edit via `omodel`\\n" + body + "\\n" (single trailing \\n).

    Scope-aware: on a unified document `agents` is cleaned inside the `"[opencode]"` block and the
    header is omo's own `// OMO configuration`, so a from-scratch write still looks like a file omo
    wrote. `cfg` is the WHOLE document either way, so this never invents a top-level `agents`."""
    # (1) Build ordered dict with $schema forced to position 0
    scope = scope_of(cfg)
    ordered: dict = {}
    if "$schema" in cfg:
        ordered["$schema"] = cfg["$schema"]
    for k, v in cfg.items():
        if k == "$schema":
            continue  # already placed first
        if scope == "root" and k == "agents" and isinstance(v, dict):
            ordered[k] = _clean_agents(v)  # drop empty ultrawork/compaction sub-objects
        elif scope == "opencode" and k == OPENCODE_BLOCK and isinstance(v, dict):
            ordered[k] = {
                ik: (_clean_agents(iv) if ik == "agents" and isinstance(iv, dict) else iv)
                for ik, iv in v.items()
            }
        else:
            ordered[k] = v

    # (3) Serialize — json.dumps preserves insertion order for dicts in Python 3.7+
    body = json.dumps(ordered, indent=2, ensure_ascii=False)

    # (4) Return with header and single trailing newline
    header = "// OMO configuration" if scope == "opencode" else "// Generated by oModel — edit via `omodel`"
    return header + "\n" + body + "\n"


# --- text-preserving render -------------------------------------------------------------
# omodel manages ONLY the top-level `agents` and `categories` objects. `render()` rewrites just
# those two value spans in place and leaves the rest of the file — other keys, formatting, and
# crucially any comments or commented-out config OUTSIDE those two — byte-for-byte intact. The
# commented palette INSIDE agents/categories is still dropped (those spans are rewritten clean;
# decision #13). `serialize()` above stays the canonical clean form (dirtiness + from-scratch
# fallback) and is never required to equal the on-disk bytes.

def _skip_trivia(text: str, j: int) -> int:
    """Advance past JSON whitespace and // line / /* block */ comments; return the new index."""
    n = len(text)
    while j < n:
        c = text[j]
        if c in " \t\r\n":
            j += 1
        elif c == "/" and j + 1 < n and text[j + 1] == "/":
            j += 2
            while j < n and text[j] != "\n":
                j += 1
        elif c == "/" and j + 1 < n and text[j + 1] == "*":
            j += 2
            while j < n and not (text[j] == "*" and j + 1 < n and text[j + 1] == "/"):
                j += 1
            j += 2  # consume the closing */
        else:
            break
    return j


def _read_string(text: str, j: int) -> int:
    """`text[j]` is a string's opening quote; return the index just past the closing quote."""
    n = len(text)
    j += 1
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == '"':
            return j + 1
        j += 1
    return j  # unterminated — best effort


def _skip_value(text: str, j: int) -> int:
    """`text[j]` is the first char of a JSON value; return the index just past that value.
    Objects/arrays are scanned with full string/comment/nesting awareness so a `}` or `"`
    inside a string or comment never ends the scan early."""
    n = len(text)
    c = text[j]
    if c == '"':
        return _read_string(text, j)
    if c in "{[":
        depth = 0
        while j < n:
            c = text[j]
            if c == '"':
                j = _read_string(text, j)
                continue
            if c == "/" and j + 1 < n and text[j + 1] == "/":
                j += 2
                while j < n and text[j] != "\n":
                    j += 1
                continue
            if c == "/" and j + 1 < n and text[j + 1] == "*":
                j += 2
                while j < n and not (text[j] == "*" and j + 1 < n and text[j + 1] == "/"):
                    j += 1
                j += 2
                continue
            if c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        return j
    # primitive (number / true / false / null): read to the next delimiter, ws, or comment
    while j < n and text[j] not in ",}]" and text[j] not in " \t\r\n":
        if text[j] == "/" and j + 1 < n and text[j + 1] in "/*":
            break
        j += 1
    return j


def _value_span(text: str, key: str, start: int = 0):
    """Locate `"key": <value>` among the DIRECT members of the object that begins at `start`
    (trivia-skipped, must be `{`). Return (value_start, value_end) of the value, or None if `key`
    is not a direct member (malformed file, or it only appears deeper). Honors strings, comments,
    and nesting. `start=0` walks the root object; passing a parent's value_start walks that
    parent's members, which is how the `"[opencode]"` block is entered."""
    n = len(text)
    i = _skip_trivia(text, start)
    if i >= n or text[i] != "{":
        return None
    i += 1  # enter the root object
    while True:
        i = _skip_trivia(text, i)
        if i >= n or text[i] == "}":
            return None
        if text[i] == ",":
            i += 1
            continue
        if text[i] != '"':
            return None  # unexpected token where a member key was expected
        key_start = i
        key_end = _read_string(text, i)
        try:
            this_key = json.loads(text[key_start:key_end])
        except ValueError:
            return None
        i = _skip_trivia(text, key_end)
        if i >= n or text[i] != ":":
            return None
        i = _skip_trivia(text, i + 1)
        if i >= n:
            return None
        value_start = i
        value_end = _skip_value(text, i)
        if this_key == key:
            return (value_start, value_end)
        i = value_end


def _span_for_path(text: str, path):
    """Walk a chain of member keys from the root object and return the LAST one's value span, or
    None if any link is missing. `["agents"]` is the legacy top-level lookup; `["[opencode]",
    "agents"]` reaches into the unified document's harness block."""
    start = 0
    span = None
    for key in path:
        span = _value_span(text, key, start)
        if span is None:
            return None
        start = span[0]
    return span


def _line_indent(text: str, pos: int) -> str:
    """Leading whitespace of the line containing `pos` — used to align a spliced value's
    continuation/closing lines under its key."""
    line_start = text.rfind("\n", 0, pos) + 1
    j = line_start
    while j < len(text) and text[j] in " \t":
        j += 1
    return text[line_start:j]


def _reindent(value_text: str, indent: str) -> str:
    """Prefix `indent` to every line after the first (the first follows `"key": ` inline), so a
    json.dumps(indent=2) block sits correctly under a key at arbitrary depth."""
    lines = value_text.split("\n")
    if len(lines) == 1:
        return value_text
    return lines[0] + "\n" + "\n".join((indent + ln) if ln else ln for ln in lines[1:])


def render(cfg: dict, base_text: str) -> str:
    """Text-preserving serialization. Return `base_text` with ONLY the managed `agents` and
    `categories` value spans replaced by their clean (comment-free) form; comments, commented-out
    config, other keys, key order, and formatting OUTSIDE those two objects are preserved
    byte-for-byte — omodel manages only those two keys. Falls back to serialize(cfg) when there is
    no base text, or either key is missing from the managed node (can't splice safely).

    On a unified document the managed node is `"[opencode]"`, so the spans are nested one level
    down and everything around them — `$schema`, `_migrations`, `profiles`, the rest of the
    `[opencode]` block, and any comments inside it — survives untouched. The serialize() fallback
    stays safe in both scopes because `cfg` is the whole document: it rewrites the file cleanly
    (losing comments) but never relocates agents/categories out of their scope."""
    if not base_text or not base_text.strip():
        return serialize(cfg)
    managed = managed_root(cfg)
    agents_val = managed.get("agents")
    agents_val = _clean_agents(agents_val) if isinstance(agents_val, dict) else (agents_val or {})
    categories_val = managed.get("categories")
    categories_val = categories_val if categories_val is not None else {}
    values = {"agents": agents_val, "categories": categories_val}

    prefix = [OPENCODE_BLOCK] if scope_of(cfg) == "opencode" else []
    spans = {}
    for key in ("agents", "categories"):
        span = _span_for_path(base_text, prefix + [key])
        if span is not None:
            spans[key] = span
        elif values[key]:
            # Something to write and nowhere to put it (non-omo / hand-broken file): degrade to a
            # clean rewrite. The cost of that is every comment in the document, which on a unified
            # config means omo's config and not just omodel's — so it is reserved for the case
            # where splicing genuinely cannot express the change.
            return serialize(cfg)
        # else: the key is absent from the file AND empty in cfg — nothing to express. Leave the
        # file alone rather than reformatting it to add `"categories": {}`.

    result = base_text
    # Splice the later span first so the earlier span's offsets stay valid.
    for key in sorted(spans, key=lambda k: spans[k][0], reverse=True):
        value_start, value_end = spans[key]
        indent = _line_indent(base_text, value_start)
        rendered = _reindent(json.dumps(values[key], indent=2, ensure_ascii=False), indent)
        result = result[:value_start] + rendered + result[value_end:]
    return result


def diff_text(cfg: dict, path: str) -> str:
    """Unified diff of render(cfg, on-disk) vs the current on-disk file (for the confirm modal),
    so the modal shows exactly what changes — agents/categories only, comments outside intact."""
    try:
        with open(path, encoding="utf-8") as f:
            old_text = f.read()
    except FileNotFoundError:
        old_text = ""
    new_text = render(cfg, old_text)

    diff_lines = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=path,
        tofile=path + " (new)",
    )
    return "".join(diff_lines)


@dataclass
class SaveResult:
    changed: bool
    backup: str = None           # path of timestamped snapshot written this save, or None
    original_created: bool = False  # True iff .backup/original.jsonc was created this save


def save(cfg: dict, path: str) -> SaveResult:
    """No diff → SaveResult(changed=False) ("nothing to save"). Else EXACT order:
      (1) if <dir>/.backup/original.jsonc absent, copy current on-disk config there (verbatim);
      (2) write verbatim timestamped snapshot .backup/YYYYMMDD-HHMMSS[.mmm].jsonc (UTC);
      (3) prune ONLY glob('[0-9]*.jsonc') (EXCLUDES original.jsonc) to the newest 20;
    then atomic temp+rename of render(cfg, on-disk). <dir> = dir of `path`. The write is
    text-preserving: only agents/categories are rewritten; comments / commented-out config
    outside them survive (render() splices in place; missing file → serialize(cfg))."""
    # Check whether anything changed
    try:
        with open(path, encoding="utf-8") as f:
            old_text = f.read()
    except FileNotFoundError:
        old_text = None

    new_text = render(cfg, old_text)

    if old_text is not None and new_text == old_text:
        return SaveResult(changed=False)

    config_dir = os.path.dirname(os.path.abspath(path))
    backup_dir = os.path.join(config_dir, ".backup")
    os.makedirs(backup_dir, exist_ok=True)

    original_path = os.path.join(backup_dir, "original.jsonc")
    original_created = False

    # (1) If original.jsonc absent AND there is an existing on-disk config, pin it verbatim
    if not os.path.exists(original_path) and old_text is not None:
        shutil.copy2(path, original_path)
        original_created = True

    # (2) Write verbatim timestamped snapshot in UTC; .mmm avoids same-second collisions
    now_utc = datetime.now(timezone.utc)
    # Format: YYYYMMDD-HHMMSS.mmm — milliseconds keep lexicographic sort stable
    ts = now_utc.strftime("%Y%m%d-%H%M%S") + f".{now_utc.microsecond // 1000:03d}"
    snapshot_name = f"{ts}.jsonc"
    snapshot_path = os.path.join(backup_dir, snapshot_name)
    if old_text is not None:
        # Verbatim byte copy of the current on-disk file
        shutil.copy2(path, snapshot_path)
    else:
        # Config didn't exist yet — snapshot an empty string so the slot exists
        with open(snapshot_path, "w", encoding="utf-8") as f:
            f.write("")

    # (3) Prune ONLY timestamped snapshots (glob '[0-9]*.jsonc' excludes original.jsonc)
    #     Keep the newest 20.
    timestamped = sorted(
        glob.glob(os.path.join(backup_dir, "[0-9]*.jsonc"))
    )  # lexicographic = chronological thanks to YYYYMMDD-… format
    if len(timestamped) > 20:
        for old_snap in timestamped[:-20]:
            try:
                os.remove(old_snap)
            except OSError:
                pass  # best-effort prune

    # Atomic temp-write + os.replace
    config_parent = os.path.dirname(os.path.abspath(path))
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=config_parent,
        delete=False,
        suffix=".tmp",
    ) as tmp:
        tmp.write(new_text)
        tmp_path = tmp.name
    os.replace(tmp_path, path)

    return SaveResult(changed=True, backup=snapshot_path, original_created=original_created)


@dataclass
class BackupInfo:
    name: str          # filename within .backup/
    path: str
    is_original: bool
    size: int


def list_backups(path: str) -> list:
    """[BackupInfo]: the pinned original.jsonc + the newest 10 timestamped snapshots,
    newest first (DESIGN --restore; items 11–20 are an unlisted on-disk buffer)."""
    config_dir = os.path.dirname(os.path.abspath(path))
    backup_dir = os.path.join(config_dir, ".backup")

    result: list = []

    # Pinned original (if present) — always first
    original_path = os.path.join(backup_dir, "original.jsonc")
    if os.path.exists(original_path):
        result.append(BackupInfo(
            name="original.jsonc",
            path=original_path,
            is_original=True,
            size=os.path.getsize(original_path),
        ))

    # Newest 10 timestamped snapshots, newest first
    timestamped = sorted(
        glob.glob(os.path.join(backup_dir, "[0-9]*.jsonc"))
    )  # lexicographic ascending = chronological; reverse for newest-first
    for snap in reversed(timestamped[-10:]):
        result.append(BackupInfo(
            name=os.path.basename(snap),
            path=snap,
            is_original=False,
            size=os.path.getsize(snap),
        ))

    return result


def _file_scope(path: str):
    """`scope_of` the JSONC at `path`, or None when it can't be read or parsed — an unreadable
    file tells us nothing, and guessing would be worse than not guarding."""
    import json5

    try:
        with open(path, encoding="utf-8") as f:
            return scope_of(json5.load(f))
    except (OSError, ValueError):
        return None


def adopt_original_backup(src_config_path: str, dst_config_path: str) -> bool:
    """Carry the pinned pre-omodel config across when the config moves to `~/.omo/`, as
    `.backup/original-legacy.jsonc`.

    Only the pin travels, not the timestamped ring: it is the config as it was before omodel ever
    touched it (never pruned, decision #13) and is irreplaceable, while the snapshots are stale
    pre-4.19.3 shapes that `restore` refuses anyway. They stay at the old path, reachable by hand.

    It lands under a DIFFERENT name for two reasons, both about it being legacy-format:
      * `list_backups` offers `original.jsonc` and the `[0-9]*.jsonc` ring, and neither pattern
        matches this one — so it is preserved and readable without becoming an entry that
        `restore` must refuse on every pick (`BackupScopeMismatch`, no override);
      * an `original.jsonc` already present would suppress the first save's pin, and that pin —
        taken from the UNIFIED config — is the one that can actually be restored.

    Returns whether a copy was made; a no-op once the destination exists."""
    src = os.path.join(os.path.dirname(os.path.abspath(src_config_path)), ".backup", "original.jsonc")
    dst_dir = os.path.join(os.path.dirname(os.path.abspath(dst_config_path)), ".backup")
    dst = os.path.join(dst_dir, "original-legacy.jsonc")
    if os.path.abspath(src) == os.path.abspath(dst) or os.path.exists(dst) or not os.path.isfile(src):
        return False
    os.makedirs(dst_dir, exist_ok=True)
    shutil.copy2(src, dst)  # the ORIGINAL stays put: this is a copy, never a move
    return True


def restore(path: str, backup_name: str) -> None:
    """Snapshot the CURRENT file first (restore is itself undoable), then copy the chosen
    backup (by `backup_name` within .backup/) to `path`.

    REFUSES with `BackupScopeMismatch` when the snapshot's scope differs from the live config's
    — see that exception for why a verbatim cross-format copy is destructive."""
    config_dir = os.path.dirname(os.path.abspath(path))
    backup_dir = os.path.join(config_dir, ".backup")
    os.makedirs(backup_dir, exist_ok=True)

    # Scope check BEFORE anything is written, so a refusal leaves no snapshot behind either.
    src_check = os.path.join(backup_dir, os.path.basename(backup_name))
    backup_scope = _file_scope(src_check)
    target_scope = _file_scope(path)
    if target_scope is None and os.path.abspath(path) == os.path.abspath(unified_config_path()):
        target_scope = "opencode"  # nothing on disk yet, but omo will read this path as unified
    if backup_scope is not None and target_scope is not None and backup_scope != target_scope:
        raise BackupScopeMismatch(
            f"'{os.path.basename(backup_name)}' is in the "
            f"{'pre-4.19.3' if backup_scope == 'root' else 'unified'} config format, but "
            f"{path} is {'unified' if target_scope == 'opencode' else 'pre-4.19.3'}. "
            "Restoring it would leave the file invalid for oh-my-openagent, which answers a "
            "schema failure by falling back to its defaults — losing far more than the models. "
            "Copy the values across by hand, or use `omodel set`."
        )

    # Snapshot the current live config first (so the restore itself is undoable)
    now_utc = datetime.now(timezone.utc)
    ts = now_utc.strftime("%Y%m%d-%H%M%S") + f".{now_utc.microsecond // 1000:03d}"
    snapshot_path = os.path.join(backup_dir, f"{ts}.jsonc")
    if os.path.exists(path):
        shutil.copy2(path, snapshot_path)
    else:
        # Nothing to snapshot; create an empty slot so the restore slot is still tracked
        with open(snapshot_path, "w", encoding="utf-8") as f:
            f.write("")

    # Copy the chosen backup verbatim to the live config path. Sanitize backup_name to a
    # bare basename so it cannot escape .backup/ (path traversal), and require it to exist.
    safe_name = os.path.basename(backup_name)
    src = os.path.join(backup_dir, safe_name)
    if not os.path.isfile(src):
        raise FileNotFoundError(f"backup not found: {safe_name}")
    shutil.copy2(src, path)
