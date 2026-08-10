"""Shared test helpers (imported as `from _helpers import …` — pytest's default prepend
import mode puts this directory on sys.path, no package/__init__.py needed).

One canonical builder for the fake `opencode models <prov> --verbose` cache blob, so the
format lives in exactly one place — it was previously copy-pasted across three test files,
and a change to opencode's verbose shape needed three coordinated edits.
"""
from __future__ import annotations

import copy
import json
import os

from omodel import cache
from omodel.suggestions import Family, Suggestions
from omodel.suggestions import load as load_suggestions


def seed_verbose(provider: str, records: dict) -> None:
    """Seed a cached `verbose-<provider>` entry so Catalog.variants_for()/detail() read it
    without a subprocess (the suite stubs opencode + isolates the cache dir via conftest).
    `records` maps model_id -> [variant, ...]; mirrors `opencode models <prov> --verbose`: a
    `provider/model` header line followed by a JSON block whose `variants` is an OBJECT keyed
    by variant name. An empty list → `variants: {}` — opencode's 'no variants' shape (kimi)."""
    parts = []
    for model, variants in records.items():
        parts.append(f"{provider}/{model}")
        parts.append(json.dumps({"id": model, "variants": {v: {} for v in variants}}))
    cache.write(
        f"verbose-{provider}",
        "\n".join(parts) + "\n",
        ["opencode", "models", provider, "--verbose"],
    )


def age_cache_entry(key: str, seconds: float) -> None:
    """Backdate a cached entry's `fetched_at` by `seconds`, in place — how you get a TTL-expired
    entry without sleeping or moving the clock. It has to be the STORED epoch rather than the
    file's mtime, since that is what `cache.read()` checks. Used by the tests around
    `variants_for`'s stale read, on both sides of the TTL boundary."""
    path = os.path.join(cache.cache_dir(), f"{key}.json")
    with open(path, encoding="utf-8") as f:
        blob = json.load(f)
    blob["fetched_at"] -= seconds
    with open(path, "w", encoding="utf-8") as f:
        json.dump(blob, f)


# ---------------------------------------------------------------------------
# Frozen suggestion chains — for tests about resolve's LOGIC, not omo's data
# ---------------------------------------------------------------------------

# Verbatim copy of omo 4.19.0's `sisyphus` fallbackChain, renamed to `probe` and frozen here.
# Rationale: resolve's logic tests (chain-order expansion, dedicated-before-gateway, variant
# precedence, the k2p5 alias, same-line substitution) are about resolve, not about which
# models omo currently recommends -- but pinned against the LIVE bundled data they broke on
# every upstream sweep. omo 4.19.1 alone replaced gpt-5.5 -> gpt-5.6-sol, claude-opus-4-7 ->
# -4-8 and kimi-k2.5/k2.6/k2p5 -> kimi-k3, failing 6 tests that the product handled correctly.
#
# This chain is deliberately a real one: it exercises an entry with a variant (opus/max), an
# entry absent from the test catalog that must fall back to a same-line substitute (kimi-k3),
# a dedup of that substitute against the chain's own entry (kimi-k2.6), the k2p5 alias, an
# entry with no variant (kimi-k2.5), and an unknown-family id (big-pickle).
#
# Do NOT sync this to new omo releases -- churn is the thing it exists to be immune to. Real
# bundled data still flows through resolve in TestRealDataIntegration.
FROZEN_AGENTS = {
    "probe": {
        "requiresProvider": [], "requiresModel": "", "requiresAnyModel": True,
        "fallbackChain": [
            {"providers": ["anthropic", "github-copilot", "opencode", "vercel"],
             "model": "claude-opus-4-7", "variant": "max"},
            {"providers": ["opencode-go", "kimi-for-coding", "moonshotai", "opencode", "vercel"],
             "model": "kimi-k3"},
            {"providers": ["opencode-go", "vercel"], "model": "kimi-k2.6"},
            {"providers": ["kimi-for-coding"], "model": "k2p5"},
            {"providers": ["opencode", "moonshotai", "moonshotai-cn", "vercel"],
             "model": "kimi-k2.5"},
            {"providers": ["openai", "github-copilot", "opencode", "vercel"],
             "model": "gpt-5.5", "variant": "medium"},
            {"providers": ["zai-coding-plan", "opencode", "vercel"], "model": "glm-5"},
            {"providers": ["opencode"], "model": "big-pickle"},
        ],
    },
}

FROZEN_CATEGORIES = {
    "probe-cat": {
        "fallbackChain": [
            {"providers": ["openai", "opencode"], "model": "gpt-5.5", "variant": "medium"},
            {"providers": ["zai-coding-plan", "opencode"], "model": "glm-5"},
        ],
    },
}


def frozen_suggestions() -> Suggestions:
    """Suggestions with FROZEN chains but the REAL family registry.

    Chains churn weekly and are frozen above. Families are NOT frozen: `detect_family` is a
    faithful port of omo's heuristic and substitution depends on it, so a frozen copy would
    quietly stop testing the real thing. Families are also stable and independently pinned
    (test_detect_family.py's family-count pin + the FAMILY_VENDOR key-set), so a change there
    is meaningful and *should* reach these tests.

    NOT sufficient for the `⚠ variant` tests: those need a family that lacks a variant, which
    is exactly the property a real family can lose overnight — see PROBE_FAMILY below.
    """
    real = load_suggestions()
    return Suggestions(
        meta={"omoVersion": "frozen-for-tests", "omoCommit": "", "generatedAt": ""},
        agents=copy.deepcopy(FROZEN_AGENTS),
        categories=copy.deepcopy(FROZEN_CATEGORIES),
        families=real.families,
        known_variants=real.known_variants,
    )


# ---------------------------------------------------------------------------
# Frozen probe FAMILY — for the ⚠ variant tests, which are about logic not data
# ---------------------------------------------------------------------------

# The `⚠ variant` tests need a family that demonstrably does NOT declare some variant. Three
# of them borrowed a real one ("glm has no max"), which is omo's DATA, not resolve's logic --
# and omo 5.0.0-beta.4 gave glm `max`, deleting the premise all three were built on and
# reddening them on a release that changed nothing about the product.
#
# frozen_suggestions() is no help here: it freezes CHAINS and deliberately keeps the REAL
# family registry (substitution + detect_family parity must exercise the real heuristic). So
# freeze ONE EXTRA family instead of the registry. PROBE_FAMILY declares low/medium/high and
# never `max`, so "this family lacks the variant" is a property of the fixture that upstream
# cannot revoke.
PROBE_MODEL = "probe-zeta"

PROBE_FAMILY = Family(
    family="probe-zeta",
    pattern=None,
    includes=[PROBE_MODEL],
    variants=["low", "medium", "high"],   # deliberately NO "max" — that is the whole point
    reasoning_efforts=[],
    reasoning_effort_aliases={},
    supports_thinking=False,
)


def probe_family_suggestions() -> Suggestions:
    """Real families with PROBE_FAMILY PREPENDED; no chains (these tests synthesize their own).

    Prepended, not appended, so first-match-wins puts PROBE_MODEL on the probe family BY
    CONSTRUCTION -- an upstream family that someday matches the id cannot steal it, and the
    fixture cannot rot into testing nothing. The real families stay behind it, so every other
    id in these tests still resolves through the real heuristic.

    PROBE_FAMILY is absent from FAMILY_VENDOR on purpose: vendor() then yields None, the probe
    provider counts zero vendors, and it is treated as dedicated -- one row per provider, which
    is what these single-entry tests assert.
    """
    real = load_suggestions()
    return Suggestions(
        meta={"omoVersion": "frozen-for-tests", "omoCommit": "", "generatedAt": ""},
        agents={},
        categories={},
        families=[PROBE_FAMILY] + list(real.families),
        known_variants=real.known_variants,
    )
