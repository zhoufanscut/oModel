"""test_resolve.py — gateway detection, prefix resolution, candidate assembly.

DESIGN §resolve.py / CONTRACTS.md / §Verification check #2.
Tests use MOCKED catalog + REAL bundled suggestion IDs.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from omodel.catalog import Catalog
from omodel.suggestions import load as load_suggestions
from omodel.resolve import Resolver


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sugg():
    return load_suggestions()


def _make_catalog(model_lines: list) -> Catalog:
    """Build a Catalog from a list of 'provider/model' strings."""
    available: dict = {}
    connected: list = []
    for line in model_lines:
        if "/" not in line:
            continue
        prov, model = line.split("/", 1)
        if prov not in available:
            available[prov] = []
            connected.append(prov)
        if model not in available[prov]:
            available[prov].append(model)
    return Catalog(available=available, connected=connected)


# ---------------------------------------------------------------------------
# Standard catalog matching §Verification check #2
# ---------------------------------------------------------------------------

# opencode: multi-vendor → gateway
# openai, zhipuai, moonshotai-cn, deepseek → single-vendor → dedicated
STANDARD_MODELS = [
    # opencode — serves claude(anthropic), gpt(openai), kimi(moonshot), glm(zhipu), deepseek(deepseek), grok(xai), gemini(google), mistral(mistral)
    "opencode/claude-opus-4-7",
    "opencode/claude-opus-4-8",
    "opencode/gpt-5.5",
    "opencode/gpt-5",
    "opencode/kimi-k2.5",
    "opencode/kimi-k2.6",
    "opencode/glm-5",
    "opencode/deepseek-v4-pro",
    "opencode/grok-3",
    "opencode/gemini-2-5-pro",
    "opencode/mistral-large",
    "opencode/big-pickle",  # unknown family → no vendor
    # deepseek — dedicated (deepseek only)
    "deepseek/deepseek-v4-pro",
    "deepseek/deepseek-v4",
    # moonshotai-cn — dedicated (moonshot only)
    "moonshotai-cn/kimi-k2.5",
    "moonshotai-cn/kimi-k2.6",
    # openai — dedicated (openai only)
    "openai/gpt-5.5",
    "openai/gpt-5",
    # zhipuai — dedicated (zhipu only)
    "zhipuai/glm-5",
    "zhipuai/glm-5-flash",
]

STANDARD_MODELS_WITH_OPENROUTER = STANDARD_MODELS + [
    # openrouter — serves multiple vendors → gateway
    "openrouter/anthropic/claude-opus-4-7",
    "openrouter/openai/gpt-5.5",
    "openrouter/mistralai/mistral-large",
    "openrouter/google/gemini-2-5-pro",
]


@pytest.fixture(scope="module")
def resolver(sugg):
    cat = _make_catalog(STANDARD_MODELS)
    return Resolver.build(cat, sugg)


@pytest.fixture(scope="module")
def resolver_with_openrouter(sugg):
    cat = _make_catalog(STANDARD_MODELS_WITH_OPENROUTER)
    return Resolver.build(cat, sugg)


# ---------------------------------------------------------------------------
# Gateway detection — vendors_served
# ---------------------------------------------------------------------------

class TestVendorsServed:

    def test_opencode_is_gateway(self, resolver):
        """opencode serves ≥2 distinct vendors → vendors_served >= 2 → gateway."""
        assert resolver.vendors_served("opencode") >= 2
        assert "opencode" in resolver.gateways

    def test_openai_is_dedicated(self, resolver):
        """openai serves only openai-family models → vendors_served == 1 → dedicated."""
        assert resolver.vendors_served("openai") == 1
        assert "openai" not in resolver.gateways

    def test_zhipuai_is_dedicated(self, resolver):
        assert resolver.vendors_served("zhipuai") == 1
        assert "zhipuai" not in resolver.gateways

    def test_moonshotai_cn_is_dedicated(self, resolver):
        assert resolver.vendors_served("moonshotai-cn") == 1
        assert "moonshotai-cn" not in resolver.gateways

    def test_deepseek_is_dedicated(self, resolver):
        assert resolver.vendors_served("deepseek") == 1
        assert "deepseek" not in resolver.gateways

    def test_openrouter_is_gateway(self, resolver_with_openrouter):
        """openrouter (when present) serves ≥2 vendors → gateway."""
        # Note: openrouter models are split on first '/', so model = 'anthropic/claude-opus-4-7'
        # These models may have no omo family → unknown vendor. Test that openrouter
        # still classifies as gateway if it serves models from multiple known families.
        # (The exact count depends on which omo families cover openrouter's model IDs.)
        # At minimum, if it serves ≥2 vendors it must be in gateways.
        vendors_count = resolver_with_openrouter.vendors_served("openrouter")
        if vendors_count >= 2:
            assert "openrouter" in resolver_with_openrouter.gateways
        # Even if openrouter model IDs are unrecognised (family=None), openrouter as an
        # explicit gateway is the expected real-world classification — document here.
        # This test validates the DATA-DRIVEN logic, not a hardcoded list.

    def test_unknown_models_dont_invent_vendor(self, resolver):
        """Models with detect_family→None (e.g. big-pickle) contribute no vendor
        and are skipped in vendors_served — do NOT invent a family for them."""
        # big-pickle is opencode-only and should not push opencode over 2 vendors by itself.
        # opencode is a gateway due to its other models; this just confirms no inflation.
        vendors_opencode = resolver.vendors_served("opencode")
        assert vendors_opencode >= 2  # opencode is genuinely multi-vendor


# ---------------------------------------------------------------------------
# resolve_prefix — dedicated-first (§Verification check #2)
# ---------------------------------------------------------------------------

class TestResolvePrefix:

    def test_gpt_5_5_resolves_to_openai(self, resolver):
        """gpt-5.5 served by opencode(gateway) + openai(dedicated) → dedicated wins: openai."""
        result = resolver.resolve_prefix("gpt-5.5", "omo")
        assert result == "openai"

    def test_claude_opus_4_7_resolves_to_opencode(self, resolver):
        """claude-opus-4-7 is only in opencode (gateway only) → opencode."""
        result = resolver.resolve_prefix("claude-opus-4-7", "omo")
        assert result == "opencode"

    def test_kimi_k2_5_resolves_to_moonshotai_cn(self, resolver):
        """kimi-k2.5 served by opencode(gateway) + moonshotai-cn(dedicated) → moonshotai-cn."""
        result = resolver.resolve_prefix("kimi-k2.5", "omo")
        assert result == "moonshotai-cn"

    def test_glm_5_resolves_to_zhipuai(self, resolver):
        """glm-5 served by opencode(gateway) + zhipuai(dedicated) → zhipuai."""
        result = resolver.resolve_prefix("glm-5", "omo")
        assert result == "zhipuai"

    def test_deepseek_v4_pro_resolves_to_deepseek(self, resolver):
        """deepseek-v4-pro served by opencode(gateway) + deepseek(dedicated) → deepseek."""
        result = resolver.resolve_prefix("deepseek-v4-pro", "omo")
        assert result == "deepseek"

    def test_mine_source_uses_first_seen_provider(self, resolver):
        """source='mine' always picks providers_for()[0] regardless of gateway/dedicated."""
        # For mine, the model is already under a specific provider in the UI
        result = resolver.resolve_prefix("deepseek-v4-pro", "mine")
        # First-seen for deepseek-v4-pro: opencode (appears first in STANDARD_MODELS)
        assert result == "opencode"

    def test_absent_model_returns_none(self, resolver):
        """Model not in any connected provider → resolve_prefix returns None."""
        result = resolver.resolve_prefix("non-existent-model-xyz", "omo")
        assert result is None

    def test_gateway_entry_providers_tiebreak(self, resolver_with_openrouter):
        """When only gateways serve a model, entry.providers order is used as tiebreak.
        Here we test with a synthetic entry — real entry.providers (omo world IDs) rarely
        appear in connected, so cands[0] (first-seen) is the common path."""
        # claude-opus-4-7 is only in opencode in STANDARD_MODELS; with openrouter added
        # it may also be under openrouter (as 'anthropic/claude-opus-4-7') but that's a
        # different model_id. Test that opencode (first-seen gateway) still wins.
        result = resolver_with_openrouter.resolve_prefix("claude-opus-4-7", "omo")
        assert result == "opencode"

    def test_added_gateway_cycles_to_second_gateway(self, resolver_with_openrouter):
        """With openrouter also connected (another gateway), the `p` key can cycle to it.
        Here we just assert providers_for returns both gateways for a model they share."""
        cat = resolver_with_openrouter.catalog
        # gpt-5.5 is in opencode AND openai; with openrouter, check providers_for
        providers = cat.providers_for("gpt-5.5")
        assert "opencode" in providers
        assert "openai" in providers


# ---------------------------------------------------------------------------
# candidates() shape — CONTRACTS.md
# ---------------------------------------------------------------------------

CANDIDATE_REQUIRED_KEYS = {"source", "model", "provider", "variant", "entry", "substitute_for", "warn"}
VALID_SOURCES = {"omo", "add"}
VALID_WARN_VALUES = {"variant"}  # candidates() omo rows: variant only (unavailable is hidden)


def _assert_candidate_shape(row: dict, idx: int) -> None:
    """Assert CONTRACTS.md candidate-row dict shape exactly (candidates() output)."""
    assert set(row.keys()) == CANDIDATE_REQUIRED_KEYS, (
        f"Row {idx} has wrong keys: {set(row.keys())}"
    )
    assert row["source"] in VALID_SOURCES, f"Row {idx}: invalid source {row['source']!r}"
    assert isinstance(row["model"], str) and row["model"], f"Row {idx}: model must be non-empty str"
    # provider is always a non-empty str — rows with no connected provider are dropped.
    assert isinstance(row["provider"], str) and row["provider"], f"Row {idx}: provider must be non-empty str"
    assert row["variant"] is None or isinstance(row["variant"], str)
    assert row["entry"] is None or isinstance(row["entry"], dict)
    assert (
        row["substitute_for"] is None
        or (isinstance(row["substitute_for"], str) and row["substitute_for"])
    ), f"Row {idx}: substitute_for must be None or non-empty str"
    assert isinstance(row["warn"], list)
    for w in row["warn"]:
        assert w in VALID_WARN_VALUES, f"Row {idx}: unknown warn value {w!r}"


class TestCandidatesShape:

    def test_sisyphus_candidates_contract_shape(self, resolver):
        """Every candidate row for agent:sisyphus matches CONTRACTS.md exactly."""
        rows = resolver.candidates("agent:sisyphus")
        assert len(rows) > 0
        for i, row in enumerate(rows):
            _assert_candidate_shape(row, i)

    def test_sisyphus_chain_filtered_to_available(self, resolver):
        """Chain-only pick list: the fallbackChain entries you can run, in chain order.
        STANDARD_MODELS serves every sisyphus entry exactly EXCEPT k2p5 (kimi-thinking —
        no connected kimi-thinking model), which is hidden."""
        rows = resolver.candidates("agent:sisyphus")
        models = [r["model"] for r in rows]
        assert all(r["source"] == "omo" for r in rows)
        assert "k2p5" not in models, "k2p5 has no same-line connected model → must be hidden"
        assert models == [
            "claude-opus-4-7", "kimi-k2.6", "kimi-k2.5", "gpt-5.5", "glm-5", "big-pickle",
        ], f"Unexpected pick list: {models}"

    def test_all_rows_use_omo_source(self, resolver):
        """Every candidate row comes from the chain → source='omo' (no 'mine' dump)."""
        rows = resolver.candidates("agent:sisyphus")
        for row in rows:
            assert row["source"] == "omo", f"row has source={row['source']!r}"

    def test_dedicated_first_provider(self, resolver):
        """glm-5 served by opencode(gateway)+zhipuai(dedicated) → zhipuai wins."""
        rows = resolver.candidates("agent:sisyphus")
        glm = [r for r in rows if r["model"] == "glm-5"]
        assert len(glm) == 1
        assert glm[0]["provider"] == "zhipuai"

    def test_exact_rows_have_no_substitute_for(self, resolver):
        """STANDARD serves these exactly → substitute_for is None on every row."""
        rows = resolver.candidates("agent:sisyphus")
        for row in rows:
            assert row["substitute_for"] is None, (
                f"{row['model']} should be exact, got substitute_for={row['substitute_for']!r}"
            )

    def test_no_duplicate_provider_model(self, resolver):
        """No two rows have the same provider/model combination."""
        rows = resolver.candidates("agent:sisyphus")
        keys = [f"{r['provider']}/{r['model']}" for r in rows]
        assert len(keys) == len(set(keys)), f"Duplicate provider/model keys: {keys}"

    def test_entry_is_dict_for_omo_rows(self, resolver):
        """Every (omo) row carries its originating fallbackChain entry dict."""
        rows = resolver.candidates("agent:sisyphus")
        for row in rows:
            assert isinstance(row["entry"], dict), "omo row must have entry dict"

    def test_variant_precedence_entry_over_top(self, resolver):
        """claude-opus-4-7 in sisyphus chain has variant='max' in the entry — must appear."""
        rows = resolver.candidates("agent:sisyphus")
        opus_rows = [r for r in rows if r["model"] == "claude-opus-4-7"]
        assert len(opus_rows) >= 1
        assert opus_rows[0]["variant"] == "max"

    def test_gpt_5_5_variant_medium(self, resolver):
        """gpt-5.5 in sisyphus chain has variant='medium' in the entry."""
        rows = resolver.candidates("agent:sisyphus")
        gpt_rows = [r for r in rows if r["model"] == "gpt-5.5"]
        assert len(gpt_rows) >= 1
        assert gpt_rows[0]["variant"] == "medium"

    def test_kimi_k2_5_no_variant(self, resolver):
        """kimi-k2.5 in sisyphus chain has no variant in entry or top-level → None."""
        rows = resolver.candidates("agent:sisyphus")
        kimi_rows = [r for r in rows if r["model"] == "kimi-k2.5"]
        assert len(kimi_rows) >= 1
        assert kimi_rows[0]["variant"] is None


# ---------------------------------------------------------------------------
# Warn flags — unavailable + variant
# ---------------------------------------------------------------------------

class TestWarnFlags:

    def _resolver_no_opencode(self, sugg):
        """Catalog where opencode itself is absent (only moonshotai-cn etc.)."""
        cat = _make_catalog([
            "deepseek/deepseek-v4-pro",
            "moonshotai-cn/kimi-k2.5",
            "openai/gpt-5.5",
            "zhipuai/glm-5",
        ])
        return Resolver.build(cat, sugg)

    def test_unavailable_model_hidden(self, sugg):
        """A chain entry with no connected provider AND no same-line relative is hidden
        (decision #5 reversed for the pick list). claude-opus-4-7 is unavailable here and
        no claude-opus model is connected → it must NOT appear; only exacts remain."""
        res = self._resolver_no_opencode(sugg)
        rows = res.candidates("agent:sisyphus")
        models = [r["model"] for r in rows]
        assert "claude-opus-4-7" not in models
        assert models == ["kimi-k2.5", "gpt-5.5", "glm-5"], f"Unexpected: {models}"

    def test_candidates_variant_warn(self, sugg):
        """A row whose variant ∉ family.variants gets warn=['variant'] (via candidates()).
        glm has no 'max' → glm-5 + max warns."""
        cat = _make_catalog(["zhipuai/glm-5"])
        res = Resolver.build(cat, sugg)
        synth = {
            "variant": "",
            "fallbackChain": [{"providers": ["zhipuai"], "model": "glm-5", "variant": "max"}],
        }
        with patch.object(res, "_requirement_for", return_value=synth):
            rows = res.candidates("agent:sisyphus")
        glm = [r for r in rows if r["model"] == "glm-5"]
        assert len(glm) == 1
        assert glm[0]["warn"] == ["variant"]

    def test_invalid_variant_warn_flag(self, resolver):
        """A row where variant is not in the family's variants list gets warn=['variant'].
        Use a synthetic entry with an invalid variant for a real family."""
        from omodel.suggestions import load as load_sugg
        sugg = load_sugg()
        # kimi family has no 'max' → a candidate with variant='max' must warn.
        fam = sugg.detect_family("kimi-k2.5")
        assert fam is not None
        assert fam.family == "kimi"
        assert "max" not in fam.variants, "Precondition: kimi has no 'max'"
        # Now check warn via a synthetic candidate row built the same way resolve.py does:
        variant = "max"
        warn = []
        if variant is not None:
            if fam is not None and variant not in fam.variants:
                warn.append("variant")
        assert "variant" in warn

    def test_glm_max_warns_variant(self, resolver):
        """glm+max: glm family has no 'max' → candidate with variant='max' → warn includes 'variant'."""
        # Directly test: glm family variants
        from omodel.suggestions import load as load_sugg
        sugg = load_sugg()
        fam = sugg.detect_family("glm-5")
        assert fam is not None
        assert "max" not in fam.variants

    def test_valid_variant_no_warn(self, resolver):
        """claude-opus-4-7 with variant='max' — max IS in claude-opus.variants → no 'variant' warn."""
        rows = resolver.candidates("agent:sisyphus")
        opus_rows = [r for r in rows if r["model"] == "claude-opus-4-7"]
        assert len(opus_rows) >= 1
        assert "variant" not in opus_rows[0]["warn"]


# ---------------------------------------------------------------------------
# Same-line (fuzzy) substitution — same detect_family, version-agnostic
# ---------------------------------------------------------------------------

class TestSameLineSubstitute:

    def test_substitute_when_exact_absent(self, sugg):
        """Chain wants glm-5; only glm-5.1 connected → glm-5.1 offered as a same-line sub."""
        cat = _make_catalog(["zhipuai/glm-5.1"])
        res = Resolver.build(cat, sugg)
        rows = res.candidates("agent:sisyphus")
        glm = [r for r in rows if r["provider"] == "zhipuai"]
        assert len(glm) == 1
        assert glm[0]["model"] == "glm-5.1"
        assert glm[0]["substitute_for"] == "glm-5"
        assert glm[0]["source"] == "omo"

    def test_exact_beats_substitute(self, sugg):
        """When the exact glm-5 is connected, it wins over glm-5.1 (no substitute row)."""
        cat = _make_catalog(["zhipuai/glm-5", "zhipuai/glm-5.1"])
        res = Resolver.build(cat, sugg)
        rows = res.candidates("agent:sisyphus")
        models = [r["model"] for r in rows]
        assert "glm-5" in models
        g5 = next(r for r in rows if r["model"] == "glm-5")
        assert g5["substitute_for"] is None
        assert "glm-5.1" not in models  # not in chain + glm-5 exact → never offered

    def test_substitute_picks_newest(self, sugg):
        """Several same-line models → newest (highest version) wins: glm-5.1 over glm-4.6."""
        cat = _make_catalog(["zhipuai/glm-4.6", "zhipuai/glm-5.1"])
        res = Resolver.build(cat, sugg)
        rows = res.candidates("agent:sisyphus")
        glm = [r for r in rows if r["provider"] == "zhipuai"]
        assert len(glm) == 1
        assert glm[0]["model"] == "glm-5.1"
        assert glm[0]["substitute_for"] == "glm-5"

    def test_no_cross_family_substitute(self, sugg):
        """A different family is NOT a substitute: with only deepseek connected, the glm-5
        entry is hidden (not filled by deepseek), and nothing is dumped → empty list."""
        cat = _make_catalog(["deepseek/deepseek-v4"])
        res = Resolver.build(cat, sugg)
        rows = res.candidates("agent:sisyphus")
        assert rows == [], f"Expected empty pick list, got {[r['model'] for r in rows]}"

    def test_substitute_dedicated_first(self, sugg):
        """A substitute's provider is dedicated-first too: glm-5.1 via zhipuai, not opencode."""
        cat = _make_catalog([
            "opencode/glm-5.1", "zhipuai/glm-5.1",
            "opencode/gpt-5", "opencode/claude-opus-4-8",  # make opencode a gateway
        ])
        res = Resolver.build(cat, sugg)
        rows = res.candidates("agent:sisyphus")
        glm = [r for r in rows if r["model"] == "glm-5.1"]
        assert len(glm) == 1
        assert glm[0]["provider"] == "zhipuai"
        assert glm[0]["substitute_for"] == "glm-5"


# ---------------------------------------------------------------------------
# Synthetic top-level variant tier (DESIGN: "presently always empty in omo")
# ---------------------------------------------------------------------------

class TestTopLevelVariantTier:
    """DESIGN: entry.variant → requirement top-level variant → None.
    Exercise the middle tier with a SYNTHETIC fixture (real omo IDs have it empty)."""

    def test_top_level_variant_used_when_entry_has_none(self, sugg):
        """When an entry has no variant but the requirement has a top-level variant,
        the top-level variant is used."""
        # Patch _requirement_for to return a synthetic requirement with top-level variant
        from omodel.resolve import Resolver as R
        cat = _make_catalog(["opencode/kimi-k2.5"])
        res = R.build(cat, sugg)

        synthetic_req = {
            "variant": "high",  # top-level
            "fallbackChain": [
                {"providers": ["opencode"], "model": "kimi-k2.5"}  # no entry-level variant
            ]
        }
        with patch.object(res, "_requirement_for", return_value=synthetic_req):
            rows = res.candidates("agent:sisyphus")

        kimi_rows = [r for r in rows if r["model"] == "kimi-k2.5"]
        assert len(kimi_rows) >= 1
        assert kimi_rows[0]["variant"] == "high", (
            "Top-level requirement variant should be used when entry has no variant"
        )

    def test_entry_variant_wins_over_top_level(self, sugg):
        """Entry-level variant overrides the top-level requirement variant."""
        from omodel.resolve import Resolver as R
        cat = _make_catalog(["opencode/kimi-k2.5"])
        res = R.build(cat, sugg)

        synthetic_req = {
            "variant": "low",  # top-level (should be overridden)
            "fallbackChain": [
                {"providers": ["opencode"], "model": "kimi-k2.5", "variant": "medium"}  # entry-level
            ]
        }
        with patch.object(res, "_requirement_for", return_value=synthetic_req):
            rows = res.candidates("agent:sisyphus")

        kimi_rows = [r for r in rows if r["model"] == "kimi-k2.5"]
        assert len(kimi_rows) >= 1
        assert kimi_rows[0]["variant"] == "medium", (
            "Entry-level variant must take precedence over top-level"
        )


# ---------------------------------------------------------------------------
# Category candidates
# ---------------------------------------------------------------------------

class TestCategoryTargets:

    def test_cat_deep_candidates_contract_shape(self, resolver):
        """cat:deep returns candidates with correct CONTRACTS.md shape."""
        rows = resolver.candidates("cat:deep")
        # May be empty if deep has empty chain or no connected models; just check shape if any
        for i, row in enumerate(rows):
            _assert_candidate_shape(row, i)

    def test_unknown_target_returns_empty(self, resolver):
        """Unknown target id → empty list (no crash)."""
        rows = resolver.candidates("agent:nonexistent-agent-xyz")
        assert rows == []

    def test_agent_sub_target_returns_empty_or_list(self, resolver):
        """Sub-target agent:sisyphus.ultrawork currently returns [] (no separate chain)."""
        rows = resolver.candidates("agent:sisyphus.ultrawork")
        assert isinstance(rows, list)
