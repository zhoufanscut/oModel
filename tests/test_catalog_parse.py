"""test_catalog_parse.py — mocked `opencode models` + verbose-record parsing.

Monkeypatches subprocess.run so tests NEVER call the real opencode CLI.
DESIGN §catalog.py / §Data sources / §Verification checks #2 and #3.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from omodel import cache
from omodel.catalog import Catalog, CatalogUnavailable, load


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

    def test_connected_is_a_list_not_a_set(self):
        """catalog.connected must be a list in first-seen order, never a set."""
        with patch("subprocess.run", return_value=_mock_run(MOCK_MODELS_OUTPUT)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                cat = load()
        assert isinstance(cat.connected, list)

    def test_connected_first_seen_order(self):
        """Providers appear in connected in the order they first appear in output."""
        with patch("subprocess.run", return_value=_mock_run(MOCK_MODELS_OUTPUT)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                cat = load()
        # opencode, deepseek, moonshotai-cn, openai, zhipuai — in first-seen order
        assert cat.connected == ["opencode", "deepseek", "moonshotai-cn", "openai", "zhipuai"]

    def test_available_dict_structure(self):
        """available maps each provider to its model list in first-seen order."""
        with patch("subprocess.run", return_value=_mock_run(MOCK_MODELS_OUTPUT)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                cat = load()
        assert "opencode" in cat.available
        assert "claude-opus-4-7" in cat.available["opencode"]
        assert "kimi-k2.5" in cat.available["opencode"]
        # Models are LISTS (first-seen order), not sets
        assert isinstance(cat.available["opencode"], list)

    def test_split_on_first_slash_only(self):
        """Lines like 'openrouter/anthropic/claude-opus-4-7' split on the FIRST '/'.
        Provider = 'openrouter'; model = 'anthropic/claude-opus-4-7'."""
        with patch("subprocess.run", return_value=_mock_run(MOCK_MODELS_OUTPUT_SLASH_IN_MODEL)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                cat = load()
        assert "openrouter" in cat.available
        assert "anthropic/claude-opus-4-7" in cat.available["openrouter"]
        assert "openai/gpt-5.5" in cat.available["openrouter"]

    def test_providers_for_returns_list(self):
        """providers_for() returns a list, not a set."""
        with patch("subprocess.run", return_value=_mock_run(MOCK_MODELS_OUTPUT)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                cat = load()
        result = cat.providers_for("kimi-k2.5")
        assert isinstance(result, list)

    def test_providers_for_first_seen_order(self):
        """providers_for('kimi-k2.5') = ['opencode','moonshotai-cn'] in first-seen order."""
        with patch("subprocess.run", return_value=_mock_run(MOCK_MODELS_OUTPUT)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                cat = load()
        assert cat.providers_for("kimi-k2.5") == ["opencode", "moonshotai-cn"]

    def test_providers_for_gpt_5_5(self):
        """gpt-5.5 served by opencode AND openai — first-seen order."""
        with patch("subprocess.run", return_value=_mock_run(MOCK_MODELS_OUTPUT)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                cat = load()
        assert cat.providers_for("gpt-5.5") == ["opencode", "openai"]

    def test_providers_for_unknown_model(self):
        """Model not in any provider → empty list."""
        with patch("subprocess.run", return_value=_mock_run(MOCK_MODELS_OUTPUT)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                cat = load()
        assert cat.providers_for("does-not-exist") == []

    def test_no_duplicate_models_per_provider(self):
        """Same line appearing twice should not duplicate the model in the list."""
        dup_output = "opencode/gpt-5.5\nopencode/gpt-5.5\n"
        with patch("subprocess.run", return_value=_mock_run(dup_output)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                cat = load()
        assert cat.available["opencode"].count("gpt-5.5") == 1

    def test_count_not_hardasserted(self):
        """Model count may vary — tests do NOT pin a specific number."""
        with patch("subprocess.run", return_value=_mock_run(MOCK_MODELS_OUTPUT)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                cat = load()
        # Just assert > 0 — never a specific total
        total = sum(len(v) for v in cat.available.values())
        assert total > 0


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
        """Zero provider/model lines (even if exit 0) → CatalogUnavailable."""
        empty_output = "Some header line with no slash\n\n"
        with patch("subprocess.run", return_value=_mock_run(empty_output, returncode=0)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                with pytest.raises(CatalogUnavailable):
                    load()

    def test_no_partial_state(self):
        """There is no partial success state: either Catalog with data, empty Catalog,
        or CatalogUnavailable — nothing in between."""
        # Lines that yield zero valid provider/model pairs → must raise
        bad_output = "not-a-model-line\nstill-no-slash\n"
        with patch("subprocess.run", return_value=_mock_run(bad_output)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                with pytest.raises(CatalogUnavailable):
                    load()


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

    def _make_catalog_with_opencode(self, models: list) -> "Catalog":
        available = {"opencode": models}
        connected = ["opencode"]
        return Catalog(available=available, connected=connected)

    def test_detail_returns_correct_context(self):
        """detail() extracts limit.context correctly."""
        cat = self._make_catalog_with_opencode(["claude-opus-4-7", "gpt-5.5", "glm-5"])
        with patch("subprocess.run", return_value=_mock_run(MOCK_VERBOSE_OUTPUT)):
            result = cat.detail("claude-opus-4-7")
        assert result is not None
        assert result["context"] == 200000

    def test_detail_returns_cost_dict(self):
        """detail() returns cost as whatever the JSON block has (a nested dict)."""
        cat = self._make_catalog_with_opencode(["claude-opus-4-7", "gpt-5.5", "glm-5"])
        with patch("subprocess.run", return_value=_mock_run(MOCK_VERBOSE_OUTPUT)):
            result = cat.detail("claude-opus-4-7")
        assert result is not None
        assert result["cost"] is not None

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

    def test_detail_extracts_reasoning_capability(self):
        """detail() extracts capabilities.reasoning as a bool."""
        cat = self._make_catalog_with_opencode(["claude-opus-4-7", "gpt-5.5", "glm-5"])
        with patch("subprocess.run", return_value=_mock_run(MOCK_VERBOSE_OUTPUT)):
            # gpt-5.5 has reasoning=True
            result = cat.detail("gpt-5.5")
        assert result is not None
        assert result["reasoning"] is True

    def test_detail_extracts_image_capability(self):
        """detail() extracts capabilities.input.image as a bool."""
        cat = self._make_catalog_with_opencode(["claude-opus-4-7", "gpt-5.5", "glm-5"])
        with patch("subprocess.run", return_value=_mock_run(MOCK_VERBOSE_OUTPUT)):
            result = cat.detail("claude-opus-4-7")
        assert result is not None
        assert result["image"] is True

    def test_detail_does_not_read_verbose_variants(self):
        """Variant logic NEVER uses --verbose.variants (decision #14).
        detail() must NOT include 'variants' in its return dict."""
        cat = self._make_catalog_with_opencode(["claude-opus-4-7", "gpt-5.5", "glm-5"])
        with patch("subprocess.run", return_value=_mock_run(MOCK_VERBOSE_OUTPUT)):
            result = cat.detail("claude-opus-4-7")
        assert result is not None
        assert "variants" not in result, (
            "detail() must NEVER expose --verbose.variants (decision #14)"
        )

    def test_detail_picks_correct_record_from_multi_block(self):
        """With 3 records, detail() picks the one matching the queried model."""
        cat = self._make_catalog_with_opencode(["claude-opus-4-7", "gpt-5.5", "glm-5"])
        with patch("subprocess.run", return_value=_mock_run(MOCK_VERBOSE_OUTPUT)):
            glm_result = cat.detail("glm-5")
        assert glm_result is not None
        # glm-5 has context 64000 and no cache cost
        assert glm_result["context"] == 64000

    def test_detail_returns_none_for_unknown_model(self):
        """Model not in any connected provider → detail() returns None."""
        cat = self._make_catalog_with_opencode(["claude-opus-4-7"])
        result = cat.detail("non-existent-model")
        assert result is None

    def test_detail_result_keys(self):
        """detail() always returns exactly: context, cost, reasoning, image."""
        cat = self._make_catalog_with_opencode(["claude-opus-4-7", "gpt-5.5", "glm-5"])
        with patch("subprocess.run", return_value=_mock_run(MOCK_VERBOSE_OUTPUT)):
            result = cat.detail("claude-opus-4-7")
        assert result is not None
        assert set(result.keys()) == {"context", "cost", "reasoning", "image"}

    def test_detail_cache_cost_in_cost_dict(self):
        """Records with cache costs carry them inside the cost dict (not top-level)."""
        cat = self._make_catalog_with_opencode(["claude-opus-4-7", "gpt-5.5", "glm-5"])
        with patch("subprocess.run", return_value=_mock_run(MOCK_VERBOSE_OUTPUT)):
            result = cat.detail("gpt-5.5")
        # gpt-5.5 has cache cost nested in cost
        assert result is not None
        cost = result["cost"]
        assert cost is not None
        assert "cache" in cost


# A no-subprocess guard: variants_for reads ONLY the cache (it must never shell out — there is no
# subprocess stub in this module, so a stray call would hit the REAL opencode binary).
_NO_SHELL = patch("subprocess.run", side_effect=AssertionError("variants_for must not shell out"))


class TestVariantsFor:
    """Catalog.variants_for — variant names from the CACHED `opencode … --verbose` output (the
    decision #14 reversal for the model pickers). Cache-only: never a subprocess. The conftest
    isolates the cache to a per-test tmp dir, so these seed it directly with cache.write."""

    def _seed(self, provider: str, records: dict) -> None:
        """Seed a cached verbose-<provider> from {model: [variants]} — mirrors opencode's shape:
        a `provider/model` header line + a JSON block whose `variants` is an OBJECT keyed by name."""
        parts = []
        for model, variants in records.items():
            parts.append(f"{provider}/{model}")
            parts.append(json.dumps({"id": model, "variants": {v: {} for v in variants}}))
        cache.write(
            f"verbose-{provider}",
            "\n".join(parts) + "\n",
            ["opencode", "models", provider, "--verbose"],
        )

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
