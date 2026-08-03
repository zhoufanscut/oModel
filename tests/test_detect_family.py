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

# (model_id, variant) pairs where omo's chain asks for a variant the heuristic family registry
# does not declare — see test_chain_variants_are_declared_by_their_family for what that does and
# does not imply (short version: the chain is right, the heuristic is narrow, and it only shows
# as a ⚠ when opencode --verbose is silent). Keyed by model, NOT by target: one such pair reaches
# several chains (kimi-k3@max lands in three), and omo moving it to a fourth next week is not new
# information. Reviewed against opencode's own variant sets and accepted for omo 4.19.2 — the pin
# exists so the NEXT pair gets the same look, not to force a fix anyone owes. Prune an entry once
# omo stops emitting it (a stale one never fails the test).
ACCEPTED_VARIANT_DRIFT = {
    # omo 4.19.2 — both still drift in 4.19.4.
    ("claude-fable-5", "xhigh"),
    ("kimi-k3", "max"),

    # omo 4.19.4, reviewed 2026-08-03. Three groups, none a defect in omo's chain:
    #
    # `off` is NEW — 4.19.4's reasoning-unification folded two variant vocabularies into one
    # 7-rung ladder (off < minimal < low < medium < high < xhigh < max, plus an `auto` sentinel),
    # renaming `none` → `off` and dropping `thinking`. Both entries sit in categories:quick on the
    # cheap/fast models, i.e. "reasoning off for the quick tier", which is coherent. `off` IS a
    # real rung (clampReasoningLevel indexes it at 0) — the heuristic families just never listed
    # the bottom one, so every use of it drifts.
    ("claude-haiku-4-5", "off"),
    ("deepseek-v4-flash", "off"),
    #
    # A top tier the heuristic registry stops short of — same shape as kimi-k3@max above, where
    # opencode turned out to report the variant and the registry was the narrow one.
    ("claude-opus-5", "xhigh"),
    ("glm-5.2", "max"),
    ("gpt-5.6-sol", "max"),
    ("minimax-m2.7", "max"),
    ("minimax-m3", "max"),
    #
    # qwen declares `variants: []` — the registry has NO data for the family, so every variant on
    # a qwen id drifts by construction. Nothing to reconcile until omo populates it.
    ("qwen3.6-flash", "low"),
    ("qwen3.8-max-preview", "max"),
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

    def test_known_variants_cover_what_chains_use(self, sugg):
        """Structural, not a count. The old pin was `== 9`, which guards nothing real:
        `known_variants` has no consumer in src/ (variant validity is opencode's — decision #14),
        so its size is pure upstream churn — omo 4.19.4 renamed `none` → `off` and dropped
        `thinking`, reddening this on a rename that changes no behaviour. What matters is that
        the list is well-formed and actually covers the chains, which
        test_every_variant_is_a_known_variant then enforces entry by entry."""
        assert sugg.known_variants, "knownVariants is empty"
        assert all(isinstance(v, str) and v for v in sugg.known_variants)
        assert len(set(sugg.known_variants)) == len(sugg.known_variants), "duplicate knownVariants"
        for tier in ("low", "medium", "high"):
            assert tier in sugg.known_variants, tier

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

    def test_chain_variants_are_declared_by_their_family(self, sugg):
        """A chain entry's variant should appear in ITS OWN family's `variants`.

        Finer-grained than test_every_variant_is_a_known_variant: that one asks "is this a
        variant oModel can write at all?", this one asks "does omo's own data agree with
        itself?" — the two disagree when omo adds a variant to a chain but not to the family
        registry (omo 4.19.2: claude-fable-5@xhigh, kimi-k3@max).

        A hit is NOT a defect in omo's chain, and usually not visible at all. The registry is
        omo's HEURISTIC_MODEL_FAMILY_REGISTRY — its guess for models it has no real data on,
        never a normative capability list. Checked against opencode, both 4.19.2 entries are
        simply RIGHT and the heuristic is the narrow one: opencode/claude-fable-5 reports
        low/medium/high/xhigh/max, moonshotai-cn/kimi-k3 reports low/high/max.

        So this pin guards the DEGRADED path, not the normal one. _variant_warn prefers
        `--verbose` and falls back to `family.variants` only when opencode is silent for that
        (provider, model) — an expired or cold cache, opencode missing from PATH, or a
        dedicated provider reporting `{}`. In exactly those states a chain entry listed here
        renders a spurious ⚠ warn-but-allow row; with a warm cache it renders clean. It never
        blocks a pick either way (decision #5). Worth a test because the entries land
        top-of-chain, so when a user IS degraded the triangle sits on omo's first
        recommendation — and a weekly --refresh-omo should not add one unnoticed.

        Models with no detected family (e.g. big-pickle) are skipped — no declaration to
        check against. Comparison is case-insensitive, mirroring _variant_warn.
        """
        drift = {}
        for section in ("agents", "categories"):
            for name, body in getattr(sugg, section).items():
                for i, entry in enumerate(body.get("fallbackChain") or ()):
                    variant = entry.get("variant")
                    if not variant:
                        continue
                    fam = sugg.detect_family(entry["model"])
                    if fam is None:
                        continue
                    if variant.lower() not in {v.lower() for v in fam.variants}:
                        key = (entry["model"], variant)
                        drift.setdefault(key, []).append(f"{section} '{name}'[{i}]")

        unreviewed = {k: v for k, v in drift.items() if k not in ACCEPTED_VARIANT_DRIFT}
        assert not unreviewed, (
            "omo chain entries request a variant their own family does not declare, and these "
            "are not in ACCEPTED_VARIANT_DRIFT:\n"
            + "\n".join(
                f"  {model}@{variant} (family {sugg.detect_family(model).family} declares "
                f"{list(sugg.detect_family(model).variants)}) at {', '.join(where)}"
                for (model, variant), where in sorted(unreviewed.items())
            )
            + "\n\nEach renders as a ⚠ warn-but-allow row. Review the refresh diff, then add the "
            "(model, variant) pair to ACCEPTED_VARIANT_DRIFT in this file to acknowledge it."
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
