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

from unittest.mock import MagicMock, patch

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
