"""Pytest-wide fixtures.

Real-cache safety (mirrors the hard real-config rule): every test gets an isolated, empty
on-disk cache under tmp_path via OMODEL_CACHE_DIR, so no test ever reads or writes the user's
real ~/.cache/omodel, and cached opencode output can't leak between tests. Combined with the
subprocess monkeypatching in test_catalog_parse.py, this keeps the suite hermetic.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_omodel_cache(tmp_path, monkeypatch):
    """Redirect the omodel cache dir to a fresh per-test tmp dir.

    Function-scoped + autouse → each test starts with an empty cache, so the first
    catalog.load()/detail() still invokes the (mocked) subprocess exactly as before.
    """
    cache_dir = tmp_path / "omodel-cache"
    monkeypatch.setenv("OMODEL_CACHE_DIR", str(cache_dir))
    # Belt-and-suspenders: also redirect XDG_CACHE_HOME so the fallback path can't reach
    # the real ~/.cache even if OMODEL_CACHE_DIR were ever unset mid-test.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    return cache_dir


@pytest.fixture(autouse=True)
def _isolate_omodel_data(tmp_path, monkeypatch):
    """Redirect the user-data dir (and drop $OMODEL_SUGGESTIONS) for every test.

    suggestions.load() consults $XDG_DATA_HOME/omodel/omo-suggestions.json (a past
    `--refresh-omo` snapshot) and — under the newest-wins rule — a NEWER real
    ~/.local/share/omodel/omo-suggestions.json on a dev machine would silently replace the
    bundled data under test, skewing family/agent counts. Same net as the cache/config
    fixtures: point it at an empty per-test tmp dir so every test resolves the bundled
    resource deterministically.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.delenv("OMODEL_SUGGESTIONS", raising=False)


@pytest.fixture(autouse=True)
def _isolate_omodel_config(tmp_path, monkeypatch):
    """Redirect the DEFAULT config location to a fresh per-test tmp dir.

    Real-config safety (the hard rule) currently holds only because every test remembers to
    pass an explicit temp `path`/`--config`. This is the equivalent net for config that
    `_isolate_omodel_cache` above is for the cache: config_path() prefers $XDG_CONFIG_HOME over
    ~/.config when set, so redirecting it here means even a future test that forgets an
    explicit path can only ever scaffold/write under this tmp dir, never the real
    ~/.config/opencode/oh-my-openagent.jsonc. Tests that monkeypatch XDG_CONFIG_HOME themselves
    set it later in the test body, so theirs overrides this — which is fine.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
