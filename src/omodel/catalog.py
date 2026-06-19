"""Availability from the `opencode` CLI.  DESIGN.md §catalog.py / §Data sources.

FROZEN CONTRACT — owned by the Core-logic specialist. Implement the bodies; do not
change the public signatures or the `Catalog` shape without the Lead reconciling
CONTRACTS.md (app.py and resolve.py depend on these exact shapes).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass

from . import cache

# Header line for `opencode models <provider> --verbose` output.
_HEADER_RE = re.compile(r"^(?P<prov>[a-z0-9_-]+)/(?P<model>\S+)$")

# Subprocess timeouts (seconds). opencode measures ~3s warm; the headroom guards against a
# hung CLI holding memory forever (each `--verbose` peaks ~320 MB). `--refresh` hits the
# network, so it gets a much longer budget.
_MODELS_TIMEOUT = 20
_VERBOSE_TIMEOUT = 20
_REFRESH_TIMEOUT = 90


class CatalogUnavailable(Exception):
    """`opencode` IS on PATH but `opencode models` exited != 0 or produced zero
    `provider/model` lines. (opencode MISSING from PATH is NOT this — `load()` returns
    an empty Catalog so the UI degrades to suggestions/add-model only.) The UI shows a
    "couldn't read models" banner + `r` retry."""


@dataclass
class Catalog:
    available: dict  # {provider: [model_id, ...]} — FIRST-SEEN order
    connected: list  # [provider, ...] — FIRST-SEEN order, NEVER a set

    def providers_for(self, model_id: str) -> list:
        """Connected providers that serve `model_id`, in first-seen order."""
        return [p for p in self.connected if model_id in self.available.get(p, [])]

    def detail(self, model_id: str, use_cache: bool = True):
        """On-demand `opencode models <prov> --verbose` for the RESOLVED provider
        (providers_for(model_id)[0]); brace-count + json.loads each record; pick the one
        whose header == `<prov>/<model_id>`. Returns a DISPLAY-ONLY dict
        {"context": int|None, "cost": {...}, "reasoning": bool, "image": bool} or None.
        NEVER read `--verbose.variants`/`.family` (decision #14).

        The ~3s `--verbose` call is cached per PROVIDER (one subprocess yields every model of
        that provider) under cache key `verbose-<prov>`; `use_cache=False` forces a live call.
        Still a blocking call on a miss — app.py invokes it from a worker, never the UI thread."""
        providers = self.providers_for(model_id)
        if not providers:
            return None
        prov = providers[0]

        stdout = cache.read(f"verbose-{prov}") if use_cache else None
        if stdout is None:
            try:
                result = subprocess.run(
                    ["opencode", "models", prov, "--verbose"],
                    capture_output=True,
                    text=True,
                    timeout=_VERBOSE_TIMEOUT,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return None
            if result.returncode != 0:
                return None
            stdout = result.stdout
            if use_cache:
                cache.write(
                    f"verbose-{prov}", stdout, ["opencode", "models", prov, "--verbose"]
                )

        return _parse_verbose_record(stdout, f"{prov}/{model_id}")


def _parse_models(stdout: str):
    """`opencode models` stdout → (available, connected). Split each line on the FIRST '/';
    both maps preserve FIRST-SEEN order (connected is a list, never a set)."""
    available: dict = {}
    connected: list = []
    for line in stdout.splitlines():
        line = line.strip()
        if "/" not in line:
            continue
        # Split on the FIRST '/' only.
        provider, model_id = line.split("/", 1)
        provider = provider.strip()
        model_id = model_id.strip()
        if not provider or not model_id:
            continue
        if provider not in available:
            available[provider] = []
            connected.append(provider)
        if model_id not in available[provider]:
            available[provider].append(model_id)
    return available, connected


def _parse_verbose_record(stdout: str, target_header: str):
    """Pick the `<prov>/<model>` record from `opencode models <prov> --verbose` stdout and
    return its DISPLAY-ONLY fields, or None. Splits records on header lines at column 0 and
    brace-counts each JSON block. NEVER reads `.variants`/`.family` (decision #14)."""
    lines = stdout.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if _HEADER_RE.match(line):
            header = line.strip()
            # Collect the JSON block for this record via brace counting.
            i += 1
            brace_depth = 0
            block_lines = []
            while i < len(lines):
                bl = lines[i]
                brace_depth += bl.count("{") - bl.count("}")
                block_lines.append(bl)
                i += 1
                if brace_depth <= 0 and block_lines:
                    break

            if header == target_header:
                try:
                    record = json.loads("\n".join(block_lines))
                except (json.JSONDecodeError, ValueError):
                    return None
                # Extract display-only fields; NEVER read .variants/.family.
                context = None
                limit = record.get("limit") or {}
                if isinstance(limit, dict):
                    context = limit.get("context")

                cost = record.get("cost")
                caps = record.get("capabilities") or {}
                reasoning = bool(caps.get("reasoning"))
                image_caps = caps.get("input") or {}
                image = bool(image_caps.get("image"))

                return {
                    "context": context,
                    "cost": cost,
                    "reasoning": reasoning,
                    "image": image,
                }
        else:
            i += 1

    return None


def load(opencode_bin: str = "opencode", use_cache: bool = True) -> Catalog:
    """`opencode models` → Catalog. Split each line on the FIRST `/`.
    Error rule (DESIGN §Data sources — the single definition):
      * `opencode` not on PATH        → return Catalog(available={}, connected=[])
      * exit != 0 OR zero lines parsed → raise CatalogUnavailable

    With `use_cache` (default), a fresh `~/.cache/omodel/models.json` (≤24h) is served
    instead of shelling out — a warm launch is instant. opencode presence is still checked
    first, so 'not on PATH → empty' is unchanged (the cache is a perf layer, not a fallback).
    A live, successful run rewrites the cache. Tests must NOT hard-assert the count (varies)."""
    if shutil.which(opencode_bin) is None:
        return Catalog(available={}, connected=[])

    if use_cache:
        cached = cache.read("models")
        if cached is not None:
            available, connected = _parse_models(cached)
            if available:
                return Catalog(available=available, connected=connected)
            # Empty/garbage cache → ignore it and go live.

    try:
        result = subprocess.run(
            [opencode_bin, "models"],
            capture_output=True,
            text=True,
            timeout=_MODELS_TIMEOUT,
        )
    except FileNotFoundError:
        return Catalog(available={}, connected=[])
    except subprocess.TimeoutExpired as exc:
        raise CatalogUnavailable(
            f"`{opencode_bin} models` timed out after {_MODELS_TIMEOUT}s"
        ) from exc

    if result.returncode != 0:
        raise CatalogUnavailable(
            f"`{opencode_bin} models` exited with code {result.returncode}"
        )

    available, connected = _parse_models(result.stdout)

    if not available:
        raise CatalogUnavailable(
            f"`{opencode_bin} models` produced zero provider/model lines"
        )

    if use_cache:
        cache.write("models", result.stdout, [opencode_bin, "models"])

    return Catalog(available=available, connected=connected)


def refresh(opencode_bin: str = "opencode") -> Catalog:
    """Force opencode to re-fetch upstream (`opencode models --refresh`), rebuild the local
    cache from the result, and return the fresh Catalog. The explicit, manual refresh behind
    the CLI `--refresh-models` flag and the app's `r` key — NEVER on the hot path (it does a
    network re-fetch). app.py runs it in a worker so it never blocks the UI thread.

    Same error contract as load(): not on PATH → empty Catalog (+ cache cleared);
    exit != 0 / zero lines → CatalogUnavailable."""
    if shutil.which(opencode_bin) is None:
        cache.clear()
        return Catalog(available={}, connected=[])

    try:
        result = subprocess.run(
            [opencode_bin, "models", "--refresh"],
            capture_output=True,
            text=True,
            timeout=_REFRESH_TIMEOUT,
        )
    except FileNotFoundError:
        cache.clear()
        return Catalog(available={}, connected=[])
    except subprocess.TimeoutExpired as exc:
        raise CatalogUnavailable(
            f"`{opencode_bin} models --refresh` timed out after {_REFRESH_TIMEOUT}s"
        ) from exc

    if result.returncode != 0:
        raise CatalogUnavailable(
            f"`{opencode_bin} models --refresh` exited with code {result.returncode}"
        )

    available, connected = _parse_models(result.stdout)
    if not available:
        raise CatalogUnavailable(
            f"`{opencode_bin} models --refresh` produced zero provider/model lines"
        )

    # A refresh invalidates ALL opencode-derived cache (the model list + every verbose-*),
    # then pre-warms the model list from this run's output so the next launch is instant.
    cache.clear()
    cache.write("models", result.stdout, [opencode_bin, "models"])

    return Catalog(available=available, connected=connected)
