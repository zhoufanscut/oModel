"""test_session.py — the headless core (session.py).

`Session` is what BOTH `app.py` and `cli.py` edit through, so these tests pin the rules that
used to be reachable only by driving the TUI: what the pick list contains, what a set writes,
and that a save publishes the config and the presets file together.

Real-config safety: every Session is built against an explicit tmp config path, so nothing
here can touch ~/.config/opencode/oh-my-openagent.jsonc (conftest's autouse fixtures are a
second net). Sessions are constructed from an injected Catalog wherever possible so no test
shells out; the two that exercise `Session.build()` stub `subprocess.run` + `shutil.which`.
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from _helpers import frozen_suggestions, seed_verbose

from omodel import config_io, presets
from omodel import session as session_mod
from omodel.catalog import Catalog
from omodel.resolve import Resolver
from omodel.session import Session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CATALOG = Catalog(
    available={
        "opencode": ["claude-opus-4-7", "kimi-k2.5", "glm-5", "gpt-5.5"],
        "moonshotai-cn": ["kimi-k2.5"],
        "zhipuai": ["glm-5"],
        "openai": ["gpt-5.5"],
    },
    connected=["opencode", "moonshotai-cn", "zhipuai", "openai"],
)

VALID_CONFIG = """\
{
  "agents": {
    "probe": {"model": "opencode/claude-opus-4-7"}
  },
  "categories": {
    "probe-cat": {"model": "opencode/gpt-5.5"}
  }
}
"""


def _write(path, text: str) -> None:
    with open(str(path), "w", encoding="utf-8") as f:
        f.write(text)


def _read(path) -> str:
    with open(str(path), encoding="utf-8") as f:
        return f.read()


def _session(tmp_path, text: str = VALID_CONFIG, catalog: Catalog = CATALOG) -> Session:
    """A Session over a tmp config with the frozen probe chains and an injected catalog —
    no subprocess, no real opencode."""
    cfg_path = tmp_path / "oh-my-openagent.jsonc"
    _write(cfg_path, text)
    suggestions = frozen_suggestions()
    cfg, resolved = config_io.load_config(str(cfg_path))
    return Session(
        catalog=catalog,
        suggestions=suggestions,
        resolver=Resolver.build(catalog, suggestions),
        cfg=cfg,
        config_path=resolved,
    )


def _mock_run(stdout: str, returncode: int = 0):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = ""
    return m


# ---------------------------------------------------------------------------
# Pure helpers / guards
# ---------------------------------------------------------------------------

class TestGuards:

    @pytest.mark.parametrize("target,expected", [
        ("agent:hephaestus", True),
        ("agent:hephaestus.compaction", True),
        ("agent:sisyphus", False),
        ("cat:deep", False),
        ("nonsense", False),
    ])
    def test_gpt_only_covers_subtargets(self, target, expected):
        assert session_mod.gpt_only(target) is expected

    @pytest.mark.parametrize("variant", [None, "", "none", "NONE", "None", " none "])
    def test_is_no_variant_true(self, variant):
        assert session_mod.is_no_variant(variant) is True

    @pytest.mark.parametrize("variant", ["max", "high", "thinking", "   "])
    def test_is_no_variant_false(self, variant):
        """A whitespace-only variant is NOT dropped — it can only come from a hand-edited config,
        and the predicate is kept identical to the TUI's so both surfaces agree. cli.py strips
        its --variant input instead."""
        assert session_mod.is_no_variant(variant) is False

    def test_subkinds_ultrawork_is_sisyphus_only(self):
        assert session_mod.subkinds_for("sisyphus") == ("ultrawork", "compaction")
        assert session_mod.subkinds_for("oracle") == ("compaction",)

    @pytest.mark.parametrize("target,expected", [
        ("agent:sisyphus", ("agent", "sisyphus", None)),
        ("agent:sisyphus.ultrawork", ("agent", "sisyphus", "ultrawork")),
        ("cat:deep", ("cat", "deep", None)),
        ("agent:sisyphus.bogus", None),   # not a real sub-kind
        ("agent:", None),
        ("cat:", None),
        ("sisyphus", None),               # no prefix at all
    ])
    def test_split_target(self, target, expected):
        assert session_mod.split_target(target) == expected

    def test_target_label_strips_the_prefix(self):
        assert session_mod.target_label("agent:sisyphus.ultrawork") == "sisyphus.ultrawork"
        assert session_mod.target_label("cat:deep") == "deep"


# ---------------------------------------------------------------------------
# Construction + the presets invariant
# ---------------------------------------------------------------------------

class TestConstruction:

    def test_seeds_a_preset_from_the_existing_config(self, tmp_path):
        s = _session(tmp_path)
        assert len(s.store.presets) == 1
        assert s.store.presets[0].name == presets.DEFAULT_NAME
        assert s.store.active == 0
        # Seeded IN MEMORY — the one write rule means nothing lands until a save.
        assert not os.path.exists(presets.presets_path(s.config_path))

    def test_seeded_session_is_not_dirty(self, tmp_path):
        """A launch that changes nothing must read clean, or `q` would prompt every time."""
        assert _session(tmp_path).is_dirty() is False

    def test_sync_conflict_when_config_matches_no_preset(self, tmp_path):
        """Something outside omodel wrote the config — the one case the invariant can't cover."""
        cfg_path = tmp_path / "oh-my-openagent.jsonc"
        _write(cfg_path, VALID_CONFIG)
        store = presets.Store(
            presets=[presets.capture("other", {"agents": {"probe": {"model": "x/y"}},
                                               "categories": {}})],
            active=0,
        )
        presets.write(str(cfg_path), store)
        s = _session(tmp_path)
        assert s.sync_conflict is True

    def test_no_sync_conflict_when_config_matches_a_preset(self, tmp_path):
        cfg_path = tmp_path / "oh-my-openagent.jsonc"
        _write(cfg_path, VALID_CONFIG)
        cfg, _ = config_io.load_config(str(cfg_path))
        presets.write(str(cfg_path), presets.seeded(cfg))
        s = _session(tmp_path)
        assert s.sync_conflict is False

    def test_build_loads_everything(self, tmp_path):
        cfg_path = tmp_path / "oh-my-openagent.jsonc"
        _write(cfg_path, VALID_CONFIG)
        with patch("shutil.which", return_value="/usr/bin/opencode"), \
             patch("subprocess.run", return_value=_mock_run("opencode/glm-5\nzhipuai/glm-5\n")):
            s = Session.build(str(cfg_path))
        assert s.resolver is not None
        assert s.catalog.connected == ["opencode", "zhipuai"]
        assert s.degraded is False
        assert s.cfg["agents"]["probe"]["model"] == "opencode/claude-opus-4-7"

    def test_build_degrades_without_opencode(self, tmp_path):
        """opencode absent → empty catalog, resolver still built, degraded flagged. The flag is
        the contract: an empty pick list here means UNKNOWN, not 'nothing works'."""
        cfg_path = tmp_path / "oh-my-openagent.jsonc"
        _write(cfg_path, VALID_CONFIG)
        with patch("shutil.which", return_value=None), \
             patch("subprocess.run", side_effect=AssertionError("must not shell out")):
            s = Session.build(str(cfg_path))
        assert s.degraded is True
        assert s.catalog.connected == []
        assert s.resolver is not None


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

class TestTargets:

    def test_known_targets_include_valid_subtargets_only(self, tmp_path):
        targets = _session(tmp_path).known_targets()
        assert "agent:probe" in targets
        assert "agent:probe.compaction" in targets
        assert "cat:probe-cat" in targets
        # probe isn't sisyphus, so ultrawork is not offered on it (ULTRAWORK_AGENTS).
        assert "agent:probe.ultrawork" not in targets

    @pytest.mark.parametrize("target,expected", [
        ("agent:probe", True),
        ("agent:probe.compaction", True),
        ("agent:probe.ultrawork", False),   # valid shape, wrong agent
        ("cat:probe-cat", True),
        ("agent:nope", False),
        ("cat:nope", False),
        ("garbage", False),
    ])
    def test_is_known(self, tmp_path, target, expected):
        assert _session(tmp_path).is_known(target) is expected


# ---------------------------------------------------------------------------
# The pick list
# ---------------------------------------------------------------------------

class TestRows:

    def test_rows_are_the_chain_filtered_to_what_you_have(self, tmp_path):
        rows = _session(tmp_path).rows("agent:probe")
        values = [f"{r['provider']}/{r['model']}" for r in rows]
        # Chain entries you can run, one row per serving provider, dedicated before gateway.
        assert "opencode/claude-opus-4-7" in values
        assert "openai/gpt-5.5" in values
        assert values.index("openai/gpt-5.5") < values.index("opencode/gpt-5.5")
        # big-pickle is in the chain but nobody serves it — hidden, never shown.
        assert not any("big-pickle" in v for v in values)

    def test_rows_surface_an_off_chain_current_assignment(self, tmp_path):
        text = json.dumps({
            "agents": {"probe": {"model": "myprovider/custom-model"}},
            "categories": {},
        })
        rows = _session(tmp_path, text).rows("agent:probe")
        off = [r for r in rows if r["model"] == "custom-model"]
        assert len(off) == 1
        assert off[0]["source"] == "add"
        # The catalog is readable and nobody serves it → ⚠ unavailable.
        assert off[0]["warn"] == ["unavailable"]

    def test_off_chain_row_is_not_warned_in_degraded_mode(self, tmp_path):
        """Availability is unknown with no catalog, so an unqualified ⚠ would mislead."""
        text = json.dumps({
            "agents": {"probe": {"model": "myprovider/custom-model"}},
            "categories": {},
        })
        empty = Catalog(available={}, connected=[])
        rows = _session(tmp_path, text, catalog=empty).rows("agent:probe")
        off = [r for r in rows if r["model"] == "custom-model"]
        assert off and off[0]["warn"] == []

    def test_caller_held_custom_rows_are_merged(self, tmp_path):
        custom = [{"source": "add", "model": "zzz-custom", "provider": "openrouter",
                   "variant": None, "entry": None, "substitute_for": None, "warn": []}]
        rows = _session(tmp_path).rows("agent:probe", custom)
        assert any(r["model"] == "zzz-custom" for r in rows)

    def test_malformed_bare_id_is_not_surfaced(self, tmp_path):
        """A value with no `provider/` would render as `/model` — skipped instead."""
        text = json.dumps({"agents": {"probe": {"model": "bare-id"}}, "categories": {}})
        rows = _session(tmp_path, text).rows("agent:probe")
        assert not any(r["model"] == "bare-id" for r in rows)

    def test_rows_are_empty_for_an_unknown_target(self, tmp_path):
        assert _session(tmp_path).rows("agent:nope") == []


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

class TestMutations:

    def test_set_model_writes_provider_slash_model(self, tmp_path):
        s = _session(tmp_path)
        s.set_model("agent:probe", "zhipuai", "glm-5", "max")
        assert s.cfg["agents"]["probe"]["model"] == "zhipuai/glm-5"
        assert s.cfg["agents"]["probe"]["variant"] == "max"

    @pytest.mark.parametrize("variant", [None, "", "none", "NONE"])
    def test_none_variant_drops_the_key(self, tmp_path, variant):
        """`none` ≡ `(none)` ≡ no variant key — never written as variant: "none"."""
        s = _session(tmp_path)
        s.set_model("agent:probe", "zhipuai", "glm-5", "max")
        s.set_model("agent:probe", "zhipuai", "glm-5", variant)
        assert "variant" not in s.cfg["agents"]["probe"]

    def test_set_creates_missing_nodes(self, tmp_path):
        s = _session(tmp_path, json.dumps({}))
        s.set_model("agent:probe.compaction", "zhipuai", "glm-5")
        assert s.cfg["agents"]["probe"]["compaction"]["model"] == "zhipuai/glm-5"

    def test_set_coerces_a_hand_mangled_non_dict_node(self, tmp_path):
        """A hand-edited `"agents": null` must not crash the write."""
        s = _session(tmp_path, json.dumps({"agents": None, "categories": None}))
        s.set_model("agent:probe", "zhipuai", "glm-5")
        assert s.cfg["agents"]["probe"]["model"] == "zhipuai/glm-5"

    def test_set_row_takes_a_candidate_row(self, tmp_path):
        s = _session(tmp_path)
        row = s.rows("agent:probe")[0]
        s.set_row("agent:probe", row)
        assert s.cfg["agents"]["probe"]["model"] == f"{row['provider']}/{row['model']}"

    def test_clear_drops_model_and_variant(self, tmp_path):
        s = _session(tmp_path)
        s.set_model("agent:probe", "zhipuai", "glm-5", "max")
        assert s.clear("agent:probe") is True
        node = s.cfg["agents"]["probe"]
        assert "model" not in node and "variant" not in node
        assert s.clear("agent:probe") is False  # nothing left to clear

    def test_delete_subtarget_removes_the_whole_node(self, tmp_path):
        s = _session(tmp_path)
        s.set_model("agent:probe.compaction", "zhipuai", "glm-5")
        s.delete_subtarget("probe", "compaction")
        assert "compaction" not in s.cfg["agents"]["probe"]

    def test_assignment_reads_back_what_was_set(self, tmp_path):
        s = _session(tmp_path)
        s.set_model("agent:probe", "zhipuai", "glm-5", "max")
        assert s.assignment("agent:probe") == ("zhipuai/glm-5", "max")
        assert s.assignment("agent:nope") == ("", None)


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------

class TestVariants:

    def test_variants_come_from_the_cached_verbose(self, tmp_path):
        seed_verbose("opencode", {"claude-opus-4-7": ["low", "max"]})
        s = _session(tmp_path)
        assert s.variants_for("opencode", "claude-opus-4-7") == ["low", "max"]

    def test_uncached_reports_nothing_rather_than_guessing(self, tmp_path):
        """Empty means "no information", not an authoritative "no variants" — the distinction
        the CLI's --variant guard depends on (decision #14)."""
        assert _session(tmp_path).variants_for("opencode", "claude-opus-4-7") == []


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

class TestPresets:

    def test_projected_store_carries_the_live_cfg(self, tmp_path):
        s = _session(tmp_path)
        s.set_model("agent:probe", "zhipuai", "glm-5")
        projected = s.projected_store()
        assert projected.presets[0].agents["probe"]["model"] == "zhipuai/glm-5"
        # The stored preset itself is untouched until a save.
        assert s.store.presets[0].agents["probe"]["model"] == "opencode/claude-opus-4-7"

    def test_switch_banks_edits_into_the_preset_you_leave(self, tmp_path):
        s = _session(tmp_path)
        s.store.presets.append(presets.capture("cheap", {
            "agents": {"probe": {"model": "zhipuai/glm-5"}}, "categories": {},
        }))
        s.set_model("agent:probe", "openai", "gpt-5.5")   # edit while on preset 0
        s.switch_preset(1)
        assert s.store.active == 1
        assert s.cfg["agents"]["probe"]["model"] == "zhipuai/glm-5"       # preset 1's models
        assert s.store.presets[0].agents["probe"]["model"] == "openai/gpt-5.5"  # banked

    def test_switch_replaces_rather_than_overlays(self, tmp_path):
        """A preset is a complete state: a target it doesn't define is CLEARED, not left over."""
        s = _session(tmp_path)
        s.store.presets.append(presets.capture("empty", {"agents": {}, "categories": {}}))
        s.switch_preset(1)
        assert s.cfg["agents"] == {}

    @pytest.mark.parametrize("ref,expected", [
        ("default", 0), ("DEFAULT", 0), ("cheap", 1), ("2", 1), (2, 1),
        ("nope", None), ("9", None), ("0", None),
    ])
    def test_preset_index_resolves_names_and_1_based_indices(self, tmp_path, ref, expected):
        s = _session(tmp_path)
        s.store.presets.append(presets.capture("cheap", {"agents": {}, "categories": {}}))
        assert s.preset_index(ref) == expected

    def test_a_preset_named_like_a_number_stays_addressable(self, tmp_path):
        s = _session(tmp_path)
        s.store.presets.append(presets.capture("2", {"agents": {}, "categories": {}}))
        # "2" is a real NAME here, so it must win over the 1-based index reading.
        assert s.preset_index("2") == 1


# ---------------------------------------------------------------------------
# Publication — the one write rule
# ---------------------------------------------------------------------------

class TestSave:

    def test_save_writes_both_files(self, tmp_path):
        s = _session(tmp_path)
        s.set_model("agent:probe", "zhipuai", "glm-5")
        result = s.save()
        assert result.changed is True
        assert "zhipuai/glm-5" in _read(s.config_path)
        store_path = presets.presets_path(s.config_path)
        assert os.path.exists(store_path)
        written = json.loads(_read(store_path))
        assert written["presets"][0]["agents"]["probe"]["model"] == "zhipuai/glm-5"

    def test_save_leaves_the_session_clean(self, tmp_path):
        s = _session(tmp_path)
        s.set_model("agent:probe", "zhipuai", "glm-5")
        assert s.is_dirty() is True
        s.save()
        assert s.is_dirty() is False

    def test_save_snapshots_a_backup_and_pins_the_original(self, tmp_path):
        s = _session(tmp_path)
        s.set_model("agent:probe", "zhipuai", "glm-5")
        result = s.save()
        backup_dir = os.path.join(os.path.dirname(s.config_path), ".backup")
        assert result.original_created is True
        assert os.path.exists(os.path.join(backup_dir, "original.jsonc"))
        assert result.backup and os.path.exists(result.backup)

    def test_save_preserves_comments_outside_agents_and_categories(self, tmp_path):
        text = '// keep me\n{\n  "agents": {"probe": {"model": "opencode/glm-5"}},\n' \
               '  "categories": {},\n  "other": 1\n}\n'
        s = _session(tmp_path, text)
        s.set_model("agent:probe", "zhipuai", "glm-5")
        s.save()
        on_disk = _read(s.config_path)
        assert "// keep me" in on_disk
        assert '"other": 1' in on_disk

    def test_config_equals_the_active_preset_after_a_save(self, tmp_path):
        """The invariant, stated directly: what's on disk IS the active preset."""
        s = _session(tmp_path)
        s.set_model("agent:probe", "zhipuai", "glm-5")
        s.save()
        reloaded = presets.load(s.config_path)
        cfg, _ = config_io.load_config(s.config_path)
        assert presets.matching_index(reloaded, cfg) == reloaded.active

    def test_write_store_raises_so_a_caller_can_report(self, tmp_path):
        """presets.write raises by contract — a silently dropped write would lie about durable
        state. app.py catches this to notify; the CLI turns it into a non-zero exit."""
        s = _session(tmp_path)
        os.makedirs(presets.presets_path(s.config_path))  # a directory in the way
        with pytest.raises(OSError):
            s.write_store()

    def test_dirty_tracks_the_presets_file_too(self, tmp_path):
        """A presets-only change (a rename) is dirty even with no config diff — `s` writes both,
        so quitting has to warn about both."""
        s = _session(tmp_path)
        s.save()
        assert s.is_dirty() is False
        s.store.presets[0].name = "renamed"
        assert s.is_dirty() is True
