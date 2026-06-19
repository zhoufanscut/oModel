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
