"""test_unified_config.py — omo 4.19.3+'s unified `~/.omo/omo.jsonc` (issue #12).

Two things changed under omodel at once, and these tests pin both:

  1. LOCATION. The config moved to `~/.omo/omo.jsonc` and the editable `agents`/`categories`
     moved INSIDE `"[opencode]"`. omo folds base → `[opencode]` (last wins), so a top-level
     pair written into a unified document is accepted, saved, and then silently outranked —
     which is why several tests here assert on the ABSENCE of a root `agents` key.
  2. SPELLING. `2026-08-reasoning-unification` renamed `variant` → `reasoning` on agents and
     categories, but NOT inside `ultrawork`/`compaction`, whose override reads `.variant` and
     nothing else. omo consults every source's `reasoning` before any source's `variant`, so the
     spelling omodel writes decides whether the write does anything at all.

Real-config safety: every test drives an explicit tmp path or a monkeypatched $HOME, so nothing
here can reach ~/.omo or ~/.config/opencode.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import types
from unittest.mock import patch

import pytest
from _helpers import frozen_suggestions

from omodel import cli, config_io, presets
from omodel.catalog import Catalog
from omodel.resolve import Resolver
from omodel.session import Session

CATALOG = Catalog(
    available={"opencode": ["claude-opus-4-7"], "openai": ["gpt-5.5"]},
    connected=["opencode", "openai"],
)

UNIFIED = """\
// OMO configuration
{
  "$schema": "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/assets/omo.schema.json",
  "[opencode]": {
    // keep me
    "agents": {
      "probe": {"model": "opencode/claude-opus-4-7", "reasoning": "high"}
    },
    "categories": {
      "deep": {"model": "openai/gpt-5.5"}
    },
    "team_mode": {"enabled": true}
  },
  "_migrations": ["2026-08-reasoning-unification"]
}
"""

LEGACY = """\
{
  "agents": {"probe": {"model": "opencode/claude-opus-4-7", "variant": "high"}},
  "categories": {},
  "team_mode": false
}
"""


def _write(path, text: str) -> None:
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as f:
        f.write(text)


def _read(path) -> str:
    with open(str(path), encoding="utf-8") as f:
        return f.read()


def _session(tmp_path, text: str = UNIFIED, name: str = "omo.jsonc") -> Session:
    cfg_path = tmp_path / name
    _write(cfg_path, text)
    suggestions = frozen_suggestions()
    cfg, resolved = config_io.load_config(str(cfg_path))
    return Session(
        catalog=CATALOG,
        suggestions=suggestions,
        resolver=Resolver.build(CATALOG, suggestions),
        cfg=cfg,
        config_path=resolved,
    )


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

class TestConfigPath:
    def test_prefers_unified_jsonc(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        _write(tmp_path / ".omo" / "omo.jsonc", "{}")
        assert config_io.config_path() == str(tmp_path / ".omo" / "omo.jsonc")

    def test_falls_back_to_omo_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        _write(tmp_path / ".omo" / "omo.json", "{}")
        assert config_io.config_path() == str(tmp_path / ".omo" / "omo.json")

    def test_falls_back_to_legacy_when_no_unified(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        legacy = tmp_path / ".config" / "opencode" / "oh-my-openagent.jsonc"
        _write(legacy, LEGACY)
        assert config_io.config_path() == str(legacy)

    def test_scaffold_target_is_unified_when_nothing_exists(self, tmp_path, monkeypatch):
        """Nothing anywhere → the NEW path, so the legacy file is never recreated."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert config_io.config_path() == str(tmp_path / ".omo" / "omo.jsonc")

    def test_unified_path_ignores_xdg_config_home(self, tmp_path, monkeypatch):
        """omo resolves `.omo` from $HOME on every platform (loader/paths.ts) and never consults
        $XDG_CONFIG_HOME. Honoring XDG here would point omodel at a file omo never reads."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        _write(tmp_path / ".omo" / "omo.jsonc", "{}")
        assert config_io.unified_config_path() == str(tmp_path / ".omo" / "omo.jsonc")
        assert config_io.config_path() == str(tmp_path / ".omo" / "omo.jsonc")
        # ...but the LEGACY path still honors it, unchanged.
        assert config_io.legacy_config_path().startswith(str(tmp_path / "xdg"))

    def test_legacy_file_not_recreated_when_unified_exists(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        _write(tmp_path / ".omo" / "omo.jsonc", UNIFIED)
        config_io.load_config(None)
        assert not (tmp_path / ".config" / "opencode" / "oh-my-openagent.jsonc").exists()


# ---------------------------------------------------------------------------
# Scope detection
# ---------------------------------------------------------------------------

class TestScope:
    @pytest.mark.parametrize("cfg", [
        {"[opencode]": {"agents": {}}},
        {"_migrations": ["x"]},
        {"legacy_migrations": {}},
        {"$schema": "https://example/assets/omo.schema.json"},
    ])
    def test_unified_markers(self, cfg):
        assert config_io.scope_of(cfg) == "opencode"

    @pytest.mark.parametrize("cfg", [
        {"agents": {}, "categories": {}},
        {},
        {"$schema": "https://example/assets/oh-my-opencode.schema.json"},
        "not-a-dict",
    ])
    def test_root_scope(self, cfg):
        assert config_io.scope_of(cfg) == "root"

    def test_managed_root_reads_block(self):
        cfg = {"[opencode]": {"agents": {"a": {}}}}
        assert config_io.managed_root(cfg) == {"agents": {"a": {}}}

    def test_managed_root_never_creates(self):
        cfg = {"_migrations": []}
        assert config_io.managed_root(cfg) == {}
        assert "[opencode]" not in cfg

    def test_managed_root_for_write_creates_block(self):
        cfg = {"_migrations": []}
        config_io.managed_root_for_write(cfg)["agents"] = {}
        assert cfg["[opencode]"] == {"agents": {}}
        assert "agents" not in cfg

    def test_managed_root_for_write_coerces_non_dict(self):
        """A hand-edited `"[opencode]": null` must not send writes to the document root, where
        they would be silently outranked once the block comes back."""
        cfg = {"[opencode]": None, "_migrations": []}
        config_io.managed_root_for_write(cfg)["agents"] = {}
        assert cfg["[opencode]"] == {"agents": {}}


# ---------------------------------------------------------------------------
# render(): nested spans
# ---------------------------------------------------------------------------

class TestRenderUnified:
    def test_preserves_comments_and_siblings(self, tmp_path):
        session = _session(tmp_path)
        session.set_model("agent:probe", "openai", "gpt-5.5", variant="low")
        session.save()
        text = _read(session.config_path)
        assert "// keep me" in text
        assert "// OMO configuration" in text
        assert '"team_mode"' in text
        assert '"_migrations"' in text
        assert "2026-08-reasoning-unification" in text

    def test_never_writes_a_root_agents_key(self, tmp_path):
        session = _session(tmp_path)
        session.set_model("agent:probe", "openai", "gpt-5.5")
        session.save()
        reloaded, _ = config_io.load_config(session.config_path)
        assert "agents" not in reloaded
        assert reloaded["[opencode]"]["agents"]["probe"]["model"] == "openai/gpt-5.5"

    def test_serialize_fallback_keeps_scope(self, tmp_path):
        """A unified document missing `categories` can't be spliced, so render() degrades to a
        clean rewrite — which must still put agents inside `[opencode]`, not at the root."""
        session = _session(tmp_path, '{"[opencode]": {"agents": {}}}\n')
        session.set_model("agent:probe", "openai", "gpt-5.5")
        session.save()
        reloaded, _ = config_io.load_config(session.config_path)
        assert "agents" not in reloaded
        assert reloaded["[opencode]"]["agents"]["probe"]["model"] == "openai/gpt-5.5"

    def test_missing_but_empty_key_does_not_cost_the_comments(self, tmp_path):
        """A `[opencode]` block with no `categories` used to force a whole-file rewrite, which on
        a unified config discards omo's comments and not just omodel's. Nothing needs writing for
        an absent-and-empty key, so the file is left alone."""
        text = '// keep me\n{\n  "[opencode]": {\n    // and me\n    "agents": {}\n  }\n}\n'
        session = _session(tmp_path, text)
        session.set_model("agent:probe", "openai", "gpt-5.5")
        session.save()
        out = _read(session.config_path)
        assert "// keep me" in out
        assert "// and me" in out
        assert '"categories"' not in out  # not invented just to have a span

    def test_legacy_document_still_splices_at_root(self, tmp_path):
        session = _session(tmp_path, LEGACY, name="oh-my-openagent.jsonc")
        assert session.scope == "root"
        session.set_model("agent:probe", "openai", "gpt-5.5")
        session.save()
        reloaded, _ = config_io.load_config(session.config_path)
        assert reloaded["agents"]["probe"]["model"] == "openai/gpt-5.5"
        assert "[opencode]" not in reloaded


# ---------------------------------------------------------------------------
# reasoning vs variant
# ---------------------------------------------------------------------------

class TestReasoningSpelling:
    def test_unified_writes_reasoning(self, tmp_path):
        session = _session(tmp_path)
        session.set_model("agent:probe", "openai", "gpt-5.5", variant="xhigh")
        node = session.managed["agents"]["probe"]
        assert node["reasoning"] == "xhigh"
        assert "variant" not in node

    def test_legacy_writes_variant(self, tmp_path):
        session = _session(tmp_path, LEGACY, name="oh-my-openagent.jsonc")
        session.set_model("agent:probe", "openai", "gpt-5.5", variant="xhigh")
        node = session.managed["agents"]["probe"]
        assert node["variant"] == "xhigh"
        assert "reasoning" not in node

    @pytest.mark.parametrize("sub", ["ultrawork", "compaction"])
    def test_subtargets_keep_variant_in_unified_scope(self, tmp_path, sub):
        """`ultrawork`/`compaction` overrides read `.variant` and never `.reasoning`
        (omo-opencode/src/plugin/ultrawork-model-override.ts:81,84,93), so the rename must NOT
        reach them — omo's own migration left them alone for the same reason."""
        session = _session(tmp_path)
        session.set_model(f"agent:probe.{sub}", "openai", "gpt-5.5", variant="max")
        node = session.managed["agents"]["probe"][sub]
        assert node["variant"] == "max"
        assert "reasoning" not in node

    def test_write_clears_the_other_spellings(self, tmp_path):
        """A stale key left beside the one omodel wrote is dead config at best — and because a
        CATEGORY's `reasoning` outranks an AGENT's `variant`, actively misleading at worst."""
        session = _session(tmp_path)
        node = session.ensure_node("agent:probe")
        node["variant"] = "stale"
        node["reasoningEffort"] = "stale"
        session.set_model("agent:probe", "openai", "gpt-5.5", variant="low")
        assert node == {"model": "openai/gpt-5.5", "reasoning": "low"}

    def test_no_variant_drops_every_spelling(self, tmp_path):
        session = _session(tmp_path)
        node = session.ensure_node("agent:probe")
        node["variant"] = "stale"
        session.set_model("agent:probe", "openai", "gpt-5.5", variant=None)
        assert node == {"model": "openai/gpt-5.5"}

    def test_none_becomes_off_under_the_unified_spelling(self, tmp_path):
        """The rename and the `variant` → `reasoning` spelling compose: on a unified document a
        `none` lands as `reasoning: "off"`, and the stale `variant` key still goes."""
        session = _session(tmp_path)
        node = session.ensure_node("agent:probe")
        node["variant"] = "stale"
        session.set_model("agent:probe", "openai", "gpt-5.5", variant="none")
        assert node == {"model": "openai/gpt-5.5", "reasoning": "off"}

    @pytest.mark.parametrize("node,expected", [
        ({"reasoning": "high", "variant": "low"}, "high"),
        ({"variant": "low", "reasoningEffort": "max"}, "low"),
        ({"reasoningEffort": "max"}, "max"),
        ({"reasoning": "   "}, None),
        ({"reasoning": None}, None),
        ({}, None),
    ])
    def test_read_precedence_matches_omo(self, node, expected):
        """omo checks reasoning → reasoningEffort → variant per source, but every source's
        `reasoning` before any source's `variant` (agent-variant.ts:102-109). Within one node the
        observable order is reasoning first; `variant` still resolves, so a pre-rename config
        keeps reporting what omo will actually use."""
        from omodel.session import read_variant
        assert read_variant(node) == expected

    def test_clear_removes_all_spellings(self, tmp_path):
        session = _session(tmp_path)
        node = session.ensure_node("agent:probe")
        node.update({"model": "x/y", "variant": "a", "reasoning": "b", "reasoningEffort": "c"})
        assert session.clear("agent:probe") is True
        assert node == {}

    def test_assignment_reports_reasoning(self, tmp_path):
        session = _session(tmp_path)
        assert session.assignment("agent:probe") == ("opencode/claude-opus-4-7", "high")


# ---------------------------------------------------------------------------
# Presets adoption
# ---------------------------------------------------------------------------

class TestScopeAdapterCompleteness:
    """Regressions for sites that reached past the scope adapter into `cfg`'s root. Each was a
    SILENT wrong answer on a unified config — the exact bug class this change exists to fix."""

    def test_check_sees_a_malformed_map_inside_the_block(self, tmp_path):
        """`omodel check` read the document root, so a broken `agents` under `"[opencode]"` was
        invisible and check called the config healthy."""
        session = _session(tmp_path, json.dumps(
            {"$schema": "x/omo.schema.json", "[opencode]": {"agents": "oops", "categories": {}}}))
        raw = session.managed.get("agents")
        assert raw is not None and not isinstance(raw, dict)

    def test_check_ignores_a_stray_root_map_on_a_unified_doc(self, tmp_path):
        """The mirror image: a leftover root `agents` is legal, outranked, and not omodel's to
        manage — flagging it made `check` exit 3 on a config that is fine."""
        session = _session(tmp_path, json.dumps({
            "[opencode]": {"agents": {}, "categories": {}}, "agents": "leftover junk"}))
        assert session.managed.get("agents") == {}

    def test_clear_drops_the_live_spelling_not_just_variant(self, tmp_path):
        """`app._remove_custom_row` popped `"variant"` by hand; on a unified config the live key
        is `reasoning`, which survived — omo kept resolving a level for a cleared target."""
        session = _session(tmp_path)
        node = session.ensure_node("agent:probe")
        node.update({"model": "openai/gpt-5.5", "reasoning": "high"})
        assert session.clear("agent:probe") is True
        assert node == {}

    def test_fingerprint_of_a_history_snapshot_uses_the_managed_node(self, tmp_path):
        """History snapshots are whole documents, so reading their root compared against
        (None, None) and always reported a difference."""
        session = _session(tmp_path)
        from omodel import presets as presets_mod
        managed = config_io.managed_root(session.cfg)
        assert presets_mod.fingerprint(
            managed.get("agents"), managed.get("categories")
        ) != presets_mod.fingerprint(session.cfg.get("agents"), session.cfg.get("categories"))


SPELLING_UNIFIED = """\
// OMO configuration
{
  "$schema": "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/assets/omo.schema.json",
  "[opencode]": {
    "agents": {"probe": {"model": "opencode/claude-opus-4-7"%(extra)s}},
    "categories": {}
  },
  "_migrations": ["2026-08-reasoning-unification"]
}
"""

SPELLING_LEGACY = """\
{
  "agents": {"probe": {"model": "opencode/claude-opus-4-7"%(extra)s}},
  "categories": {}
}
"""


class TestSpellingReconciliation:
    """omodel normalizes a PRESET's spelling in memory while the CONFIG keeps whatever is on
    disk. `fingerprint` therefore has to compare the two spelling-insensitively (`presets._canon`)
    — otherwise `reasoning: high` in the store never equals `variant: high` in the config, and a
    config the user has never touched reconciles against no preset at all: `sync_conflict` on
    every launch, permanently dirty, and every no-op verb reporting `changed: true` with the file
    byte-identical. That last one is the "claims success but nothing moved" shape this whole
    change exists to eliminate, so it must not be reintroduced by the fix for it."""

    @pytest.mark.parametrize("extra", [
        pytest.param(', "variant": "high"', id="variant"),
        pytest.param(', "reasoningEffort": "high"', id="reasoningEffort"),
        pytest.param(', "ultrawork": {"model": "openai/gpt-5.5", "reasoning": "max"}',
                     id="ultrawork-reasoning"),
    ])
    def test_unified_config_in_a_legacy_spelling_reconciles_clean(self, tmp_path, extra):
        session = _session(tmp_path, SPELLING_UNIFIED % {"extra": extra})
        assert session.scope == "opencode"
        assert session.sync_conflict is False
        assert session.store_is_dirty() is False
        assert session.is_dirty() is False

    @pytest.mark.parametrize("extra", [
        pytest.param(', "reasoning": "high"', id="reasoning"),
        pytest.param(', "reasoningEffort": "high"', id="reasoningEffort"),
    ])
    def test_legacy_config_in_a_unified_spelling_reconciles_clean(self, tmp_path, extra):
        """Mirror image, and a regression guard: before the unified-config work, a legacy config
        carrying `reasoning`/`reasoningEffort` opened clean, and it must keep doing so."""
        session = _session(tmp_path, SPELLING_LEGACY % {"extra": extra},
                           name="oh-my-openagent.jsonc")
        assert session.scope == "root"
        assert session.sync_conflict is False
        assert session.store_is_dirty() is False
        assert session.is_dirty() is False

    def test_fingerprint_folds_the_three_spellings(self):
        from omodel import presets as presets_mod
        base = {"probe": {"model": "x/y", "reasoning": "high"}}
        for other in ("variant", "reasoningEffort"):
            assert presets_mod.fingerprint(base, {}) == presets_mod.fingerprint(
                {"probe": {"model": "x/y", other: "high"}}, {})
        # ...but a different LEVEL is still a difference, and so is a blank one vs unset.
        assert presets_mod.fingerprint(base, {}) != presets_mod.fingerprint(
            {"probe": {"model": "x/y", "variant": "low"}}, {})
        assert presets_mod.fingerprint({"probe": {"model": "x/y", "variant": "  "}}, {}) == \
            presets_mod.fingerprint({"probe": {"model": "x/y"}}, {})

    def test_folding_applies_to_categories_and_subtargets(self):
        from omodel import presets as presets_mod
        assert presets_mod.fingerprint({}, {"deep": {"model": "x/y", "reasoning": "high"}}) == \
            presets_mod.fingerprint({}, {"deep": {"model": "x/y", "variant": "high"}})
        sub = {"probe": {"model": "x/y", "ultrawork": {"model": "a/b", "variant": "max"}}}
        other = {"probe": {"model": "x/y", "ultrawork": {"model": "a/b", "reasoning": "max"}}}
        assert presets_mod.fingerprint(sub, {}) == presets_mod.fingerprint(other, {})


class TestDirtinessAgreesWithTheDiff:
    """`is_dirty()` asks `serialize(cfg) != saved_text`; the save path asks whether
    `render(cfg, on_disk)` differs from disk. The empty-key skip made those two disagree — the
    canonical form always emits `agents`/`categories`, the splice skips an absent-and-empty one —
    so a config missing one of the keys could read dirty with nothing to write, and the TUI's
    "Nothing to save." branch returned WITHOUT re-baselining. `q` then warned about unsaved work
    forever while `s` insisted there was none, with no escape from inside the app."""

    # agents already canonical, `categories` absent entirely — the shape that triggers it.
    NO_CATEGORIES = '// OMO configuration\n{\n  "[opencode]": {\n    "agents": {}\n  },\n  "_migrations": []\n}\n'

    def test_switch_preset_does_not_strand_the_session_dirty(self, tmp_path):
        session = _session(tmp_path, self.NO_CATEGORIES)
        assert session.is_dirty() is False
        session.switch_preset(0)  # assigns BOTH keys, so cfg gains an empty `categories`
        # The divergence itself is expected and harmless...
        assert not session.diff().strip()
        # ...as long as a save reconciles it rather than leaving it stuck.
        session.save_config()
        assert session.is_dirty() is False

    @pytest.mark.parametrize("missing", ["agents", "categories"])
    def test_a_save_always_reconciles_the_two_notions(self, tmp_path, missing):
        keep = "categories" if missing == "agents" else "agents"
        session = _session(
            tmp_path,
            '{\n  "[opencode]": {\n    "' + keep + '": {}\n  },\n  "_migrations": []\n}\n')
        session.switch_preset(0)
        session.save_config()
        assert session.is_dirty() is False
        # and the file still has not been reformatted to add the absent key
        assert f'"{missing}"' not in _read(session.config_path)


class TestMalformedRoots:
    def test_opencode_block_present_but_null_is_still_unified(self):
        """Presence of the key is the marker — a legacy document never has it. Classifying this
        as legacy would send writes to the document root, where the block outranks them."""
        assert config_io.scope_of({"[opencode]": None, "team_mode": True}) == "opencode"

    def test_non_object_root_raises_config_parse_error(self, tmp_path):
        """Valid JSON, wrong shape. Every caller assumes a mapping, so this must be the same
        friendly error a malformed file gets, not an AttributeError traceback."""
        path = tmp_path / "arr.jsonc"
        _write(path, "[1, 2, 3]\n")
        with pytest.raises(config_io.ConfigParseError, match="top level"):
            config_io.load_config(str(path))


class TestRestoreScopeGuard:
    """`restore` is a verbatim copy, so a cross-format restore leaves the file invalid for omo —
    whose root schema is `.strict()` and whose loader answers a validation failure with the
    ALL-DEFAULT config. That would reset far more than the models, so it is refused outright."""

    def _with_backup(self, tmp_path, config_text: str, backup_text: str, name: str = "omo.jsonc"):
        cfg = tmp_path / name
        _write(cfg, config_text)
        _write(tmp_path / ".backup" / "20260101-000000.000.jsonc", backup_text)
        return str(cfg)

    def test_refuses_legacy_backup_onto_unified_config(self, tmp_path):
        path = self._with_backup(tmp_path, UNIFIED, LEGACY)
        with pytest.raises(config_io.BackupScopeMismatch, match="pre-4.19.3"):
            config_io.restore(path, "20260101-000000.000.jsonc")
        assert _read(path) == UNIFIED  # untouched

    def test_refuses_unified_backup_onto_legacy_config(self, tmp_path):
        path = self._with_backup(tmp_path, LEGACY, UNIFIED, name="oh-my-openagent.jsonc")
        with pytest.raises(config_io.BackupScopeMismatch, match="unified"):
            config_io.restore(path, "20260101-000000.000.jsonc")
        assert _read(path) == LEGACY

    def test_refusal_writes_nothing_at_all(self, tmp_path):
        """The guard runs before the safety snapshot, so a refusal doesn't even leave that
        behind — otherwise every refused attempt would consume a slot in the 20-deep ring."""
        path = self._with_backup(tmp_path, UNIFIED, LEGACY)
        before = sorted(os.listdir(tmp_path / ".backup"))
        with pytest.raises(config_io.BackupScopeMismatch):
            config_io.restore(path, "20260101-000000.000.jsonc")
        assert sorted(os.listdir(tmp_path / ".backup")) == before

    def test_same_scope_restore_still_works(self, tmp_path):
        other = UNIFIED.replace("opencode/claude-opus-4-7", "openai/gpt-5.5")
        path = self._with_backup(tmp_path, UNIFIED, other)
        config_io.restore(path, "20260101-000000.000.jsonc")
        assert _read(path) == other

    def test_unreadable_backup_is_not_guessed_at(self, tmp_path):
        """An unparseable snapshot yields no scope, and the guard declines to guess — it stays
        the pre-existing verbatim restore rather than a refusal on a file we can't classify."""
        path = self._with_backup(tmp_path, UNIFIED, "{ not json at all ")
        config_io.restore(path, "20260101-000000.000.jsonc")
        assert _read(path) == "{ not json at all "


LEGACY_PRESETS = json.dumps({
    "version": 2,
    "active": 0,
    "presets": [
        {"name": "cheap", "saved_at": "2026-07-01T00:00:00Z",
         "agents": {"probe": {"model": "openai/gpt-5.5", "variant": "low"}}, "categories": {}},
        {"name": "beefy", "saved_at": "2026-07-01T00:00:00Z", "agents": {}, "categories": {}},
    ],
})


def _staged_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    _write(tmp_path / ".omo" / "omo.jsonc", UNIFIED)
    _write(tmp_path / ".config" / "opencode" / ".omodel-presets.json", LEGACY_PRESETS)
    return (tmp_path / ".omo" / ".omodel-presets.json",
            tmp_path / ".config" / "opencode" / ".omodel-presets.json")


class TestPresetsAdoption:
    def test_adopts_and_deletes_original(self, tmp_path, monkeypatch):
        new, old = _staged_home(tmp_path, monkeypatch)
        count = presets.adopt(config_io.legacy_config_path(), config_io.unified_config_path())
        assert count == 2
        assert new.exists() and not old.exists()
        assert [p.name for p in presets.load(str(tmp_path / ".omo" / "omo.jsonc")).presets] == [
            "cheap", "beefy"]

    def test_no_op_when_destination_already_has_presets(self, tmp_path, monkeypatch):
        new, old = _staged_home(tmp_path, monkeypatch)
        _write(new, LEGACY_PRESETS)
        assert presets.adopt(config_io.legacy_config_path(),
                             config_io.unified_config_path()) is None
        assert old.exists()  # nothing taken, nothing deleted

    def test_no_op_when_source_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        _write(tmp_path / ".omo" / "omo.jsonc", UNIFIED)
        assert presets.adopt(config_io.legacy_config_path(),
                             config_io.unified_config_path()) is None

    def test_same_path_is_a_no_op(self, tmp_path, monkeypatch):
        _staged_home(tmp_path, monkeypatch)
        legacy = config_io.legacy_config_path()
        assert presets.adopt(legacy, legacy) is None

    def test_session_adopts_on_default_path(self, tmp_path, monkeypatch):
        new, old = _staged_home(tmp_path, monkeypatch)
        suggestions = frozen_suggestions()
        cfg, resolved = config_io.load_config(None)
        session = Session(catalog=CATALOG, suggestions=suggestions,
                          resolver=Resolver.build(CATALOG, suggestions),
                          cfg=cfg, config_path=resolved)
        assert session.adopted_presets == 2
        assert new.exists() and not old.exists()

    def test_session_does_not_adopt_for_explicit_config(self, tmp_path, monkeypatch):
        """`--config /tmp/scratch.jsonc` must never drag the real presets off to a temp dir."""
        _new, old = _staged_home(tmp_path, monkeypatch)
        scratch = tmp_path / "scratch" / "omo.jsonc"
        _write(scratch, UNIFIED)
        suggestions = frozen_suggestions()
        cfg, resolved = config_io.load_config(str(scratch))
        session = Session(catalog=CATALOG, suggestions=suggestions,
                          resolver=Resolver.build(CATALOG, suggestions),
                          cfg=cfg, config_path=resolved)
        assert session.adopted_presets is None
        assert old.exists()
        assert not (tmp_path / "scratch" / ".omodel-presets.json").exists()

    def test_adopted_presets_are_respelled_in_memory(self, tmp_path, monkeypatch):
        """A preset captured before the rename carries `variant`; applied to a unified config it
        would resolve behind `reasoning` and the switch would silently do nothing."""
        _staged_home(tmp_path, monkeypatch)
        suggestions = frozen_suggestions()
        cfg, resolved = config_io.load_config(None)
        session = Session(catalog=CATALOG, suggestions=suggestions,
                          resolver=Resolver.build(CATALOG, suggestions),
                          cfg=cfg, config_path=resolved)
        cheap = next(p for p in session.store.presets if p.name == "cheap")
        assert cheap.agents["probe"] == {"model": "openai/gpt-5.5", "reasoning": "low"}
        session.switch_preset(session.preset_index("cheap"))
        assert session.managed["agents"]["probe"]["reasoning"] == "low"

    def test_adopts_the_pinned_original_as_a_legacy_archive(self, tmp_path, monkeypatch):
        """The pre-omodel pin is irreplaceable, but it is LEGACY-shaped: it lands under a name
        `list_backups` does not offer, so it is preserved without becoming a restore entry that
        the scope guard must refuse on every pick — and without suppressing the first save's pin
        of the unified config, which is the one that CAN be restored."""
        _staged_home(tmp_path, monkeypatch)
        _write(tmp_path / ".config" / "opencode" / ".backup" / "original.jsonc", LEGACY)
        assert config_io.adopt_original_backup(
            config_io.legacy_config_path(), config_io.unified_config_path()) is True
        archive = tmp_path / ".omo" / ".backup" / "original-legacy.jsonc"
        assert archive.exists()
        assert _read(archive) == LEGACY
        # the source is COPIED, never moved
        assert (tmp_path / ".config" / "opencode" / ".backup" / "original.jsonc").exists()
        # ...and it is not offered as a restore candidate
        names = [b.name for b in config_io.list_backups(str(tmp_path / ".omo" / "omo.jsonc"))]
        assert names == []

    def test_adopt_original_is_idempotent(self, tmp_path, monkeypatch):
        _staged_home(tmp_path, monkeypatch)
        _write(tmp_path / ".config" / "opencode" / ".backup" / "original.jsonc", LEGACY)
        args = (config_io.legacy_config_path(), config_io.unified_config_path())
        assert config_io.adopt_original_backup(*args) is True
        assert config_io.adopt_original_backup(*args) is False

    def test_respelled_preset_reconciles_against_the_config(self, tmp_path, monkeypatch):
        """A legacy-spelled preset describing the SAME assignments as the (now unified-spelled)
        config must reconcile clean: respelling happens before the dirtiness baseline, so an
        upgrade that changes nothing reports no unsaved work and no sync conflict. Without the
        respelling, `variant: high` vs `reasoning: high` would fingerprint differently and every
        upgraded user would be told their config had drifted."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        _write(tmp_path / ".omo" / "omo.jsonc", UNIFIED)
        _write(tmp_path / ".config" / "opencode" / ".omodel-presets.json", json.dumps({
            "version": 2, "active": 0,
            "presets": [{
                "name": "mine", "saved_at": "2026-07-01T00:00:00Z",
                # the pre-rename spelling of exactly what UNIFIED holds
                "agents": {"probe": {"model": "opencode/claude-opus-4-7", "variant": "high"}},
                "categories": {"deep": {"model": "openai/gpt-5.5"}},
            }],
        }))
        suggestions = frozen_suggestions()
        cfg, resolved = config_io.load_config(None)
        session = Session(catalog=CATALOG, suggestions=suggestions,
                          resolver=Resolver.build(CATALOG, suggestions),
                          cfg=cfg, config_path=resolved)
        assert session.adopted_presets == 1
        assert session.sync_conflict is False
        assert session.store_is_dirty() is False
        assert session.is_dirty() is False

    def test_adoption_that_cannot_land_keeps_the_original(self, tmp_path, monkeypatch):
        """The presets store has no backup ring, so the delete has to be gated on the copy. With
        `~/.omo` unwritable the write raises, adoption reports nothing, and the only copy of the
        user's presets stays exactly where it was."""
        _, legacy_presets = _staged_home(tmp_path, monkeypatch)
        omo_dir = tmp_path / ".omo"
        omo_dir.chmod(0o555)
        try:
            suggestions = frozen_suggestions()
            cfg, resolved = config_io.load_config(None)
            session = Session(catalog=CATALOG, suggestions=suggestions,
                              resolver=Resolver.build(CATALOG, suggestions),
                              cfg=cfg, config_path=resolved)
            assert session.adopted_presets is None
            assert legacy_presets.exists()
        finally:
            omo_dir.chmod(0o755)

    def test_switch_respells_into_a_legacy_config(self, tmp_path):
        """The mirror of the unified case, and the one that decides whether a switch DOES
        anything: a store written by a newer omodel carries `reasoning`, and on a pre-4.19.3
        config only `variant` resolves. Sub-objects keep `variant` in both scopes."""
        session = _session(tmp_path, LEGACY, name="oh-my-openagent.jsonc")
        session.store.presets.append(presets.Preset(
            name="newer", saved_at="2026-07-01T00:00:00Z",
            agents={"probe": {"model": "openai/gpt-5.5", "reasoning": "low",
                              "ultrawork": {"model": "openai/gpt-5.5", "reasoning": "max"}}},
            categories={"deep": {"model": "openai/gpt-5.5", "reasoningEffort": "medium"}},
        ))
        session._normalize_store_spelling()
        session.switch_preset(1)
        session.save()
        reloaded, _ = config_io.load_config(session.config_path)
        probe = reloaded["agents"]["probe"]
        assert probe["variant"] == "low"
        assert "reasoning" not in probe
        assert probe["ultrawork"]["variant"] == "max"
        assert reloaded["categories"]["deep"]["variant"] == "medium"


# ---------------------------------------------------------------------------
# Harness safety
# ---------------------------------------------------------------------------

def test_conftest_nets_the_default_config_path(tmp_path):
    """`config_path()` with no override must land inside the per-test tmp dir.

    conftest's `_isolate_omodel_config` is the net for a test that forgets an explicit path, and
    redirecting $XDG_CONFIG_HOME alone stopped being enough here: `~/.omo/omo.jsonc` is resolved
    from $HOME and deliberately ignores XDG. Un-netted, a path-less test resolves the developer's
    real config — and `Session.__post_init__` adopts on that path, DELETING the real
    ~/.config/opencode/.omodel-presets.json. This asserts the net, not the resolution."""
    assert os.path.abspath(config_io.config_path()).startswith(str(tmp_path) + os.sep)


# ---------------------------------------------------------------------------
# JSONC the span scanner has to survive
# ---------------------------------------------------------------------------

GNARLY = """\
// OMO configuration
{
  "$schema": "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/assets/omo.schema.json",
  "note": "a } brace, a \\" quote, a literal [opencode] and // not-a-comment",
  "[opencode]": {
    /* block comment { with braces } */
    "agents": {
      "prométhée": {"model": "opencode/claude-opus-4-7", "reasoning": "high"}, // trailing
    },
    "categories": {
      "深い": {"model": "openai/gpt-5.5"},
    },
    "claude_code": {"enabled": false},
  },
  "_migrations": ["2026-08-reasoning-unification"],
}
"""


def test_gnarly_jsonc_splices_without_collateral(tmp_path):
    """The span scanner walks two levels of a hand-written document. A `}` or a literal
    `[opencode]` inside a string, a block comment carrying braces, trailing commas and non-ASCII
    keys each give it a chance to mis-locate the span and rewrite the wrong bytes."""
    session = _session(tmp_path, GNARLY)
    assert session.scope == "opencode"
    session.set_model("agent:probe", "openai", "gpt-5.5", variant="low")
    session.save()
    out = _read(session.config_path)
    assert "/* block comment { with braces } */" in out
    assert '"note": "a } brace, a \\" quote, a literal [opencode] and // not-a-comment",' in out
    assert '"claude_code": {"enabled": false},' in out          # sibling + its trailing comma
    assert '"_migrations": ["2026-08-reasoning-unification"],' in out
    reloaded, _ = config_io.load_config(session.config_path)
    assert "agents" not in reloaded
    block = reloaded[config_io.OPENCODE_BLOCK]
    assert block["agents"]["prométhée"]["reasoning"] == "high"   # untouched neighbour
    assert block["agents"]["probe"] == {"model": "openai/gpt-5.5", "reasoning": "low"}
    assert block["categories"]["深い"] == {"model": "openai/gpt-5.5"}


# ---------------------------------------------------------------------------
# End to end, through cli.main
# ---------------------------------------------------------------------------

MOCK_MODELS = "opencode/claude-opus-4-7\nopencode/gpt-5.5\nopenai/gpt-5.5\n"

CLI_UNIFIED = """\
// OMO configuration
{
  "$schema": "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/assets/omo.schema.json",
  "[opencode]": {
    // survive me
    "agents": {},
    "categories": {},
    "team_mode": {"enabled": true}
  },
  "_migrations": ["2026-08-reasoning-unification"]
}
"""


def _run_cli(argv) -> int:
    """cli.main with `opencode` present and its output stubbed (hard rule: never the real one)."""
    def _stub(*args, **kwargs):
        return types.SimpleNamespace(returncode=0, stdout=MOCK_MODELS, stderr="")

    with patch("shutil.which", return_value="/usr/bin/opencode"), \
         patch.object(subprocess, "run", _stub):
        return cli.main(argv)


class TestCliOnAUnifiedConfig:
    def test_show_reports_the_scope(self, tmp_path, capsys):
        for text, name, expected in ((UNIFIED, "omo.jsonc", "opencode"),
                                     (LEGACY, "oh-my-openagent.jsonc", "root")):
            path = tmp_path / name
            _write(path, text)
            assert _run_cli(["--config", str(path), "show", "--json"]) == 0
            assert json.loads(capsys.readouterr().out)["config_scope"] == expected

    def test_apply_lands_every_spelling_in_one_save(self, tmp_path, capsys):
        """`apply` is the batch verb, so it is the one that can get a whole config's worth of
        spellings wrong at once — agents and categories take `reasoning`, the two sub-objects
        keep `variant`, and it all happens under a single backup."""
        path = tmp_path / "omo.jsonc"
        _write(path, CLI_UNIFIED)
        payload = json.dumps({
            "agent:sisyphus": {"model": "opencode/claude-opus-4-7", "variant": "thinking"},
            "agent:sisyphus.ultrawork": {"model": "opencode/gpt-5.5", "variant": "high"},
            "agent:sisyphus.compaction": {"model": "opencode/gpt-5.5", "variant": "high"},
            "cat:deep": {"model": "openai/gpt-5.5", "variant": "medium"},
        })
        with patch("sys.stdin", io.StringIO(payload)):
            assert _run_cli(["--config", str(path), "apply", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["ok"] is True

        reloaded, _ = config_io.load_config(str(path))
        assert "agents" not in reloaded
        agent = reloaded[config_io.OPENCODE_BLOCK]["agents"]["sisyphus"]
        assert agent["reasoning"] == "thinking" and "variant" not in agent
        assert agent["ultrawork"]["variant"] == "high" and "reasoning" not in agent["ultrawork"]
        assert agent["compaction"]["variant"] == "high"
        assert reloaded[config_io.OPENCODE_BLOCK]["categories"]["deep"]["reasoning"] == "medium"
        assert "// survive me" in _read(path)
        assert len(os.listdir(tmp_path / ".backup")) == 2  # the pinned original + one snapshot

    def test_repeating_a_set_changes_nothing_and_burns_no_backup(self, tmp_path, capsys):
        """An agent verifying its own work re-runs the same `set`. The second call has to be a
        real no-op — stable bytes, no snapshot, `changed: false` — or the 20-deep ring evicts the
        user's history one confirmation at a time."""
        path = tmp_path / "omo.jsonc"
        _write(path, CLI_UNIFIED)
        argv = ["--config", str(path), "set", "agent:sisyphus", "openai/gpt-5.5", "--json"]
        assert _run_cli(argv) == 0
        capsys.readouterr()
        after_first = path.read_bytes()
        ring = sorted(os.listdir(tmp_path / ".backup"))

        assert _run_cli(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["changed"] is False
        assert payload["backup"] is None
        assert path.read_bytes() == after_first
        assert sorted(os.listdir(tmp_path / ".backup")) == ring

    def test_restore_refuses_a_cross_format_snapshot_with_exit_3(self, tmp_path, capsys):
        """Exit 3 is "refused by a guard", not the exit 1 omodel uses for its own failures — and
        the refusal has to leave the ring untouched, safety snapshot included."""
        path = tmp_path / "omo.jsonc"
        _write(path, UNIFIED)
        _write(tmp_path / ".backup" / "20260101-000000.000.jsonc", LEGACY)
        before, ring = path.read_bytes(), sorted(os.listdir(tmp_path / ".backup"))

        with patch("builtins.input", return_value="1"):
            assert _run_cli(["--config", str(path), "--restore"]) == 3
        assert "Refused" in capsys.readouterr().err
        assert path.read_bytes() == before
        assert sorted(os.listdir(tmp_path / ".backup")) == ring
