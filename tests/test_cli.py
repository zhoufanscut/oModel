"""test_cli.py — argparse dispatch: --version, --check, --print, --restore.

`--print` and `--check` both reach `catalog.load()`; this machine has a real `opencode` on
PATH, so an unstubbed call would shell out for real (~3s / ~320 MB — DESIGN §Data sources).
Every test below that can reach the catalog stubs `subprocess.run` and/or `shutil.which`
(mirrors test_catalog_parse.py's convention). `--restore` and `--version` never touch the
catalog, so they need no such stub.

All tests pass an explicit `--config` temp path — the real
~/.config/opencode/oh-my-openagent.jsonc is never touched (conftest.py's autouse
`_isolate_omodel_config` fixture is a second net even if a test forgot to).
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from omodel import cli, config_io

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_run(stdout: str, returncode: int = 0):
    """Return a mock subprocess.CompletedProcess-alike (mirrors test_catalog_parse.py)."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = ""
    return m


# Guardrail for tests that must NEVER shell out at all (e.g. degraded-mode paths, where
# shutil.which already short-circuits catalog.load() before any subprocess.run call).
_NO_SHELL = patch("subprocess.run", side_effect=AssertionError("must not shell out to opencode"))

MOCK_MODELS_OUTPUT = "opencode/glm-5\nzhipuai/glm-5\n"

VALID_CONFIG = """\
{
  "agents": {
    "sisyphus": {"model": "opencode/claude-opus-4-7"}
  },
  "categories": {
    "summarizer": {"model": "opencode/gpt-5.5-mini"}
  }
}
"""

# A comment-bearing seed so save() (called to pre-populate backups) always finds a real diff
# against the freshly-serialized config and actually writes a snapshot.
SEED_JSONC = """\
// seed comment — used to pre-populate .backup/ for --restore tests
{
  "agents": {
    "sisyphus": {
      "model": "opencode/claude-opus-4-7"
    }
  },
  "categories": {}
}
"""


def _write(path, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _read(path) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _seed_backups(cfg_path) -> None:
    """Write SEED_JSONC, then save a differing config so a real original.jsonc + timestamped
    snapshot both land under <cfg_path's dir>/.backup/."""
    _write(cfg_path, SEED_JSONC)
    new_cfg = {"agents": {"sisyphus": {"model": "deepseek/deepseek-v4-pro"}}, "categories": {}}
    config_io.save(new_cfg, str(cfg_path))


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------

class TestVersion:

    def test_version_prints_and_returns_0(self, capsys):
        import omodel

        rc = cli.main(["--version"])

        captured = capsys.readouterr()
        assert rc == 0
        assert omodel.__version__ in captured.out


# ---------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------

class TestCheck:

    def test_check_full_mode(self, capsys):
        with patch("subprocess.run", return_value=_mock_run(MOCK_MODELS_OUTPUT)):
            with patch("shutil.which", return_value="/usr/bin/opencode"):
                rc = cli.main(["--check"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "[check] OK (full mode)" in captured.out

    def test_check_degraded_mode(self, capsys):
        # opencode absent → catalog.load() returns before ever calling subprocess.run.
        with _NO_SHELL, patch("shutil.which", return_value=None):
            rc = cli.main(["--check"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "[check] Degraded mode" in captured.out
        assert "[check] OK (degraded mode)" in captured.out


# ---------------------------------------------------------------------------
# --print
# ---------------------------------------------------------------------------

class TestPrint:

    def test_print_explicit_config(self, tmp_path, capsys):
        cfg_path = tmp_path / "oh-my-openagent.jsonc"
        _write(cfg_path, VALID_CONFIG)

        with _NO_SHELL, patch("shutil.which", return_value=None):
            rc = cli.main(["--print", "--config", str(cfg_path)])

        captured = capsys.readouterr()
        assert rc == 0
        assert "AGENTS:" in captured.out
        assert "CATEGORIES:" in captured.out
        assert "sisyphus: opencode/claude-opus-4-7" in captured.out
        assert "summarizer: opencode/gpt-5.5-mini" in captured.out

    def test_print_malformed_config_returns_1(self, tmp_path, capsys):
        cfg_path = tmp_path / "oh-my-openagent.jsonc"
        _write(cfg_path, "{ this is not valid json ][")

        with _NO_SHELL, patch("shutil.which", return_value=None):
            rc = cli.main(["--print", "--config", str(cfg_path)])

        captured = capsys.readouterr()
        assert rc == 1
        assert captured.out == "" or "Traceback" not in captured.out
        assert "Traceback" not in captured.err
        assert str(cfg_path) in captured.err
        assert "--restore" in captured.err


# ---------------------------------------------------------------------------
# --restore
# ---------------------------------------------------------------------------

class TestRestore:

    def test_restore_no_backups(self, tmp_path, capsys):
        cfg_path = tmp_path / "oh-my-openagent.jsonc"

        rc = cli.main(["--restore", "--config", str(cfg_path)])

        captured = capsys.readouterr()
        assert rc == 0
        assert "No backups found." in captured.out

    def test_restore_valid_number_restores_file(self, tmp_path, capsys, monkeypatch):
        cfg_path = tmp_path / "oh-my-openagent.jsonc"
        _seed_backups(cfg_path)

        # list_backups() always puts the pinned original.jsonc first → choice "1".
        monkeypatch.setattr("builtins.input", lambda prompt="": "1")
        rc = cli.main(["--restore", "--config", str(cfg_path)])

        captured = capsys.readouterr()
        assert rc == 0
        assert "Restored original.jsonc" in captured.out
        assert _read(cfg_path) == SEED_JSONC

    def test_restore_cancel_with_q(self, tmp_path, capsys, monkeypatch):
        cfg_path = tmp_path / "oh-my-openagent.jsonc"
        _seed_backups(cfg_path)
        content_before = _read(cfg_path)

        monkeypatch.setattr("builtins.input", lambda prompt="": "q")
        rc = cli.main(["--restore", "--config", str(cfg_path)])

        captured = capsys.readouterr()
        assert rc == 0
        assert "Cancelled." in captured.out
        assert _read(cfg_path) == content_before, "cancelling must not modify the config"

    def test_restore_out_of_range_returns_1(self, tmp_path, capsys, monkeypatch):
        cfg_path = tmp_path / "oh-my-openagent.jsonc"
        _seed_backups(cfg_path)

        monkeypatch.setattr("builtins.input", lambda prompt="": "999")
        rc = cli.main(["--restore", "--config", str(cfg_path)])

        captured = capsys.readouterr()
        assert rc == 1
        assert "Choice out of range." in captured.err

    def test_restore_non_numeric_returns_1(self, tmp_path, capsys, monkeypatch):
        cfg_path = tmp_path / "oh-my-openagent.jsonc"
        _seed_backups(cfg_path)

        monkeypatch.setattr("builtins.input", lambda prompt="": "not-a-number")
        rc = cli.main(["--restore", "--config", str(cfg_path)])

        captured = capsys.readouterr()
        assert rc == 1
        assert "Invalid choice." in captured.err

    def test_restore_eof_cancels(self, tmp_path, capsys, monkeypatch):
        """Ctrl+D at the prompt (EOFError) must not traceback."""
        cfg_path = tmp_path / "oh-my-openagent.jsonc"
        _seed_backups(cfg_path)

        def _raise_eof(prompt=""):
            raise EOFError()

        monkeypatch.setattr("builtins.input", _raise_eof)
        rc = cli.main(["--restore", "--config", str(cfg_path)])

        captured = capsys.readouterr()
        assert rc == 1
        assert "Cancelled." in captured.out
        assert "Traceback" not in captured.err

    def test_restore_keyboard_interrupt_cancels(self, tmp_path, capsys, monkeypatch):
        """Ctrl+C at the prompt (KeyboardInterrupt) must not traceback."""
        cfg_path = tmp_path / "oh-my-openagent.jsonc"
        _seed_backups(cfg_path)

        def _raise_kb(prompt=""):
            raise KeyboardInterrupt()

        monkeypatch.setattr("builtins.input", _raise_kb)
        rc = cli.main(["--restore", "--config", str(cfg_path)])

        captured = capsys.readouterr()
        assert rc == 1
        assert "Cancelled." in captured.out
        assert "Traceback" not in captured.err


# ===========================================================================
# The agent surface — subcommands, JSON, exit codes
#
# `omodel` grew a second audience: an LLM agent that reads JSON and branches on the exit code.
# These tests pin the contract that surface promises (CONTRACTS.md §agent JSON) — above all
# that a refusal is exit 3 and a tool failure is exit 1, since an agent that can't tell those
# apart either gives up on a fixable pick or loops on a broken one.
#
# Every test passes an explicit --config temp path and stubs subprocess.run: the agent verbs
# reach catalog.load(), and this machine has a real `opencode` (~3s / ~320 MB per call).
# ===========================================================================

import json as _json

# A config using REAL omo target names (sisyphus / quick), assigned models the mock catalog
# below actually serves — so availability guards see a coherent world.
AGENT_CONFIG = """\
// keep me — proves the text-preserving write survives the CLI too
{
  "agents": {
    "sisyphus": {"model": "opencode/glm-5"}
  },
  "categories": {
    "quick": {"model": "zhipuai/glm-5"}
  },
  "somethingElse": {"keep": true}
}
"""


def _agent_cfg(tmp_path):
    path = tmp_path / "oh-my-openagent.jsonc"
    _write(path, AGENT_CONFIG)
    return str(path)


def _run(argv, stdout=MOCK_MODELS_OUTPUT):
    """Run cli.main with `opencode models` stubbed to `stdout`."""
    with patch("shutil.which", return_value="/usr/bin/opencode"), \
         patch("subprocess.run", return_value=_mock_run(stdout)):
        return cli.main(argv)


def _run_json(argv, stdout=MOCK_MODELS_OUTPUT):
    """Run and parse the JSON payload. Returns (rc, payload)."""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _run(argv, stdout)
    return rc, _json.loads(buf.getvalue())


def _run_degraded(argv):
    """Run with opencode absent — availability unknown, never a subprocess."""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), \
         patch("shutil.which", return_value=None), _NO_SHELL:
        rc = cli.main(argv)
    return rc, buf.getvalue()


class TestBackCompat:
    """The flat flags predate the subcommands and must keep working untouched."""

    def test_bare_flags_still_parse(self, tmp_path, capsys):
        cfg = _agent_cfg(tmp_path)
        assert _run(["--print", "--config", cfg]) == 0
        assert "AGENTS:" in capsys.readouterr().out

    def test_check_flag_still_always_exits_0(self):
        """CI runs `omodel --check` unconditionally — the subcommand is the strict one."""
        with patch("shutil.which", return_value=None), _NO_SHELL:
            assert cli.main(["--check"]) == 0

    def test_config_before_or_after_the_subcommand(self, tmp_path):
        """`--config X show` and `show --config X` must both reach the same file — argparse
        would otherwise let the subparser's default clobber the main parser's value."""
        cfg = _agent_cfg(tmp_path)
        rc_a, a = _run_json(["--config", cfg, "show", "--json"])
        rc_b, b = _run_json(["show", "--config", cfg, "--json"])
        assert rc_a == rc_b == 0
        assert a["config_path"] == b["config_path"] == cfg


class TestTargets:

    def test_lists_known_targets(self, tmp_path):
        rc, payload = _run_json(["targets", "--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 0
        assert "agent:sisyphus" in payload["targets"]
        assert "cat:quick" in payload["targets"]
        # ultrawork is honored on sisyphus only (session.ULTRAWORK_AGENTS)
        assert "agent:sisyphus.ultrawork" in payload["targets"]
        assert "agent:oracle.ultrawork" not in payload["targets"]


class TestShow:

    def test_reports_assignments_and_providers(self, tmp_path):
        rc, payload = _run_json(["show", "--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 0
        assert payload["schema"] == 1
        assert payload["degraded"] is False
        assert payload["providers"] == ["opencode", "zhipuai"]
        row = next(t for t in payload["targets"] if t["target"] == "agent:sisyphus")
        assert row["model"] == "opencode/glm-5"
        assert row["provider"] == "opencode"
        assert row["bare"] == "glm-5"
        assert row["assigned"] is True
        assert row["available"] is True

    def test_seeded_preset_is_reported(self, tmp_path):
        _, payload = _run_json(["show", "--config", _agent_cfg(tmp_path), "--json"])
        assert payload["active_preset"]["name"] == "default"
        assert payload["presets"][0]["active"] is True

    def test_unknown_configured_entry_is_surfaced_not_hidden(self, tmp_path):
        """A stale/hand-added agent must be visible, or an agent could 'fix' a config while a
        broken entry it never saw stayed put."""
        path = tmp_path / "oh-my-openagent.jsonc"
        _write(path, '{"agents": {"ghost": {"model": "opencode/glm-5"}}, "categories": {}}')
        _, payload = _run_json(["show", "--config", str(path), "--json"])
        ghost = next(t for t in payload["targets"] if t["target"] == "agent:ghost")
        assert ghost["known"] is False

    def test_degraded_is_flagged_rather_than_looking_empty(self, tmp_path):
        rc, out = _run_degraded(["show", "--config", _agent_cfg(tmp_path), "--json"])
        payload = _json.loads(out)
        assert rc == 0
        assert payload["degraded"] is True
        assert payload["providers"] == []
        # Availability is UNKNOWN, not False — the distinction the flag exists to carry.
        row = next(t for t in payload["targets"] if t["target"] == "agent:sisyphus")
        assert row["available"] is None


class TestCandidates:

    def test_lists_values_ready_to_set(self, tmp_path):
        rc, payload = _run_json(
            ["candidates", "agent:sisyphus", "--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 0
        assert payload["candidates"], "the chain should offer glm-5 from the mock catalog"
        for c in payload["candidates"]:
            # `value` is pre-assembled so a caller never builds provider/model itself.
            assert c["value"] == f"{c['provider']}/{c['model']}"
        # The raw omo fallbackChain entry must NOT leak — it would freeze omo's schema into ours.
        assert "entry" not in payload["candidates"][0]

    def test_marks_the_current_assignment(self, tmp_path):
        _, payload = _run_json(
            ["candidates", "agent:sisyphus", "--config", _agent_cfg(tmp_path), "--json"])
        assert payload["current"] == "opencode/glm-5"
        assert any(c["current"] for c in payload["candidates"])

    def test_gpt_only_is_advertised(self, tmp_path):
        _, payload = _run_json(
            ["candidates", "agent:hephaestus", "--config", _agent_cfg(tmp_path), "--json"])
        assert payload["gpt_only"] is True

    def test_unknown_target_is_rejected(self, tmp_path):
        rc, payload = _run_json(
            ["candidates", "agent:nope", "--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 3
        assert payload["ok"] is False
        assert payload["error"] == "unknown_target"


class TestCheckCommand:

    def test_clean_config_exits_0(self, tmp_path):
        rc, payload = _run_json(["check", "--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 0
        assert payload["ok"] is True
        assert payload["problems"] == []

    def test_unavailable_model_exits_3(self, tmp_path):
        path = tmp_path / "oh-my-openagent.jsonc"
        _write(path, '{"agents": {"sisyphus": {"model": "ghost/nope"}}, "categories": {}}')
        rc, payload = _run_json(["check", "--config", str(path), "--json"])
        assert rc == 3
        assert payload["problems"][0]["problem"] == "unavailable"

    def test_degraded_does_not_invent_problems(self, tmp_path):
        """Availability is unknown without opencode — reporting it as broken would be a lie."""
        path = tmp_path / "oh-my-openagent.jsonc"
        _write(path, '{"agents": {"sisyphus": {"model": "ghost/nope"}}, "categories": {}}')
        rc, out = _run_degraded(["check", "--config", str(path), "--json"])
        assert rc == 0
        assert _json.loads(out)["problems"] == []


class TestSet:

    def test_writes_both_files(self, tmp_path):
        cfg = _agent_cfg(tmp_path)
        rc, payload = _run_json(
            ["set", "agent:sisyphus", "zhipuai/glm-5", "--config", cfg, "--json"])
        assert rc == 0
        assert payload["ok"] is True
        assert payload["from"] == "opencode/glm-5"
        assert payload["to"] == "zhipuai/glm-5"
        assert '"zhipuai/glm-5"' in _read(cfg)
        assert os.path.exists(os.path.join(os.path.dirname(cfg), ".omodel-presets.json"))

    def test_preserves_comments_outside_agents_and_categories(self, tmp_path):
        cfg = _agent_cfg(tmp_path)
        _run(["set", "agent:sisyphus", "zhipuai/glm-5", "--config", cfg])
        on_disk = _read(cfg)
        assert "// keep me" in on_disk
        assert '"somethingElse"' in on_disk

    def test_dry_run_writes_nothing(self, tmp_path):
        cfg = _agent_cfg(tmp_path)
        before = _read(cfg)
        rc, payload = _run_json(
            ["set", "agent:sisyphus", "zhipuai/glm-5", "--config", cfg, "--dry-run", "--json"])
        assert rc == 0
        assert payload["dry_run"] is True
        assert payload["diff"].strip()
        assert _read(cfg) == before
        assert not os.path.exists(os.path.join(os.path.dirname(cfg), ".omodel-presets.json"))

    def test_variant_none_drops_the_key(self, tmp_path):
        cfg = _agent_cfg(tmp_path)
        _run(["set", "agent:sisyphus", "zhipuai/glm-5", "--variant", "max", "--config", cfg])
        _run(["set", "agent:sisyphus", "zhipuai/glm-5", "--variant", "none", "--config", cfg])
        assert '"variant"' not in _read(cfg)

    def test_whitespace_variant_is_treated_as_none(self, tmp_path):
        """Junk an agent could easily send — stripped at the CLI rather than written verbatim."""
        cfg = _agent_cfg(tmp_path)
        _run(["set", "agent:sisyphus", "zhipuai/glm-5", "--variant", "   ", "--config", cfg])
        assert '"variant"' not in _read(cfg)

    def test_config_still_equals_the_active_preset(self, tmp_path):
        """The invariant, through the CLI: a set must not orphan the config from its preset."""
        from omodel import config_io as _cio
        from omodel import presets as _presets
        cfg = _agent_cfg(tmp_path)
        _run(["set", "agent:sisyphus", "zhipuai/glm-5", "--config", cfg])
        store = _presets.load(cfg)
        loaded, _ = _cio.load_config(cfg)
        assert _presets.matching_index(store, loaded) == store.active

    @pytest.mark.parametrize("argv,error", [
        (["set", "agent:nope", "opencode/glm-5"], "unknown_target"),
        (["set", "agent:sisyphus", "bare-id"], "bad_value"),
        (["set", "agent:sisyphus", "ghost/nope"], "unavailable"),
    ])
    def test_guards_exit_3(self, tmp_path, argv, error):
        rc, payload = _run_json(argv + ["--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 3
        assert payload["ok"] is False
        assert payload["error"] == error

    def test_guard_leaves_the_config_untouched(self, tmp_path):
        cfg = _agent_cfg(tmp_path)
        before = _read(cfg)
        _run(["set", "agent:sisyphus", "ghost/nope", "--config", cfg])
        assert _read(cfg) == before

    def test_force_overrides_unavailable_and_reports_the_warn(self, tmp_path):
        rc, payload = _run_json(
            ["set", "agent:sisyphus", "ghost/nope", "--force",
             "--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 0
        assert payload["warn"] == ["unavailable"]

    def test_gpt_only_agent_refuses_even_with_force(self, tmp_path):
        """omo's no-hephaestus-non-gpt hook would reassign the session, so the config could not
        take effect — --force must not be able to write config that cannot work."""
        cfg = _agent_cfg(tmp_path)
        for extra in ([], ["--force"]):
            rc, payload = _run_json(
                ["set", "agent:hephaestus", "zhipuai/glm-5", "--config", cfg, "--json"] + extra)
            assert rc == 3
            assert payload["error"] == "gpt_only"

    def test_bad_variant_refuses_when_opencode_reports_a_set(self, tmp_path):
        from _helpers import seed_verbose
        seed_verbose("zhipuai", {"glm-5": ["low", "high"]})
        rc, payload = _run_json(
            ["set", "agent:sisyphus", "zhipuai/glm-5", "--variant", "bogus",
             "--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 3
        assert payload["error"] == "bad_variant"

    def test_variant_is_allowed_when_opencode_is_silent(self, tmp_path):
        """variants_for is cache-only and dedicated providers report {} — empty means "no
        information", so refusing on silence would reject valid picks on a cold cache."""
        rc, _ = _run_json(
            ["set", "agent:sisyphus", "zhipuai/glm-5", "--variant", "anything",
             "--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 0

    def test_variant_guard_does_not_refuse_on_an_expired_set(self, tmp_path):
        """The hard guard reads TTL'd data (`variants_for(..., stale_ok=False)`), unlike the `⚠`
        marker and the pickers, which read the cache at any age.

        A refusal must not rest on a file of unbounded age: an expired set predating an upstream
        addition would make `set` reject a variant opencode now accepts, and THIS surface never
        calls `catalog.detail()` — nothing re-warms the cache behind an agent, so the wrong
        verdict would stick until a human ran `--refresh-models`. Expired → `[]` → "no
        information" → allow, which is what it did before the read became TTL-exempt.
        (`test_bad_variant_refuses_when_opencode_reports_a_set` pins the fresh-cache half — the
        guard must still refuse when it has current data.)"""
        import json as _j
        import os as _os

        from _helpers import seed_verbose

        from omodel import cache
        seed_verbose("zhipuai", {"glm-5": ["low", "high"]})   # a set from before `max` existed
        path = _os.path.join(cache.cache_dir(), "verbose-zhipuai.json")
        with open(path, encoding="utf-8") as f:
            blob = _j.load(f)
        blob["fetched_at"] -= 5 * 86400
        with open(path, "w", encoding="utf-8") as f:
            _j.dump(blob, f)

        rc, payload = _run_json(
            ["set", "agent:sisyphus", "zhipuai/glm-5", "--variant", "max",
             "--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 0, f"an expired variant set must not produce a refusal: {payload}"

    def test_check_does_not_flag_a_variant_on_an_expired_set(self, tmp_path):
        """Same rule for `omodel check` (exit 3 on a problem) — it shares `_variant_offered`, so
        a stale set must not turn a valid config into `bad_variant` for an agent polling it."""
        import json as _j
        import os as _os

        from _helpers import seed_verbose

        from omodel import cache
        seed_verbose("zhipuai", {"glm-5": ["low", "high"]})
        path = _os.path.join(cache.cache_dir(), "verbose-zhipuai.json")
        with open(path, encoding="utf-8") as f:
            blob = _j.load(f)
        blob["fetched_at"] -= 5 * 86400
        with open(path, "w", encoding="utf-8") as f:
            _j.dump(blob, f)

        cfg = str(tmp_path / "oh-my-openagent.jsonc")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write('{ "agents": { "sisyphus": '
                    '{ "model": "zhipuai/glm-5", "variant": "max" } }, "categories": {} }')
        rc, payload = _run_json(["check", "--config", cfg, "--json"])
        assert rc == 0, f"check must not flag a variant on unbounded-age data: {payload}"
        assert payload["ok"] is True, payload

    def test_degraded_writes_instead_of_refusing_everything(self, tmp_path):
        """agent-usage §6: while degraded, availability is UNKNOWN, so `set` must SKIP the
        availability guard. Refusing would leave an agent unable to set anything at all on a
        machine where opencode simply isn't reachable — the one state it can't fix itself."""
        cfg = _agent_cfg(tmp_path)
        rc, out = _run_degraded(
            ["set", "cat:quick", "someprovider/some-model", "--config", cfg, "--json"])
        payload = _json.loads(out)
        assert rc == 0
        assert payload["warn"] == []          # nothing to warn ABOUT — not "verified fine"
        assert '"someprovider/some-model"' in _read(cfg)

    def test_creates_a_missing_subtarget_node(self, tmp_path):
        """A sub-target is addressable whether or not the config carries it (`known_targets`),
        so `set` has to build the agent object and the sub-object on the way down."""
        cfg = _agent_cfg(tmp_path)
        rc, _ = _run_json(
            ["set", "agent:sisyphus.compaction", "zhipuai/glm-5", "--config", cfg, "--json"])
        assert rc == 0
        loaded, _p = config_io.load_config(cfg)
        assert loaded["agents"]["sisyphus"]["compaction"] == {"model": "zhipuai/glm-5"}

    def test_repeated_sets_each_snapshot_a_backup(self, tmp_path):
        """The premise `apply` exists for: the ring keeps 20, so N sets cost N of the user's
        own snapshots. If this ever stopped being true, `apply`'s reason to exist would go
        with it."""
        cfg = _agent_cfg(tmp_path)
        for model in ("zhipuai/glm-5", "opencode/glm-5", "zhipuai/glm-5"):
            assert _run(["set", "cat:quick", model, "--config", cfg]) == 0
        snapshots = os.listdir(os.path.join(os.path.dirname(cfg), ".backup"))
        assert len([s for s in snapshots if s[0].isdigit()]) == 3


class TestClear:

    def test_clears_and_reports_what_was_there(self, tmp_path):
        cfg = _agent_cfg(tmp_path)
        rc, payload = _run_json(["clear", "agent:sisyphus", "--config", cfg, "--json"])
        assert rc == 0
        assert payload["from"] == "opencode/glm-5"
        assert payload["to"] is None
        assert "opencode/glm-5" not in _read(cfg)

    def test_unknown_target_exits_3(self, tmp_path):
        rc, payload = _run_json(["clear", "cat:nope", "--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 3
        assert payload["error"] == "unknown_target"

    def test_dry_run_writes_nothing(self, tmp_path):
        """Same promise as `set --dry-run`, and just as easy to get wrong separately: `clear`
        has its own call into `_publish`."""
        cfg = _agent_cfg(tmp_path)
        before = _read(cfg)
        rc, payload = _run_json(
            ["clear", "agent:sisyphus", "--config", cfg, "--dry-run", "--json"])
        assert rc == 0
        assert payload["dry_run"] is True and payload["backup"] is None
        assert _read(cfg) == before
        assert not os.path.exists(os.path.join(os.path.dirname(cfg), ".backup"))
        assert not os.path.exists(os.path.join(os.path.dirname(cfg), ".omodel-presets.json"))

    def test_clearing_an_unset_target_is_a_no_op_not_a_snapshot(self, tmp_path):
        """An agent may clear defensively before setting. That must not burn a slot in the
        20-deep backup ring on a write that changes nothing."""
        cfg = _agent_cfg(tmp_path)
        _run(["clear", "cat:quick", "--config", cfg])          # real clear: takes a snapshot
        rc, payload = _run_json(["clear", "cat:quick", "--config", cfg, "--json"])
        assert rc == 0
        assert payload["from"] is None
        assert payload["changed"] is False and payload["backup"] is None

    def test_clearing_a_subtarget_collapses_the_empty_node(self, tmp_path):
        """`clear` keeps the node, but config_io drops an empty ultrawork/compaction
        sub-object on serialize — so the sub-target must not linger as `"compaction": {}`."""
        cfg = _agent_cfg(tmp_path)
        _run(["set", "agent:sisyphus.compaction", "zhipuai/glm-5", "--config", cfg])
        _run(["clear", "agent:sisyphus.compaction", "--config", cfg])
        loaded, _p = config_io.load_config(cfg)
        assert "compaction" not in loaded["agents"]["sisyphus"]


class TestApply:

    def _stdin(self, monkeypatch, text):
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(text))

    def test_applies_many_in_one_save(self, tmp_path, monkeypatch):
        """One backup, not N — the ring keeps only 20, so a batch must not evict the user's
        own snapshots."""
        cfg = _agent_cfg(tmp_path)
        self._stdin(monkeypatch, _json.dumps({
            "agent:sisyphus": {"model": "zhipuai/glm-5", "variant": "max"},
            "cat:quick": "opencode/glm-5",
        }))
        rc, payload = _run_json(["apply", "--config", cfg, "--json"])
        assert rc == 0
        assert len(payload["applied"]) == 2
        snapshots = os.listdir(os.path.join(os.path.dirname(cfg), ".backup"))
        assert len([s for s in snapshots if s[0].isdigit()]) == 1

    def test_a_bare_string_is_accepted_as_the_model(self, tmp_path, monkeypatch):
        cfg = _agent_cfg(tmp_path)
        self._stdin(monkeypatch, _json.dumps({"cat:quick": "opencode/glm-5"}))
        assert _run(["apply", "--config", cfg]) == 0
        assert '"opencode/glm-5"' in _read(cfg)

    def test_one_bad_entry_writes_nothing(self, tmp_path, monkeypatch):
        """All-or-nothing: a half-applied config is worse than a refused one."""
        cfg = _agent_cfg(tmp_path)
        before = _read(cfg)
        self._stdin(monkeypatch, _json.dumps({
            "cat:quick": "opencode/glm-5",
            "agent:nope": "opencode/glm-5",
        }))
        rc, payload = _run_json(["apply", "--config", cfg, "--json"])
        assert rc == 3
        assert payload["error"] == "unknown_target"
        assert _read(cfg) == before

    def test_malformed_stdin_is_a_usage_error(self, tmp_path, monkeypatch):
        self._stdin(monkeypatch, "not json")
        rc, payload = _run_json(["apply", "--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 2
        assert payload["error"] == "bad_input"

    @pytest.mark.parametrize("stdin", ["", "   ", "null", "[]", '"a string"', "42"])
    def test_non_object_stdin_is_a_usage_error_not_a_crash(self, tmp_path, monkeypatch, stdin):
        """An agent piping the wrong thing (an empty heredoc, a bare list, a truncated stream)
        must get the usage code, not a traceback — 2 tells it to fix the call, and unlike a 1
        it never reads as "omodel is broken"."""
        self._stdin(monkeypatch, stdin)
        rc, payload = _run_json(["apply", "--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 2
        assert payload["error"] == "bad_input"

    def test_an_empty_batch_writes_no_snapshot(self, tmp_path, monkeypatch):
        """`{}` is a legal (if pointless) batch. It must not spend a slot in the 20-deep ring.

        The FIRST save on a hand-written file legitimately rewrites it — the agents/categories
        spans are re-rendered clean — so canonicalize first; only then is "nothing changed" a
        statement about the batch rather than about the file's formatting."""
        cfg = _agent_cfg(tmp_path)
        self._stdin(monkeypatch, "{}")
        assert _run(["apply", "--config", cfg]) == 0          # canonicalizes; 1 snapshot
        before = _read(cfg)
        backups = os.path.join(os.path.dirname(cfg), ".backup")
        first = sorted(os.listdir(backups))

        self._stdin(monkeypatch, "{}")
        rc, payload = _run_json(["apply", "--config", cfg, "--json"])
        assert rc == 0
        assert payload["applied"] == [] and payload["changed"] is False
        assert _read(cfg) == before
        assert sorted(os.listdir(backups)) == first

    def test_dry_run_writes_neither_file(self, tmp_path, monkeypatch):
        cfg = _agent_cfg(tmp_path)
        before = _read(cfg)
        self._stdin(monkeypatch, _json.dumps({
            "agent:sisyphus": {"model": "zhipuai/glm-5"},
            "cat:quick": "opencode/glm-5",
        }))
        rc, payload = _run_json(["apply", "--config", cfg, "--dry-run", "--json"])
        assert rc == 0
        assert len(payload["applied"]) == 2
        assert payload["dry_run"] is True and payload["backup"] is None
        assert _read(cfg) == before
        assert not os.path.exists(os.path.join(os.path.dirname(cfg), ".backup"))
        assert not os.path.exists(os.path.join(os.path.dirname(cfg), ".omodel-presets.json"))

    def test_a_gpt_only_violation_refuses_the_whole_batch_even_forced(self, tmp_path,
                                                                     monkeypatch):
        """The one guard --force never opens, checked through the batch door too: `apply` must
        not be a looser route to hephaestus than `set` is."""
        cfg = _agent_cfg(tmp_path)
        before = _read(cfg)
        self._stdin(monkeypatch, _json.dumps({
            "cat:quick": "opencode/glm-5",
            "agent:hephaestus": "zhipuai/glm-5",
        }))
        rc, payload = _run_json(["apply", "--config", cfg, "--force", "--json"])
        assert rc == 3
        assert payload["error"] == "gpt_only"
        assert _read(cfg) == before


class TestPreset:

    def test_ls_reports_the_seeded_preset(self, tmp_path):
        rc, payload = _run_json(["preset", "ls", "--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 0
        assert payload["presets"][0]["name"] == "default"
        assert payload["presets"][0]["active"] is True

    def test_new_then_use_switches_the_models(self, tmp_path):
        cfg = _agent_cfg(tmp_path)
        _run(["preset", "new", "cheap", "--config", cfg])
        _run(["set", "cat:quick", "opencode/glm-5", "--config", cfg])
        assert _run(["preset", "use", "default", "--config", cfg]) == 0
        # 'default' still holds the ORIGINAL assignment; the edit stayed in 'cheap'.
        assert '"zhipuai/glm-5"' in _read(cfg)

    def test_use_accepts_a_1_based_index(self, tmp_path):
        cfg = _agent_cfg(tmp_path)
        _run(["preset", "new", "cheap", "--config", cfg])
        rc, payload = _run_json(["preset", "use", "1", "--config", cfg, "--json"])
        assert rc == 0
        assert payload["name"] == "default"

    def test_rm_refuses_the_active_preset(self, tmp_path):
        """The config equals the active preset — deleting it would orphan the config."""
        rc, payload = _run_json(
            ["preset", "rm", "default", "--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 3
        assert payload["error"] == "active_preset"

    def test_unknown_preset_exits_3(self, tmp_path):
        rc, payload = _run_json(
            ["preset", "use", "ghost", "--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 3
        assert payload["error"] == "unknown_preset"

    def test_missing_name_is_a_usage_error(self, tmp_path):
        rc, payload = _run_json(["preset", "use", "--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 2
        assert payload["error"] == "bad_input"


class TestMalformedConfig:

    def test_agent_verbs_report_it_as_exit_1_not_a_guard_refusal(self, tmp_path):
        """A broken config is omodel failing to read, not the agent asking for something silly —
        the 1-vs-3 split is what tells it to stop rather than retry."""
        path = tmp_path / "oh-my-openagent.jsonc"
        _write(path, "{ this is not json")
        for argv in (["show"], ["targets"], ["set", "agent:sisyphus", "opencode/glm-5"]):
            assert _run(argv + ["--config", str(path)]) == 1


class TestAgentGuide:
    """`omodel agent-guide` is the discovery path: an agent has the binary and no repo
    checkout, so the contract must be readable FROM the package (importlib.resources), not
    just from GitHub."""

    def test_prints_the_guide(self, capsys):
        rc = cli.main(["agent-guide"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Using omodel from an LLM agent" in out
        # The line the whole document exists to deliver.
        assert "Do not hand-edit" in out

    def test_documents_the_exit_codes(self, capsys):
        """An agent that can't tell 1 from 3 either retries a broken tool or gives up on a
        fixable pick — so the guide must state both."""
        cli.main(["agent-guide"])
        out = capsys.readouterr().out
        assert "3 means try something else; 1 means stop" in out

    def test_needs_no_config_and_never_shells_out(self):
        """It must work before omodel has ever been configured, and cost nothing."""
        with _NO_SHELL, patch("shutil.which", return_value=None):
            assert cli.main(["agent-guide"]) == 0

    def test_is_advertised_in_help(self, capsys):
        """--help is an agent's first move; the guide has to be findable from there."""
        with pytest.raises(SystemExit):
            cli.main(["--help"])
        assert "agent-guide" in capsys.readouterr().out


class TestReviewRegressions:
    """Guards for defects found in review of the agent surface. Each names the failure it
    prevents, because several of them are silent and none was caught by the original suite."""

    # ----- crash on agent-supplied JSON -----

    @pytest.mark.parametrize("bad", [123, True, 1.5])
    def test_apply_refuses_a_non_string_model_instead_of_crashing(
        self, tmp_path, monkeypatch, bad
    ):
        """`"/" not in 123` raises TypeError → traceback + exit 1 ("stop and report") on what is
        really a caller error the agent could fix (exit 3). Agent-supplied JSON makes this
        entirely reachable."""
        import io
        cfg = _agent_cfg(tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO(_json.dumps({"cat:quick": {"model": bad}})))
        rc, payload = _run_json(["apply", "--config", cfg, "--json"])
        assert rc == 3
        assert payload["error"] == "bad_value"

    def test_apply_refuses_a_non_string_variant(self, tmp_path, monkeypatch):
        """A dict variant passed every guard and landed in the JSONC as a nested object."""
        import io
        cfg = _agent_cfg(tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO(_json.dumps(
            {"cat:quick": {"model": "zhipuai/glm-5", "variant": {"x": 1}}})))
        rc, payload = _run_json(["apply", "--config", cfg, "--json"])
        assert rc == 3
        assert payload["error"] == "bad_input"
        assert "variant" not in _read(cfg)

    # ----- no-op writes -----

    def test_a_no_op_set_writes_nothing(self, tmp_path):
        """Without a dirtiness gate, re-setting the value already assigned still rewrites the
        file and burns one of the 20 backup slots — so an agent verifying its own work evicts
        the user's snapshots a call at a time."""
        cfg = _agent_cfg(tmp_path)
        _run(["set", "agent:sisyphus", "zhipuai/glm-5", "--config", cfg])
        after_first = _read(cfg)
        def snaps():
            backup_dir = os.path.join(os.path.dirname(cfg), ".backup")
            return len([s for s in os.listdir(backup_dir) if s[0].isdigit()])

        n = snaps()
        rc, payload = _run_json(
            ["set", "agent:sisyphus", "zhipuai/glm-5", "--config", cfg, "--json"])
        assert rc == 0
        assert payload["changed"] is False
        assert payload["backup"] is None
        assert _read(cfg) == after_first
        assert snaps() == n, "a no-op set must not burn a backup slot"

    def test_an_empty_apply_writes_nothing(self, tmp_path, monkeypatch):
        """Once the file is in omodel's clean form, an empty batch must be a true no-op.

        NB the FIRST write to a hand-written config legitimately reformats the agents/categories
        spans (decision #13), so `changed: True` there is honest — the file really did change.
        The no-op contract applies from then on, which is the state an agent doing repeated work
        is actually in."""
        import io
        cfg = _agent_cfg(tmp_path)
        _run(["set", "agent:sisyphus", "opencode/glm-5", "--config", cfg])  # normalize
        before = _read(cfg)
        snaps = len([s for s in os.listdir(
            os.path.join(os.path.dirname(cfg), ".backup")) if s[0].isdigit()])

        monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
        rc, payload = _run_json(["apply", "--config", cfg, "--json"])

        assert rc == 0
        assert payload["changed"] is False
        assert payload["backup"] is None
        assert _read(cfg) == before
        assert len([s for s in os.listdir(
            os.path.join(os.path.dirname(cfg), ".backup")) if s[0].isdigit()]) == snaps

    # ----- sync conflict -----

    def _conflicted(self, tmp_path):
        """A config that matches NO preset — i.e. something outside omodel wrote it."""
        cfg = _agent_cfg(tmp_path)
        _run(["preset", "new", "second", "--config", cfg])   # 2 presets, 'second' active
        _write(cfg, '{"agents": {"sisyphus": {"model": "handedited/zzz"}}, "categories": {}}')
        return cfg

    def test_preset_rm_does_not_rewrite_the_surviving_preset(self, tmp_path):
        """`rm` defaulted to projected_store(), which folds the live cfg into the ACTIVE preset.
        Under a sync conflict that silently replaced the surviving preset's content with the
        foreign edit — and .omodel-presets.json has no backup ring, so it was unrecoverable."""
        from omodel import presets as _presets
        cfg = self._conflicted(tmp_path)
        kept = _presets.load(cfg).presets[1]
        assert kept.categories.get("quick"), "precondition: the survivor holds a category model"

        rc = _run(["preset", "rm", "default", "--config", cfg])
        assert rc == 0

        after = _presets.load(cfg)
        assert len(after.presets) == 1
        assert after.presets[0].name == "second"
        assert after.presets[0].agents == kept.agents, "rm must not rewrite the survivor"
        assert after.presets[0].categories == kept.categories

    def test_preset_use_does_not_bank_a_foreign_edit(self, tmp_path):
        """switch_preset banks the live cfg into the preset you leave. Under a sync conflict the
        cfg was never that preset's content, so banking destroys it."""
        from omodel import presets as _presets
        cfg = self._conflicted(tmp_path)
        leaving = _presets.load(cfg).presets[1]   # 'second' is active

        rc = _run(["preset", "use", "default", "--config", cfg])
        assert rc == 0

        after = _presets.load(cfg)
        second = next(p for p in after.presets if p.name == "second")
        assert second.agents == leaving.agents, "the preset being left must not absorb the edit"
        assert "handedited/zzz" not in _json.dumps(second.agents)
        # And the switch resolved the conflict: config now equals the active preset.
        loaded, _ = config_io.load_config(cfg)
        assert _presets.matching_index(after, loaded) == after.active

    def test_mutating_payloads_surface_sync_conflict(self, tmp_path):
        """`set` under a conflict adopts the config into the active preset. That is a defensible
        resolution; doing it without telling the agent is not."""
        cfg = self._conflicted(tmp_path)
        _, payload = _run_json(
            ["set", "cat:quick", "zhipuai/glm-5", "--config", cfg, "--json"])
        assert payload["sync_conflict"] is True

    def test_clean_config_reports_no_sync_conflict(self, tmp_path):
        _, payload = _run_json(
            ["set", "cat:quick", "zhipuai/glm-5", "--config", _agent_cfg(tmp_path), "--json"])
        assert payload["sync_conflict"] is False

    # ----- payload uniformity -----

    @pytest.mark.parametrize("argv", [
        ["targets"], ["show"], ["candidates", "agent:sisyphus"], ["preset", "ls"],
    ])
    def test_every_success_payload_carries_ok(self, tmp_path, argv):
        """An agent branching on payload["ok"] KeyError'd on exactly the calls that succeeded."""
        rc, payload = _run_json(argv + ["--config", _agent_cfg(tmp_path), "--json"])
        assert rc == 0
        assert payload["ok"] is True
        assert payload["schema"] == 1

    # ----- variant normalization -----

    def test_check_and_set_agree_on_a_padded_variant(self, tmp_path):
        """`check` used str(v).lower() while `set` used .strip().lower(), so a hand-edited
        `variant: " max "` was flagged by one and accepted by the other."""
        from _helpers import seed_verbose
        seed_verbose("zhipuai", {"glm-5": ["max"]})
        path = tmp_path / "oh-my-openagent.jsonc"
        _write(path, '{"agents": {"sisyphus": {"model": "zhipuai/glm-5", "variant": " max "}},'
                     ' "categories": {}}')
        rc_check, payload = _run_json(["check", "--config", str(path), "--json"])
        rc_set = _run(["set", "agent:sisyphus", "zhipuai/glm-5", "--variant", " max ",
                       "--config", str(path)])
        assert (rc_check == 0) == (rc_set == 0), (
            f"check said {rc_check}, set said {rc_set} — they must agree"
        )
        assert payload["problems"] == []


class TestHostileConfigShapes:
    """A hand-mangled `agents`/`categories` must give a sentence, never a traceback.

    `cfg.get("agents", {})` and `(cfg.get("agents") or {})` both look defensive and neither is:
    the first only defaults when the key is ABSENT (a present `null` returns None), the second
    rescues `null`/`[]` but not a truthy non-dict. `session.read_map` is the one that holds.
    Reachable from any hand-edited config, and `show` made it newly reachable."""

    @pytest.mark.parametrize("body", [
        '{"agents": null, "categories": null}',
        '{"agents": "oops", "categories": {}}',
        '{"agents": {}, "categories": "oops"}',
        '{"agents": [], "categories": []}',
        '{}',
    ])
    @pytest.mark.parametrize("argv", [
        ["--print"], ["show"], ["show", "--json"], ["check"], ["check", "--json"],
        ["targets"], ["set", "agent:sisyphus", "opencode/glm-5"],
    ])
    def test_no_traceback_from_a_mangled_map(self, tmp_path, capsys, body, argv):
        path = tmp_path / "oh-my-openagent.jsonc"
        _write(path, body)
        rc = _run(argv + ["--config", str(path)])
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "AttributeError" not in captured.err
        assert rc in (0, 3), f"{argv} on {body} gave rc={rc}"

    def test_set_still_lands_on_a_null_agents_map(self, tmp_path):
        """Coercing must not just avoid the crash — the edit has to work."""
        path = tmp_path / "oh-my-openagent.jsonc"
        _write(path, '{"agents": null, "categories": null}')
        assert _run(["set", "agent:sisyphus", "opencode/glm-5", "--config", str(path)]) == 0
        assert '"opencode/glm-5"' in _read(path)


class TestCheckSeesWhatSetEnforces:
    """`check` is how an agent verifies its own work, so anything `set` refuses it must report —
    otherwise a config `set` would never have produced reads as healthy."""

    def test_check_reports_a_non_gpt_hephaestus(self, tmp_path):
        """The GPT-only lock is the one guard --force cannot open, and `check` couldn't see it.
        A preset captured from a foreign config re-installs such a model on every switch."""
        path = tmp_path / "oh-my-openagent.jsonc"
        _write(path, '{"agents": {"hephaestus": {"model": "zhipuai/glm-5"}}, "categories": {}}')
        rc, payload = _run_json(["check", "--config", str(path), "--json"])
        assert rc == 3
        assert any(p["problem"] == "gpt_only" for p in payload["problems"])

    def test_check_and_set_agree_on_a_variant_while_degraded(self, tmp_path):
        """Variant validity comes from the CACHED --verbose, not from opencode being on PATH, so
        gating the check on `degraded` made the two verbs contradict each other on one file."""
        from _helpers import seed_verbose
        seed_verbose("zhipuai", {"glm-5": ["max"]})
        path = tmp_path / "oh-my-openagent.jsonc"
        _write(path, '{"agents": {"sisyphus": {"model": "zhipuai/glm-5", "variant": "bogus"}},'
                     ' "categories": {}}')
        rc_check, _ = _run_degraded(["check", "--config", str(path), "--json"])
        rc_set, _ = _run_degraded(
            ["set", "agent:sisyphus", "zhipuai/glm-5", "--variant", "bogus",
             "--config", str(path), "--json"])
        assert rc_check == 3, "check must flag the bad variant even with opencode off PATH"
        assert rc_set == 3
        assert (rc_check == 0) == (rc_set == 0)


class TestConflictIsNeverSilent:
    """A conflict means the next write adopts a config the caller never approved. Both surfaces
    have to say so — the JSON one did, the prose one claimed 'OK'."""

    def _conflicted(self, tmp_path):
        cfg = _agent_cfg(tmp_path)
        _run(["set", "agent:sisyphus", "opencode/glm-5", "--config", cfg])
        _write(cfg, '{"agents": {"sisyphus": {"model": "handedited/zzz"}}, "categories": {}}')
        return cfg

    def test_prose_check_names_the_conflict(self, tmp_path, capsys):
        _run(["check", "--config", self._conflicted(tmp_path)])
        out = capsys.readouterr().out.lower()
        assert "conflict" in out
        assert "ok — no problems found." not in out, (
            "a clean bill of health is misleading while a conflict is pending"
        )

    def test_prose_show_names_the_conflict(self, tmp_path, capsys):
        _run(["show", "--config", self._conflicted(tmp_path)])
        assert "conflict" in capsys.readouterr().out.lower()

    def test_clean_config_says_nothing_about_conflicts(self, tmp_path, capsys):
        _run(["check", "--config", _agent_cfg(tmp_path)])
        out = capsys.readouterr().out.lower()
        assert "conflict" not in out
        assert "ok — no problems found." in out


class TestCandidatesMarksWhatSetWouldRefuse:
    """The list includes the target's CURRENT assignment even when omodel wouldn't let you pick
    it, while the guide says to use a row's `value` verbatim. Marking beats hiding: an agent
    still needs to see what is configured."""

    def test_unsettable_current_row_is_flagged(self, tmp_path):
        path = tmp_path / "oh-my-openagent.jsonc"
        _write(path, '{"agents": {"hephaestus": {"model": "zhipuai/glm-5"}}, "categories": {}}')
        _, payload = _run_json(
            ["candidates", "agent:hephaestus", "--config", str(path), "--json"])
        rows = {c["value"]: c for c in payload["candidates"]}
        assert rows["zhipuai/glm-5"]["settable"] is False
        assert rows["zhipuai/glm-5"]["current"] is True
        # And every row it DOES mark settable really is.
        for c in payload["candidates"]:
            if c["settable"]:
                assert _run(["set", "agent:hephaestus", c["value"],
                             "--config", str(path)]) == 0, f"{c['value']} marked settable but set refused"

    def test_ordinary_target_marks_everything_settable(self, tmp_path):
        _, payload = _run_json(
            ["candidates", "agent:sisyphus", "--config", _agent_cfg(tmp_path), "--json"])
        assert all(c["settable"] for c in payload["candidates"])


class TestSettableMatchesTheRealGuard:
    """`settable` must be derived from `_validate`, not re-derived alongside it.

    Hand-rolling the conditions covered the gpt_only guard and missed availability, so the
    synthesized off-chain row — the common case: a model set while its provider was connected,
    still in the config after you disconnect it — advertised `settable: true` and then exited 3."""

    def test_an_unavailable_row_is_not_settable(self, tmp_path):
        path = tmp_path / "oh-my-openagent.jsonc"
        _write(path, '{"agents": {"sisyphus": {"model": "ghost/nope"}}, "categories": {}}')
        _, payload = _run_json(
            ["candidates", "agent:sisyphus", "--config", str(path), "--json"])
        row = next(c for c in payload["candidates"] if c["value"] == "ghost/nope")
        assert row["warn"] == ["unavailable"]
        assert row["settable"] is False, "a row set would refuse must not advertise settable"
        assert row["current"] is True

    @pytest.mark.parametrize("body,target", [
        ('{"agents": {"sisyphus": {"model": "ghost/nope"}}, "categories": {}}',
         "agent:sisyphus"),
        ('{"agents": {"hephaestus": {"model": "zhipuai/glm-5"}}, "categories": {}}',
         "agent:hephaestus"),
        ('{"agents": {}, "categories": {"quick": {"model": "ghost/gone"}}}', "cat:quick"),
    ])
    def test_settable_predicts_set_exactly(self, tmp_path, body, target):
        """The contract, checked in both directions on every row: settable ⇔ set succeeds."""
        path = tmp_path / "oh-my-openagent.jsonc"
        _write(path, body)
        _, payload = _run_json(["candidates", target, "--config", str(path), "--json"])
        assert payload["candidates"], "precondition: there is something to check"
        for c in payload["candidates"]:
            _write(path, body)  # reset between attempts
            rc = _run(["set", target, c["value"], "--config", str(path)])
            assert (rc == 0) == c["settable"], (
                f"{c['value']}: settable={c['settable']} but set returned {rc}"
            )


class TestMalformedMapIsReported:
    """`read_map` stops every surface crashing on a non-dict `agents`/`categories`, but
    surviving it is not the same as it being fine — everything under that key is discarded, and
    `check` calling such a config healthy is how it stays broken."""

    @pytest.mark.parametrize("body,key", [
        ('{"agents": "oops", "categories": {}}', "agents"),
        ('{"agents": {}, "categories": 5}', "categories"),
        ('{"agents": [], "categories": {}}', "agents"),
    ])
    def test_check_reports_it(self, tmp_path, body, key):
        path = tmp_path / "oh-my-openagent.jsonc"
        _write(path, body)
        rc, payload = _run_json(["check", "--config", str(path), "--json"])
        assert rc == 3
        problem = next(p for p in payload["problems"] if p["problem"] == "malformed_map")
        assert key in problem["message"]
        assert problem["target"] is None, "a whole-map problem belongs to no single target"

    def test_prose_does_not_render_a_none_target(self, tmp_path, capsys):
        path = tmp_path / "oh-my-openagent.jsonc"
        _write(path, '{"agents": "oops", "categories": {}}')
        _run(["check", "--config", str(path)])
        out = capsys.readouterr().out
        assert "malformed_map" in out
        assert "None:" not in out

    def test_a_null_map_is_not_reported(self, tmp_path):
        """`null` is how an absent key is legitimately spelled; only a PRESENT non-dict is junk."""
        path = tmp_path / "oh-my-openagent.jsonc"
        _write(path, '{"agents": null, "categories": null}')
        rc, payload = _run_json(["check", "--config", str(path), "--json"])
        assert not any(p["problem"] == "malformed_map" for p in payload["problems"])
        assert rc == 0


class TestClosedStdout:
    """`omodel show --json | head` — the reader stops early and ~1 KB of the 9 KB payload never
    lands. Python's stdio buffer is 8 KB, so the failure does NOT surface in `print`; it waits
    for the interpreter's shutdown flush, which is past any caller's reach — it prints
    `Exception ignored on flushing sys.stdout` and exits **120**. A fifth exit code, reading
    like a crash, on the one surface whose contract is that there are exactly four (0/1/2/3).

    `main` flushes while it can still catch, and a reader that stopped reading is not a failure:
    EXIT_OK, silent. Every test here keeps `sys.stdout` monkeypatched to an object with no real
    fileno — `_drop_stdout` dup2s /dev/null onto fd 1, which under `pytest -s` would be the live
    terminal for the rest of the session."""

    @staticmethod
    def _dead(where: str):
        import errno
        import io

        class _DeadPipe(io.StringIO):
            flushes = 0

            def flush(self):
                type(self).flushes += 1
                if where == "flush":
                    raise BrokenPipeError(errno.EPIPE, "Broken pipe")

            def write(self, s):
                if where == "write":
                    raise BrokenPipeError(errno.EPIPE, "Broken pipe")
                return super().write(s)

        return _DeadPipe()

    def test_main_flushes_while_it_can_still_catch(self, tmp_path, monkeypatch):
        """The load-bearing half, and it must be asserted as a FLUSH HAPPENING — not merely as
        "a raising flush is absorbed". The latter passes against code that never flushes at all,
        which is the whole bug: the leftover buffer then fails at interpreter shutdown, where
        EXIT_OK is no longer anyone's to return."""
        pipe = self._dead("flush")
        monkeypatch.setattr("sys.stdout", pipe)
        assert _run(["show", "--config", _agent_cfg(tmp_path), "--json"]) == cli.EXIT_OK
        assert type(pipe).flushes >= 1

    def test_a_pipe_dying_mid_print_is_ok_not_120(self, tmp_path, monkeypatch):
        """The same pipe, dying while the command is still writing rather than at the end."""
        monkeypatch.setattr("sys.stdout", self._dead("write"))
        assert _run(["show", "--config", _agent_cfg(tmp_path), "--json"]) == cli.EXIT_OK

    def test_the_four_real_codes_are_untouched(self, tmp_path):
        """The guard must absorb a dead pipe and nothing else — a refusal is still a 3."""
        cfg = _agent_cfg(tmp_path)
        assert _run(["set", "agent:nope", "opencode/glm-5", "--config", cfg]) == cli.EXIT_REJECTED
        assert _run(["targets", "--config", cfg]) == cli.EXIT_OK

    def test_drop_stdout_survives_a_stdout_with_no_fileno(self, monkeypatch):
        """Captured stdout raises io.UnsupportedOperation from fileno() — both an OSError and a
        ValueError. Swallowed, or the guard would trade 120 for a traceback."""
        import io

        monkeypatch.setattr("sys.stdout", io.StringIO())
        cli._drop_stdout()
