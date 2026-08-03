"""Named presets — the working state.  DESIGN.md §presets.py (decision #17).

A **preset** is a named set of assignments (the `agents` + `categories` subtrees, sub-targets
included).  There is no cap on how many you keep and no such thing as an empty one: the list is
DENSE, seeded with a single `default` and grown by `a` (fork).  Exactly one is **active**, and
the load-bearing invariant is:

    the config on disk always equals the ACTIVE preset — never a fourth, orphan state.

That single rule decides the rest of the design.  Edits flow into the active preset (so editing
can't create an orphan state); the presets file and the config must therefore move together, so
**only a save writes anything** — `a` (fork), `x` (delete) and the first-launch seed all stage
in memory, and app.py's `s` publishes the whole store plus the config in one step.  Quitting
without saving discards both, in lockstep, leaving disk exactly as it was: still consistent.

Keep the three save-ish things apart (GLOSSARY): a *backup* is the verbatim on-disk file copy
taken automatically at every save (config_io.py, `--restore`); the *history* is the in-session
undo stack (history.py); a *preset* is a named set of assignments you switch between.  "Slot"
stays reserved for a TARGET — a preset is addressed by INDEX (0-based, shown 1-based).  Because
the list is dense, a DELETE SHIFTS every later index; app.py remaps its undo-history references
in the same breath (`History.map_aux_key`), which is the one hazard the dense list introduces.

Stored next to the ACTIVE config as `<config_dir>/.omodel-presets.json`, so a `--config`
override gets its own set — which is what keeps the real-config safety rule satisfiable in
tests with no extra env override.

**Read = best-effort, write = loud.**  `load()` never raises (missing / corrupt / wrong version
-> an empty store; a non-dict `agents`/`categories` -> `{}`; an out-of-range `active` -> 0),
because a hand-mangled presets file must not stop you editing models.  It also MIGRATES the
original fixed-3 shape in place (`FILE_VERSION` 1 -> 2): `null` holes (an "empty slot" back when
there were exactly three) are dropped and `active` is remapped onto the compacted list, so an
existing file keeps working.  `write()` DOES raise on failure so app.py can notify: a silently
dropped preset write would be a lie about durable state, whereas cache.py swallows its write
errors because a lost cache write costs only speed.

Pure data + file IO — no Textual, no omodel imports (a leaf like history.py).  That is also why
`fingerprint()` re-implements config_io._clean_agents' empty-sub-object rule instead of
importing it: the one deliberate duplication here, and a contained one — the fingerprint decides
which preset the config matches at launch and nothing else.
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

# On-disk shape version.  We WRITE 2 (dense, unbounded list) and READ both:
#   1 = the original fixed-3 list, `null` for an empty slot, `active` indexing that 3-list
#   2 = a dense list of any length
# `load` migrates 1 -> 2 in memory (see `_LEGACY_VERSIONS`); anything else reads as "no presets"
# rather than raising.  The bump is NOT about reading — the payload shape never changed, so
# unversioned reading would work fine.  It is about the DOWNGRADE: a build that predates
# unlimited presets accepts a version-1 file, silently truncates it to 3, and its next save
# deletes the rest with no copy kept (verified against that build).  Given an unknown version it
# instead reads empty AND `_preserve_unreadable` moves the file to `<path>.corrupt` first, so a
# rollback costs you the presets pane, not the presets.
FILE_VERSION = 2
_LEGACY_VERSIONS = (1,)

# Cap on a preset name — what fits the 32-wide pane once the `● 12 ` prefix is drawn.
MAX_NAME = 24

# Name given to the preset seeded from your existing config on first launch.
DEFAULT_NAME = "default"

_FILENAME = ".omodel-presets.json"

# Sub-target kinds an agent may carry (mirrors app._SUBKINDS / config_io's clean rule).
_SUBKINDS = ("ultrawork", "compaction")


@dataclass
class Preset:
    """One named set of assignments.  `agents`/`categories` are always dicts (see `_as_map`)."""

    name: str
    saved_at: str
    agents: dict
    categories: dict


@dataclass
class Store:
    """The whole presets file: a DENSE list of presets (no holes, no cap) plus which one is
    ACTIVE.  `active` always points at a real preset when the store holds any, so app.py never
    has to handle "active points at nothing"."""

    presets: list = field(default_factory=list)
    active: int = 0

    def current(self) -> Preset | None:
        """The active preset, or None when the store is empty."""
        if 0 <= self.active < len(self.presets):
            return self.presets[self.active]
        return None

    def is_empty(self) -> bool:
        return not self.presets


def presets_path(config_path: str) -> str:
    """`<config_dir>/.omodel-presets.json` — sibling of the config and of `.backup/`, so the
    presets follow whichever config is active (`--config` included)."""
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), _FILENAME)


def timestamp() -> str:
    """UTC `saved_at` stamp, second precision — `2026-07-26T09:14:03Z`."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_map(value) -> dict:
    """Deep copy of a mapping; anything else (absent, `null`, a scalar — a hand-mangled file)
    becomes `{}`.  Copying here is what keeps a stored Preset from aliasing the live cfg: the
    app's later edits must not mutate the preset, and switching to a preset must not hand the
    app a reference into it."""
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _render_safe(name: str) -> str:
    """Drop `[`/`]` from a preset name.

    Textual 8 parses plain `str` handed to a widget as CONTENT MARKUP, so a name containing a
    tag-like run explodes in the compositor: a preset called `[/b]` raises MarkupError from
    `_populate_presets` and — because the name is PERSISTED — takes the app down on every
    subsequent launch until the file is hand-edited. Applied both to typed names
    (`sanitize_name`) and to names read off disk (`_entry`), so no render site has to remember."""
    return name.replace("[", "").replace("]", "")


def _entry(raw) -> Preset | None:
    """One on-disk entry -> Preset, tolerating anything: a non-dict entry is an empty preset,
    and missing/non-string/non-dict fields fall back rather than raising."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    saved_at = raw.get("saved_at")
    return Preset(
        name=_render_safe(name) if isinstance(name, str) else "",
        saved_at=saved_at if isinstance(saved_at, str) else "",
        agents=_as_map(raw.get("agents")),
        categories=_as_map(raw.get("categories")),
    )


def normalize_active(store: Store) -> Store:
    """Force `active` into range — out of bounds falls back to the first preset (0 when the
    store is empty).  Called on every read and after every mutation (a DELETE shifts indices),
    so `Store.current()` is None only for a genuinely empty store."""
    if not 0 <= store.active < len(store.presets):
        store.active = 0
    return store


def load(config_path: str) -> Store:
    """The presets Store for `config_path`.

    NEVER raises — missing file, unreadable file, malformed JSON, a non-dict root or a version
    mismatch all read as an empty store, and `active` is normalized into range.

    Migrates a version-1 file (the original fixed-3 shape): `null` entries (the old "empty slot")
    are DROPPED and `active` follows the preset it pointed at into the compacted list, so a file
    written before presets went unlimited loads as the 1-3 real presets it actually held.  The
    compaction is unconditional rather than gated on the version, because it is also the right
    reading of a hand-edited version-2 file that happens to contain a `null`."""
    try:
        with open(presets_path(config_path), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return Store()
    if not isinstance(data, dict) or data.get("version") not in (FILE_VERSION, *_LEGACY_VERSIONS):
        return Store()
    raw = data.get("presets")
    if not isinstance(raw, list):
        return Store()
    active = data.get("active")
    active = active if isinstance(active, int) else 0
    items = []
    remapped = 0  # active pointing at a hole (or out of range) falls back to the first preset
    for i, r in enumerate(raw):
        entry = _entry(r)
        if entry is None:  # a legacy `null` hole, or a junk entry — neither survives compaction
            continue
        if i == active:
            remapped = len(items)  # where the preset that WAS active lands once holes are gone
        items.append(entry)
    return normalize_active(Store(presets=items, active=remapped))


def _payload(store: Store) -> str:
    return (
        json.dumps(
            {
                "version": FILE_VERSION,
                "active": store.active,
                "presets": [
                    {
                        "name": p.name,
                        "saved_at": p.saved_at,
                        "agents": _as_map(p.agents),
                        "categories": _as_map(p.categories),
                    }
                    for p in store.presets
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


def _preserve_unreadable(path: str) -> None:
    """If a presets file exists but doesn't parse, move it aside to `<path>.corrupt` before we
    overwrite it.

    `load()` is best-effort by design, so an unreadable file reads as an empty store and the app
    seeds a fresh one — at which point the first save would clobber presets the app never saw.
    Best-effort itself: any failure here just leaves the overwrite to proceed."""
    if not os.path.isfile(path):
        # Nothing there, or something that isn't a regular file (a directory in the way). Only a
        # FILE is ours to move aside — a directory stays put and the write fails loudly, which
        # is the honest outcome.
        return
    readable = False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        readable = isinstance(data, dict) and data.get("version") in (
            FILE_VERSION,
            *_LEGACY_VERSIONS,
        )
    except Exception:
        readable = False  # unparseable / unreadable → worth keeping a copy of
    if readable:
        return
    try:
        os.replace(path, path + ".corrupt")
    except OSError:
        pass  # can't preserve it (a directory, no permission) — let the write proceed and report


def adopt(src_config_path: str, dst_config_path: str):
    """Move the presets belonging to `src_config_path` alongside `dst_config_path`, returning how
    many were adopted — or None when there was nothing to do.

    omo 4.19.3+ relocates the config to `~/.omo/omo.jsonc`, and presets follow the config
    (`presets_path`), so an upgrade would otherwise strand every preset at the old location and
    start the user over from a single default. This is the one-time hand-over.

    The delete is GATED on the copy having landed: the destination is written, read back, and
    checked for the same preset names before the original is removed. Any failure leaves the
    original exactly where it was — `write()` raises, and a mismatch returns without deleting —
    because the presets file has no backup ring, so a bad adoption would be unrecoverable.

    Only ever called for the DEFAULT unified path (see `Session.__post_init__`); pointing
    `--config` at a scratch file must never drag the real presets off to a temp directory."""
    src, dst = presets_path(src_config_path), presets_path(dst_config_path)
    if os.path.abspath(src) == os.path.abspath(dst):
        return None
    if os.path.exists(dst) or not os.path.isfile(src):
        return None
    store = load(src_config_path)
    if store.is_empty():
        return None

    landed = write(dst_config_path, store)  # raises on a failed write — nothing deleted
    if store_fingerprint(landed) != store_fingerprint(store):
        # Whole-store comparison, not just the names: this delete is irreversible and the presets
        # file has no backup ring, so the gate checks the assignments and `active` too.
        return None  # read-back disagrees: keep the original and adopt nothing
    try:
        os.remove(src)
    except OSError:
        pass  # copy is safely in place; a stale original is untidy, not lossy
    return len(landed.presets)


def write(config_path: str, store: Store) -> Store:
    """Persist the whole store (atomic temp+rename) and return it as READ BACK FROM DISK, so
    the caller renders what actually landed.  RAISES on failure (read-only dir, the path taken
    by a directory) — app.py notifies; see the module docstring.

    This is the ONLY function here that touches disk, and app.py calls it only from `s`,
    alongside the config write. That is what keeps config-equals-active-preset true at rest."""
    path = presets_path(config_path)
    _preserve_unreadable(path)
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(_payload(store))
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass  # best-effort cleanup; the real error is the one we re-raise
        raise
    return load(config_path)


def capture(name: str, managed: dict) -> Preset:
    """Snapshot the editable state under `name` — staged edits included. Deep-copied IN
    (`_as_map`), so later edits can't mutate the stored preset.

    `managed` is the node that HOLDS `agents`/`categories`, not necessarily the whole config:
    on omo's unified document that is `cfg["[opencode]"]`. Callers pass `Session.managed`; this
    module stays a leaf and never has to know which scope it is looking at."""
    return Preset(
        name=name,
        saved_at=timestamp(),
        agents=_as_map(managed.get("agents")),
        categories=_as_map(managed.get("categories")),
    )


def assignments(preset: Preset) -> tuple:
    """`(agents, categories)` deep-copied OUT, ready to replace cfg's two subtrees.  Copying on
    the way out is what stops a switch from aliasing the preset (which would make every later
    edit silently rewrite the preset you switched away from)."""
    return _as_map(preset.agents), _as_map(preset.categories)


def seeded(managed: dict, name: str = DEFAULT_NAME) -> Store:
    """The first-launch store: preset 1 captured from your existing config, active.  Returned
    IN MEMORY, never written — the first `s` materializes it (one write rule).  Until then a
    fresh launch that changes nothing stays clean, and re-seeding next time is identical.
    `managed` is the agents/categories-holding node — see `capture`."""
    return Store(presets=[capture(name, managed)], active=0)


def matching_index(store: Store, managed: dict):
    """Index of the preset whose assignments equal the config's, or None.  Used at launch to
    answer "does the config still reflect one of the presets?" — the invariant's only real test.
    `managed` is the agents/categories-holding node — see `capture`."""
    target = fingerprint(managed.get("agents"), managed.get("categories"))
    for i, preset in enumerate(store.presets):
        if fingerprint(preset.agents, preset.categories) == target:
            return i
    return None


def model_count(preset: Preset) -> int:
    """How many targets the preset actually assigns — agents, their ultrawork/compaction
    sub-targets, and categories.  The `12 models` half of the pane's border subtitle."""
    total = 0
    for data in _as_map(preset.agents).values():
        if not isinstance(data, dict):
            continue
        if data.get("model"):
            total += 1
        for kind in _SUBKINDS:
            sub = data.get(kind)
            if isinstance(sub, dict) and sub.get("model"):
                total += 1
    for data in _as_map(preset.categories).values():
        if isinstance(data, dict) and data.get("model"):
            total += 1
    return total


def sanitize_name(text: str, index: int) -> str:
    """Clean a typed preset name: drop non-printables (a newline in an OptionList prompt
    renders as two lines and would double a row's height in a bounded card), collapse
    whitespace, cap at MAX_NAME.  Empty (or all-junk) falls back to `preset <N>`, N being the
    1-based index."""
    # Non-printables become spaces rather than vanishing (so "a\nb" reads "a b", not "ab"),
    # then whitespace runs collapse — one row, one line, no stray padding.
    cleaned = " ".join(
        "".join(ch if ch.isprintable() else " " for ch in _render_safe(text or "")).split()
    )
    return cleaned[:MAX_NAME].strip() or f"preset {index + 1}"


# omo spells one reasoning level three ways, and which one a node carries depends on when it was
# written and how deep it sits. Two nodes that MEAN the same thing must not read as a difference
# here, or a config omodel never edited reconciles against no preset — permanent `sync_conflict`,
# permanently dirty, and no-op saves claiming `changed: true`. Folded to a key no real config can
# collide with; comparison only, never written (see `fingerprint`).
_REASONING_KEYS = ("reasoning", "variant", "reasoningEffort")
_CANON_REASONING = "\0reasoning"


def _canon(node: dict) -> dict:
    """A copy of `node` with whichever reasoning spelling it carries folded into one key, in omo's
    own read precedence (`session.read_variant`). A blank or non-string level counts as unset, so
    `"reasoning": null` and an absent key compare equal — they resolve the same in omo."""
    level = None
    for key in _REASONING_KEYS:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            level = value
            break
    out = {k: v for k, v in node.items() if k not in _REASONING_KEYS}
    if level is not None:
        out[_CANON_REASONING] = level
    return out


def _clean(agents: dict) -> dict:
    """config_io._clean_agents' rule, re-implemented to keep this module a leaf: an
    ultrawork/compaction sub-object with no `model` serializes away, so it must not read as a
    difference here either. Reasoning spellings are folded at both levels (`_canon`)."""
    out: dict = {}
    for name, data in agents.items():
        if not isinstance(data, dict):
            out[name] = data
            continue
        node: dict = {}
        for k, v in data.items():
            if k in _SUBKINDS and isinstance(v, dict):
                if not v.get("model"):
                    continue  # empty sub-object serializes away
                node[k] = _canon(v)
            else:
                node[k] = v
        out[name] = _canon(node)
    return out


def fingerprint(agents, categories) -> str:
    """Identity of an assignment set, for comparison only.

    Cleaned (empty sub-objects dropped), reasoning spellings folded (`_canon`), and `sort_keys`ed
    — so neither key order, nor an added-but-unfilled sub-target, nor `variant` vs `reasoning`
    vs `reasoningEffort` reads as a difference. The spelling folding matters because omodel
    normalizes a preset's spelling in memory while the config keeps whatever is on disk: without
    it, `reasoning: high` in the store never equals `variant: high` in the config, and a config
    the user never touched reports a sync conflict on every launch.
    Its one job is `matching_index` (does the config still reflect a preset?); it is never an
    input to what gets WRITTEN, so a disagreement can only mis-answer that question, never
    corrupt a save."""
    return json.dumps(
        {
            "agents": _clean(_as_map(agents)),
            "categories": {n: _canon(d) if isinstance(d, dict) else d
                           for n, d in _as_map(categories).items()},
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def store_fingerprint(store: Store) -> str:
    """Identity of the WHOLE store (contents + names + which is active), for dirtiness: app.py
    compares this against the store as of launch to know whether `s` has anything to persist.
    `saved_at` is deliberately excluded — re-stamping alone must not make the app look dirty."""
    return json.dumps(
        {
            "active": store.active,
            "presets": [
                {"name": p.name, "assignments": fingerprint(p.agents, p.categories)}
                for p in store.presets
            ],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
