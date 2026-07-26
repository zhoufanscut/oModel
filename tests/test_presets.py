"""test_presets.py — the 3 named presets (DESIGN §presets.py, decision #17).

Unit half of §Verification check #9; the Pilot half lives in test_app_pilot.py.

The contracts that dominate this file:
  * READ is best-effort — missing / unreadable / malformed / wrong-version / short-or-long list,
    a well-formed entry whose agents/categories is null or a scalar, and an `active` that is out
    of range or points at an empty entry, must all degrade and NEVER raise. A hand-mangled
    presets file must not stop you editing models.
  * WRITE is loud — `write()` raises on failure so app.py can notify. A preset that didn't land
    must never look like it did.
  * `write()` is the ONLY function here that touches disk (app.py calls it from `s` alone,
    alongside the config write). That is what keeps "the config equals the active preset" true.

Real-config safety: every test writes under tmp_path only.
"""
from __future__ import annotations

import json
import os

import pytest

from omodel import presets


def _cfg_path(tmp_path) -> str:
    return str(tmp_path / "oh-my-openagent.jsonc")


def _write_raw(tmp_path, text: str) -> str:
    path = _cfg_path(tmp_path)
    with open(presets.presets_path(path), "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


SAMPLE_CFG = {
    "agents": {
        "sisyphus": {"model": "zhipuai/glm-5.1", "ultrawork": {"model": "opencode/x"}},
        "oracle": {"model": "openai/gpt-5.5", "variant": "high"},
    },
    "categories": {"deep": {"model": "openai/gpt-5.5"}},
}


def _sample(name: str = "daily-cheap") -> presets.Preset:
    return presets.capture(name, SAMPLE_CFG)


def _store_of(*names) -> presets.Store:
    store = presets.Store()
    for i, name in enumerate(names):
        store.presets[i] = _sample(name) if name else None
    return presets.normalize_active(store)


class TestLocation:
    def test_sits_next_to_the_active_config(self, tmp_path):
        # Presets FOLLOW the config file — a --config override gets its own set, which is what
        # keeps the real-config safety rule satisfiable without a new env override.
        assert presets.presets_path(_cfg_path(tmp_path)) == str(tmp_path / ".omodel-presets.json")

    def test_two_configs_have_independent_presets(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        presets.write(str(a / "c.jsonc"), _store_of("in-a"))
        assert [p.name for p in presets.load(str(a / "c.jsonc")).presets if p] == ["in-a"]
        assert presets.load(str(b / "c.jsonc")).is_empty()


class TestReadIsBestEffort:
    def test_missing_file(self, tmp_path):
        store = presets.load(_cfg_path(tmp_path))
        assert store.is_empty() and len(store.presets) == presets.PRESET_COUNT
        assert store.current() is None

    def test_corrupt_json(self, tmp_path):
        assert presets.load(_write_raw(tmp_path, "{not json at all,,,")).is_empty()

    def test_non_dict_root(self, tmp_path):
        assert presets.load(_write_raw(tmp_path, "[1, 2, 3]")).is_empty()

    def test_wrong_version(self, tmp_path):
        raw = json.dumps({"version": 99, "presets": [{"name": "x"}, None, None]})
        assert presets.load(_write_raw(tmp_path, raw)).is_empty()

    def test_presets_not_a_list(self, tmp_path):
        raw = json.dumps({"version": presets.FILE_VERSION, "presets": {"0": {"name": "x"}}})
        assert presets.load(_write_raw(tmp_path, raw)).is_empty()

    def test_short_list_is_padded_and_long_list_truncated(self, tmp_path):
        short = json.dumps({"version": presets.FILE_VERSION, "presets": [{"name": "only"}]})
        loaded = presets.load(_write_raw(tmp_path, short)).presets
        assert len(loaded) == 3
        assert loaded[0].name == "only" and loaded[1] is None and loaded[2] is None

        long = json.dumps(
            {"version": presets.FILE_VERSION, "presets": [{"name": str(i)} for i in range(9)]}
        )
        assert [p.name for p in presets.load(_write_raw(tmp_path, long)).presets] == ["0", "1", "2"]

    @pytest.mark.parametrize("bad", [None, 5, "nope", [1, 2]])
    def test_non_dict_subtrees_coerce_to_empty(self, tmp_path, bad):
        raw = json.dumps(
            {
                "version": presets.FILE_VERSION,
                "presets": [{"name": "x", "agents": bad, "categories": bad}, None, None],
            }
        )
        loaded = presets.load(_write_raw(tmp_path, raw)).presets[0]
        assert loaded.agents == {} and loaded.categories == {}

    def test_garbage_entry_types_and_fields(self, tmp_path):
        raw = json.dumps(
            {
                "version": presets.FILE_VERSION,
                "presets": ["a string entry", {"name": 42, "saved_at": []}, None],
            }
        )
        loaded = presets.load(_write_raw(tmp_path, raw)).presets
        assert loaded[0] is None  # non-dict entry reads as empty
        assert loaded[1].name == "" and loaded[1].saved_at == ""

    def test_unreadable_file_is_a_miss_not_a_crash(self, tmp_path):
        path = _cfg_path(tmp_path)
        os.mkdir(presets.presets_path(path))  # a DIRECTORY where the file should be
        assert presets.load(path).is_empty()


class TestActiveIsAlwaysReal:
    """`Store.current()` is None only for a genuinely empty store — app.py never has to handle
    'active points at nothing', which is what makes the config-equals-active invariant checkable."""

    @pytest.mark.parametrize("bad", [7, -1, None, "0", 1.5])
    def test_out_of_range_active_falls_back(self, tmp_path, bad):
        raw = json.dumps(
            {
                "version": presets.FILE_VERSION,
                "active": bad,
                "presets": [None, {"name": "second"}, None],
            }
        )
        store = presets.load(_write_raw(tmp_path, raw))
        assert store.active == 1 and store.current().name == "second"

    def test_active_pointing_at_an_empty_entry_falls_back(self, tmp_path):
        raw = json.dumps(
            {
                "version": presets.FILE_VERSION,
                "active": 0,
                "presets": [None, None, {"name": "third"}],
            }
        )
        store = presets.load(_write_raw(tmp_path, raw))
        assert store.active == 2 and store.current().name == "third"

    def test_empty_store_has_no_current(self):
        assert presets.Store().current() is None

    def test_normalize_keeps_a_valid_active(self):
        store = _store_of("a", "b", "c")
        store.active = 2
        assert presets.normalize_active(store).active == 2

    def test_active_round_trips(self, tmp_path):
        path = _cfg_path(tmp_path)
        store = _store_of("a", "b")
        store.active = 1
        assert presets.write(path, store).active == 1
        assert presets.load(path).active == 1


class TestWriteIsLoud:
    def test_round_trip(self, tmp_path):
        path = _cfg_path(tmp_path)
        returned = presets.write(path, _store_of(None, "daily-cheap"))
        assert [p and p.name for p in returned.presets] == [None, "daily-cheap", None]
        # The returned store IS what is on disk (read back, not the in-memory copy).
        assert [p and p.name for p in presets.load(path).presets] == [None, "daily-cheap", None]
        stored = returned.presets[1]
        assert stored.agents["sisyphus"]["model"] == "zhipuai/glm-5.1"
        assert stored.categories["deep"]["model"] == "openai/gpt-5.5"
        assert stored.saved_at.endswith("Z")

    def test_write_replaces_the_whole_file(self, tmp_path):
        # One write rule: app.py hands over the entire store, so a delete is just an entry that
        # is now None — there is no per-entry mutator that could half-apply.
        path = _cfg_path(tmp_path)
        presets.write(path, _store_of("a", "b", "c"))
        presets.write(path, _store_of("a", None, "c"))
        assert [p and p.name for p in presets.load(path).presets] == ["a", None, "c"]
        assert _read_json(presets.presets_path(path))["presets"][1] is None

    def test_write_failure_raises_and_cleans_up(self, tmp_path):
        path = _cfg_path(tmp_path)
        os.mkdir(presets.presets_path(path))  # os.replace onto a directory fails
        with pytest.raises(OSError):
            presets.write(path, _store_of("a"))
        # No stray temp file left behind next to the config.
        assert [n for n in os.listdir(tmp_path) if ".tmp-" in n] == []

    def test_file_shape(self, tmp_path):
        path = _cfg_path(tmp_path)
        presets.write(path, _store_of("a"))
        data = _read_json(presets.presets_path(path))
        assert data["version"] == presets.FILE_VERSION
        assert data["active"] == 0
        assert len(data["presets"]) == presets.PRESET_COUNT


class TestSeedAndMatch:
    def test_seeded_captures_the_config_and_activates_it(self):
        store = presets.seeded(SAMPLE_CFG)
        assert store.active == 0
        assert store.current().name == presets.DEFAULT_NAME
        assert store.current().agents["oracle"]["model"] == "openai/gpt-5.5"
        assert store.presets[1] is None and store.presets[2] is None

    def test_seeded_writes_nothing(self, tmp_path):
        # One write rule: the seed is in-memory until the first save materializes it.
        presets.seeded(SAMPLE_CFG)
        assert not os.path.exists(presets.presets_path(_cfg_path(tmp_path)))

    def test_seeded_of_an_empty_config(self):
        store = presets.seeded({})
        assert store.current().agents == {} and store.current().categories == {}

    def test_matching_index_finds_the_config(self):
        store = _store_of("a")
        assert presets.matching_index(store, SAMPLE_CFG) == 0

    def test_matching_index_is_none_when_the_config_drifted(self):
        store = _store_of("a")
        drifted = {"agents": {"sisyphus": {"model": "someone/else"}}, "categories": {}}
        assert presets.matching_index(store, drifted) is None

    def test_matching_index_ignores_empty_entries(self):
        store = presets.Store()
        assert presets.matching_index(store, SAMPLE_CFG) is None

    def test_matching_index_returns_the_first_match(self):
        store = _store_of("a", "b")  # both hold SAMPLE_CFG's assignments
        assert presets.matching_index(store, SAMPLE_CFG) == 0


class TestNoAliasing:
    def test_capture_deep_copies_in(self):
        cfg = {"agents": {"sisyphus": {"model": "a/b"}}, "categories": {}}
        preset = presets.capture("p", cfg)
        cfg["agents"]["sisyphus"]["model"] = "changed/after"
        cfg["agents"]["oracle"] = {"model": "new/one"}
        assert preset.agents == {"sisyphus": {"model": "a/b"}}

    def test_assignments_deep_copies_out(self):
        preset = _sample()
        agents, categories = presets.assignments(preset)
        agents["sisyphus"]["model"] = "mutated/by-the-app"
        categories.clear()
        assert preset.agents["sisyphus"]["model"] == "zhipuai/glm-5.1"
        assert preset.categories["deep"]["model"] == "openai/gpt-5.5"

    def test_load_returns_independent_objects(self, tmp_path):
        path = _cfg_path(tmp_path)
        presets.write(path, _store_of("a"))
        first, second = presets.load(path).presets[0], presets.load(path).presets[0]
        first.agents["sisyphus"]["model"] = "mutated/here"
        assert second.agents["sisyphus"]["model"] == "zhipuai/glm-5.1"


class TestFingerprint:
    def test_key_order_is_not_a_difference(self):
        a = {"sisyphus": {"model": "x/y"}, "oracle": {"model": "p/q"}}
        b = {"oracle": {"model": "p/q"}, "sisyphus": {"model": "x/y"}}
        assert presets.fingerprint(a, {}) == presets.fingerprint(b, {})

    def test_empty_sub_object_reads_as_absent(self):
        # config_io drops a model-less ultrawork/compaction on save, so the two states are
        # byte-identical on disk — "does the config still match a preset?" must agree.
        with_empty = {"sisyphus": {"model": "x/y", "compaction": {}}}
        without = {"sisyphus": {"model": "x/y"}}
        assert presets.fingerprint(with_empty, {}) == presets.fingerprint(without, {})

    def test_a_filled_sub_object_does_count(self):
        filled = {"sisyphus": {"model": "x/y", "compaction": {"model": "a/b"}}}
        without = {"sisyphus": {"model": "x/y"}}
        assert presets.fingerprint(filled, {}) != presets.fingerprint(without, {})

    def test_real_differences_are_seen(self):
        assert presets.fingerprint({"a": {"model": "x/y"}}, {}) != presets.fingerprint(
            {"a": {"model": "x/z"}}, {}
        )
        assert presets.fingerprint({}, {"deep": {"model": "x/y"}}) != presets.fingerprint({}, {})

    @pytest.mark.parametrize("bad", [None, 5, "nope"])
    def test_non_dict_inputs_do_not_raise(self, bad):
        assert presets.fingerprint(bad, bad) == presets.fingerprint({}, {})


class TestStoreFingerprint:
    """Dirtiness for the presets half of a save: app.py compares this against the launch/last-save
    baseline to know whether `s` has anything to persist."""

    def test_saved_at_alone_is_not_a_change(self):
        a = _store_of("x")
        b = _store_of("x")
        b.presets[0].saved_at = "1999-01-01T00:00:00Z"
        assert presets.store_fingerprint(a) == presets.store_fingerprint(b)

    def test_active_is_a_change(self):
        a = _store_of("x", "y")
        b = _store_of("x", "y")
        b.active = 1
        assert presets.store_fingerprint(a) != presets.store_fingerprint(b)

    def test_rename_is_a_change(self):
        assert presets.store_fingerprint(_store_of("x")) != presets.store_fingerprint(
            _store_of("renamed")
        )

    def test_content_is_a_change(self):
        a = _store_of("x")
        b = _store_of("x")
        b.presets[0].agents["sisyphus"]["model"] = "other/model"
        assert presets.store_fingerprint(a) != presets.store_fingerprint(b)

    def test_delete_is_a_change(self):
        assert presets.store_fingerprint(_store_of("x", "y")) != presets.store_fingerprint(
            _store_of("x")
        )

    def test_round_trip_through_disk_is_not_a_change(self, tmp_path):
        # The baseline is compared across a write; a save must not leave the app looking dirty.
        path = _cfg_path(tmp_path)
        store = _store_of("x", "y")
        assert presets.store_fingerprint(presets.write(path, store)) == presets.store_fingerprint(
            store
        )


class TestNameAndCount:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  daily cheap  ", "daily cheap"),
            ("a\nb", "a b"),  # a newline would render as a SECOND row and break the 5-line card
            ("tab\there", "tab here"),
            ("multi   spaces", "multi spaces"),
            ("", "preset 1"),
            ("   ", "preset 1"),
            ("\n\t", "preset 1"),
        ],
    )
    def test_sanitize(self, raw, expected):
        assert presets.sanitize_name(raw, 0) == expected

    def test_empty_falls_back_to_the_one_based_index(self):
        assert presets.sanitize_name("", 2) == "preset 3"

    def test_length_cap(self):
        assert len(presets.sanitize_name("x" * 200, 0)) == presets.MAX_NAME

    def test_model_count_covers_agents_subtargets_and_categories(self):
        preset = presets.capture(
            "p",
            {
                "agents": {
                    "sisyphus": {
                        "model": "a/b",
                        "ultrawork": {"model": "c/d"},
                        "compaction": {},  # model-less: not a set model
                    },
                    "oracle": {},  # present but unset
                },
                "categories": {"deep": {"model": "e/f"}, "quick": {}},
            },
        )
        assert presets.model_count(preset) == 3

    def test_model_count_of_an_empty_preset(self):
        assert presets.model_count(presets.capture("p", {})) == 0


class TestNeverTouchesTheRealConfig:
    def test_paths_stay_under_tmp(self, tmp_path):
        # Belt-and-braces for the hard rule: everything this module writes is derived from the
        # config path it is handed. NB: assert the real file is UNCHANGED, not that it is
        # absent — a developer running this suite may legitimately have presets of their own,
        # and "absent" would fail for the wrong reason.
        real = os.path.expanduser("~/.config/opencode/.omodel-presets.json")
        before = os.path.getmtime(real) if os.path.exists(real) else None

        path = _cfg_path(tmp_path)
        presets.write(path, _store_of("a"))
        written = presets.presets_path(path)
        assert written.startswith(str(tmp_path))
        assert os.path.exists(written)

        after = os.path.getmtime(real) if os.path.exists(real) else None
        assert after == before, "the real presets file must not be created or touched"


class TestRenderSafety:
    """Textual parses plain strings as content markup, so a name containing a tag-like run
    crashed the compositor — and a preset name is PERSISTED, so it crashed every launch after."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("[/b]", "/b"), ("[b]bold", "bbold"), ("a[1]b", "a1b"), ("plain", "plain"), ("[]", "preset 1")],
    )
    def test_sanitize_strips_brackets(self, raw, expected):
        assert presets.sanitize_name(raw, 0) == expected

    def test_names_read_off_disk_are_stripped_too(self, tmp_path):
        # A hand-edited sidecar must not be able to take the app down.
        raw = json.dumps(
            {"version": presets.FILE_VERSION, "presets": [{"name": "[/b]x"}, None, None]}
        )
        assert presets.load(_write_raw(tmp_path, raw)).presets[0].name == "/bx"


class TestCorruptFileIsPreserved:
    """`load()` is best-effort, so an unreadable sidecar reads as empty and the app seeds a fresh
    store — at which point a save would clobber presets the app never saw."""

    def test_unparseable_file_is_moved_aside_before_the_write(self, tmp_path):
        path = _cfg_path(tmp_path)
        sidecar = presets.presets_path(path)
        with open(sidecar, "w", encoding="utf-8") as f:
            f.write('{"version": 1, "presets": [ THIS IS BROKEN')
        presets.write(path, _store_of("fresh"))
        assert os.path.exists(sidecar + ".corrupt"), "the unreadable file must be kept"
        with open(sidecar + ".corrupt", encoding="utf-8") as f:
            assert "THIS IS BROKEN" in f.read()
        assert [p and p.name for p in presets.load(path).presets] == ["fresh", None, None]

    def test_a_readable_file_is_not_moved_aside(self, tmp_path):
        path = _cfg_path(tmp_path)
        presets.write(path, _store_of("first"))
        presets.write(path, _store_of("second"))
        assert not os.path.exists(presets.presets_path(path) + ".corrupt")

    def test_a_directory_in_the_way_is_left_alone(self, tmp_path):
        # Only a regular FILE is ours to move; a directory stays put and the write fails loudly.
        path = _cfg_path(tmp_path)
        os.mkdir(presets.presets_path(path))
        with pytest.raises(OSError):
            presets.write(path, _store_of("a"))
        assert os.path.isdir(presets.presets_path(path))
        assert not os.path.exists(presets.presets_path(path) + ".corrupt")
