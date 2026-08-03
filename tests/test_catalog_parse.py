"""test_catalog_parse.py — mocked `opencode models` + verbose-record parsing.

Monkeypatches subprocess.run so tests NEVER call the real opencode CLI.
DESIGN §catalog.py / §Data sources / §Verification checks #2 and #3.
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from _helpers import age_cache_entry, seed_verbose

from omodel import cache
from omodel.catalog import Catalog, CatalogUnavailable, load, refresh

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_run(stdout: str, returncode: int = 0):
    """Return a mock subprocess.CompletedProcess."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = ""
    return m


def _load_from(stdout: str) -> Catalog:
    """load() over a mocked `opencode models` stdout — the patch nest every parsing test
    needs, in one place. The conftest gives each test an empty cache, so this always
    reaches the (mocked) subprocess."""
    with patch("subprocess.run", return_value=_mock_run(stdout)):
        with patch("shutil.which", return_value="/usr/bin/opencode"):
            return load()


# ---------------------------------------------------------------------------
# `opencode models` parsing — DESIGN §Data sources
# ---------------------------------------------------------------------------

# Representative output covering the verified prefixes; count is kept flexible.
MOCK_MODELS_OUTPUT = """\
opencode/claude-opus-4-7
opencode/claude-opus-4-8
opencode/gpt-5.5
opencode/kimi-k2.5
opencode/kimi-k2.6
opencode/glm-5
opencode/deepseek-v4-pro
opencode/big-pickle
deepseek/deepseek-v4-pro
deepseek/deepseek-v4
moonshotai-cn/kimi-k2.5
moonshotai-cn/kimi-k2.6
openai/gpt-5.5
openai/gpt-5
zhipuai/glm-5
zhipuai/glm-5-flash
"""

MOCK_MODELS_OUTPUT_SLASH_IN_MODEL = """\
openrouter/anthropic/claude-opus-4-7
openrouter/openai/gpt-5.5
opencode/kimi-k2.5
"""


class TestCatalogLoad:

    def test_connected_first_seen_order(self):
        """Providers appear in connected as a LIST in the order they first appear in output
        (never a set — the `==` against an ordered literal pins both)."""
        cat = _load_from(MOCK_MODELS_OUTPUT)
        assert cat.connected == ["opencode", "deepseek", "moonshotai-cn", "openai", "zhipuai"]

    def test_available_maps_provider_to_model_list(self):
        """available maps each provider to its models as a LIST in first-seen order."""
        cat = _load_from(MOCK_MODELS_OUTPUT)
        assert cat.available["opencode"][:2] == ["claude-opus-4-7", "claude-opus-4-8"]
        assert "kimi-k2.5" in cat.available["opencode"]

    def test_split_on_first_slash_only(self):
        """Lines like 'openrouter/anthropic/claude-opus-4-7' split on the FIRST '/'.
        Provider = 'openrouter'; model = 'anthropic/claude-opus-4-7'."""
        cat = _load_from(MOCK_MODELS_OUTPUT_SLASH_IN_MODEL)
        assert "openrouter" in cat.available
        assert "anthropic/claude-opus-4-7" in cat.available["openrouter"]
        assert "openai/gpt-5.5" in cat.available["openrouter"]

    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("kimi-k2.5", ["opencode", "moonshotai-cn"]),
            ("gpt-5.5", ["opencode", "openai"]),
            ("does-not-exist", []),
        ],
    )
    def test_providers_for_is_a_first_seen_list(self, model, expected):
        """providers_for() returns a list in first-seen order; unknown model → []."""
        assert _load_from(MOCK_MODELS_OUTPUT).providers_for(model) == expected

    def test_no_duplicate_models_per_provider(self):
        """Same line appearing twice should not duplicate the model in the list."""
        cat = _load_from("opencode/gpt-5.5\nopencode/gpt-5.5\n")
        assert cat.available["opencode"].count("gpt-5.5") == 1


# ---------------------------------------------------------------------------
# Error rules — DESIGN §Data sources (the SINGLE definition)
# ---------------------------------------------------------------------------

class TestCatalogErrorRules:

    def test_opencode_not_on_path_returns_empty_catalog(self):
        """If opencode is not on PATH, returns Catalog(available={}, connected=[])
        rather than raising CatalogUnavailable."""
        with patch("shutil.which", return_value=None):
            cat = load()
        assert cat.available == {}
        assert cat.connected == []

    def test_exit_nonzero_raises_catalog_unavailable(self):
        """exit code != 0 (opencode IS on PATH) → CatalogUnavailable."""
        with patch("subprocess.run", return_value=_mock_run("", returncode=1)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                with pytest.raises(CatalogUnavailable):
                    load()

    def test_zero_parsed_lines_raises_catalog_unavailable(self):
        """Zero provider/model lines (even if exit 0) → CatalogUnavailable. There is no
        partial-success state: either a Catalog with data, an empty Catalog, or this."""
        empty_output = "Some header line with no slash\n\n"
        with patch("subprocess.run", return_value=_mock_run(empty_output, returncode=0)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                with pytest.raises(CatalogUnavailable):
                    load()


# ---------------------------------------------------------------------------
# load()'s cache-hit branch — DESIGN §cache.py (the whole point of the cache: a warm
# launch skips the opencode subprocess entirely).
# ---------------------------------------------------------------------------

class TestCatalogLoadCacheHit:

    def test_warm_cache_skips_subprocess(self):
        cache.write("models", MOCK_MODELS_OUTPUT, ["opencode", "models"])

        def _must_not_run(*a, **kw):
            raise AssertionError("subprocess must not be called on a cache hit")

        with patch("subprocess.run", side_effect=_must_not_run):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                cat = load()
        assert "opencode" in cat.available
        assert "claude-opus-4-7" in cat.available["opencode"]

    def test_empty_cached_blob_falls_through_to_subprocess(self):
        """A cached but empty/garbage stdout (zero parsed provider/model lines — no '/' at
        all) is treated as a miss — load() falls through to the (here, stubbed-OK) subprocess
        rather than returning an empty Catalog."""
        cache.write("models", "no slash lines in this blob at all\n", ["opencode", "models"])
        with patch("subprocess.run", return_value=_mock_run(MOCK_MODELS_OUTPUT)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                cat = load()
        assert "opencode" in cat.available
        assert "claude-opus-4-7" in cat.available["opencode"]


# ---------------------------------------------------------------------------
# catalog.refresh() — the `r` key / `--refresh-models` (DESIGN §cache.py / §Data sources)
# ---------------------------------------------------------------------------

class TestCatalogRefresh:
    """catalog.refresh() forces `opencode models --refresh` and rebuilds the local cache from
    scratch — the manual-refresh path behind the `r` key / `--refresh-models`."""

    def test_not_on_path_returns_empty_and_clears_cache(self):
        """Not on PATH → empty Catalog, and any existing cache entries are cleared."""
        cache.write("models", "opencode/stale-model\n", ["opencode", "models"])
        cache.write(
            "verbose-opencode", "stale verbose blob",
            ["opencode", "models", "opencode", "--verbose"],
        )
        with patch("shutil.which", return_value=None):
            cat = refresh()
        assert cat.available == {}
        assert cat.connected == []
        assert cache.read("models") is None
        assert cache.read("verbose-opencode") is None

    def test_timeout_raises_catalog_unavailable(self):
        timeout_exc = subprocess.TimeoutExpired(cmd=["opencode"], timeout=90)
        with patch("subprocess.run", side_effect=timeout_exc):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                with pytest.raises(CatalogUnavailable):
                    refresh()

    def test_nonzero_exit_raises_catalog_unavailable(self):
        with patch("subprocess.run", return_value=_mock_run("", returncode=1)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                with pytest.raises(CatalogUnavailable):
                    refresh()

    def test_zero_lines_raises_catalog_unavailable(self):
        empty_output = "no slash lines here\n"
        with patch("subprocess.run", return_value=_mock_run(empty_output)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                with pytest.raises(CatalogUnavailable):
                    refresh()

    def test_happy_path_returns_catalog_and_rebuilds_cache(self):
        """A successful refresh returns the parsed Catalog AND rewrites the cache: stale
        verbose-* keys are gone, and a fresh `models` entry holds this run's stdout."""
        cache.write("models", "opencode/old-stale-model\n", ["opencode", "models"])
        cache.write(
            "verbose-opencode", "old stale verbose blob",
            ["opencode", "models", "opencode", "--verbose"],
        )

        with patch("subprocess.run", return_value=_mock_run(MOCK_MODELS_OUTPUT)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                cat = refresh()

        assert "opencode" in cat.available
        assert "claude-opus-4-7" in cat.available["opencode"]
        assert cache.read("verbose-opencode") is None  # stale verbose-* cleared
        assert cache.read("models") == MOCK_MODELS_OUTPUT  # fresh models cache written


# ---------------------------------------------------------------------------
# Verbose record parsing — DESIGN §Data sources "per-model detail"
# ---------------------------------------------------------------------------

# Realistic 3-record --verbose blob (simulates real opencode output structure).
# Field names match the DESIGN spec: limit.context, cost.input/output, capabilities.
_VERBOSE_RECORD_1 = {
    "limit": {"context": 200000},
    "cost": {"input": 3, "output": 15},  # numeric, as real opencode emits
    "capabilities": {
        "reasoning": False,
        "input": {"image": True, "pdf": True},
    },
    "variants": {"max": {"context": 200000}},  # opencode's runtime ns — must NOT be read
}

_VERBOSE_RECORD_2 = {
    "limit": {"context": 128000},
    "cost": {"input": 1, "output": 4, "cache": {"read": 0.3, "write": 0.5}},
    "capabilities": {
        "reasoning": True,
        "input": {"image": False},
    },
    "variants": {},
}

_VERBOSE_RECORD_3 = {
    "limit": {"context": 64000},
    "cost": {"input": 0, "output": 0},
    "capabilities": {
        "reasoning": False,
        "input": {"image": False},
    },
}

MOCK_VERBOSE_OUTPUT = (
    "opencode/claude-opus-4-7\n"
    + json.dumps(_VERBOSE_RECORD_1, indent=2) + "\n"
    + "opencode/gpt-5.5\n"
    + json.dumps(_VERBOSE_RECORD_2, indent=2) + "\n"
    + "opencode/glm-5\n"
    + json.dumps(_VERBOSE_RECORD_3, indent=2) + "\n"
)


class TestVerboseParsing:
    """DESIGN §catalog.py .detail() / §Verification check #3."""

    def _make_catalog_with_opencode(self, models: list) -> Catalog:
        available = {"opencode": models}
        connected = ["opencode"]
        return Catalog(available=available, connected=connected)

    def _detail(self, model: str) -> dict:
        cat = self._make_catalog_with_opencode(["claude-opus-4-7", "gpt-5.5", "glm-5"])
        with patch("subprocess.run", return_value=_mock_run(MOCK_VERBOSE_OUTPUT)):
            result = cat.detail(model)
        assert result is not None, f"no detail record parsed for {model!r}"
        return result

    def test_detail_extracts_every_field_of_the_queried_record(self):
        """detail() returns EXACTLY {context, cost, reasoning, image}, picking the record that
        matches the queried model out of the 3-record blob — never `--verbose.variants`
        (decision #14). RECORD_1 (claude-opus-4-7) and RECORD_2 (gpt-5.5) between them cover
        both bools, a plain cost and a cache-bearing one."""
        opus = self._detail("claude-opus-4-7")
        assert set(opus) == {"context", "cost", "reasoning", "image"}
        assert "variants" not in opus, "detail() must NEVER expose --verbose.variants"
        assert opus["context"] == 200000
        assert opus["cost"] == {"input": 3, "output": 15}
        assert opus["reasoning"] is False
        assert opus["image"] is True

        gpt = self._detail("gpt-5.5")
        assert gpt["reasoning"] is True
        assert gpt["image"] is False
        assert gpt["cost"]["cache"] == {"read": 0.3, "write": 0.5}, "cache cost nests in cost"

        # A third record proves the multi-block scan isn't just returning the first match.
        assert self._detail("glm-5")["context"] == 64000

    def test_detail_line_renders_numeric_cost(self):
        """app._detail_line renders the (numeric-cost) detail dict as '$in/$out' and never
        '$$' — exercises the display path the reviewer found untested. Real opencode emits
        numeric costs (verified), so the fixture uses numbers too."""
        from omodel.app import OModelApp
        cat = self._make_catalog_with_opencode(["claude-opus-4-7", "gpt-5.5", "glm-5"])
        with patch("subprocess.run", return_value=_mock_run(MOCK_VERBOSE_OUTPUT)):
            info = cat.detail("claude-opus-4-7")
        line = OModelApp._detail_line(info)
        assert "ctx 200k" in line
        assert "$3/$15" in line
        assert "$$" not in line
        assert "image" in line

    def test_detail_returns_none_for_unknown_model(self):
        """Model not in any connected provider → detail() returns None."""
        cat = self._make_catalog_with_opencode(["claude-opus-4-7"])
        result = cat.detail("non-existent-model")
        assert result is None

    def test_detail_provider_param_selects_that_providers_record(self):
        """detail(model, provider=p) describes (p, model) when p serves the model — the detail
        pane passes the ASSIGNED provider so a gateway assignment shows the gateway's record
        (its cost/context can differ), never silently the first-seen provider's. An unknown /
        non-serving provider falls back to the old first-of-providers_for behavior. Seeds the
        cache for both providers (no subprocess), mirroring TestVariantsFor."""
        from omodel import cache

        cat = Catalog(
            available={"openai": ["gpt-5.5"], "opencode": ["gpt-5.5"]},
            connected=["openai", "opencode"],
        )
        openai_record = {"limit": {"context": 400000}, "cost": {"input": 1, "output": 8}}
        opencode_record = {"limit": {"context": 128000}, "cost": {"input": 2, "output": 16}}
        cache.write("verbose-openai", "openai/gpt-5.5\n" + json.dumps(openai_record, indent=2))
        cache.write(
            "verbose-opencode", "opencode/gpt-5.5\n" + json.dumps(opencode_record, indent=2)
        )

        with _NO_SHELL:
            assert cat.detail("gpt-5.5", provider="opencode")["context"] == 128000
            assert cat.detail("gpt-5.5", provider="openai")["context"] == 400000
            # No provider / a provider that doesn't serve it → first of providers_for (openai).
            assert cat.detail("gpt-5.5")["context"] == 400000
            assert cat.detail("gpt-5.5", provider="zhipuai")["context"] == 400000


# A no-subprocess guard: variants_for reads ONLY the cache (it must never shell out — there is no
# subprocess stub in this module, so a stray call would hit the REAL opencode binary).
_NO_SHELL = patch("subprocess.run", side_effect=AssertionError("variants_for must not shell out"))


class TestVariantsFor:
    """Catalog.variants_for — variant names from the CACHED `opencode … --verbose` output (the
    decision #14 reversal for the model pickers). Cache-only: never a subprocess. The conftest
    isolates the cache to a per-test tmp dir, so these seed it directly with cache.write."""

    def _seed(self, provider: str, records: dict) -> None:
        """Delegates to the shared canonical seeder (tests/_helpers.py)."""
        seed_verbose(provider, records)

    def test_reads_variant_keys_from_cached_verbose(self):
        """The KEYS of the model's `variants` object (opencode's order, lowercased). Reuses the
        realistic blob: RECORD_1 → {"max": …}, RECORD_2 → {}, RECORD_3 → no variants key."""
        cache.write(
            "verbose-opencode", MOCK_VERBOSE_OUTPUT, ["opencode", "models", "opencode", "--verbose"]
        )
        cat = Catalog(
            available={"opencode": ["claude-opus-4-7", "gpt-5.5", "glm-5"]}, connected=["opencode"]
        )
        with _NO_SHELL:
            assert cat.variants_for("opencode", "claude-opus-4-7") == ["max"]
            assert cat.variants_for("opencode", "gpt-5.5") == []       # variants: {}
            assert cat.variants_for("opencode", "glm-5") == []         # no variants key

    def test_total_cache_miss_returns_empty(self):
        """Nothing cached anywhere → [] (caller shows nothing). Crucially NO subprocess."""
        cat = Catalog(available={"opencode": ["gpt-5.5"]}, connected=["opencode"])
        with _NO_SHELL:
            assert cat.variants_for("opencode", "gpt-5.5") == []

    def test_opencode_none_is_converted_to_omo_off(self):
        """The single seam where opencode's vocabulary enters omodel. opencode still reports the
        bottom reasoning rung as `none`; omo 4.19.4 renamed it `off`. Converting here means no
        picker, guard or warning downstream ever has to handle both spellings — and position is
        preserved, since the rung's place in the ladder has not moved."""
        self._seed("openai", {"gpt-5.5": ["none", "low", "high"]})
        cat = Catalog(available={"openai": ["gpt-5.5"]}, connected=["openai"])
        with _NO_SHELL:
            assert cat.variants_for("openai", "gpt-5.5") == ["off", "low", "high"]

    def test_conversion_dedupes_against_omos_own_spelling(self):
        """A provider reporting BOTH spellings must not yield `off` twice — the rename can
        collide with a level the same provider already names omo's way."""
        self._seed("openai", {"gpt-5.5": ["none", "off", "high"]})
        cat = Catalog(available={"openai": ["gpt-5.5"]}, connected=["openai"])
        with _NO_SHELL:
            assert cat.variants_for("openai", "gpt-5.5") == ["off", "high"]

    def test_other_variant_names_pass_through_untouched(self):
        """Only `none` is renamed. An unknown value is forwarded to the provider verbatim by omo
        (`normalizeReasoning`'s passthrough), so omodel must not mangle it either."""
        self._seed("openai", {"gpt-5.5": ["minimal", "xhigh", "some-future-rung"]})
        cat = Catalog(available={"openai": ["gpt-5.5"]}, connected=["openai"])
        with _NO_SHELL:
            assert cat.variants_for("openai", "gpt-5.5") == [
                "minimal", "xhigh", "some-future-rung",
            ]

    def test_empty_object_everywhere_is_no_variants(self):
        """kimi: every serving provider reports `variants: {}` → [] (no variant step)."""
        self._seed("opencode", {"kimi-k2.5": []})
        self._seed("moonshotai-cn", {"kimi-k2.5": []})
        cat = Catalog(
            available={"opencode": ["kimi-k2.5"], "moonshotai-cn": ["kimi-k2.5"]},
            connected=["opencode", "moonshotai-cn"],
        )
        with _NO_SHELL:
            assert cat.variants_for("moonshotai-cn", "kimi-k2.5") == []

    def test_prefers_non_empty_across_providers(self):
        """A dedicated provider reporting `{}` falls through to the gateway's real set — glm-5.2 →
        high/max lives in the opencode gateway's verbose, not zhipuai's empty one."""
        self._seed("zhipuai", {"glm-5.2": []})                 # dedicated → empty object
        self._seed("opencode", {"glm-5.2": ["high", "max"]})   # gateway → the real set
        cat = Catalog(
            available={"zhipuai": ["glm-5.2"], "opencode": ["glm-5.2"]},
            connected=["opencode", "zhipuai"],
        )
        with _NO_SHELL:
            assert cat.variants_for("zhipuai", "glm-5.2") == ["high", "max"]

    def test_picked_provider_non_empty_wins(self):
        """When the picked provider reports its OWN non-empty set, that wins over the gateway's
        (variants are genuinely per-endpoint)."""
        self._seed("zhipuai", {"glm-5.2": ["low", "medium"]})
        self._seed("opencode", {"glm-5.2": ["high", "max"]})
        cat = Catalog(
            available={"zhipuai": ["glm-5.2"], "opencode": ["glm-5.2"]},
            connected=["opencode", "zhipuai"],
        )
        with _NO_SHELL:
            assert cat.variants_for("zhipuai", "glm-5.2") == ["low", "medium"]

    def test_dedicated_provider_may_out_report_the_gateway(self):
        """The counter-case to test_prefers_non_empty_across_providers, and the reason the search
        starts at the PICKED provider rather than the gateway.

        "Dedicated reports `{}`, the gateway has the real set" is only a tendency. Measured
        against live opencode, moonshotai-cn reports low/high/max for kimi-k3 while the opencode
        gateway reports just max — the dedicated endpoint is the richer one, and a gateway-first
        lookup would silently narrow the picker from three rungs to one."""
        self._seed("moonshotai-cn", {"kimi-k3": ["low", "high", "max"]})
        self._seed("opencode", {"kimi-k3": ["max"]})
        cat = Catalog(
            available={"opencode": ["kimi-k3"], "moonshotai-cn": ["kimi-k3"]},
            connected=["opencode", "moonshotai-cn"],
        )
        with _NO_SHELL:
            assert cat.variants_for("moonshotai-cn", "kimi-k3") == ["low", "high", "max"]
            assert cat.variants_for("opencode", "kimi-k3") == ["max"]

    def test_unknown_model_returns_empty(self):
        """A model no connected provider serves → [] (no record anywhere, no subprocess)."""
        self._seed("opencode", {"gpt-5.5": ["low", "medium", "high"]})
        cat = Catalog(available={"opencode": ["gpt-5.5"]}, connected=["opencode"])
        with _NO_SHELL:
            assert cat.variants_for("opencode", "no-such-model") == []

    def test_provider_mismatch_falls_through_to_serving_provider(self):
        """A picked provider that does NOT serve the model (a typed mismatch like openai/gpt-5.5
        when only opencode serves it here) still finds the variants via a provider that DOES serve
        it — variants_for scans [provider, *providers_for(model)]."""
        self._seed("opencode", {"gpt-5.5": ["low", "medium", "high"]})
        cat = Catalog(available={"opencode": ["gpt-5.5"]}, connected=["opencode"])
        with _NO_SHELL:
            # verbose-openai isn't cached (the openai miss must NOT shell out); opencode serves it.
            assert cat.variants_for("openai", "gpt-5.5") == ["low", "medium", "high"]

    def test_expired_entry_is_still_read(self):
        """STALE-WHILE-REVALIDATE, read half: variants_for ignores the 24h TTL (`_STALE_OK`).

        An expired `verbose-<prov>.json` is still opencode's answer, and dropping it doesn't buy
        fresher data — it buys NONE, which is strictly worse: resolve then falls back to the
        coarse heuristic `family.variants` and flags omo's own suggestion `⚠ variant`, while `v`
        offers nothing. Variant sets barely move next to availability, so day-old beats guessed.
        A user's `verbose-opencode.json` sitting 5 days old is what made this the common case,
        not the edge one — the gateway is where ~every model's real variants live."""
        self._seed("opencode", {"glm-5": ["high", "max"]})
        age_cache_entry("verbose-opencode", 5 * 86400)  # 5 days — well past the 24h TTL
        assert cache.read("verbose-opencode") is None, "precondition: TTL-expired for normal reads"
        cat = Catalog(available={"opencode": ["glm-5"]}, connected=["opencode"])
        with _NO_SHELL:  # and it still must not shell out to get there
            assert cat.variants_for("opencode", "glm-5") == ["high", "max"]

    def test_ttl_gates_the_verdict_not_the_provider_search(self):
        """`stale_ok=False` must never answer from a DIFFERENT provider than `stale_ok=True`.

        The loop returns the first NON-EMPTY set across [provider, *providers_for(model)], so
        applying the TTL inside it would skip an expired provider and let the next one answer —
        two different non-empty sets for one model. That is not exotic: the gateway and openai
        both report real sets (DESIGN §Data sources) and their cache ages drift apart by design,
        since detail() only re-warms the provider of an assignment you actually view. The
        advisory `candidates --json` (stale-ok) would then advertise a variant the CLI guard
        rejects with exit 3 — the contradiction the split exists to prevent.

        Both modes settle on openai here; the guard only downgrades ITS answer to "no
        information", it never substitutes opencode's."""
        self._seed("openai", {"gpt-5.5": ["low", "medium", "high", "xhigh"]})
        age_cache_entry("verbose-openai", 5 * 86400)          # stale, but still the answerer
        self._seed("opencode", {"gpt-5.5": ["low", "medium", "high"]})   # fresh, must NOT answer
        cat = Catalog(
            available={"openai": ["gpt-5.5"], "opencode": ["gpt-5.5"]},
            connected=["opencode", "openai"],
        )
        with _NO_SHELL:
            advisory = cat.variants_for("openai", "gpt-5.5")
            guard = cat.variants_for("openai", "gpt-5.5", stale_ok=False)
        assert advisory == ["low", "medium", "high", "xhigh"], advisory
        assert guard == [], (
            f"an expired answerer means 'no information', never another provider's set: {guard}"
        )
        # The invariant in one line: the guard is either silent or a subset of what was advertised.
        assert not guard or set(guard) <= set(advisory), (guard, advisory)

    def test_expired_entry_still_refetched_by_detail(self):
        """…and the REVALIDATE half is intact: detail() keeps the TTL, so the same expired entry
        drives a fresh `--verbose` whose write re-warms exactly the file variants_for reads.
        Serving stale variants must never become "never refresh" — that pairing is the whole
        design, and `r` (cache.clear) remains the hard reset for a genuinely removed variant."""
        self._seed("opencode", {"glm-5": ["high", "max"]})
        age_cache_entry("verbose-opencode", 5 * 86400)
        fresh = 'opencode/glm-5\n{"id": "glm-5", "variants": {"low": {}}, "limit": {"context": 9}}\n'
        with patch("subprocess.run", return_value=_mock_run(fresh)) as run:
            cat = Catalog(available={"opencode": ["glm-5"]}, connected=["opencode"])
            assert cat.detail("glm-5")["context"] == 9
            assert run.called, "an expired entry must still trigger detail()'s live --verbose"
        # The refetch rewrote the cache, so the stale read now yields the NEW set.
        with _NO_SHELL:
            assert cat.variants_for("opencode", "glm-5") == ["low"]
