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

    def detail(self, model_id: str, use_cache: bool = True, provider: str = None):
        """On-demand `opencode models <prov> --verbose` for `provider` — when given AND it
        serves the model — else the RESOLVED provider (providers_for(model_id)[0]). The detail
        pane passes the current assignment's provider so an `opencode/x` assignment shows the
        gateway's record (its cost can differ), never silently the dedicated provider's.
        Brace-count + json.loads each record; pick the one whose header == `<prov>/<model_id>`.
        Returns a DISPLAY-ONLY dict
        {"context": int|None, "cost": {...}, "reasoning": bool, "image": bool} or None.
        Reads NEITHER `--verbose.variants` NOR `.family`: variants are sourced separately by
        variants_for (the decision #14 picker carve-out); `.family` is never read.

        The ~3s `--verbose` call is cached per PROVIDER (one subprocess yields every model of
        that provider) under cache key `verbose-<prov>`; `use_cache=False` forces a live call.
        Still a blocking call on a miss — app.py invokes it from a worker, never the UI thread."""
        providers = self.providers_for(model_id)
        if provider is not None and provider in providers:
            prov = provider
        elif providers:
            prov = providers[0]
        else:
            return None

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

    def variants_for(self, provider: str, model: str) -> list:
        """The variant names opencode exposes for (provider, model), read from the CACHED
        `opencode models <prov> --verbose` output — NEVER a fresh subprocess, so it is safe to
        call on the UI thread. This is the authoritative variant source for the model pickers:
        opencode's per-(provider, model) `variants` map, not the heuristic family registry (the
        deliberate reversal of decision #14 for variants — `.family` is still never read).

        Returns the FIRST NON-EMPTY variant set (lowercased, opencode's order) found across
        `provider` then any other connected provider serving `model`. A provider that reports an
        EMPTY `variants` object is treated as "no info from this endpoint, keep looking" rather
        than an authoritative "no variants" — the dedicated providers (zhipuai, moonshotai-cn)
        report `{}` for every model (DESIGN §Data sources), so trusting their emptiness would hide
        real variants that live in the `opencode` gateway's verbose (e.g. glm-5.2 → high/max). So:
        `provider`'s own non-empty set wins if it has one; else the gateway's (usually warm,
        covers ~every model); else []. Returns [] when NO cached provider reports a non-empty set
        — i.e. opencode genuinely lists none (kimi) OR nothing is cached for the model anywhere (a
        total miss): the caller offers nothing, we never guess. Same 24h cache as detail(); the
        `r` key / `--refresh-models` clears it."""
        tried = set()
        for prov in [provider, *self.providers_for(model)]:
            if prov in tried:
                continue
            tried.add(prov)
            stdout = cache.read(f"verbose-{prov}")
            if stdout is None:
                continue
            variants = _parse_verbose_variants(stdout, f"{prov}/{model}")
            if variants:  # non-empty → authoritative for this model; empty/None → keep looking
                return variants
        return []


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


def _find_verbose_record(stdout: str, target_header: str):
    """The parsed JSON record whose header line == `target_header` in
    `opencode models <prov> --verbose` stdout, or None (not found / unparseable). Records are
    split on header lines at column 0 (`_HEADER_RE`) and each JSON block is brace-counted. Shared
    scan for _parse_verbose_record (display fields) and _parse_verbose_variants (variant keys)."""
    lines = stdout.splitlines()
    i = 0
    while i < len(lines):
        if _HEADER_RE.match(lines[i]):
            header = lines[i].strip()
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
                    return json.loads("\n".join(block_lines))
                except (json.JSONDecodeError, ValueError):
                    return None
        else:
            i += 1

    return None


def _parse_verbose_record(stdout: str, target_header: str):
    """The DISPLAY-ONLY fields for the `<prov>/<model>` record, or None. Reads NEITHER
    `.variants` NOR `.family`: variants are read separately by _parse_verbose_variants /
    Catalog.variants_for (the deliberate decision #14 carve-out); `.family` is never read."""
    record = _find_verbose_record(stdout, target_header)
    if record is None:
        return None

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


def _parse_verbose_variants(stdout: str, target_header: str):
    """The variant names opencode exposes for the `<prov>/<model>` record — the KEYS of its
    `variants` object (opencode's order, lowercased) — or [] when that object is empty/missing,
    or None when the record isn't in this stdout (so Catalog.variants_for keeps looking in the
    next provider). Reads ONLY `.variants` (the deliberate decision #14 carve-out for the model
    pickers); still never `.family`."""
    record = _find_verbose_record(stdout, target_header)
    if record is None:
        return None
    variants = record.get("variants")
    if not isinstance(variants, dict):
        return []
    return [str(k).lower() for k in variants]


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
