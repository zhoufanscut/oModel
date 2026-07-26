"""test_detect_family.py — parity vs omo heuristics.

Tests encode the SPEC as per DESIGN.md §Verification check #4 and §suggestions.py.
Fixtures use REAL omo suggestion IDs from the bundled data/omo-suggestions.json.
"""
from __future__ import annotations

import re

import pytest

from omodel.suggestions import (
    FAMILY_VENDOR,
    Family,
    Suggestions,
    load,
    normalize_model_id,
)


@pytest.fixture(scope="module")
def sugg():
    """Load bundled suggestions once for the module."""
    return load()


# ---------------------------------------------------------------------------
# normalize_model_id
# ---------------------------------------------------------------------------

class TestNormalizeModelId:
    r"""re.sub(r'\.(\d+)', r'-\1', s).lower() — kimi-k2.7 → kimi-k2-7."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("kimi-k2.7", "kimi-k2-7"),         # dot-version → hyphen
            ("claude-3.5.1", "claude-3-5-1"),   # every dot-version
            ("GPT-5.5", "gpt-5-5"),             # …and lowercased
            ("deepseek-v4-pro", "deepseek-v4-pro"),  # no dots → unchanged
            ("k2p5", "k2p5"),                   # no dots → unchanged
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_model_id(raw) == expected


# ---------------------------------------------------------------------------
# detect_family parity (DESIGN §Verification #4) — REAL omo suggestion IDs
# ---------------------------------------------------------------------------

class TestDetectFamilyParity:

    @pytest.mark.parametrize(
        ("model_id", "family", "has", "lacks"),
        [
            ("kimi-k2.5", "kimi", (), ("max",)),
            ("kimi-k2.6", "kimi", (), ("max",)),
            # kimi-thinking is ordered BEFORE kimi, so k2p5 must not fall through to kimi.
            ("k2p5", "kimi-thinking", (), ()),
            # claude-opus is ordered BEFORE claude-non-opus (more specific wins).
            ("claude-opus-4-7", "claude-opus", ("max",), ()),
            ("claude-sonnet-4-6", "claude-non-opus", (), ("max",)),
            ("gpt-5.5", "gpt-5", ("xhigh",), ()),
            ("glm-5", "glm", (), ("max",)),
            ("deepseek-v4-pro", "deepseek", ("max",), ()),
        ],
    )
    def test_family_and_variants(self, sugg, model_id, family, has, lacks):
        """Each REAL omo suggestion id resolves to its family, carrying that family's
        variant list (the `max`/`xhigh` membership resolve's ⚠ warn keys on)."""
        fam = sugg.detect_family(model_id)
        assert fam is not None, f"{model_id} must resolve to a family"
        assert fam.family == family
        for v in has:
            assert v in fam.variants, f"{family} should offer {v!r}: {fam.variants}"
        for v in lacks:
            assert v not in fam.variants, f"{family} should NOT offer {v!r}: {fam.variants}"

    def test_unknown_model_returns_none(self, sugg):
        """An unrecognised model ID → None (e.g. opencode's big-pickle)."""
        fam = sugg.detect_family("big-pickle")
        assert fam is None


# ---------------------------------------------------------------------------
# Ordering guarantees (parity matters — DESIGN §suggestions.py)
# ---------------------------------------------------------------------------

class TestFamilyOrdering:

    @pytest.mark.parametrize(
        ("earlier", "later"),
        [
            ("openai-reasoning", "gpt-5"),
            ("kimi-thinking", "kimi"),
            ("claude-opus", "claude-non-opus"),
        ],
    )
    def test_specific_family_precedes_general(self, sugg, earlier, later):
        """detect_family is first-match-wins, so the more specific family must sort first."""
        families = [f.family for f in sugg.families]
        i, j = families.index(earlier), families.index(later)
        assert i < j, f"{earlier} ({i}) must precede {later} ({j})"


# ---------------------------------------------------------------------------
# Bundled data integrity (DESIGN §Verification check #5)
# ---------------------------------------------------------------------------

# Names, not just counts: a rename (one dropped + one added) keeps the count equal while
# silently changing which targets oModel offers. Target sets are stable upstream in a way
# chain lengths are not, so pinning them stays refresh-friendly. Module scope, not class
# attributes — a mutable class attribute trips RUF012.
AGENT_NAMES = {
    "atlas", "explore", "hephaestus", "librarian", "metis", "momus",
    "multimodal-looker", "oracle", "prometheus", "sisyphus", "sisyphus-junior",
}
CATEGORY_NAMES = {
    "artistry", "deep", "quick", "ultrabrain",
    "unspecified-high", "unspecified-low", "visual-engineering", "writing",
}


class TestBundledSuggestionsLoad:

    def test_loads_without_omo_checkout(self, sugg):
        """importlib.resources loads successfully with no omo checkout present."""
        assert sugg is not None

    def test_11_agents(self, sugg):
        assert len(sugg.agents) == 11, f"Expected 11 agents, got {len(sugg.agents)}: {list(sugg.agents)}"
        assert set(sugg.agents) == AGENT_NAMES, (
            f"agent set changed: +{set(sugg.agents) - AGENT_NAMES} "
            f"-{AGENT_NAMES - set(sugg.agents)}"
        )

    def test_8_categories(self, sugg):
        assert len(sugg.categories) == 8, f"Expected 8 categories, got {len(sugg.categories)}: {list(sugg.categories)}"
        assert set(sugg.categories) == CATEGORY_NAMES, (
            f"category set changed: +{set(sugg.categories) - CATEGORY_NAMES} "
            f"-{CATEGORY_NAMES - set(sugg.categories)}"
        )

    def test_15_families(self, sugg):
        assert len(sugg.families) == 15, f"Expected 15 families, got {len(sugg.families)}"

    def test_9_known_variants(self, sugg):
        assert len(sugg.known_variants) == 9, f"Expected 9 knownVariants, got {sugg.known_variants}"

    def test_meta_present(self, sugg):
        assert "omoVersion" in sugg.meta
        assert "omoCommit" in sugg.meta
        assert "generatedAt" in sugg.meta

    def test_fallback_chains_are_well_formed(self, sugg):
        """Every agent/category chain is non-empty; each entry has providers + a model id.

        Deliberately structural, NOT a count. Chain *lengths* are pure upstream churn — a
        weekly `--refresh-omo` routinely adds or drops entries (omo 4.19.0 alone moved five
        chains), so pinning one fails on data that is perfectly fine. The counts worth
        pinning are the ones above, which guard something real: `test_15_families` backs
        the FAMILY_VENDOR key-set, agents/categories back target coverage.
        """
        for section in ("agents", "categories"):
            for name, body in getattr(sugg, section).items():
                chain = body.get("fallbackChain")
                assert chain, f"{section} '{name}' has a missing/empty fallbackChain"
                for i, entry in enumerate(chain):
                    # isinstance(list) is load-bearing, not belt-and-braces: a bare
                    # "providers": "opencode" is truthy AND passes an all()-over-str check
                    # (it iterates characters), so a shape regression would sail through
                    # the very assertion meant to catch it — and resolve.py would then
                    # iterate those characters as if they were provider names.
                    assert isinstance(entry.get("providers"), list) and entry["providers"], (
                        f"{section} '{name}'[{i}]: providers must be a non-empty list, "
                        f"got {entry.get('providers')!r}"
                    )
                    assert all(isinstance(p, str) and p for p in entry["providers"]), (
                        f"{section} '{name}'[{i}]: non-string provider in {entry['providers']}"
                    )
                    assert isinstance(entry.get("model"), str) and entry["model"], (
                        f"{section} '{name}'[{i}]: missing/empty model id"
                    )

    def test_every_variant_is_a_known_variant(self, sugg):
        """No chain entry (or target default) may carry a variant outside knownVariants.

        This is the refresh-stable half of what the old chain-length pin was reaching for:
        it ignores harmless churn but fails loudly if omo introduces a variant oModel does
        not know how to write to config.
        """
        known = set(sugg.known_variants)
        for section in ("agents", "categories"):
            for name, body in getattr(sugg, section).items():
                if "variant" in body:
                    assert body["variant"] in known, (
                        f"{section} '{name}': unknown default variant {body['variant']!r}"
                    )
                for i, entry in enumerate(body.get("fallbackChain") or ()):
                    if "variant" in entry:
                        assert entry["variant"] in known, (
                            f"{section} '{name}'[{i}]: unknown variant {entry['variant']!r}"
                        )

    def test_patterns_are_compiled(self, sugg):
        """All pattern fields are compiled re.Pattern objects (not raw strings)."""
        for fam in sugg.families:
            if fam.pattern is not None:
                assert hasattr(fam.pattern, "search"), (
                    f"family '{fam.family}' pattern is not a compiled re.Pattern"
                )


# ---------------------------------------------------------------------------
# FAMILY_VENDOR key-set pin (DESIGN §suggestions.py): a family rename/add/remove in a weekly
# --refresh-omo data update must not silently drop (or orphan) a vendor mapping.
# ---------------------------------------------------------------------------

class TestFamilyVendorSync:

    def test_family_vendor_keys_match_bundled_families(self, sugg):
        bundled_families = {f.family for f in sugg.families}
        assert set(FAMILY_VENDOR.keys()) == bundled_families, (
            "FAMILY_VENDOR (suggestions.py) is out of sync with the bundled family list — "
            "update FAMILY_VENDOR after a --refresh-omo family rename/add/remove."
        )


# ---------------------------------------------------------------------------
# Faithful-port guard: a family may carry BOTH `pattern` and `includes`.
# omo (detectHeuristicModelFamily) tests pattern THEN includes for EVERY family
# (two independent `if`s), so `includes` is reachable even when a `pattern` is
# present. The bundled data's only both-fields family (kimi-thinking) has a
# pattern that already covers its includes, so no real id can expose a
# regression — these synthetic families lock the structure so a future
# `--refresh-omo` (e.g. an include the pattern doesn't cover) can't silently rot
# detect_family back into the pattern-XOR-includes shape.
# ---------------------------------------------------------------------------

class TestPatternAndIncludesBothChecked:

    @staticmethod
    def _fam(name, pattern=None, includes=()):
        return Family(
            family=name,
            pattern=re.compile(pattern) if pattern is not None else None,
            includes=list(includes),
            variants=[],
            reasoning_efforts=[],
            reasoning_effort_aliases={},
            supports_thinking=False,
        )

    def _mk(self, *families):
        return Suggestions(
            meta={}, agents={}, categories={}, families=list(families), known_variants=[]
        )

    def test_includes_checked_even_when_pattern_present(self):
        """A both-fields family: an id matching `includes` but NOT `pattern` still resolves
        to that family (omo parity) — it must not fall through to a later family. This is the
        exact regression: the old `if pattern / else includes` returned 'widget' here."""
        both = self._fam(
            "widget-thinking",
            pattern=r"widget-(?:thinking|think)",
            includes=["widget-thinking", "widget-reasoner"],
        )
        later = self._fam("widget", pattern=r"widget")  # catches it iff includes are skipped
        sugg = self._mk(both, later)
        # 'widget-reasoner' matches the include but NOT the pattern.
        fam = sugg.detect_family("widget-reasoner-v2")
        assert fam is not None and fam.family == "widget-thinking"

    def test_pattern_still_wins_first_within_family(self):
        """Pattern is tested before includes: an id matching the pattern resolves via it."""
        both = self._fam(
            "widget-thinking",
            pattern=r"widget-(?:thinking|think)",
            includes=["widget-reasoner"],
        )
        assert self._mk(both).detect_family("widget-thinking-x").family == "widget-thinking"

    def test_earlier_family_still_wins_over_later_includes(self):
        """Ordering is preserved: an earlier family's pattern beats a later family that would
        also match via includes — the includes check adds reachability, not reordering."""
        first = self._fam("alpha", pattern=r"shared")
        second = self._fam("beta", includes=["shared"])
        assert self._mk(first, second).detect_family("x-shared-y").family == "alpha"
