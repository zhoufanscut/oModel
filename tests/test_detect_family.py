"""test_detect_family.py — parity vs omo heuristics.

Tests encode the SPEC as per DESIGN.md §Verification check #4 and §suggestions.py.
Fixtures use REAL omo suggestion IDs from the bundled data/omo-suggestions.json.
"""
from __future__ import annotations

import pytest
from omodel.suggestions import load, normalize_model_id


@pytest.fixture(scope="module")
def sugg():
    """Load bundled suggestions once for the module."""
    return load()


# ---------------------------------------------------------------------------
# normalize_model_id
# ---------------------------------------------------------------------------

class TestNormalizeModelId:
    r"""re.sub(r'\.(\d+)', r'-\1', s).lower() — kimi-k2.7 → kimi-k2-7."""

    def test_dot_version_replaced(self):
        # re.sub(r'\.(\d+)', r'-\1', s).lower() -> 'kimi-k2.7' -> 'kimi-k2-7'
        assert normalize_model_id("kimi-k2.7") == "kimi-k2-7"

    def test_multiple_dots(self):
        assert normalize_model_id("claude-3.5.1") == "claude-3-5-1"

    def test_lowercased(self):
        assert normalize_model_id("GPT-5.5") == "gpt-5-5"

    def test_no_change(self):
        assert normalize_model_id("deepseek-v4-pro") == "deepseek-v4-pro"

    def test_k2p5_unchanged(self):
        # k2p5 has no dots — stays as-is (lowercased)
        assert normalize_model_id("k2p5") == "k2p5"


# ---------------------------------------------------------------------------
# detect_family parity (DESIGN §Verification #4) — REAL omo suggestion IDs
# ---------------------------------------------------------------------------

class TestDetectFamilyParity:

    def test_kimi_k2_5_is_kimi(self, sugg):
        """kimi-k2.5 → family 'kimi' (not kimi-thinking; no 'max' variant)."""
        fam = sugg.detect_family("kimi-k2.5")
        assert fam is not None
        assert fam.family == "kimi"
        assert "max" not in fam.variants

    def test_k2p5_is_kimi_thinking(self, sugg):
        """k2p5 → family 'kimi-thinking' (kimi-thinking before kimi in ordering)."""
        fam = sugg.detect_family("k2p5")
        assert fam is not None
        assert fam.family == "kimi-thinking"

    def test_claude_opus_4_7_is_claude_opus(self, sugg):
        """claude-opus-4-7 → family 'claude-opus'; has 'max' variant."""
        fam = sugg.detect_family("claude-opus-4-7")
        assert fam is not None
        assert fam.family == "claude-opus"
        assert "max" in fam.variants

    def test_gpt_5_5_is_gpt_5(self, sugg):
        """gpt-5.5 → family 'gpt-5'; has 'xhigh' variant."""
        fam = sugg.detect_family("gpt-5.5")
        assert fam is not None
        assert fam.family == "gpt-5"
        assert "xhigh" in fam.variants

    def test_glm_5_is_glm(self, sugg):
        """glm-5 → family 'glm'; no 'max' variant."""
        fam = sugg.detect_family("glm-5")
        assert fam is not None
        assert fam.family == "glm"
        assert "max" not in fam.variants

    def test_deepseek_v4_pro_is_deepseek(self, sugg):
        """deepseek-v4-pro → family 'deepseek'; has 'max' variant."""
        fam = sugg.detect_family("deepseek-v4-pro")
        assert fam is not None
        assert fam.family == "deepseek"
        assert "max" in fam.variants

    def test_unknown_model_returns_none(self, sugg):
        """An unrecognised model ID → None (e.g. opencode's big-pickle)."""
        fam = sugg.detect_family("big-pickle")
        assert fam is None

    def test_kimi_k2_6_is_kimi(self, sugg):
        """kimi-k2.6 → kimi (not thinking)."""
        fam = sugg.detect_family("kimi-k2.6")
        assert fam is not None
        assert fam.family == "kimi"

    def test_claude_sonnet_is_claude_non_opus(self, sugg):
        """claude-sonnet-4-6 → claude-non-opus (not claude-opus; no 'max')."""
        fam = sugg.detect_family("claude-sonnet-4-6")
        assert fam is not None
        assert fam.family == "claude-non-opus"
        assert "max" not in fam.variants


# ---------------------------------------------------------------------------
# Ordering guarantees (parity matters — DESIGN §suggestions.py)
# ---------------------------------------------------------------------------

class TestFamilyOrdering:

    def test_openai_reasoning_before_gpt5(self, sugg):
        """openai-reasoning family appears earlier in families list than gpt-5."""
        families = [f.family for f in sugg.families]
        idx_reasoning = families.index("openai-reasoning")
        idx_gpt5 = families.index("gpt-5")
        assert idx_reasoning < idx_gpt5, (
            f"openai-reasoning ({idx_reasoning}) must precede gpt-5 ({idx_gpt5})"
        )

    def test_kimi_thinking_before_kimi(self, sugg):
        """kimi-thinking appears earlier than kimi in families list."""
        families = [f.family for f in sugg.families]
        idx_kt = families.index("kimi-thinking")
        idx_k = families.index("kimi")
        assert idx_kt < idx_k, (
            f"kimi-thinking ({idx_kt}) must precede kimi ({idx_k})"
        )

    def test_claude_opus_before_claude_non_opus(self, sugg):
        """claude-opus before claude-non-opus ensures more-specific match wins."""
        families = [f.family for f in sugg.families]
        idx_opus = families.index("claude-opus")
        idx_non = families.index("claude-non-opus")
        assert idx_opus < idx_non, (
            f"claude-opus ({idx_opus}) must precede claude-non-opus ({idx_non})"
        )


# ---------------------------------------------------------------------------
# Bundled data integrity (DESIGN §Verification check #5)
# ---------------------------------------------------------------------------

class TestBundledSuggestionsLoad:

    def test_loads_without_omo_checkout(self, sugg):
        """importlib.resources loads successfully with no omo checkout present."""
        assert sugg is not None

    def test_11_agents(self, sugg):
        assert len(sugg.agents) == 11, f"Expected 11 agents, got {len(sugg.agents)}: {list(sugg.agents)}"

    def test_8_categories(self, sugg):
        assert len(sugg.categories) == 8, f"Expected 8 categories, got {len(sugg.categories)}: {list(sugg.categories)}"

    def test_15_families(self, sugg):
        assert len(sugg.families) == 15, f"Expected 15 families, got {len(sugg.families)}"

    def test_9_known_variants(self, sugg):
        assert len(sugg.known_variants) == 9, f"Expected 9 knownVariants, got {sugg.known_variants}"

    def test_meta_present(self, sugg):
        assert "omoVersion" in sugg.meta
        assert "omoCommit" in sugg.meta
        assert "generatedAt" in sugg.meta

    def test_sisyphus_has_7_fallback_entries(self, sugg):
        chain = sugg.agents["sisyphus"]["fallbackChain"]
        assert len(chain) == 7, f"sisyphus chain length = {len(chain)}"

    def test_patterns_are_compiled(self, sugg):
        """All pattern fields are compiled re.Pattern objects (not raw strings)."""
        for fam in sugg.families:
            if fam.pattern is not None:
                assert hasattr(fam.pattern, "search"), (
                    f"family '{fam.family}' pattern is not a compiled re.Pattern"
                )
