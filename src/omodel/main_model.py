"""OmO Native's main-session model — the `main` target.  DESIGN.md §Main model.

The interactive `omo` TUI starts a fresh session on `defaultProvider` + `defaultModel` from the
engine's `settings.json`, and on that model's remembered reasoning level from `state.json`
(`modelThinkingLevels["provider/model"]`, which senpi reads before `defaultThinkingLevel`).
Both files sit in omo's agent dir (`~/.omo/agent`). omo's own `[native].model_profile` is NOT
used: senpi-omo applies it only to desktop (rpc) and headless runs, never the TUI.

These are omo's files, not omodel's: omo rewrites both as you use it. So this module edits only
the keys above — every other key is kept — re-reads the files at save time rather than writing
back what it loaded, and copies each file verbatim into the config's `.backup/` before changing
it. A `settings.jsonc` (which omo prefers when present) is read but never written: a clean JSON
rewrite would drop its comments.

The in-memory form is a cfg-style node, `{"model": "provider/model", "reasoning": level}`, so
the session edits it exactly as it edits an agent. A missing level means "leave omo's
remembered level alone" — omodel only ever writes a level you chose.

Depends on `catalog.native_agent_dir` and `config_io` paths only; no Textual.
"""
from __future__ import annotations

import contextlib
import copy
import difflib
import glob
import json
import os
import shutil
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import catalog as catalog_mod
from . import config_io

SETTINGS = "settings.json"
SETTINGS_JSONC = "settings.jsonc"
STATE = "state.json"
_LEVELS = "modelThinkingLevels"
_LAST_ON = "modelLastOnThinkingLevels"
_RING = 20


def agent_dir_for(config_path: str) -> str:
    """Where the main-model files live for `config_path`: omo's agent dir for the default
    `~/.omo/omo.jsonc` (or `omo.json`), else `<config_dir>/agent`. The second rule keeps a
    `--config /tmp/x.jsonc` run — the documented way to test — off the real `~/.omo/agent`
    (a config INSIDE `~/.omo` other than omo.jsonc still maps to `~/.omo/agent`, its own
    `<config_dir>/agent`)."""
    here = os.path.abspath(config_path)
    unified_dir = os.path.dirname(os.path.abspath(config_io.unified_config_path()))
    if os.path.dirname(here) == unified_dir and os.path.basename(here) in ("omo.jsonc", "omo.json"):
        return catalog_mod.native_agent_dir()
    return os.path.join(os.path.dirname(here), "agent")


def _read_object(path: str):
    """`(dict, None)` for a readable JSON(C) object, `({}, None)` for a missing file, and
    `({}, reason)` for anything else."""
    import json5

    try:
        with open(path, encoding="utf-8") as f:
            data = json5.load(f)
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError) as exc:
        return {}, f"cannot read {path}: {exc}"
    if not isinstance(data, dict):
        return {}, f"{path} is not a JSON object"
    return data, None


@dataclass
class MainState:
    """What `load` read: the live node, and why it may not be written (`problem`)."""

    agent_dir: str
    node: dict = field(default_factory=dict)
    problem: str | None = None
    # `enabledModels` in settings.json: senpi starts on the first of these BEFORE the default,
    # so a main model set here does not take effect while it is non-empty.
    shadowed_by: list | None = None

    @property
    def settings_path(self) -> str:
        return os.path.join(self.agent_dir, SETTINGS)

    @property
    def state_path(self) -> str:
        return os.path.join(self.agent_dir, STATE)


def _level_for(state: dict, settings: dict, model: str):
    """The remembered level for `model`: `state.json` first, then a pre-migration
    `settings.json` copy of the same map (senpi moves it to state.json on its next write)."""
    for source in (state, settings):
        levels = source.get(_LEVELS)
        if isinstance(levels, dict):
            value = levels.get(model)
            if isinstance(value, str) and value in catalog_mod.NATIVE_LADDER:
                return value
    return None


def load(config_path: str) -> MainState:
    """Read the main model for `config_path`. Never raises: an unreadable file becomes a
    `problem` (shown, and every write refused) and an empty node."""
    agent_dir = agent_dir_for(config_path)
    out = MainState(agent_dir=agent_dir)
    jsonc = os.path.join(agent_dir, SETTINGS_JSONC)
    if os.path.exists(jsonc):
        settings, problem = _read_object(jsonc)
        out.problem = problem or (
            f"omo reads {jsonc}; omodel edits only {SETTINGS} (it would drop the comments)"
        )
    else:
        settings, out.problem = _read_object(out.settings_path)
    state, state_problem = _read_object(out.state_path)
    out.problem = out.problem or state_problem
    provider, model = settings.get("defaultProvider"), settings.get("defaultModel")
    if isinstance(provider, str) and provider and isinstance(model, str) and model:
        value = f"{provider}/{model}"
        out.node["model"] = value
        level = _level_for(state, settings, value)
        if level is not None:
            out.node["reasoning"] = level
    enabled = settings.get("enabledModels")
    if isinstance(enabled, list) and enabled:
        out.shadowed_by = [str(e) for e in enabled]
    return out


def remembered_level(agent_dir: str, model: str):
    """omo's remembered level for `model` ("provider/model"), or None."""
    state, _ = _read_object(os.path.join(agent_dir, STATE))
    settings, _ = _read_object(os.path.join(agent_dir, SETTINGS))
    return _level_for(state, settings, model)


def fingerprint(node) -> str:
    """Identity of a main node, for dirtiness."""
    node = node if isinstance(node, dict) else {}
    return json.dumps({k: node.get(k) for k in ("model", "reasoning")}, sort_keys=True)


def _apply(settings: dict, state: dict, node: dict):
    """`(settings, state)` with `node` applied — copies; every other key kept as it was."""
    settings, state = copy.deepcopy(settings), copy.deepcopy(state)
    model = node.get("model")
    if isinstance(model, str) and "/" in model:
        provider, bare = model.split("/", 1)
        settings["defaultProvider"] = provider
        settings["defaultModel"] = bare
        level = node.get("reasoning")
        # What senpi reads: state.json's map, else a pre-migration settings.json copy.
        current = state.get(_LEVELS)
        if not isinstance(current, dict):
            current = settings.get(_LEVELS)
        remembered = current.get(model) if isinstance(current, dict) else None
        # Only a CHANGED level is written: a picked model's remembered level comes back here
        # unchanged (`Session.set_model`), and rewriting it would touch state.json for nothing.
        if isinstance(level, str) and level and level != remembered:
            # What senpi's setModelThinkingLevel writes: the level, and — unless it is `off` —
            # the level `/reasoning on` restores.
            for key, write in ((_LEVELS, True), (_LAST_ON, level != "off")):
                if not write:
                    continue
                levels = state.get(key)
                if not isinstance(levels, dict):
                    # A pre-migration agent dir keeps the maps in settings.json. senpi copies a
                    # map into state.json only when state.json LACKS it, so start from that copy
                    # or every other model's remembered level is lost on omo's next start.
                    levels = settings.get(key)
                levels = dict(levels) if isinstance(levels, dict) else {}
                levels[model] = level
                state[key] = levels
    else:
        settings.pop("defaultProvider", None)
        settings.pop("defaultModel", None)
    return settings, state


def _text(data: dict) -> str:
    """senpi's own serialization (`JSON.stringify(settings, null, 2)`, no trailing newline)."""
    return json.dumps(data, indent=2, ensure_ascii=False)


def _planned(main: MainState, node: dict):
    """`[(path, old_text_or_None, new_text)]` for each file the node would change, read fresh
    from disk. Raises ValueError when a file can't be read — a write would clobber it."""
    settings, problem = _read_object(main.settings_path)
    state, state_problem = _read_object(main.state_path)
    if problem or state_problem:
        raise ValueError(problem or state_problem)
    new_settings, new_state = _apply(settings, state, node)
    out = []
    for path, old, new in ((main.settings_path, settings, new_settings),
                           (main.state_path, state, new_state)):
        if new == old:
            continue
        try:
            with open(path, encoding="utf-8", newline="") as f:
                old_text = f.read()
        except FileNotFoundError:
            old_text = None
        out.append((path, old_text, _text(new)))
    return out


def _with_newline(text: str) -> str:
    """senpi writes no trailing newline; compare line-wise without a spurious last-line change."""
    return text if not text or text.endswith("\n") else text + "\n"


def diff_text(main: MainState, node: dict) -> str:
    """Unified diff of what `save` would write to settings.json / state.json."""
    if main.problem:
        return ""
    try:
        planned = _planned(main, node)
    except ValueError as exc:
        return f"(cannot preview the main model: {exc})\n"
    chunks = []
    for path, old_text, new_text in planned:
        chunks.append("".join(difflib.unified_diff(
            _with_newline(old_text or "").splitlines(keepends=True),
            (new_text + "\n").splitlines(keepends=True),
            fromfile=path, tofile=path + " (new)",
        )))
    return "".join(chunks)


def _write(path: str, text: str, backup_dir: str) -> str | None:
    """Back `path` up verbatim, then replace it atomically (through a symlink, keeping its mode,
    as `config_io.save` does). Returns the backup path, or None for a file that didn't exist."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    backup = None
    target = os.path.realpath(path)
    mode = None
    with contextlib.suppress(OSError):
        mode = stat.S_IMODE(os.stat(target).st_mode)
    tmp = f"{target}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            with contextlib.suppress(OSError):
                f.flush()
                os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        if os.path.exists(path):
            os.makedirs(backup_dir, exist_ok=True)
            now = datetime.now(UTC)
            ts = now.strftime("%Y%m%d-%H%M%S") + f".{now.microsecond // 1000:03d}"
            stem = os.path.splitext(os.path.basename(path))[0]
            backup = os.path.join(backup_dir, f"{stem}-{ts}.json")
            shutil.copy2(path, backup)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        if backup is not None:
            with contextlib.suppress(OSError):
                os.remove(backup)
        raise
    stem = os.path.splitext(os.path.basename(path))[0]
    for old in sorted(glob.glob(os.path.join(backup_dir, f"{stem}-[0-9]*.json")))[:-_RING]:
        with contextlib.suppress(OSError):
            os.remove(old)
    return backup


def save(main: MainState, node: dict, config_path: str) -> list:
    """Write `node` into settings.json / state.json; returns the files written. Raises on a
    `problem`, an unreadable file, or a failed write. Backups go to the CONFIG's `.backup/`
    (`settings-<ts>.json`, `state-<ts>.json`, newest 20 each) — not listed by `--restore`."""
    if main.problem:
        raise ValueError(main.problem)
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(config_path)), ".backup")
    written = []
    for path, _old, new_text in _planned(main, node):
        _write(path, new_text, backup_dir)
        written.append(path)
    return written
