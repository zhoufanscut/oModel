"""test_refresh.py — `omodel --refresh-omo`. DESIGN.md §refresh.py.

Monkeypatches subprocess.run/shutil.which so tests NEVER call the real bun CLI, and always
isolates $XDG_DATA_HOME (this file's autouse fixture) plus points the write target at a tmp
dir via sys.frozen + $XDG_DATA_HOME where relevant, so a test can never read or write the
real repo's src/omodel/data/omo-suggestions.json or a developer's real
~/.local/share/omodel/omo-suggestions.json.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from omodel import refresh as refresh_mod
from omodel import suggestions


@pytest.fixture(autouse=True)
def _isolate_xdg_data(tmp_path, monkeypatch):
    """Every test in this file gets an isolated $XDG_DATA_HOME (and no $OMODEL_SUGGESTIONS),
    so a stray real ~/.local/share/omodel/omo-suggestions.json can never leak into a
    refresh()/suggestions.load() call. Individual tests may still override XDG_DATA_HOME with
    their own content afterwards."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data-default"))
    monkeypatch.delenv("OMODEL_SUGGESTIONS", raising=False)


def _mock_run(stdout: str = "", returncode: int = 0, stderr: str = ""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def _make_omo_src(tmp_path) -> str:
    """A tmp dir with the packages/model-core/src shape refresh() checks for."""
    omo_src = tmp_path / "omo-src"
    (omo_src / "packages" / "model-core" / "src").mkdir(parents=True)
    return str(omo_src)


def _which_bun_only(name):
    return "/usr/bin/bun" if name == "bun" else None


VALID_SNAPSHOT_JSON = json.dumps({
    "meta": {
        "omoVersion": "9.9.9",
        "omoCommit": "abcdef1234567890",
        "generatedAt": "2030-01-01T00:00:00Z",
    },
    "agents": {}, "categories": {}, "families": [], "knownVariants": [],
})


# ---------------------------------------------------------------------------
# Fix 1 — frozen-binary write target must never shadow-write into the ephemeral
# _MEIPASS extraction dir.
# ---------------------------------------------------------------------------

class TestResolveWriteTargetFrozen:

    def test_frozen_skips_repo_checkout_uses_xdg(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        xdg = tmp_path / "xdg-data"
        monkeypatch.setenv("XDG_DATA_HOME", str(xdg))

        target = refresh_mod._resolve_write_target()

        assert target == os.path.join(str(xdg), "omodel", "omo-suggestions.json")

    def test_not_frozen_uses_repo_checkout_when_writable(self, monkeypatch):
        monkeypatch.delattr(sys, "frozen", raising=False)
        expected_dir = os.path.join(
            os.path.dirname(os.path.abspath(refresh_mod.__file__)), "data"
        )
        if not os.access(expected_dir, os.W_OK):
            pytest.skip("dev checkout data/ dir is not writable in this environment")

        target = refresh_mod._resolve_write_target()

        assert target == os.path.join(expected_dir, "omo-suggestions.json")


# ---------------------------------------------------------------------------
# Fix 2 — bun must never hang forever.
# ---------------------------------------------------------------------------

class TestBunTimeout:

    def test_bun_timeout_is_non_fatal(self, monkeypatch, tmp_path, capsys):
        omo_src = _make_omo_src(tmp_path)
        monkeypatch.setattr(shutil, "which", _which_bun_only)

        def _raise_timeout(*a, **kw):
            raise subprocess.TimeoutExpired(cmd=["bun"], timeout=refresh_mod._BUN_TIMEOUT)

        monkeypatch.setattr(subprocess, "run", _raise_timeout)

        rc = refresh_mod.refresh(omo_src=omo_src)

        assert rc == 0
        out = capsys.readouterr().out.lower()
        assert "timed out" in out
        assert "non-fatal" in out

    def test_bun_run_carries_a_timeout_kwarg(self, monkeypatch, tmp_path):
        """Pins the actual fix: the bun subprocess.run call must pass timeout=."""
        omo_src = _make_omo_src(tmp_path)
        monkeypatch.setattr(shutil, "which", _which_bun_only)
        captured = {}

        def _fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return _mock_run(VALID_SNAPSHOT_JSON)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))

        refresh_mod.refresh(omo_src=omo_src)

        assert captured.get("timeout") == refresh_mod._BUN_TIMEOUT


# ---------------------------------------------------------------------------
# Fix 3 — the materialized-to-tempfile snapshot_omo.ts (frozen/zipimport case) must not leak.
# ---------------------------------------------------------------------------

class _FakeUnmaterializedResource:
    """Mimics an importlib.resources Traversable whose str() is not a real filesystem path
    (the frozen/zipimport case) so refresh() takes the materialize-to-tempfile branch."""

    def __init__(self, fake_path: str, text: str):
        self._fake_path = fake_path
        self._text = text

    def __str__(self):
        return self._fake_path

    def read_text(self, encoding="utf-8"):
        return self._text


class TestTempFileCleanup:

    def _patch_fake_resource(self, monkeypatch, tmp_path, name="nonexistent-in-bundle"):
        fake_path = str(tmp_path / name / "snapshot_omo.ts")  # parent dir deliberately absent
        resource = _FakeUnmaterializedResource(fake_path, "// fake snapshot_omo.ts source")

        class _FakeDir:
            def __truediv__(self, other):
                return resource

        monkeypatch.setattr(refresh_mod, "files", lambda pkg: _FakeDir())

    def test_temp_ts_file_removed_after_successful_run(self, monkeypatch, tmp_path):
        omo_src = _make_omo_src(tmp_path)
        monkeypatch.setattr(shutil, "which", _which_bun_only)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
        self._patch_fake_resource(monkeypatch, tmp_path)

        captured = {}

        def _fake_run(cmd, **kwargs):
            ts_path = cmd[2]
            captured["ts_path"] = ts_path
            assert os.path.isfile(ts_path), "temp .ts file must exist while bun runs"
            return _mock_run(VALID_SNAPSHOT_JSON)

        monkeypatch.setattr(subprocess, "run", _fake_run)

        rc = refresh_mod.refresh(omo_src=omo_src)

        assert rc == 0
        assert "ts_path" in captured
        assert not os.path.exists(captured["ts_path"]), "temp .ts file must be cleaned up"

    def test_temp_ts_file_removed_even_on_bun_failure(self, monkeypatch, tmp_path):
        omo_src = _make_omo_src(tmp_path)
        monkeypatch.setattr(shutil, "which", _which_bun_only)
        self._patch_fake_resource(monkeypatch, tmp_path, name="nonexistent-in-bundle-2")

        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["ts_path"] = cmd[2]
            return _mock_run("", returncode=1, stderr="boom")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        rc = refresh_mod.refresh(omo_src=omo_src)

        assert rc == 0
        assert not os.path.exists(captured["ts_path"])


# ---------------------------------------------------------------------------
# Fix 4 — suggestions.load()'s stale-shadow: an old $XDG_DATA_HOME snapshot must not
# permanently outrank newer bundled data.
# ---------------------------------------------------------------------------

def _write_xdg_suggestions(xdg_home, generated_at, marker_agent="xdg-marker-agent"):
    path = os.path.join(str(xdg_home), "omodel", "omo-suggestions.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "agents": {marker_agent: {"fallbackChain": []}},
        "categories": {},
        "families": [],
        "knownVariants": [],
    }
    if generated_at is not None:
        payload["meta"] = {
            "omoVersion": "0.0.0-test",
            "omoCommit": "0" * 40,
            "generatedAt": generated_at,
        }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return path


class TestSuggestionsStaleShadow:

    def test_xdg_newer_wins(self, monkeypatch, tmp_path):
        xdg_home = tmp_path / "xdg-data"
        monkeypatch.setenv("XDG_DATA_HOME", str(xdg_home))
        _write_xdg_suggestions(xdg_home, generated_at="2999-01-01T00:00:00Z")

        sugg = suggestions.load()

        assert "xdg-marker-agent" in sugg.agents

    def test_xdg_older_bundled_wins(self, monkeypatch, tmp_path):
        xdg_home = tmp_path / "xdg-data"
        monkeypatch.setenv("XDG_DATA_HOME", str(xdg_home))
        _write_xdg_suggestions(xdg_home, generated_at="1999-01-01T00:00:00Z")

        sugg = suggestions.load()

        assert "xdg-marker-agent" not in sugg.agents

    def test_xdg_missing_meta_bundled_wins(self, monkeypatch, tmp_path):
        xdg_home = tmp_path / "xdg-data"
        monkeypatch.setenv("XDG_DATA_HOME", str(xdg_home))
        _write_xdg_suggestions(xdg_home, generated_at=None)  # no `meta` key at all

        sugg = suggestions.load()

        assert "xdg-marker-agent" not in sugg.agents

    def test_omodel_suggestions_env_wins_even_when_older(self, monkeypatch, tmp_path):
        """$OMODEL_SUGGESTIONS is an explicit, unconditional override — it must win even
        though a (newer, so it would otherwise win) XDG snapshot is also present."""
        xdg_home = tmp_path / "xdg-data"
        monkeypatch.setenv("XDG_DATA_HOME", str(xdg_home))
        _write_xdg_suggestions(xdg_home, generated_at="2999-01-01T00:00:00Z")

        explicit_path = tmp_path / "explicit-suggestions.json"
        payload = {
            "meta": {"generatedAt": "1000-01-01T00:00:00Z"},
            "agents": {"explicit-marker-agent": {"fallbackChain": []}},
            "categories": {}, "families": [], "knownVariants": [],
        }
        explicit_path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setenv("OMODEL_SUGGESTIONS", str(explicit_path))

        sugg = suggestions.load()

        assert "explicit-marker-agent" in sugg.agents
        assert "xdg-marker-agent" not in sugg.agents


# ---------------------------------------------------------------------------
# Basic non-fatal-path tests for refresh.refresh()
# ---------------------------------------------------------------------------

class TestRefreshNonFatalPaths:

    def test_omo_src_missing_is_non_fatal(self, tmp_path, capsys):
        rc = refresh_mod.refresh(omo_src=str(tmp_path / "does-not-exist"))
        assert rc == 0
        assert "non-fatal" in capsys.readouterr().out.lower()

    def test_bun_missing_is_non_fatal(self, monkeypatch, tmp_path, capsys):
        omo_src = _make_omo_src(tmp_path)
        monkeypatch.setattr(shutil, "which", lambda name: None)

        rc = refresh_mod.refresh(omo_src=omo_src)

        assert rc == 0
        assert "non-fatal" in capsys.readouterr().out.lower()

    def test_bun_nonzero_exit_is_non_fatal(self, monkeypatch, tmp_path, capsys):
        omo_src = _make_omo_src(tmp_path)
        monkeypatch.setattr(shutil, "which", _which_bun_only)
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **kw: _mock_run("", returncode=1, stderr="boom")
        )

        rc = refresh_mod.refresh(omo_src=omo_src)

        assert rc == 0
        assert "non-fatal" in capsys.readouterr().out.lower()

    def test_bun_invalid_json_is_non_fatal(self, monkeypatch, tmp_path, capsys):
        omo_src = _make_omo_src(tmp_path)
        monkeypatch.setattr(shutil, "which", _which_bun_only)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _mock_run("not json {{{"))

        rc = refresh_mod.refresh(omo_src=omo_src)

        assert rc == 0
        assert "non-fatal" in capsys.readouterr().out.lower()

    def test_bun_valid_json_writes_to_resolved_target(self, monkeypatch, tmp_path, capsys):
        omo_src = _make_omo_src(tmp_path)
        monkeypatch.setattr(shutil, "which", _which_bun_only)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _mock_run(VALID_SNAPSHOT_JSON))
        # Point the write target at tmp via sys.frozen=True + $XDG_DATA_HOME — never the real
        # repo data file.
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        xdg_home = tmp_path / "xdg-data"
        monkeypatch.setenv("XDG_DATA_HOME", str(xdg_home))

        rc = refresh_mod.refresh(omo_src=omo_src)

        assert rc == 0
        target = os.path.join(str(xdg_home), "omodel", "omo-suggestions.json")
        assert os.path.isfile(target)
        with open(target, encoding="utf-8") as f:
            written = json.load(f)
        assert written["meta"]["omoVersion"] == "9.9.9"
        assert "Written to" in capsys.readouterr().out
