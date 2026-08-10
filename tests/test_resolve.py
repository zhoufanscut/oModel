"""test_resolve.py — gateway detection, prefix resolution, candidate assembly.

DESIGN §resolve.py / CONTRACTS.md / §Verification check #2.

Tests use a MOCKED catalog throughout. Suggestions come from one of two sources, and which
one a test uses is a deliberate choice:

- `frozen_sugg` / `frozen_resolver` — FROZEN chains (_helpers.FROZEN_AGENTS) for tests about
  resolve's LOGIC. These may name exact models and pin an exact pick list, because the data
  cannot move under them. Use this for anything that needs a specific model to be present.
- `sugg` / `resolver` — the REAL bundled data, for TestRealDataIntegration and the
  detect_family-dependent tests. Assertions here must be STRUCTURAL: an omo release that
  swaps models around must never red them (omo 4.19.1 replaced gpt-5.5 -> gpt-5.6-sol,
  claude-opus-4-7 -> -4-8 and kimi-k2.5/k2.6/k2p5 -> kimi-k3 in one sweep).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from _helpers import (
    PROBE_MODEL,
    frozen_suggestions,
    probe_family_suggestions,
    seed_verbose,
)

from omodel.catalog import Catalog
from omodel.resolve import Resolver
from omodel.suggestions import load as load_suggestions

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sugg():
    return load_suggestions()


@pytest.fixture(scope="module")
def frozen_sugg():
    """Frozen chains + real families — see _helpers.FROZEN_AGENTS for why."""
    return frozen_suggestions()


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

# A second gateway whose ids CARRY the vendor: split on the first '/', so the model is
# 'anthropic/claude-opus-4-7'. Its own scenario (below) — the only place a slash-bearing id
# reaches vendors_served, and the only place `gateways` holds more than one provider.
STANDARD_MODELS_WITH_OPENROUTER = STANDARD_MODELS + [
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
def frozen_resolver(frozen_sugg):
    """STANDARD_MODELS catalog against the frozen `probe` chain — deterministic across omo
    releases, so assertions here may name exact models and pin an exact pick list."""
    return Resolver.build(_make_catalog(STANDARD_MODELS), frozen_sugg)


# ---------------------------------------------------------------------------
# Gateway detection — vendors_served
# ---------------------------------------------------------------------------

class TestVendorsServed:

    def test_opencode_is_gateway(self, resolver):
        """opencode serves ≥2 distinct vendors → vendors_served >= 2 → gateway."""
        assert resolver.vendors_served("opencode") >= 2
        assert "opencode" in resolver.gateways

    @pytest.mark.parametrize(
        "provider", ["openai", "zhipuai", "moonshotai-cn", "deepseek"]
    )
    def test_single_vendor_provider_is_dedicated(self, resolver, provider):
        """A provider serving exactly one vendor's line → vendors_served == 1 → dedicated."""
        assert resolver.vendors_served(provider) == 1
        assert provider not in resolver.gateways

    def test_openrouter_with_slash_bearing_ids_is_a_gateway(self, sugg):
        """A gateway whose ids carry the vendor ('openrouter/anthropic/claude-opus-4-7' → model
        'anthropic/claude-opus-4-7') is still classified by the vendors it serves → gateway,
        alongside opencode. Deliberately UNCONDITIONAL: this was once guarded by
        `if vendors_served(...) >= 2:`, which would have passed silently had the count gone to
        0 — the exact regression it exists to catch. This is the only test feeding a
        slash-bearing id through vendors_served, and the only one where `gateways` holds more
        than one provider (verification.md Check 2)."""
        res = Resolver.build(_make_catalog(STANDARD_MODELS_WITH_OPENROUTER), sugg)
        assert res.vendors_served("openrouter") >= 2
        assert "openrouter" in res.gateways
        assert "opencode" in res.gateways
        # A model only gateways serve still resolves by first-seen — openrouter's spelling is a
        # DIFFERENT model id, so opencode remains its only serving provider.
        assert res.resolve_prefix("claude-opus-4-7", "omo") == "opencode"


# ---------------------------------------------------------------------------
# resolve_prefix — dedicated-first (§Verification check #2)
# ---------------------------------------------------------------------------

class TestResolvePrefix:

    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("gpt-5.5", "openai"),
            ("kimi-k2.5", "moonshotai-cn"),
            ("glm-5", "zhipuai"),
            ("deepseek-v4-pro", "deepseek"),
        ],
    )
    def test_dedicated_provider_wins_over_gateway(self, resolver, model, expected):
        """Each of these is served by opencode(gateway) + one dedicated provider → the
        dedicated one wins."""
        assert resolver.resolve_prefix(model, "omo") == expected

    def test_claude_opus_4_7_resolves_to_opencode(self, resolver):
        """claude-opus-4-7 is only in opencode (gateway only) → opencode."""
        result = resolver.resolve_prefix("claude-opus-4-7", "omo")
        assert result == "opencode"

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
    """candidates() row shape and ordering, against the FROZEN `probe` chain.

    These assertions name exact models and pin an exact pick list, which is only safe
    because the chain is frozen (_helpers.FROZEN_AGENTS). Pinned against live bundled data
    they broke on every omo sweep while the product was behaving correctly.
    """

    def test_probe_candidates_contract_shape(self, frozen_resolver):
        """Every candidate row for agent:probe matches CONTRACTS.md exactly."""
        rows = frozen_resolver.candidates("agent:probe")
        assert len(rows) > 0
        for i, row in enumerate(rows):
            _assert_candidate_shape(row, i)

    def test_chain_filtered_to_available(self, frozen_resolver):
        """Chain-only pick list, in chain order, EXPANDED to one row per serving provider —
        dedicated (single-vendor) before aggregator (gateway). Models served by both a
        dedicated provider and opencode show twice (dedicated first). kimi-k3 is not in the
        catalog → same-line substitute kimi-k2.6, which dedups against the chain's own
        kimi-k2.6 entry. The k2p5 entry is hardcode-aliased to kimi-k2.5
        (_OMO_MODEL_ALIASES) and dedups against the chain's own kimi-k2.5 rows."""
        rows = frozen_resolver.candidates("agent:probe")
        assert all(r["source"] == "omo" for r in rows)
        assert "k2p5" not in [r["model"] for r in rows], "k2p5 is aliased to kimi-k2.5"
        keys = [f"{r['provider']}/{r['model']}" for r in rows]
        assert keys == [
            "opencode/claude-opus-4-7",
            "moonshotai-cn/kimi-k2.6", "opencode/kimi-k2.6",
            "moonshotai-cn/kimi-k2.5", "opencode/kimi-k2.5",
            "openai/gpt-5.5", "opencode/gpt-5.5",
            "zhipuai/glm-5", "opencode/glm-5",
            "opencode/big-pickle",
        ], f"Unexpected pick list: {keys}"

    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("gpt-5.5", ["openai/gpt-5.5", "opencode/gpt-5.5"]),
            ("glm-5", ["zhipuai/glm-5", "opencode/glm-5"]),
        ],
    )
    def test_all_providers_shown_dedicated_first(self, frozen_resolver, model, expected):
        """Headline behavior: a model served by a dedicated provider AND an aggregator shows
        one row EACH, dedicated (single-vendor) first — you can pick either."""
        rows = frozen_resolver.candidates("agent:probe")
        got = [f"{r['provider']}/{r['model']}" for r in rows if r["model"] == model]
        assert got == expected

    def test_exact_rows_have_no_substitute_for(self, frozen_resolver):
        """The catalog serves every surviving row exactly (kimi-k3's substitute dedups into
        the chain's own kimi-k2.6 entry) → substitute_for is None on every row."""
        rows = frozen_resolver.candidates("agent:probe")
        for row in rows:
            assert row["substitute_for"] is None, (
                f"{row['model']} should be exact, got substitute_for={row['substitute_for']!r}"
            )

    def test_entry_is_dict_for_omo_rows(self, frozen_resolver):
        """Every (omo) row carries its originating fallbackChain entry dict."""
        rows = frozen_resolver.candidates("agent:probe")
        for row in rows:
            assert isinstance(row["entry"], dict), "omo row must have entry dict"

    @pytest.mark.parametrize(
        ("model", "variant"),
        [
            ("claude-opus-4-7", "max"),   # entry-level variant
            ("gpt-5.5", "medium"),        # entry-level variant
            ("kimi-k2.5", None),          # no variant in entry or top-level
        ],
    )
    def test_entry_variant_carried_onto_rows(self, frozen_resolver, model, variant):
        """A chain entry's `variant` (or its absence) reaches every row for that model."""
        rows = [r for r in frozen_resolver.candidates("agent:probe") if r["model"] == model]
        assert len(rows) >= 1
        assert rows[0]["variant"] == variant


# ---------------------------------------------------------------------------
# Real bundled data — structural only, so an omo sweep can never red these
# ---------------------------------------------------------------------------

class TestRealDataIntegration:
    """The real bundled suggestions still flow through resolve end-to-end.

    Deliberately asserts INVARIANTS, never specific model ids: which models omo recommends
    is upstream churn, but "every row obeys the contract" must hold for any data. This is
    what still catches a real regression when the frozen tests above cannot.
    """

    def test_every_real_target_resolves_cleanly(self, resolver, sugg):
        """Every agent and category resolves to contract-shaped rows, with no duplicate
        provider/model and no row inventing a source."""
        targets = [f"agent:{a}" for a in sugg.agents] + [f"cat:{c}" for c in sugg.categories]
        assert len(targets) == len(sugg.agents) + len(sugg.categories)
        for target in targets:
            rows = resolver.candidates(target)
            for i, row in enumerate(rows):
                _assert_candidate_shape(row, i)
            keys = [f"{r['provider']}/{r['model']}" for r in rows]
            assert len(keys) == len(set(keys)), f"{target}: duplicate rows {keys}"
            assert all(r["source"] == "omo" for r in rows), f"{target}: non-omo source"

    def test_dedicated_precedes_gateway_for_every_real_row(self, resolver, sugg):
        """Wherever one model is served by both a dedicated provider and a gateway, the
        dedicated row precedes the gateway row — the ordering rule, checked against whatever
        omo currently recommends rather than against a hardcoded pair."""
        for target in [f"agent:{a}" for a in sugg.agents]:
            rows = resolver.candidates(target)
            by_model: dict = {}
            for idx, row in enumerate(rows):
                by_model.setdefault(row["model"], []).append((idx, row["provider"]))
            for model, entries in by_model.items():
                gateways = [i for i, p in entries if resolver.vendors_served(p) >= 2]
                dedicated = [i for i, p in entries if resolver.vendors_served(p) < 2]
                if gateways and dedicated:
                    assert max(dedicated) < min(gateways), (
                        f"{target}/{model}: gateway row precedes a dedicated one"
                    )

    def test_substitutes_stay_in_family(self, resolver, sugg):
        """Any same-line substitute shares a detect_family with the entry it stands in for —
        the substitution rule, independent of which models are involved today."""
        for target in [f"agent:{a}" for a in sugg.agents] + [f"cat:{c}" for c in sugg.categories]:
            for row in resolver.candidates(target):
                if row["substitute_for"] is None:
                    continue
                got = sugg.detect_family(row["model"])
                want = sugg.detect_family(row["substitute_for"])
                assert got is not None and want is not None, (
                    f"{target}: substitute {row['model']}→{row['substitute_for']} has no family"
                )
                assert got.family == want.family, (
                    f"{target}: {row['model']} ({got.family}) substitutes for "
                    f"{row['substitute_for']} ({want.family}) — cross-family"
                )


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

    def test_candidates_variant_warn(self):
        """A row whose variant ∉ family.variants gets warn=['variant'] (via candidates()).

        Uses the frozen PROBE_FAMILY (low/medium/high, never `max`), not a real family: this
        asserts resolve's LOGIC, and borrowing omo's data for the "lacks it" half means an
        upstream release can delete the premise. It did — this test read `glm has no max`
        until omo 5.0.0-beta.4 gave glm `max`."""
        sugg = probe_family_suggestions()
        cat = _make_catalog([f"p/{PROBE_MODEL}"])
        res = Resolver.build(cat, sugg)
        synth = {
            "variant": "",
            "fallbackChain": [{"providers": ["p"], "model": PROBE_MODEL, "variant": "max"}],
        }
        with patch.object(res, "_requirement_for", return_value=synth):
            rows = res.candidates("agent:sisyphus")
        hit = [r for r in rows if r["model"] == PROBE_MODEL]
        assert len(hit) == 1
        assert hit[0]["warn"] == ["variant"]

    def test_probe_family_really_lacks_max(self):
        """Guards the fixture the test above depends on. Without this, a probe family that
        silently gained `max` would turn that test green-but-vacuous rather than red."""
        sugg = probe_family_suggestions()
        fam = sugg.detect_family(PROBE_MODEL)
        assert fam is not None and fam.family == "probe-zeta", fam
        assert "max" not in fam.variants

    def test_valid_variant_no_warn(self, frozen_resolver):
        """claude-opus-4-7 with variant='max' — max IS in claude-opus.variants → no 'variant'
        warn. Frozen chain: the point is that a VALID variant stays unflagged, not that omo
        still recommends this particular model."""
        rows = frozen_resolver.candidates("agent:probe")
        opus_rows = [r for r in rows if r["model"] == "claude-opus-4-7"]
        assert len(opus_rows) >= 1
        assert "variant" not in opus_rows[0]["warn"]


# ---------------------------------------------------------------------------
# Same-line (fuzzy) substitution — same detect_family, version-agnostic
# ---------------------------------------------------------------------------

class TestSameLineSubstitute:
    """Frozen chain throughout: every test here needs a specific entry (`glm-5`) to exist and
    names exact models, which is what the frozen fixture is for. Against live data they instead
    pinned whatever omo happened to recommend — 4.19.4 renumbered the entry to glm-5.2 and
    reddened four of them at once, on behaviour that was entirely correct."""

    def test_substitute_when_exact_absent(self, frozen_sugg):
        """Chain wants glm-5; only glm-5.1 connected → glm-5.1 offered as a same-line sub."""
        cat = _make_catalog(["zhipuai/glm-5.1"])
        res = Resolver.build(cat, frozen_sugg)
        rows = res.candidates("agent:probe")
        glm = [r for r in rows if r["provider"] == "zhipuai"]
        assert len(glm) == 1
        assert glm[0]["model"] == "glm-5.1"
        assert glm[0]["substitute_for"] == "glm-5"
        assert glm[0]["source"] == "omo"

    def test_exact_beats_substitute(self, frozen_sugg):
        """When the exact glm-5 is connected, it wins over glm-5.1 (no substitute row)."""
        cat = _make_catalog(["zhipuai/glm-5", "zhipuai/glm-5.1"])
        res = Resolver.build(cat, frozen_sugg)
        rows = res.candidates("agent:probe")
        models = [r["model"] for r in rows]
        assert "glm-5" in models
        g5 = next(r for r in rows if r["model"] == "glm-5")
        assert g5["substitute_for"] is None
        assert "glm-5.1" not in models  # not in chain + glm-5 exact → never offered

    def test_substitute_picks_newest(self, frozen_sugg):
        """Several same-line models → newest (highest version) wins: glm-5.1 over glm-4.6."""
        cat = _make_catalog(["zhipuai/glm-4.6", "zhipuai/glm-5.1"])
        res = Resolver.build(cat, frozen_sugg)
        rows = res.candidates("agent:probe")
        glm = [r for r in rows if r["provider"] == "zhipuai"]
        assert len(glm) == 1
        assert glm[0]["model"] == "glm-5.1"
        assert glm[0]["substitute_for"] == "glm-5"

    def test_no_cross_family_substitute(self, frozen_sugg):
        """A different family is NOT a substitute: with only deepseek connected, the glm-5
        entry is hidden (not filled by deepseek), and nothing is dumped → empty list.

        Frozen because the assertion is 'the WHOLE list is empty' — it holds only while no chain
        entry is a deepseek, which is omo's call to change at any release."""
        cat = _make_catalog(["deepseek/deepseek-v4"])
        res = Resolver.build(cat, frozen_sugg)
        rows = res.candidates("agent:probe")
        assert rows == [], f"Expected empty pick list, got {[r['model'] for r in rows]}"

    def test_newest_substitute_not_demoted_by_own_chain_entry(self, sugg):
        """Reported bug: an unavailable newer entry must resolve to the NEWEST same-line model
        you have — not an older one — even when that newest model is itself a later chain entry.

        Synthetic glm chain mirrors the real minimax case (chain wants m3, you have m2.7 + m2.5):
        chain = [glm-5 (unavailable), glm-4.6 (available, its own entry)], and you also have the
        OLDER non-chain glm-4.5. glm-5 must defer to glm-4.6's exact row (the newest you have),
        and the strictly-older glm-4.5 must NOT be surfaced as glm-5's substitute."""
        from omodel.resolve import Resolver as R
        cat = _make_catalog(["zhipuai/glm-4.5", "zhipuai/glm-4.6"])
        res = R.build(cat, sugg)
        synthetic_req = {
            "fallbackChain": [
                {"providers": ["zhipuai"], "model": "glm-5"},     # newer, unavailable
                {"providers": ["zhipuai"], "model": "glm-4.6"},   # older, available (own entry)
            ]
        }
        with patch.object(res, "_requirement_for", return_value=synthetic_req):
            rows = res.candidates("agent:sisyphus")
        models = [r["model"] for r in rows]
        # glm-4.6 shows as its own EXACT row (newest you have); glm-4.5 hidden; no demoted sub.
        assert models == ["glm-4.6"], f"Expected only the exact glm-4.6, got {models}"
        assert rows[0]["substitute_for"] is None
        assert "glm-4.5" not in models  # strictly-older non-chain model never surfaced

    def test_substitute_dedicated_first(self, frozen_sugg):
        """A substitute expands across providers too, dedicated-first: glm-5.1 (filling glm-5)
        shows zhipuai/glm-5.1 then opencode/glm-5.1, both substitute_for='glm-5'."""
        cat = _make_catalog([
            "opencode/glm-5.1", "zhipuai/glm-5.1",
            "opencode/gpt-5", "opencode/claude-opus-4-8",  # make opencode a gateway
        ])
        res = Resolver.build(cat, frozen_sugg)
        rows = res.candidates("agent:probe")
        glm = [r for r in rows if r["model"] == "glm-5.1"]
        assert [f"{r['provider']}/{r['model']}" for r in glm] == [
            "zhipuai/glm-5.1", "opencode/glm-5.1",
        ]
        assert all(r["substitute_for"] == "glm-5" for r in glm)


# ---------------------------------------------------------------------------
# Hardcoded omo-id alias: k2p5 ≡ kimi-k2.5
# ---------------------------------------------------------------------------

class TestK2p5Alias:
    """omo's `k2p5` is hardcode-aliased to kimi-k2.5 (_OMO_MODEL_ALIASES): a provider's dot-free
    spelling of k2.5, NOT the kimi-thinking model omo's heuristic would otherwise route it to."""

    @staticmethod
    def _k2p5_only(sugg, available):
        """A Resolver + a synthetic single-entry chain whose only model is `k2p5`."""
        res = Resolver.build(_make_catalog(available), sugg)
        req = {"fallbackChain": [{"providers": ["moonshotai-cn"], "model": "k2p5"}]}
        return res, req

    def test_k2p5_exact_matches_kimi_k2_5(self, sugg):
        """With kimi-k2.5 connected, k2p5 resolves to the EXACT kimi-k2.5 (substitute_for=None)."""
        res, req = self._k2p5_only(sugg, ["moonshotai-cn/kimi-k2.5"])
        with patch.object(res, "_requirement_for", return_value=req):
            rows = res.candidates("agent:sisyphus")
        assert [r["model"] for r in rows] == ["kimi-k2.5"]
        assert rows[0]["substitute_for"] is None
        assert rows[0]["provider"] == "moonshotai-cn"

    def test_thinking_model_does_not_fill_k2p5(self, sugg):
        """A kimi-THINKING model must NOT fill the k2p5 (=kimi-k2.5, plain-kimi) slot."""
        res, req = self._k2p5_only(sugg, ["moonshotai-cn/kimi-k2-thinking"])
        with patch.object(res, "_requirement_for", return_value=req):
            rows = res.candidates("agent:sisyphus")
        assert rows == [], f"kimi-k2-thinking must not fill k2p5, got {[r['model'] for r in rows]}"

    def test_k2p5_falls_to_newest_kimi_when_no_k2_5(self, sugg):
        """No kimi-k2.5 but a newer same-line kimi (kimi-k2.6) → k2p5 substitutes to it."""
        res, req = self._k2p5_only(sugg, ["moonshotai-cn/kimi-k2.6"])
        with patch.object(res, "_requirement_for", return_value=req):
            rows = res.candidates("agent:sisyphus")
        assert [r["model"] for r in rows] == ["kimi-k2.6"]
        assert rows[0]["substitute_for"] == "kimi-k2.5"


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


# ---------------------------------------------------------------------------
# Noise-tolerant exact match — date stamps, sub-version tags, `.`/`-` spelling
# ---------------------------------------------------------------------------

# A realistic multi-vendor gateway catalog (one provider mirroring many vendors' lines) that
# exercises every id-noise shape: compact date stamps (claude-…-20251001), HYPHENATED dates
# (gpt-…-2026-04-24), sub-version tags (…-jibao, …-yd, …-codex, …-200k, …-turbo), mixed case
# (MiniMax-M3) and `.`/`-` spelling. Provider name is a generic placeholder.
GATEWAY = "acme"
GATEWAY_MODELS = [GATEWAY + "/" + m for m in [
    "claude-haiku-4-5-20251001", "claude-opus-4-5-20251101", "claude-opus-4-6",
    "claude-opus-4-7", "claude-opus-4-8", "claude-sonnet-4-5-20250929",
    "claude-sonnet-4-6", "claude-sonnet-4-8-jibao",
    "deepseek-v3.1-latest", "deepseek-v3.2-latest", "deepseek-v4-flash", "deepseek-v4-pro",
    "gemini-3.1-flash-lite-preview", "gemini-3.1-pro", "gemini-3.5-flash",
    "glm-5", "glm-5-turbo", "glm-5.1", "glm-5.2", "glm-5v-turbo",
    "gpt-5.2-2025-12-11", "gpt-5.2-codex-2026-01-14", "gpt-5.3-codex-2026-02-24",
    "gpt-5.4-2026-03-05", "gpt-5.4-pro-2026-03-05", "gpt-5.5-200k", "gpt-5.5-2026-04-24",
    "kimi-k2.6", "kimi-k2.6-inhouse-yd", "kimi-k2.6-yd", "kimi-k2.7-code",
    "MiniMax-M2.5", "MiniMax-M2.7", "MiniMax-M3",
    "qwen3.5-plus", "qwen3.6-plus", "qwen3.7-max", "qwen3.7-plus",
]]


class TestNoiseTolerantMatch:
    """An available id may carry provider noise the bare omo id lacks: a date stamp
    (claude-haiku-4-5-20251001), a hyphenated date (gpt-5.5-2026-04-24) or a sub-version tag
    (claude-sonnet-4-8-jibao), in either `.`/`-` spelling and any case. Such an id fills the omo
    entry EXACTLY (it IS that model; substitute_for is None). But a real modifier token
    (mini/fast/flash/…) or a version bump is NOT noise."""

    def test_reported_bug_librarian_haiku_not_filled_by_sonnet(self, sugg):
        """Reported regression: librarian's claude-haiku-4-5 entry rendered the newest non-opus
        claude (claude-sonnet-4-8-jibao) as '≈ omo claude-haiku-4-5'. The haiku entry must
        instead match the date-stamped haiku EXACTLY, and no sonnet may appear (the chain has
        no sonnet entry — the bug surfaced one as a same-line stand-in for the haiku slot)."""
        res = Resolver.build(_make_catalog(GATEWAY_MODELS), sugg)
        rows = res.candidates("agent:librarian")
        haiku = [r for r in rows if r["model"] == "claude-haiku-4-5-20251001"]
        assert len(haiku) == 1 and haiku[0]["substitute_for"] is None
        assert all("sonnet" not in r["model"] for r in rows)
        assert all(r["substitute_for"] != "claude-haiku-4-5" for r in rows)

    def test_quick_category_also_fixed(self, sugg):
        """categories:quick wants claude-haiku-4-5 too → same noise-tolerant exact match."""
        res = Resolver.build(_make_catalog(GATEWAY_MODELS), sugg)
        rows = res.candidates("cat:quick")
        haiku = [r for r in rows if r["model"] == "claude-haiku-4-5-20251001"]
        assert len(haiku) == 1 and haiku[0]["substitute_for"] is None
        assert all("sonnet" not in r["model"] for r in rows)

    def test_sonnet_entry_resolves_to_sonnet_not_haiku(self, sugg):
        """The mirror image: a claude-sonnet-4-6 entry resolves to the exact sonnet, never a
        haiku (the size guard cuts both ways).

        Synthetic chain, since this needs a sonnet entry to EXIST and omo may drop one at any
        release — 4.19.4 moved atlas from claude-sonnet-4-6 to claude-sonnet-5, leaving the old
        assertion matching nothing at all and passing its `all(...)` half vacuously."""
        res = Resolver.build(_make_catalog(GATEWAY_MODELS), sugg)
        req = {"fallbackChain": [{"providers": [GATEWAY], "model": "claude-sonnet-4-6"}]}
        with patch.object(res, "_requirement_for", return_value=req):
            rows = res.candidates("agent:atlas")
        sonnet = [r for r in rows if r["model"] == "claude-sonnet-4-6"]
        assert len(sonnet) == 1 and sonnet[0]["substitute_for"] is None
        assert all("haiku" not in r["model"] for r in rows)

    def test_compact_date_stamp_resolves_to_available_id(self, sugg):
        """The resolved model is the AVAILABLE id (what saves to config), not the bare omo id."""
        res = Resolver.build(_make_catalog(GATEWAY_MODELS), sugg)
        assert res._matches_omo_id("claude-haiku-4-5-20251001", "claude-haiku-4-5")
        assert res._resolve_available("claude-haiku-4-5") == "claude-haiku-4-5-20251001"

    def test_hyphenated_date_stamp_is_exact(self, sugg, frozen_sugg):
        """YYYY-MM-DD splits into 4-/2-/2-digit tokens; the year opens the date so the whole tail
        is noise. A gpt-5.5 chain entry is served EXACTLY by the dated build, not a substitute.
        The id-matching half runs against real data; the end-to-end half uses the frozen chain,
        since it needs a gpt-5.5 entry to exist and omo may drop one at any release."""
        res = Resolver.build(_make_catalog(GATEWAY_MODELS), sugg)
        assert res._matches_omo_id("gpt-5.5-2026-04-24", "gpt-5.5")
        assert res._matches_omo_id("gpt-5.2-2025-12-11", "gpt-5.2")
        frozen = Resolver.build(_make_catalog(GATEWAY_MODELS), frozen_sugg)
        gpt = [r for r in frozen.candidates("agent:probe") if r["model"].startswith("gpt-5.5")]
        assert gpt and gpt[0]["substitute_for"] is None

    def test_subversion_tag_is_exact(self, sugg):
        """A chain wanting claude-sonnet-4-8 is filled by ...-4-8-jibao — exact, no substitute."""
        res = Resolver.build(_make_catalog(GATEWAY_MODELS), sugg)
        req = {"fallbackChain": [{"providers": [GATEWAY], "model": "claude-sonnet-4-8"}]}
        with patch.object(res, "_requirement_for", return_value=req):
            rows = res.candidates("agent:sisyphus")
        assert [r["model"] for r in rows] == ["claude-sonnet-4-8-jibao"]
        assert rows[0]["substitute_for"] is None

    def test_case_insensitive_exact_returns_available_spelling(self, sugg):
        """chain minimax-m3 is served by available 'MiniMax-M3' → that exact casing is returned."""
        res = Resolver.build(_make_catalog(GATEWAY_MODELS), sugg)
        assert res._resolve_available("minimax-m3") == "MiniMax-M3"

    def test_dot_dash_spelling_matches(self, sugg):
        res = Resolver.build(_make_catalog([]), sugg)
        assert res._matches_omo_id("claude-haiku-4.5", "claude-haiku-4-5")

    def test_real_modifier_token_not_stripped(self, sugg):
        """mini is a product tier and fast a mode — both are tokens omo names, so they are
        protected: gpt-5.4-mini-fast must NOT fill a gpt-5.4-mini entry, nor glm-5-flash glm-5,
        nor the vision split glm-5v-turbo the bare glm-5."""
        res = Resolver.build(_make_catalog(["p/gpt-5.4-mini-fast", "p/glm-5-flash"]), sugg)
        assert not res._matches_omo_id("gpt-5.4-mini-fast", "gpt-5.4-mini")
        assert not res._matches_omo_id("glm-5-flash", "glm-5")
        assert not res._matches_omo_id("glm-5v-turbo", "glm-5")
        assert res._resolve_available("gpt-5.4-mini") is None
        assert res._resolve_available("glm-5") is None

    def test_exact_spelling_wins_over_noise_variants(self, sugg):
        """glm-5 entry: the exact glm-5 beats glm-5-turbo (turbo=noise) and glm-5.1/5.2 (a
        version is not noise), so the clean id is chosen."""
        res = Resolver.build(_make_catalog(GATEWAY_MODELS), sugg)
        assert res._resolve_available("glm-5") == "glm-5"

    def test_protected_set_contains_real_modifiers_not_noise(self, sugg):
        """real_tokens is derived from omo's own chain ids, over the `_TIER_TOKENS` floor: real
        modifiers are in; provider sub-tags (jibao/yd/codex/latest/turbo) are not."""
        res = Resolver.build(_make_catalog([]), sugg)
        for tok in ("mini", "fast", "nano", "flash", "pro", "plus", "highspeed", "haiku", "sonnet"):
            assert tok in res.real_tokens, tok
        for noise in ("jibao", "yd", "codex", "latest", "turbo", "inhouse"):
            assert noise not in res.real_tokens, noise

    def test_tier_token_survives_omo_dropping_its_last_id(self, frozen_sugg):
        """Regression (omo 4.19.4): a size/tier token must stay protected even when NO chain id
        carries it. 4.19.4 dropped gpt-5.4-mini-fast — its only `mini` — and a purely derived
        real_tokens lost the token, so a provider's cheaper gpt-5.4-mini began filling a bare
        gpt-5.4 entry EXACTLY: substitute_for None, no warn, a smaller model served as the real
        one. `_TIER_TOKENS` is the floor that keeps it a distinct model.

        Frozen chains contain no `mini` either, which is exactly the state under test."""
        res = Resolver.build(_make_catalog(["p/gpt-5.4-mini"]), frozen_sugg)
        assert not any("mini" in e.get("model", "")
                       for e in frozen_sugg.agents["probe"]["fallbackChain"])
        assert "mini" in res.real_tokens
        assert not res._matches_omo_id("gpt-5.4-mini", "gpt-5.4")
        assert res._resolve_available("gpt-5.4") is None

    def test_version_bump_is_not_a_stamp(self, sugg, frozen_sugg):
        """A short trailing digit is a version, not a date stamp: glm-5.1 != glm-5, so it stays
        a same-line SUBSTITUTE rather than collapsing into an exact glm-5 match.

        Split like test_hyphenated_date_stamp_is_exact: the id-matching half runs against real
        data, the end-to-end half against the frozen chain, since it needs a glm-5 entry."""
        res = Resolver.build(_make_catalog(["p/glm-5.1"]), sugg)
        assert not res._matches_omo_id("glm-5.1", "glm-5")
        frozen = Resolver.build(_make_catalog(["p/glm-5.1"]), frozen_sugg)
        glm = [r for r in frozen.candidates("agent:probe") if r["model"] == "glm-5.1"]
        assert glm and glm[0]["substitute_for"] == "glm-5"


class TestClaudeLineGuard:
    """omo lumps every non-opus Claude — haiku, sonnet, and newer lines like fable/mythos — into
    one detect_family (claude-non-opus). A same-line substitute must still respect the product
    LINE: a haiku slot is never filled by a sonnet, nor a fable by a mythos. The line is derived
    (first non-numeric token after `claude`), so new lines are handled with no code change."""

    def test_line_extraction_covers_new_models(self, sugg):
        from omodel.resolve import _claude_line
        assert _claude_line("claude-fable-5") == "fable"
        assert _claude_line("claude-mythos-5") == "mythos"
        assert _claude_line("claude-haiku-4-5") == "haiku"
        assert _claude_line("claude-3-5-sonnet-20241022") == "sonnet"  # legacy id order
        assert _claude_line("claude-fable-5-20260301") == "fable"      # provider date stamp
        assert _claude_line("claude-2") is None                        # no line token

    def test_sonnet_does_not_fill_haiku(self, sugg):
        res = Resolver.build(_make_catalog(["p/claude-sonnet-4-6"]), sugg)
        assert res._same_line_match("claude-haiku-4-5") is None

    def test_haiku_does_not_fill_sonnet(self, sugg):
        res = Resolver.build(_make_catalog(["p/claude-haiku-4-5"]), sugg)
        assert res._same_line_match("claude-sonnet-4-6") is None

    def test_sonnet_does_not_fill_fable(self, sugg):
        """Reported case: a fable slot (omo's most-capable pick) must not be filled by a sonnet
        just because both are claude-non-opus and the sonnet sorts newest by version."""
        res = Resolver.build(_make_catalog(["p/claude-sonnet-4-6"]), sugg)
        assert res._same_line_match("claude-fable-5") is None

    def test_mythos_does_not_fill_fable(self, sugg):
        """fable and mythos are distinct lines (mythos is Project-Glasswing-only) → no cross-fill."""
        res = Resolver.build(_make_catalog(["p/claude-mythos-5"]), sugg)
        assert res._same_line_match("claude-fable-5") is None

    def test_same_line_different_version_substitutes(self, sugg):
        """A different-version, SAME-line claude IS a valid same-line substitute (haiku & fable)."""
        res = Resolver.build(_make_catalog(["p/claude-haiku-4-3"]), sugg)
        assert res._same_line_match("claude-haiku-4-5") == "claude-haiku-4-3"
        res2 = Resolver.build(_make_catalog(["p/claude-fable-4"]), sugg)
        assert res2._same_line_match("claude-fable-5") == "claude-fable-4"

    def test_fable_date_stamp_is_exact_match(self, sugg):
        """A provider may date-stamp the new models too; that still resolves as an exact match."""
        res = Resolver.build(_make_catalog(["acme/claude-fable-5-20260301"]), sugg)
        assert res._resolve_available("claude-fable-5") == "claude-fable-5-20260301"

    def test_opus_unaffected_by_guard(self, sugg):
        """claude-opus is its own family (not claude-non-opus) → no line guard, normal newest."""
        res = Resolver.build(_make_catalog(["p/claude-opus-4-6", "p/claude-opus-4-8"]), sugg)
        assert res._same_line_match("claude-opus-4-7") == "claude-opus-4-8"


class TestVariantWarnOpencodeFirst:
    """Resolver._variant_warn via candidates(): opencode --verbose is the truth source for the
    omo-suggestion variant ⚠, with the heuristic family.variants as the fallback when opencode is
    silent. One data-driven path for every model — no per-model special-casing. The conftest
    isolates the cache to a per-test tmp dir; seed verbose-<prov> directly with cache.write."""

    @staticmethod
    def _seed(provider: str, records: dict) -> None:
        """Delegates to the shared canonical seeder (tests/_helpers.py)."""
        seed_verbose(provider, records)

    @staticmethod
    def _warn_for(res, model, provider, variant):
        """The candidates() row warn for a synthetic single-entry requirement."""
        synth = {
            "variant": "",
            "fallbackChain": [{"providers": [provider], "model": model, "variant": variant}],
        }
        with patch.object(res, "_requirement_for", return_value=synth):
            rows = res.candidates("agent:sisyphus")
        hit = [r for r in rows if r["model"] == model and r["provider"] == provider]
        assert len(hit) == 1, f"expected one {provider}/{model} row, got {len(hit)}"
        return hit[0]["warn"]

    def test_opencode_nonempty_excluding_variant_warns(self, sugg):
        """opencode lists a NON-EMPTY set that omits the suggested variant → warn. gpt-5-nano's
        heuristic (gpt-5) HAS xhigh, but opencode says [minimal,low,medium,high] → ⚠ (truth wins)."""
        self._seed("opencode", {"gpt-5-nano": ["minimal", "low", "medium", "high"]})
        res = Resolver.build(_make_catalog(["opencode/gpt-5-nano"]), sugg)
        assert self._warn_for(res, "gpt-5-nano", "opencode", "xhigh") == ["variant"]

    def test_opencode_nonempty_including_variant_no_warn(self, sugg):
        """opencode's non-empty set contains the suggested variant → no warn."""
        self._seed("opencode", {"gpt-5-nano": ["minimal", "low", "medium", "high"]})
        res = Resolver.build(_make_catalog(["opencode/gpt-5-nano"]), sugg)
        assert self._warn_for(res, "gpt-5-nano", "opencode", "high") == []

    def test_opencode_allows_what_heuristic_would_reject(self, sugg):
        """The reversal both ways: claude-haiku's heuristic (claude-non-opus) has NO 'max', but
        opencode says [high,max] → 'max' is allowed, no warn (opencode overrides the heuristic)."""
        self._seed("opencode", {"claude-haiku-4-5": ["high", "max"]})
        res = Resolver.build(_make_catalog(["opencode/claude-haiku-4-5"]), sugg)
        assert self._warn_for(res, "claude-haiku-4-5", "opencode", "max") == []

    def test_opencode_empty_falls_back_to_heuristic_warn(self):
        """opencode reports `{}` (glm-5, kimi, …) → heuristic fallback: the family has no 'max'
        → still warns. The conservative empty handling is identical for every such model.

        Frozen PROBE_FAMILY rather than a real one — the "family lacks max" half is the fixture's
        job, not omo's (omo 5.0.0-beta.4 gave glm `max` and deleted it)."""
        sugg = probe_family_suggestions()
        self._seed("p", {PROBE_MODEL: []})
        res = Resolver.build(_make_catalog([f"p/{PROBE_MODEL}"]), sugg)
        assert self._warn_for(res, PROBE_MODEL, "p", "max") == ["variant"]

    def test_cold_cache_no_spurious_warn(self, sugg):
        """Nothing cached (cold --verbose) → heuristic fallback, NOT a blanket warn: a valid
        heuristic variant stays clean (gpt-5.5 + high), so a fresh machine doesn't scream ⚠."""
        res = Resolver.build(_make_catalog(["openai/gpt-5.5"]), sugg)
        assert self._warn_for(res, "gpt-5.5", "openai", "high") == []

    def test_warn_differs_between_the_two_rows_of_one_entry(self, sugg):
        """ONE chain entry, expanded to two provider rows, can warn on one and not the other —
        the ⚠ is a property of the (provider, model) PAIR, never of the model or of omo's
        suggestion. Every other test here uses a single provider, so nothing else pins this.

        Real case (omo 4.19.4 metis, kimi-k3 @ low): moonshotai-cn reports low/high/max and the
        opencode gateway reports only max, so the dedicated row is clean and the gateway row is
        flagged. This is what a gateway-first `variants_for` would break — it would clear the ⚠
        on a row where the variant genuinely is not offered, which is worse than a spurious one:
        a warn-but-allow marker that stays silent teaches the user to trust it."""
        self._seed("moonshotai-cn", {"kimi-k3": ["low", "high", "max"]})
        self._seed("opencode", {"kimi-k3": ["max"]})
        res = Resolver.build(_make_catalog([
            "opencode/kimi-k3", "moonshotai-cn/kimi-k3",
            "opencode/gpt-5.5", "opencode/claude-opus-4-8",  # 2 vendors → opencode is a gateway
        ]), sugg)
        synth = {
            "variant": "",
            "fallbackChain": [
                {"providers": ["moonshotai-cn", "opencode"], "model": "kimi-k3", "variant": "low"},
            ],
        }
        with patch.object(res, "_requirement_for", return_value=synth):
            rows = [r for r in res.candidates("agent:sisyphus") if r["model"] == "kimi-k3"]
        assert [(r["provider"], r["warn"]) for r in rows] == [
            ("moonshotai-cn", []),        # dedicated, offers low → clean (and sorts first)
            ("opencode", ["variant"]),    # gateway, offers only max → ⚠
        ]

    def test_no_variant_requested_cannot_warn(self, sugg):
        """The other half of the same confusion: a chain entry with NO variant never warns, on
        any provider, whatever opencode reports — `_variant_warn` returns [] on its first line.
        omo 4.19.4's sisyphus asks for kimi-k3 with no variant at all, so that row is clean for
        this reason and not because its variant was validated against anything."""
        self._seed("opencode", {"kimi-k3": ["max"]})  # would flag `low`, but none is asked for
        res = Resolver.build(_make_catalog(["opencode/kimi-k3"]), sugg)
        assert self._warn_for(res, "kimi-k3", "opencode", None) == []
