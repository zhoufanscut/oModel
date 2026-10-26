"""test_main_model.py — OmO Native's main-session model (the `main` target). DESIGN §Main model.

Hermetic: `omo` is stubbed (test_native_harness's `_stub_omo`), and omo's settings.json /
state.json live in a tmp agent dir — `$OMO_CODING_AGENT_DIR` for the default config path, or
`<config_dir>/agent` for any other.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys

import pytest
from test_native_harness import UNIFIED, _agent_dir, _cli_json, _stub_omo

from omodel import main_model, presets, session

SETTINGS = {"defaultProvider": "zai", "defaultModel": "glm-5.3", "quietStartup": True}
STATE = {"tipsHistory": {"a": 1}, "modelThinkingLevels": {"zai/glm-5.3": "high",
                                                          "chatgpt-subscription/gpt-6-astra": "low"}}


@pytest.fixture
def main_env(tmp_path, monkeypatch):
    """A unified config at the default path, omo stubbed, and omo's settings/state in the agent dir."""
    _stub_omo(monkeypatch)
    home = tmp_path / "home"
    (home / ".omo").mkdir(parents=True)
    agent = _agent_dir(tmp_path, auth={"zai": {}, "chatgpt-subscription": {}})
    monkeypatch.setenv("OMO_CODING_AGENT_DIR", agent)
    (tmp_path / "agent" / "settings.json").write_text(json.dumps(SETTINGS, indent=2))
    (tmp_path / "agent" / "state.json").write_text(json.dumps(STATE, indent=2))
    cfg = home / ".omo" / "omo.jsonc"
    cfg.write_text(UNIFIED)
    return cfg


def _read(path):
    with open(path) as f:
        return json.load(f)


def _build(cfg):
    return session.Session.build(str(cfg), harness="native")


class TestLoad:
    def test_model_and_remembered_level(self, main_env, tmp_path):
        state = main_model.load(str(main_env))
        assert state.agent_dir == str(tmp_path / "agent")
        assert state.node == {"model": "zai/glm-5.3", "reasoning": "high"}
        assert state.problem is None and state.shadowed_by is None

    def test_other_config_path_uses_its_own_agent_dir(self, tmp_path):
        cfg = tmp_path / "x" / "omo.jsonc"
        assert main_model.agent_dir_for(str(cfg)) == str(tmp_path / "x" / "agent")

    def test_missing_files_read_as_unset(self, tmp_path):
        state = main_model.load(str(tmp_path / "omo.jsonc"))
        assert state.node == {} and state.problem is None

    def test_settings_jsonc_is_read_but_unwritable(self, main_env, tmp_path):
        (tmp_path / "agent" / "settings.jsonc").write_text('// c\n{"defaultProvider": "zai", "defaultModel": "m"}')
        state = main_model.load(str(main_env))
        assert state.node["model"] == "zai/m"
        assert "settings.jsonc" in state.problem
        with pytest.raises(ValueError):
            main_model.save(state, {"model": "zai/glm-5.3"}, str(main_env))

    def test_garbage_settings_is_a_problem(self, main_env, tmp_path):
        (tmp_path / "agent" / "settings.json").write_text("{nope")
        assert main_model.load(str(main_env)).problem

    def test_enabled_models_shadow(self, main_env, tmp_path):
        (tmp_path / "agent" / "settings.json").write_text(json.dumps({**SETTINGS, "enabledModels": ["a/b"]}))
        assert main_model.load(str(main_env)).shadowed_by == ["a/b"]


class TestSession:
    def test_main_is_a_native_target_only(self, main_env):
        s = _build(main_env)
        assert s.known_targets()[0] == "main" and s.is_known("main")
        assert s.assignment("main") == ("zai/glm-5.3", "high")
        oc = session.Session.build(str(main_env))
        assert not oc.is_known("main") and "main" not in oc.known_targets()

    def test_save_touches_only_the_main_keys(self, main_env, tmp_path):
        s = _build(main_env)
        before = main_env.read_text()
        s.set_model("main", "chatgpt-subscription", "gpt-6-astra", "xhigh")
        assert s.is_dirty() and "gpt-6-astra" in s.diff()
        s.save()
        settings = _read(tmp_path / "agent" / "settings.json")
        state = _read(tmp_path / "agent" / "state.json")
        assert settings == {**SETTINGS, "defaultProvider": "chatgpt-subscription", "defaultModel": "gpt-6-astra"}
        assert state["modelThinkingLevels"] == {"zai/glm-5.3": "high", "chatgpt-subscription/gpt-6-astra": "xhigh"}
        assert state["modelLastOnThinkingLevels"] == {"chatgpt-subscription/gpt-6-astra": "xhigh"}
        assert state["tipsHistory"] == {"a": 1}
        assert main_env.read_text() == before  # the config itself is not touched
        backups = os.listdir(os.path.join(os.path.dirname(main_env), ".backup"))
        assert any(b.startswith("settings-") for b in backups)
        assert any(b.startswith("state-") for b in backups)
        assert not _build(main_env).is_dirty()

    def test_no_level_keeps_the_remembered_one(self, main_env, tmp_path):
        s = _build(main_env)
        s.set_model("main", "chatgpt-subscription", "gpt-6-astra")
        assert s.assignment("main") == ("chatgpt-subscription/gpt-6-astra", "low")
        state_before = (tmp_path / "agent" / "state.json").read_text()
        s.save()
        assert (tmp_path / "agent" / "state.json").read_text() == state_before

    def test_clear_drops_the_default_keeps_levels(self, main_env, tmp_path):
        s = _build(main_env)
        assert s.clear("main")
        s.save()
        settings = _read(tmp_path / "agent" / "settings.json")
        assert "defaultModel" not in settings and "defaultProvider" not in settings
        assert _read(tmp_path / "agent" / "state.json") == STATE

    def test_save_rereads_omos_other_keys(self, main_env, tmp_path):
        s = _build(main_env)
        s.set_model("main", "chatgpt-subscription", "gpt-6-astra", "high")
        # omo writes another key while omodel is open: it must survive the save.
        (tmp_path / "agent" / "settings.json").write_text(json.dumps({**SETTINGS, "theme": "dark"}))
        s.save()
        assert _read(tmp_path / "agent" / "settings.json")["theme"] == "dark"


class TestPresets:
    def test_switch_applies_a_presets_main(self, main_env):
        s = _build(main_env)
        s.add_preset("astra")
        s.set_model("main", "chatgpt-subscription", "gpt-6-astra", "high")
        s.switch_preset(0)
        assert s.assignment("main") == ("zai/glm-5.3", "high")
        s.switch_preset(1)
        assert s.assignment("main") == ("chatgpt-subscription/gpt-6-astra", "high")

    def test_preset_without_main_leaves_it(self, main_env, tmp_path):
        (tmp_path / "agent" / "settings.json").write_text("{}")
        s = _build(main_env)
        s.add_preset("two")
        s.set_model("main", "zai", "glm-5.3")
        s.switch_preset(0)  # 'default' was seeded with no main
        assert s.assignment("main")[0] == "zai/glm-5.3"

    def test_outside_change_is_absorbed_without_conflict(self, main_env, tmp_path):
        s = _build(main_env)
        s.save()
        (tmp_path / "agent" / "settings.json").write_text(json.dumps({**SETTINGS, "defaultModel": "glm-6"}))
        again = _build(main_env)
        assert not again.sync_conflict and not again.is_dirty()
        assert again.store.current().main == {"model": "zai/glm-6"}

    def test_main_kept_out_of_the_opencode_presets_file(self, main_env):
        oc = session.Session.build(str(main_env))
        oc.set_model("agent:sisyphus", "a", "c")
        oc.save()
        with open(presets.presets_path(str(main_env))) as f:
            assert "main" not in json.load(f)["presets"][0]


class TestCli:
    def test_set_and_refusals(self, main_env, capsys, tmp_path):
        code, out = _cli_json(["--harness", "native", "set", "main", "zai/glm-5.3", "--variant", "low", "--json"], capsys)
        assert code == 0 and out["changed"]
        assert _read(tmp_path / "agent" / "state.json")["modelThinkingLevels"]["zai/glm-5.3"] == "low"
        code, out = _cli_json(["set", "main", "zai/glm-5.3", "--json"], capsys)
        assert code == 3 and out["error"] == "unknown_target"
        (tmp_path / "agent" / "settings.jsonc").write_text("{}")
        code, out = _cli_json(["--harness", "native", "clear", "main", "--json"], capsys)
        assert code == 3 and out["error"] == "main_unwritable"

    def test_check_reports_shadowing(self, main_env, capsys, tmp_path):
        (tmp_path / "agent" / "settings.json").write_text(json.dumps({**SETTINGS, "enabledModels": ["a/b"]}))
        code, out = _cli_json(["--harness", "native", "check", "--json"], capsys)
        assert code == 3
        assert [p["problem"] for p in out["problems"] if p["target"] == "main"] == ["main_shadowed"]


def test_tui_main_row_and_undo(main_env):
    from textual.widgets import OptionList

    from omodel.app import OModelApp

    app = OModelApp(session=_build(main_env))

    async def _run():
        async with app.run_test() as pilot:
            await pilot.pause()
            targets = app.query_one("#targets", OptionList)
            assert targets.get_option_at_index(1).id == "main"
            app._stage_row("main", {"provider": "chatgpt-subscription", "model": "gpt-6-astra",
                                    "variant": "high"}, "set main")
            assert app.session.assignment("main")[0] == "chatgpt-subscription/gpt-6-astra"
            assert app.session.is_dirty()
            await pilot.press("u")
            await pilot.pause()
            assert app.session.assignment("main") == ("zai/glm-5.3", "high")
            assert not app.session.is_dirty()
            assert "\0omodel-main" not in app.cfg

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Round-1 review fixes
# ---------------------------------------------------------------------------

def _two_presets(main_env, tmp_path):
    """Preset 1 'default' (zai/glm-5.3) and preset 2 'astra' (gpt-6-astra, active), saved; the
    two also differ in a category so a switch has a config write to make."""
    s = _build(main_env)
    s.set_model("cat:quick", "zai", "glm-5.3")
    s.save()
    s.add_preset("astra")
    s.set_model("main", "chatgpt-subscription", "gpt-6-astra", "high")
    s.set_model("cat:quick", "chatgpt-subscription", "gpt-6-astra")
    s.save()
    return s


def test_switch_under_main_unwritable_skips_the_main(main_env, tmp_path, capsys):
    _two_presets(main_env, tmp_path)
    (tmp_path / "agent" / "settings.jsonc").write_text('{"defaultProvider": "chatgpt-subscription", "defaultModel": "gpt-6-astra"}')
    settings_before = (tmp_path / "agent" / "settings.json").read_text()
    code, out = _cli_json(["--harness", "native", "preset", "use", "default", "--json"], capsys)
    assert code == 0 and out["main_skipped"] is True and out["changed"]
    assert "zai/glm-5.3" in main_env.read_text()  # the config did switch
    assert (tmp_path / "agent" / "settings.json").read_text() == settings_before
    again = _build(main_env)
    assert again.store.active == 0
    # Frozen while blocked: each preset keeps its own main, not the live one.
    assert again.store.presets[0].main == {"model": "zai/glm-5.3", "reasoning": "high"}
    assert again.store.presets[1].main == {"model": "chatgpt-subscription/gpt-6-astra", "reasoning": "high"}


def test_session_switch_leaves_main_under_problem(main_env, tmp_path):
    _two_presets(main_env, tmp_path)
    (tmp_path / "agent" / "settings.jsonc").write_text('{"defaultProvider": "x", "defaultModel": "y"}')
    s = _build(main_env)
    assert s.main_skipped(0)
    s.switch_preset(0)
    assert s.assignment("main") == ("x/y", None)
    assert s.projected_store().presets[0].main == {"model": "zai/glm-5.3", "reasoning": "high"}


def test_apply_reports_the_remembered_level(main_env, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"main": {"model": "chatgpt-subscription/gpt-6-astra"}})))
    code, out = _cli_json(["--harness", "native", "apply", "--dry-run", "--json"], capsys)
    assert code == 0 and out["applied"][0]["variant"] == "low"


def test_pre_migration_levelless_set_writes_no_state(main_env, tmp_path):
    (tmp_path / "agent" / "state.json").unlink()
    (tmp_path / "agent" / "settings.json").write_text(json.dumps(
        {**SETTINGS, "modelThinkingLevels": {"zai/glm-5.3": "high", "kimi-coding/kimi-k3": "max"}}))
    s = _build(main_env)
    s.set_model("main", "kimi-coding", "kimi-k3")
    assert s.assignment("main") == ("kimi-coding/kimi-k3", "max")
    s.save()
    assert not (tmp_path / "agent" / "state.json").exists()


def test_main_write_failure_reports_config_written(main_env, tmp_path, capsys, monkeypatch):
    def boom(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(main_model, "_write", boom)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"cat:quick": {"model": "zai/glm-5.3"}, "main": {"model": "chatgpt-subscription/gpt-6-astra"}})))
    code, out = _cli_json(["--harness", "native", "apply", "--json"], capsys)
    assert code == 1 and out["config_written"] is True
    assert "main model" in out["message"]
    assert "glm-5.3" in main_env.read_text()


def test_variant_payload_reports_the_remembered_level(main_env, capsys):
    code, out = _cli_json(["--harness", "native", "set", "main", "chatgpt-subscription/gpt-6-astra", "--json"], capsys)
    assert code == 0 and out["variant"] == "low"


def test_pre_migration_levels_are_kept(main_env, tmp_path):
    (tmp_path / "agent" / "state.json").unlink()
    (tmp_path / "agent" / "settings.json").write_text(json.dumps(
        {**SETTINGS, "modelThinkingLevels": {"zai/glm-5.3": "high", "x/y": "low"}}))
    s = _build(main_env)
    s.set_model("main", "chatgpt-subscription", "gpt-6-astra", "xhigh")
    s.save()
    assert _read(tmp_path / "agent" / "state.json")["modelThinkingLevels"] == {
        "zai/glm-5.3": "high", "x/y": "low", "chatgpt-subscription/gpt-6-astra": "xhigh"}


def test_store_dirtiness_sees_the_main(main_env):
    s = _build(main_env)
    s.add_preset("two")
    s.save()
    s.store.presets[0].main = {"model": "a/b"}
    assert s.store_is_dirty()


def _run_tui(app, steps):
    async def _run():
        async with app.run_test() as pilot:
            await pilot.pause()
            await steps(app, pilot)

    asyncio.run(_run())


def test_tui_save_writes_settings(main_env, tmp_path):
    from omodel.app import OModelApp

    app = OModelApp(session=_build(main_env))

    async def steps(app, pilot):
        app._stage_row("main", {"provider": "chatgpt-subscription", "model": "gpt-6-astra",
                                "variant": "high"}, "set main")
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert not app.session.is_dirty()

    _run_tui(app, steps)
    assert _read(tmp_path / "agent" / "settings.json")["defaultModel"] == "gpt-6-astra"


def test_tui_levelless_preset_main_does_not_stay_dirty(main_env, tmp_path):
    from omodel.app import OModelApp

    s = _build(main_env)
    s.add_preset("two")
    s.save()
    # Preset 1 holds the same model with no level (captured while omo remembered none); the live
    # main carries omo's remembered `high`. Switching to it changes nothing on disk.
    s.store.presets[0].main = {"model": "zai/glm-5.3"}
    s.write_store(s.store)
    app = OModelApp(session=_build(main_env))

    async def steps(app, pilot):
        app._switch_preset(0)
        await pilot.press("s")
        await pilot.pause()
        if app.screen.__class__.__name__ == "ConfirmModal":
            await pilot.press("y")
            await pilot.pause()
        assert not app.session.is_dirty()

    _run_tui(app, steps)


def test_tui_switch_under_main_unwritable_warns(main_env, tmp_path):
    from omodel.app import OModelApp

    s = _two_presets(main_env, tmp_path)
    s.store.presets[0].name = "Daily"  # mixed case: the toast must not lowercase it
    s.write_store(s.store)
    (tmp_path / "agent" / "settings.jsonc").write_text("{}")
    app = OModelApp(session=_build(main_env))

    async def steps(app, pilot):
        app._switch_preset(0)
        await pilot.pause()
        assert app.session.store.active == 0 and app.session.assignment("main") == ("", None)
        assert any(str(n.message).startswith("The main model stays") and "'Daily' sets zai/glm-5.3" in str(n.message)
                   for n in app._notifications)
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert not app.session.is_dirty()

    _run_tui(app, steps)
    assert "zai/glm-5.3" in main_env.read_text()
