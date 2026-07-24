"""test_cache.py — on-disk cache for the opencode subprocess output. DESIGN.md §cache.py.

The autouse `_isolate_omodel_cache` fixture in tests/conftest.py already redirects
$OMODEL_CACHE_DIR to a fresh per-test tmp dir, so these tests exercise cache.py directly
without ever touching the real ~/.cache/omodel.
"""
from __future__ import annotations

import json
import os
import time

from omodel import cache

# ---------------------------------------------------------------------------
# read() — miss cases
# ---------------------------------------------------------------------------

class TestReadMiss:

    def test_missing_file_is_miss(self):
        assert cache.read("does-not-exist") is None

    def test_corrupt_json_is_miss(self):
        path = cache._path_for("bad")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        assert cache.read("bad") is None

    def test_wrong_version_is_miss(self):
        cache.write("v", "stdout-data")
        path = cache._path_for("v")
        with open(path, encoding="utf-8") as f:
            blob = json.load(f)
        blob["version"] = cache.CACHE_VERSION + 1
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blob, f)
        assert cache.read("v") is None

    def test_expired_ttl_is_miss(self):
        cache.write("old", "stdout-data")
        path = cache._path_for("old")
        with open(path, encoding="utf-8") as f:
            blob = json.load(f)
        blob["fetched_at"] = time.time() - (cache._TTL_DEFAULT + 3600)  # well past the 24h TTL
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blob, f)
        assert cache.read("old") is None


# ---------------------------------------------------------------------------
# read() — hit case
# ---------------------------------------------------------------------------

class TestReadHit:

    def test_fresh_entry_returns_exact_stdout(self):
        cache.write("k", "hello world stdout")
        assert cache.read("k") == "hello world stdout"


# ---------------------------------------------------------------------------
# $OMODEL_CACHE_TTL override
# ---------------------------------------------------------------------------

class TestTTLOverride:

    def test_env_ttl_override_honored(self, monkeypatch):
        """A short override TTL expires an entry the default 24h TTL would still serve."""
        cache.write("k", "data")
        path = cache._path_for("k")
        with open(path, encoding="utf-8") as f:
            blob = json.load(f)
        blob["fetched_at"] = time.time() - 10  # 10s old
        with open(path, "w", encoding="utf-8") as f:
            json.dump(blob, f)

        monkeypatch.setenv("OMODEL_CACHE_TTL", "5")
        assert cache.read("k") is None

        monkeypatch.setenv("OMODEL_CACHE_TTL", "3600")
        assert cache.read("k") == "data"

    def test_malformed_ttl_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("OMODEL_CACHE_TTL", "not-a-number")
        assert cache._ttl_default() == cache._TTL_DEFAULT

    def test_malformed_ttl_env_does_not_break_read(self, monkeypatch):
        """A garbage $OMODEL_CACHE_TTL must not raise; read() falls back to the 24h default,
        so a just-written entry is still a hit."""
        cache.write("k", "data")
        monkeypatch.setenv("OMODEL_CACHE_TTL", "not-a-number")
        assert cache.read("k") == "data"


# ---------------------------------------------------------------------------
# write() — best-effort, never propagates OSError
# ---------------------------------------------------------------------------

class TestWriteSwallowsOSError:

    def test_os_replace_failure_is_swallowed(self, monkeypatch):
        def _raise(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", _raise)

        cache.write("k", "data")  # must not raise

        assert cache.read("k") is None


# ---------------------------------------------------------------------------
# age_seconds()
# ---------------------------------------------------------------------------

class TestAgeSeconds:

    def test_fresh_write_is_near_zero(self):
        cache.write("k", "data")
        age = cache.age_seconds("k")
        assert age is not None
        assert 0 <= age < 5

    def test_missing_key_is_none(self):
        assert cache.age_seconds("nope") is None


# ---------------------------------------------------------------------------
# clear()
# ---------------------------------------------------------------------------

class TestClear:

    def test_removes_json_and_orphaned_tmp_but_leaves_others(self):
        d = cache.cache_dir()
        os.makedirs(d, exist_ok=True)
        cache.write("a", "data-a")
        cache.write("b", "data-b")

        # Orphaned atomic-write temp file (crash between open() and os.replace()).
        orphan = os.path.join(d, "c.json.tmp-99999")
        with open(orphan, "w", encoding="utf-8") as f:
            f.write("{}")

        # A file cache.py does not own — must survive clear().
        other = os.path.join(d, "README.txt")
        with open(other, "w", encoding="utf-8") as f:
            f.write("not ours")

        cache.clear()

        remaining = set(os.listdir(d))
        assert remaining == {"README.txt"}
