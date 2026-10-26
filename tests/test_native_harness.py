"""test_native_harness.py — the OmO Native harness (`--harness native`). DESIGN §Harnesses.

Hermetic like the rest of the suite: `omo` is never run (subprocess.run and shutil.which are
stubbed), the login check reads a tmp agent dir and an explicit env, and every config is a tmp
file.
"""
from __future__ import annotations

import asyncio
import json
import os
import types

import json5
import pytest

from omodel import cache, catalog, cli, config_io, presets, session, suggestions

LIST_MODELS = """\
provider              model              context  max-out  thinking  images
chatgpt-subscription  gpt-6-luna-fast    922K     128K     yes       yes
chatgpt-subscription  gpt-6-astra        1M       128K     yes       yes
deepseek              deepseek-flash     1M       64K      yes       no
groq                  llama-9            128K     8K       no        no
opencode              claude-haiku-5-5   200K     64K      yes       yes
opencode              qwen3.8-flash      256K     32K      no        no
zai                   glm-5.3            200K     128K     yes       no
"""


def _stub_omo(monkeypatch, stdout=LIST_MODELS, returncode=0, calls=None):
    """`omo` on PATH (and nothing else: `opencode` reads as absent), `omo --list-models`
    answering `stdout`; records each call in `calls`."""

    def which(name, *a, **k):
        return "/usr/bin/omo" if name == "omo" else None

    def run(args, **kwargs):
        if calls is not None:
            calls.append(list(args))
        if list(args[:2]) != ["omo", "--list-models"]:
            raise AssertionError(f"unexpected subprocess: {args}")
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(catalog.shutil, "which", which)
    monkeypatch.setattr(catalog.subprocess, "run", run)


def _agent_dir(tmp_path, auth=None, models=None):
    d = tmp_path / "agent"
    d.mkdir(exist_ok=True)
    if auth is not None:
        (d / "auth.json").write_text(json.dumps(auth))
    if models is not None:
        (d / "models.json").write_text(json.dumps(models))
    return str(d)


# ---------------------------------------------------------------------------
# catalog: logins, the table, the catalog
# ---------------------------------------------------------------------------

class TestNativeLogins:
    def test_auth_json_keys(self, tmp_path):
        d = _agent_dir(tmp_path, auth={"zai": {"type": "api_key", "key": "s"}, "deepseek": {}})
        assert catalog.native_logins(d, env={}) == {"zai", "deepseek"}

    def test_env_key_from_the_bundled_table(self, tmp_path):
        d = _agent_dir(tmp_path)
        assert catalog.native_logins(d, env={"GROQ_API_KEY": "x"}) == {"groq"}
        # One var can serve two providers (pi-ai maps MOONSHOT_API_KEY to both).
        assert {"moonshotai", "moonshotai-cn"} <= catalog.native_logins(d, env={"MOONSHOT_API_KEY": "x"})
        # An empty value is not a login.
        assert catalog.native_logins(d, env={"GROQ_API_KEY": ""}) == set()

    def test_anthropic_auth_token_counts(self, tmp_path):
        # Measured against the engine: ANTHROPIC_AUTH_TOKEN alone makes `anthropic` available.
        assert catalog.native_logins(_agent_dir(tmp_path), env={"ANTHROPIC_AUTH_TOKEN": "x"}) == {"anthropic"}

    def test_custom_provider_needs_an_api_key(self, tmp_path):
        d = _agent_dir(tmp_path, models={"providers": {
            "keyed": {"apiKey": "k", "models": [{"id": "m"}]},
            "keyless": {"models": [{"id": "m"}]},
        }})
        assert catalog.native_logins(d, env={}) == {"keyed"}

    def test_claude_subscription_tokens(self, tmp_path):
        d = _agent_dir(tmp_path)
        for name in ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN_2"):
            assert catalog.native_logins(d, env={name: "x"}) == {"anthropic-subscription"}
        assert catalog.native_logins(d, env={"CLAUDE_CODE_OAUTH_TOKEN_X": "x"}) == set()

    def test_bedrock_rules(self, tmp_path):
        d = _agent_dir(tmp_path)
        assert catalog.native_logins(d, env={"AWS_PROFILE": "p"}) == {"amazon-bedrock"}
        assert catalog.native_logins(d, env={"AWS_ACCESS_KEY_ID": "a"}) == set()
        both = {"AWS_ACCESS_KEY_ID": "a", "AWS_SECRET_ACCESS_KEY": "s"}
        assert catalog.native_logins(d, env=both) == {"amazon-bedrock"}

    def test_vertex_adc_needs_project_and_location(self, tmp_path):
        d = _agent_dir(tmp_path)
        adc = tmp_path / "adc.json"
        adc.write_text("{}")
        env = {"GOOGLE_APPLICATION_CREDENTIALS": str(adc), "GOOGLE_CLOUD_PROJECT": "p"}
        assert "google-vertex" not in catalog.native_logins(d, env=env)
        env["GOOGLE_CLOUD_LOCATION"] = "us"
        assert "google-vertex" in catalog.native_logins(d, env=env)

    def test_agent_dir_override_and_default(self, tmp_path):
        assert catalog.native_agent_dir({"SENPI_CODING_AGENT_DIR": str(tmp_path)}) == str(tmp_path)
        assert catalog.native_agent_dir({"HOME": "/h"}) == os.path.join("/h", ".omo", "agent")

    def test_garbage_files_read_as_no_logins(self, tmp_path):
        d = _agent_dir(tmp_path)
        (tmp_path / "agent" / "auth.json").write_text("[1, 2")
        (tmp_path / "agent" / "models.json").write_text('{"providers": []}')
        assert catalog.native_logins(d, env={}) == set()


class TestListModelsTable:
    def test_rows_and_columns(self):
        rows = catalog._parse_list_models(LIST_MODELS)
        assert rows[0] == ("chatgpt-subscription", "gpt-6-luna-fast", {
            "context": "922K", "max_out": "128K", "reasoning": True, "image": True,
        })
        assert len(rows) == 7

    def test_no_table_is_no_rows(self):
        assert catalog._parse_list_models("No models available. Use /login …\n") == []
        # Rows before the header (a warning line shaped like a row) are not models.
        assert catalog._parse_list_models("a b c d yes yes\n") == []


class TestLoadNative:
    def test_filters_to_logins_in_table_order(self, tmp_path, monkeypatch):
        _stub_omo(monkeypatch)
        d = _agent_dir(tmp_path, auth={"zai": {}, "chatgpt-subscription": {}})
        cat = catalog.load_native(agent_dir=d, env={"DEEPSEEK_API_KEY": "x"})
        assert cat.connected == ["chatgpt-subscription", "deepseek", "zai"]
        assert cat.available["chatgpt-subscription"] == ["gpt-6-luna-fast", "gpt-6-astra"]
        assert "groq" not in cat.available

    def test_cache_read_through_and_logins_not_cached(self, tmp_path, monkeypatch):
        calls = []
        _stub_omo(monkeypatch, calls=calls)
        d = _agent_dir(tmp_path, auth={"zai": {}})
        assert catalog.load_native(agent_dir=d, env={}).connected == ["zai"]
        # A new login shows up on the next load without re-running omo.
        cat = catalog.load_native(agent_dir=d, env={"GROQ_API_KEY": "x"})
        assert cat.connected == ["groq", "zai"]
        assert len(calls) == 1
        assert cache.read("native-models") == LIST_MODELS

    def test_not_on_path_is_empty(self, monkeypatch):
        monkeypatch.setattr(catalog.shutil, "which", lambda *a, **k: None)
        cat = catalog.load_native(env={})
        assert (cat.available, cat.connected) == ({}, [])

    @pytest.mark.parametrize("stdout,code", [(LIST_MODELS, 1), ("no table\n", 0)])
    def test_failure_is_catalog_unavailable(self, monkeypatch, stdout, code):
        _stub_omo(monkeypatch, stdout=stdout, returncode=code)
        with pytest.raises(catalog.CatalogUnavailable):
            catalog.load_native(env={})

    def test_refresh_rewrites_only_the_native_entry(self, tmp_path, monkeypatch):
        calls = []
        _stub_omo(monkeypatch, calls=calls)
        cache.write("models", "opencode/x\n", ["opencode", "models"])
        cache.write("native-models", "stale", None)
        d = _agent_dir(tmp_path, auth={"zai": {}})
        assert catalog.refresh_native(agent_dir=d, env={}).connected == ["zai"]
        assert cache.read("native-models") == LIST_MODELS
        assert cache.read("models") == "opencode/x\n"
        assert len(calls) == 1

    def test_detail_and_variants(self, tmp_path, monkeypatch):
        _stub_omo(monkeypatch)
        d = _agent_dir(tmp_path, auth={"chatgpt-subscription": {}, "opencode": {}})
        cat = catalog.load_native(agent_dir=d, env={})
        assert cat.detail("gpt-6-astra") == {
            "context": 1_000_000, "cost": None, "reasoning": True, "image": True,
        }
        assert cat.variants_for("chatgpt-subscription", "gpt-6-astra") == list(catalog.NATIVE_LADDER)
        assert cat.variants_for("opencode", "qwen3.8-flash") == []  # not a thinking model
        assert cat.variants_for("opencode", "nope") == []


# ---------------------------------------------------------------------------
# bundled data
# ---------------------------------------------------------------------------

class TestBundledNativeData:
    def test_native_section_loads(self):
        data = suggestions.load()
        native = data.for_harness("native")
        assert native is not None
        assert "explore" in native.agents and "architect" in native.categories
        assert "sisyphus" not in native.agents
        assert native.families is data.families
        assert data.for_harness("opencode") is data

    def test_reviewer_agents_borrow_their_categories_chains(self):
        native = suggestions.load().for_harness("native")
        reviewer = native.agents["omo-native-code-reviewer"]
        via = reviewer["viaCategories"]
        expected = [e for c in via for e in native.categories[c]["fallbackChain"]]
        assert reviewer["fallbackChain"] == expected

    def test_snapshot_without_native(self):
        raw = json.loads((config_io.Path(suggestions.__file__).parent / "data" / "omo-suggestions.json").read_text())
        raw.pop("native")
        assert suggestions.parse(json.dumps(raw)).for_harness("native") is None

    def test_bad_native_shape_is_refused(self):
        raw = json.loads((config_io.Path(suggestions.__file__).parent / "data" / "omo-suggestions.json").read_text())
        raw["native"] = {"agents": []}
        with pytest.raises(TypeError):
            suggestions.parse(json.dumps(raw))

    def test_auth_table_is_bundled(self):
        table = catalog._auth_env_table()
        assert table["openai"] == ["OPENAI_API_KEY"]
        assert "ANTHROPIC_AUTH_TOKEN" in table["anthropic"]


# ---------------------------------------------------------------------------
# config_io: the [native] block
# ---------------------------------------------------------------------------

UNIFIED = """\
// OMO configuration
{
  "$schema": "https://example/omo.schema.json",
  // keep me
  "[opencode]": {
    "agents": {"sisyphus": {"model": "a/b"}}
  }
}
"""


def _set_native(cfg, kind, name, value):
    root = config_io.managed_root_for_write(cfg, "native")
    root.setdefault(kind, {})[name] = value


class TestNativeRender:
    def test_block_added_at_the_end_keeping_comments(self):
        cfg = json5.loads(UNIFIED)
        _set_native(cfg, "agents", "explore", {"model": "zai/glm-5.3", "reasoning": "low"})
        out = config_io.render(cfg, UNIFIED, "native")
        assert out.startswith(UNIFIED[: UNIFIED.rindex("}")].rstrip())
        assert "// keep me" in out and '"agents": {"sisyphus": {"model": "a/b"}}' in out
        assert json5.loads(out)["[native]"] == {"agents": {"explore": {"model": "zai/glm-5.3", "reasoning": "low"}}}

    def test_keys_added_into_an_existing_block(self):
        base = '{\n  "[native]": {\n    "memory": {"a": 1} // c\n  }\n}\n'
        cfg = json5.loads(base)
        _set_native(cfg, "agents", "explore", {"model": "p/m"})
        _set_native(cfg, "categories", "quick", {"model": "p/q"})
        out = config_io.render(cfg, base, "native")
        assert "// c" in out and '"memory": {"a": 1}' in out
        parsed = json5.loads(out)["[native]"]
        assert list(parsed) == ["memory", "agents", "categories"]

    def test_keys_added_into_an_empty_block(self):
        base = '{\n  "[native]": {}\n}\n'
        cfg = json5.loads(base)
        _set_native(cfg, "agents", "explore", {"model": "p/m"})
        _set_native(cfg, "categories", "quick", {"model": "p/q"})
        out = config_io.render(cfg, base, "native")
        assert json5.loads(out)["[native]"] == {"agents": {"explore": {"model": "p/m"}},
                                                "categories": {"quick": {"model": "p/q"}}}

    def test_existing_spans_rewritten_opencode_untouched(self):
        base = UNIFIED.replace(
            '  }\n}\n', '  },\n  "[native]": {\n    "agents": {"explore": {"model": "x/y"}}\n  }\n}\n'
        )
        cfg = json5.loads(base)
        _set_native(cfg, "agents", "explore", {"model": "zai/glm-5.3"})
        out = config_io.render(cfg, base, "native")
        assert '"agents": {"sisyphus": {"model": "a/b"}}' in out  # [opencode] byte-for-byte
        assert json5.loads(out)["[native]"]["agents"]["explore"]["model"] == "zai/glm-5.3"

    def test_empty_block_with_nothing_to_write_leaves_the_file(self):
        cfg = json5.loads(UNIFIED)
        config_io.managed_root_for_write(cfg, "native")
        assert config_io.render(cfg, UNIFIED, "native") == UNIFIED

    def test_opencode_missing_keys_still_clean_rewrite(self):
        # The insert path is native-only; the opencode harness keeps its old fallback here.
        base = '// c\n{\n  "$schema": "x/omo.schema.json",\n  "[opencode]": {"claude_code": {}}\n}\n'
        cfg = json5.loads(base)
        config_io.managed_root_for_write(cfg)["agents"] = {"sisyphus": {"model": "a/b"}}
        assert config_io.render(cfg, base) == config_io.serialize(cfg)

    def test_presets_files_are_separate(self, tmp_path):
        p = str(tmp_path / "omo.jsonc")
        assert presets.presets_path(p).endswith(".omodel-presets.json")
        assert presets.presets_path(p, "native").endswith(".omodel-presets-native.json")


# ---------------------------------------------------------------------------
# session + CLI
# ---------------------------------------------------------------------------

@pytest.fixture
def native_env(tmp_path, monkeypatch):
    """A unified config at the default path, omo stubbed, logged into zai + chatgpt-subscription."""
    _stub_omo(monkeypatch)
    home = tmp_path / "home"
    (home / ".omo").mkdir(parents=True)
    _agent_dir(tmp_path, auth={"zai": {}, "chatgpt-subscription": {}, "opencode": {}})
    monkeypatch.setenv("OMO_CODING_AGENT_DIR", str(tmp_path / "agent"))
    cfg = home / ".omo" / "omo.jsonc"
    cfg.write_text(UNIFIED)
    return cfg


class TestNativeSession:
    def test_targets_and_no_subtargets(self, native_env):
        s = session.Session.build(str(native_env), harness="native")
        targets = s.known_targets()
        assert "agent:explore" in targets and "cat:architect" in targets
        assert not any("." in t for t in targets)
        assert not s.is_known("agent:explore.compaction")
        assert not s.is_known("agent:sisyphus")

    def test_save_writes_native_block_and_native_presets(self, native_env):
        s = session.Session.build(str(native_env), harness="native")
        s.set_model("cat:quick", "zai", "glm-5.3", "low")
        s.save()
        doc = json5.loads(native_env.read_text())
        assert doc["[native]"]["categories"]["quick"] == {"model": "zai/glm-5.3", "reasoning": "low"}
        assert doc["[opencode]"] == {"agents": {"sisyphus": {"model": "a/b"}}}
        assert "// keep me" in native_env.read_text()
        assert os.path.exists(presets.presets_path(str(native_env), "native"))
        assert not os.path.exists(presets.presets_path(str(native_env)))
        # Reopened: clean, and the opencode harness still sees only its own block.
        assert not session.Session.build(str(native_env), harness="native").is_dirty()
        oc = session.Session.build(str(native_env))
        assert oc.assignment("cat:quick") == ("", None)

    def test_rows_use_native_chain_and_providers(self, native_env):
        s = session.Session.build(str(native_env), harness="native")
        values = [f"{r['provider']}/{r['model']}" for r in s.rows("cat:quick")]
        assert values[0] == "chatgpt-subscription/gpt-6-luna-fast"

    def test_legacy_path_is_refused(self, tmp_path, monkeypatch):
        legacy = config_io.legacy_config_path()
        os.makedirs(os.path.dirname(legacy), exist_ok=True)
        with open(legacy, "w") as f:
            f.write('{"agents": {}}')
        with pytest.raises(session.HarnessUnavailable):
            session.Session.build(legacy, harness="native")

    def test_switch_to_empty_preset_creates_no_block(self, native_env):
        s = session.Session.build(str(native_env), harness="native")
        s.switch_preset(0)
        assert "[native]" not in s.cfg
        assert not s.is_dirty()


def _cli_json(argv, capsys):
    code = cli.main(argv)
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else None)


class TestNativeCli:
    def test_both_flag_positions_and_stamp(self, native_env, capsys):
        for argv in (["--harness", "native", "show"], ["show", "--harness", "native"]):
            code, payload = _cli_json([*argv, "--config", str(native_env), "--json"], capsys)
            assert code == 0
            assert payload["harness"] == "native"
            assert payload["providers"] == ["chatgpt-subscription", "opencode", "zai"]

    def test_default_harness_is_opencode(self, native_env, capsys, monkeypatch):
        monkeypatch.setattr(catalog.shutil, "which", lambda *a, **k: None)
        code, payload = _cli_json(["show", "--config", str(native_env), "--json"], capsys)
        assert code == 0
        assert payload["harness"] == "opencode"
        assert payload["degraded_reason"] == "opencode is not on PATH"

    def test_set_and_variant_guard(self, native_env, capsys):
        base = ["--harness", "native", "--config", str(native_env), "--json"]
        code, payload = _cli_json(["set", "agent:plan-reviewer", "chatgpt-subscription/gpt-6-astra",
                                   "--variant", "xhigh", *base], capsys)
        assert (code, payload["ok"]) == (0, True)
        code, payload = _cli_json(["set", "cat:quick", "zai/glm-5.3", "--variant", "turbo", *base], capsys)
        assert (code, payload["error"]) == (3, "bad_variant")
        assert json5.loads(native_env.read_text())["[native]"]["agents"]["plan-reviewer"]["reasoning"] == "xhigh"

    def test_unknown_opencode_target_under_native(self, native_env, capsys):
        code, payload = _cli_json(["set", "agent:sisyphus", "zai/glm-5.3", "--harness", "native",
                                   "--config", str(native_env), "--json"], capsys)
        assert (code, payload["error"]) == (3, "unknown_target")

    def test_harness_unavailable_on_legacy(self, tmp_path, capsys):
        legacy = config_io.legacy_config_path()
        os.makedirs(os.path.dirname(legacy), exist_ok=True)
        with open(legacy, "w") as f:
            f.write('{"agents": {}}')
        code, payload = _cli_json(["show", "--harness", "native", "--config", legacy, "--json"], capsys)
        assert (code, payload["error"]) == (3, "harness_unavailable")

    def test_misuse_with_update(self, capsys):
        assert cli.main(["--harness", "native", "--update"]) == 2

    def test_refresh_models_native(self, native_env, capsys):
        assert cli.main(["--harness", "native", "--refresh-models"]) == 0
        assert "Refreshed 5 models across 3 providers" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# TUI: header, `t`, run_app's restart loop
# ---------------------------------------------------------------------------

def _native_app(cfg_path):
    from omodel.app import OModelApp

    return OModelApp(session=session.Session.build(str(cfg_path), harness="native"))


def test_header_names_native(native_env):
    from textual.widgets import Static

    async def _run():
        app = _native_app(native_env)
        async with app.run_test() as pilot:
            await pilot.pause()
            text = str(app.query_one("#providers", Static).render())
            assert text.startswith("oModel [native]: chatgpt-subscription")

    asyncio.run(_run())


def test_t_restarts_on_the_other_harness_when_clean(native_env):
    app = _native_app(native_env)

    async def _run():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()

    asyncio.run(_run())
    assert app.return_value == ("harness", "opencode")


def test_t_refuses_while_dirty(native_env):
    app = _native_app(native_env)

    async def _run():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.session.set_model("cat:quick", "zai", "glm-5.3")
            await pilot.press("t")
            await pilot.pause()
            assert app.is_running
            assert any("before switching" in str(n.message) for n in app._notifications)

    asyncio.run(_run())
    assert app.return_value is None


def test_run_app_restarts_on_the_harness_t_asked_for(monkeypatch):
    from omodel import app as app_mod

    seen = []
    results = iter([("harness", "native"), ("harness", "opencode"), None])

    class _Fake:
        def __init__(self, harness):
            seen.append(harness)

        def run(self):
            return next(results)

    monkeypatch.setattr(app_mod, "create_app", lambda path, harness: _Fake(harness))
    app_mod.run_app("/x", "opencode")
    assert seen == ["opencode", "native", "opencode"]


# ---------------------------------------------------------------------------
# Round-1 review fixes
# ---------------------------------------------------------------------------

def test_tui_save_diff_is_the_native_splice(native_env):
    """The confirm modal must show the diff the native save will write — not the opencode one,
    which (cfg carrying a [native] block the file lacks) fell back to a clean rewrite."""
    from omodel.app import ConfirmModal

    app = _native_app(native_env)
    seen = {}

    async def _run():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.session.set_model("cat:quick", "zai", "glm-5.3")
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)
            seen["body"] = app.screen._body

    asyncio.run(_run())
    assert '"[native]"' in seen["body"]
    assert "-  // keep me" not in seen["body"]


def test_opencode_readback_unchanged_for_a_non_dict_block():
    """`"[opencode]": null` with nothing to write keeps its old answer (the clean rewrite); only
    native treats an absent block as empty."""
    base = '// c\n{\n  "$schema": "x/omo.schema.json",\n  "[opencode]": null\n}\n'
    cfg = json5.loads(base)
    assert config_io.render(cfg, base) == config_io.serialize(cfg)


def test_no_login_is_catalog_unavailable_not_missing_omo(tmp_path, monkeypatch):
    _stub_omo(monkeypatch)
    with pytest.raises(catalog.CatalogUnavailable, match="no logged-in provider"):
        catalog.load_native(agent_dir=_agent_dir(tmp_path), env={})


def test_no_login_degraded_reason(native_env, capsys, tmp_path):
    os.remove(tmp_path / "agent" / "auth.json")
    code, payload = _cli_json(["show", "--harness", "native", "--config", str(native_env), "--json"], capsys)
    assert code == 0
    assert payload["degraded"] is True
    assert "no logged-in provider" in payload["degraded_reason"]


def test_senpi_block_without_native_is_refused(native_env, capsys):
    native_env.write_text('{\n  "$schema": "x/omo.schema.json",\n  "[senpi]": {"memory": {}}\n}\n')
    code, payload = _cli_json(["show", "--harness", "native", "--config", str(native_env), "--json"], capsys)
    assert (code, payload["error"], payload["harness"]) == (3, "harness_unavailable", "native")
    # With [native] present omo drops [senpi] itself — editing [native] is then right.
    native_env.write_text('{\n  "[senpi]": {},\n  "[native]": {}\n}\n')
    code, payload = _cli_json(["show", "--harness", "native", "--config", str(native_env), "--json"], capsys)
    assert code == 0


def test_bad_variant_message_names_omo(native_env, capsys):
    code, payload = _cli_json(["set", "cat:quick", "zai/glm-5.3", "--variant", "turbo", "--harness",
                               "native", "--config", str(native_env), "--json"], capsys)
    assert code == 3
    assert payload["error"] == "bad_variant" and "omo reports" in payload["message"]


def test_explicit_opencode_harness_misuse(capsys):
    assert cli.main(["--harness", "opencode", "--update"]) == 2


def test_print_refuses_the_legacy_path(capsys):
    legacy = config_io.legacy_config_path()
    os.makedirs(os.path.dirname(legacy), exist_ok=True)
    with open(legacy, "w") as f:
        f.write('{"agents": {}}')
    assert cli.main(["--harness", "native", "--print", "--config", legacy]) == 3


def test_t_refused_while_a_fetch_is_in_flight(native_env):
    app = _native_app(native_env)

    async def _run():
        async with app.run_test() as pilot:
            await pilot.pause()
            app._detail_fetching = True
            await pilot.press("t")
            await pilot.pause()
            assert app.is_running
            app._detail_fetching = False

    asyncio.run(_run())
    assert app.return_value is None


def test_t_names_a_running_refresh(native_env):
    app = _native_app(native_env)

    async def _run():
        async with app.run_test() as pilot:
            await pilot.pause()
            app._refresh_inflight = True
            await pilot.press("t")
            await pilot.pause()
            assert app.is_running
            assert any("Refresh still running" in str(n.message) for n in app._notifications)
            app._refresh_inflight = False

    asyncio.run(_run())
    assert app.return_value is None


# ---------------------------------------------------------------------------
# No logged-in provider: omo answered, so every surface says "log in", not "retry"
# ---------------------------------------------------------------------------

def test_no_login_is_its_own_error_without_the_force_hint(tmp_path, monkeypatch):
    _stub_omo(monkeypatch)
    with pytest.raises(catalog.NoNativeLogin) as info:
        catalog.load_native(agent_dir=_agent_dir(tmp_path), env={})
    assert "/login" in str(info.value) and "--force" not in str(info.value)


def test_no_login_cli_note_says_log_in(native_env, capsys, tmp_path):
    os.remove(tmp_path / "agent" / "auth.json")
    assert cli.main(["show", "--harness", "native", "--config", str(native_env)]) == 0
    err = capsys.readouterr().err
    assert "availability is unknown: no logged-in provider found" in err
    assert "could not be read" not in err and "retries it" not in err


def test_no_login_refresh_models_exits_0_and_caches(native_env, capsys, tmp_path):
    os.remove(tmp_path / "agent" / "auth.json")
    assert cli.main(["--harness", "native", "--refresh-models"]) == 0
    assert "0 providers logged in" in capsys.readouterr().out
    assert cache.read("native-models") == LIST_MODELS


def test_no_login_header_says_log_in(native_env, tmp_path):
    from textual.widgets import Static

    os.remove(tmp_path / "agent" / "auth.json")

    async def _run():
        app = _native_app(native_env)
        async with app.run_test() as pilot:
            await pilot.pause()
            text = str(app.query_one("#providers", Static).render())
            assert text.startswith("oModel [native]: ⚠ no logged-in provider"), text

    asyncio.run(_run())


def test_check_flag_names_an_unavailable_harness_and_exits_0(capsys):
    legacy = config_io.legacy_config_path()
    os.makedirs(os.path.dirname(legacy), exist_ok=True)
    with open(legacy, "w") as f:
        f.write('{"agents": {}}')
    assert cli.main(["--harness", "native", "--check", "--config", legacy]) == 0
    out = capsys.readouterr().out
    assert "[check] native unavailable:" in out and "pre-4.19.3" in out
    assert "OK" not in out


def test_no_login_print_names_the_login(native_env, capsys, tmp_path):
    os.remove(tmp_path / "agent" / "auth.json")
    assert cli.main(["--harness", "native", "--print", "--config", str(native_env)]) == 0
    assert "Providers: (none — no omo provider logged in)" in capsys.readouterr().out


def test_no_login_refresh_in_the_tui_warns_and_keeps_running(native_env, tmp_path):
    app = _native_app(native_env)

    async def _run():
        async with app.run_test() as pilot:
            await pilot.pause()
            os.remove(tmp_path / "agent" / "auth.json")
            await pilot.press("r")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.is_running
            assert isinstance(app.catalog_error, catalog.NoNativeLogin)
            assert any("no logged-in provider" in str(n.message) for n in app._notifications)
            # omo answered, so the old list is not kept: nothing is runnable until you log in.
            assert app.catalog.connected == []

    asyncio.run(_run())


def test_opencode_writes_into_a_native_only_file_go_to_the_opencode_block(tmp_path):
    """A document with only `[native]` is unified: plain omodel must not write at the root,
    which OmO Native folds in too."""
    cfg_path = tmp_path / "omo.jsonc"
    native = {"agents": {"explore": {"model": "zai/glm-5.3"}}}
    cfg_path.write_text(json.dumps({"[native]": native}))
    s = session.Session.build(str(cfg_path))
    assert s.scope == "opencode"
    s.set_model("agent:sisyphus", "opencode", "claude-haiku-5-5", "high")
    s.save()
    doc = json5.loads(cfg_path.read_text())
    assert "agents" not in doc
    assert doc["[native]"] == native
    assert doc["[opencode]"]["agents"]["sisyphus"] == {"model": "opencode/claude-haiku-5-5",
                                                      "reasoning": "high"}


def test_show_prose_loads_the_catalog_once_and_reports_once(native_env, capsys, monkeypatch):
    calls = []
    _stub_omo(monkeypatch, returncode=1, calls=calls)
    assert cli.main(["show", "--harness", "native", "--config", str(native_env)]) == 0
    err = capsys.readouterr().err
    assert len(calls) == 1
    assert err.count("exited with code 1") == 1 and "[warn]" not in err
