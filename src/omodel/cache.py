"""On-disk cache for the (~3s) `opencode` CLI calls.  DESIGN.md §cache.py.

The two opencode subprocesses (`opencode models` and `opencode models <prov> --verbose`)
each take ~3s, so their stdout is cached under ~/.cache/omodel/ for 24h. catalog.py reads
through this cache; the app/CLI bust it on an explicit `--refresh-models` / `r`.

Design notes:
  * Location: $OMODEL_CACHE_DIR, else $XDG_CACHE_HOME/omodel, else ~/.cache/omodel — FLAT
    (one file per key, no subfolders). Tests set $OMODEL_CACHE_DIR to a tmp dir.
  * Files are `<key>.json` wrappers: {version, fetched_at, args, stdout}. fetched_at is an
    explicit epoch (not mtime — survives copies); version auto-invalidates on format change.
  * Best-effort: every read tolerates missing/corrupt files (→ miss), every write swallows
    OSError (read-only home, full disk). The cache NEVER fails or blocks the caller.
"""
from __future__ import annotations

import json
import os
import re
import time

# Bump when the on-disk wrapper shape changes → silently invalidates old files.
CACHE_VERSION = 1

# 24h, overridable via $OMODEL_CACHE_TTL (seconds) for power users / debugging.
_TTL_DEFAULT = 86400


def _ttl_default() -> float:
    raw = os.environ.get("OMODEL_CACHE_TTL")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _TTL_DEFAULT


def cache_dir() -> str:
    """Resolve the cache directory (does NOT create it):
    $OMODEL_CACHE_DIR, else $XDG_CACHE_HOME/omodel, else ~/.cache/omodel."""
    override = os.environ.get("OMODEL_CACHE_DIR")
    if override:
        return override
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = xdg if xdg else os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "omodel")


def _path_for(key: str) -> str:
    """`<cache_dir>/<sanitized key>.json`. Provider ids are `[a-z0-9_-]+`, but sanitize
    defensively so a stray '/' (e.g. a verbose key) can't escape the cache dir."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)
    return os.path.join(cache_dir(), f"{safe}.json")


def read(key: str, ttl_seconds: float | None = None) -> str | None:
    """Cached stdout for `key` if present and younger than the TTL, else None.
    A missing, corrupt, wrong-version, or expired entry is a miss (returns None)."""
    if ttl_seconds is None:
        ttl_seconds = _ttl_default()
    try:
        with open(_path_for(key), encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(blob, dict) or blob.get("version") != CACHE_VERSION:
        return None
    fetched_at = blob.get("fetched_at")
    stdout = blob.get("stdout")
    if not isinstance(fetched_at, (int, float)) or not isinstance(stdout, str):
        return None
    if (time.time() - fetched_at) > ttl_seconds:
        return None
    return stdout


def write(key: str, stdout: str, args: list | None = None) -> None:
    """Cache `stdout` under `key` (atomic write). Best-effort: any OSError is swallowed so
    a non-writable cache never breaks the caller."""
    path = _path_for(key)
    tmp = f"{path}.tmp-{os.getpid()}"
    blob = {
        "version": CACHE_VERSION,
        "fetched_at": time.time(),
        "args": list(args) if args else None,
        "stdout": stdout,
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(blob, f)
        os.replace(tmp, path)  # atomic on POSIX/Windows
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def age_seconds(key: str) -> float | None:
    """Seconds since `key` was cached, or None if there is no (readable) entry.
    Ignores the TTL — a cache-introspection helper (staleness in seconds); not currently
    surfaced in the UI, kept as part of the cache API (CONTRACTS.md)."""
    try:
        with open(_path_for(key), encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, ValueError):
        return None
    fetched_at = blob.get("fetched_at") if isinstance(blob, dict) else None
    if not isinstance(fetched_at, (int, float)):
        return None
    return max(0.0, time.time() - fetched_at)


def clear() -> None:
    """Remove every cached entry (the *.json files we own) from the cache dir. Best-effort;
    used by the explicit `--refresh-models` / `r` refresh. Never touches non-.json files."""
    d = cache_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return
    for name in names:
        # Our cache files, plus any orphaned atomic-write temp (`<name>.json.tmp-<pid>`) left
        # by a crash between open() and os.replace().
        if name.endswith(".json") or ".json.tmp-" in name:
            try:
                os.remove(os.path.join(d, name))
            except OSError:
                pass
