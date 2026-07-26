"""test_presets.py — the named presets (DESIGN §presets.py, decision #17).

Unit half of §Verification check #9; the Pilot half lives in test_app_pilot.py.

The contracts that dominate this file:
  * READ is best-effort — missing / unreadable / malformed / wrong-version, a well-formed entry
    whose agents/categories is null or a scalar, and an out-of-range `active`, must all degrade
    and NEVER raise. A hand-mangled presets file must not stop you editing models.
  * READ also MIGRATES the original fixed-3 shape: `null` holes are dropped and `active` follows
    the preset it named into the compacted list. The list is now DENSE and unbounded.
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
    """A dense store of the named presets — `None` names are skipped, not stored as holes."""
    return presets.normalize_active(
        presets.Store(presets=[_sample(n) for n in names if n])
    )


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
        assert [p.name for p in presets.load(str(a / "c.jsonc")).presets] == ["in-a"]
        assert presets.load(str(b / "c.jsonc")).is_empty()


class TestReadIsBestEffort:
    def test_missing_file(self, tmp_path):
        store = presets.load(_cfg_path(tmp_path))
        assert store.is_empty() and store.presets == []
        assert store.current() is None

    def test_corrupt_json(self, tmp_path):
        assert presets.load(_write_raw(tmp_path, "{not json at all,,,")).is_empty()

    def test_non_dict_root(self, tmp_path):
        assert presets.load(_write_raw(tmp_path, "[1, 2, 3]")).is_empty()

    @pytest.mark.parametrize("version", [0, 3, 99, "1", None])
    def test_unknown_version(self, tmp_path, version):
        # Only FILE_VERSION and the documented legacy versions are read; anything else (INCLUDING
        # a future one, which is the point) degrades to empty rather than being misinterpreted.
        raw = json.dumps({"version": version, "presets": [{"name": "x"}]})
        assert presets.load(_write_raw(tmp_path, raw)).is_empty()

    @pytest.mark.parametrize("version", [1, 2])
    def test_supported_versions_load(self, tmp_path, version):
        raw = json.dumps({"version": version, "presets": [{"name": "x"}]})
        assert [p.name for p in presets.load(_write_raw(tmp_path, raw)).presets] == ["x"]

    def test_presets_not_a_list(self, tmp_path):
        raw = json.dumps({"version": presets.FILE_VERSION, "presets": {"0": {"name": "x"}}})
        assert presets.load(_write_raw(tmp_path, raw)).is_empty()

    def test_any_length_loads_whole(self, tmp_path):
        # No cap and no padding: what the file holds is what you get.
        short = json.dumps({"version": presets.FILE_VERSION, "presets": [{"name": "only"}]})
        assert [p.name for p in presets.load(_write_raw(tmp_path, short)).presets] == ["only"]

        long = json.dumps(
            {"version": presets.FILE_VERSION, "presets": [{"name": str(i)} for i in range(9)]}
        )
        loaded = presets.load(_write_raw(tmp_path, long)).presets
        assert [p.name for p in loaded] == [str(i) for i in range(9)]

    @pytest.mark.parametrize("bad", [None, 5, "nope", [1, 2]])
    def test_non_dict_subtrees_coerce_to_empty(self, tmp_path, bad):
        raw = json.dumps(
            {
                "version": presets.FILE_VERSION,
                "presets": [{"name": "x", "agents": bad, "categories": bad}],
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
        # A non-dict entry and a `null` are both dropped by compaction, not kept as holes.
        assert len(loaded) == 1
        assert loaded[0].name == "" and loaded[0].saved_at == ""

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
                "presets": [{"name": "first"}, {"name": "second"}],
            }
        )
        store = presets.load(_write_raw(tmp_path, raw))
        assert store.active == 0 and store.current().name == "first"

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


class TestLegacyThreeSlotFileMigrates:
    """Files written before presets went unlimited hold exactly three entries, `null` for an
    empty slot. They must load as the presets they actually held — dropping a preset, or landing
    the user on the wrong one, would be losing their work to a refactor."""

    def _legacy(self, tmp_path, entries, active):
        raw = json.dumps(
            {"version": presets.FILE_VERSION, "active": active, "presets": entries}
        )
        return presets.load(_write_raw(tmp_path, raw))

    def test_holes_are_dropped(self, tmp_path):
        store = self._legacy(tmp_path, [{"name": "a"}, None, None], 0)
        assert [p.name for p in store.presets] == ["a"]
        assert store.active == 0

    def test_active_follows_its_preset_across_the_gap(self, tmp_path):
        # Slot 3 was active; with slot 2 empty and gone, that preset is now index 1.
        store = self._legacy(tmp_path, [{"name": "a"}, None, {"name": "c"}], 2)
        assert [p.name for p in store.presets] == ["a", "c"]
        assert store.current().name == "c"

    def test_a_leading_hole_shifts_everything_down(self, tmp_path):
        store = self._legacy(tmp_path, [None, {"name": "b"}, {"name": "c"}], 1)
        assert [p.name for p in store.presets] == ["b", "c"]
        assert store.current().name == "b"

    def test_all_holes_reads_as_empty(self, tmp_path):
        assert self._legacy(tmp_path, [None, None, None], 0).is_empty()

    def test_active_pointing_AT_a_hole_falls_back(self, tmp_path):
        # The old shape allowed `active` to name an empty slot; it fell back to the first real
        # preset, and must still.
        store = self._legacy(tmp_path, [None, {"name": "b"}, {"name": "c"}], 0)
        assert store.current().name == "b"

    def test_a_version_2_file_needs_no_migration(self, tmp_path):
        raw = json.dumps(
            {"version": 2, "active": 1, "presets": [{"name": "a"}, {"name": "b"}]}
        )
        store = presets.load(_write_raw(tmp_path, raw))
        assert [p.name for p in store.presets] == ["a", "b"] and store.current().name == "b"

    def test_the_next_write_is_dense(self, tmp_path):
        path = _cfg_path(tmp_path)
        raw = json.dumps(
            {"version": presets.FILE_VERSION, "active": 2,
             "presets": [{"name": "a"}, None, {"name": "c"}]}
        )
        _write_raw(tmp_path, raw)
        presets.write(path, presets.load(path))
        assert _read_json(presets.presets_path(path))["presets"] == [
            {"name": "a", "saved_at": "", "agents": {}, "categories": {}},
            {"name": "c", "saved_at": "", "agents": {}, "categories": {}},
        ]


class TestWriteIsLoud:
    def test_round_trip(self, tmp_path):
        path = _cfg_path(tmp_path)
        returned = presets.write(path, _store_of("daily-cheap"))
        assert [p.name for p in returned.presets] == ["daily-cheap"]
        # The returned store IS what is on disk (read back, not the in-memory copy).
        assert [p.name for p in presets.load(path).presets] == ["daily-cheap"]
        stored = returned.presets[0]
        assert stored.agents["sisyphus"]["model"] == "zhipuai/glm-5.1"
        assert stored.categories["deep"]["model"] == "openai/gpt-5.5"
        assert stored.saved_at.endswith("Z")

    def test_write_replaces_the_whole_file(self, tmp_path):
        # One write rule: app.py hands over the entire store, so a delete is just a shorter list
        # — there is no per-entry mutator that could half-apply.
        path = _cfg_path(tmp_path)
        presets.write(path, _store_of("a", "b", "c"))
        presets.write(path, _store_of("a", "c"))
        assert [p.name for p in presets.load(path).presets] == ["a", "c"]
        assert len(_read_json(presets.presets_path(path))["presets"]) == 2

    def test_write_takes_as_many_as_you_keep(self, tmp_path):
        # No cap: the card scrolls, the file just gets longer.
        path = _cfg_path(tmp_path)
        names = [f"p{i}" for i in range(25)]
        presets.write(path, _store_of(*names))
        assert [p.name for p in presets.load(path).presets] == names

    def test_write_failure_raises_and_cleans_up(self, tmp_path):
        path = _cfg_path(tmp_path)
        os.mkdir(presets.presets_path(path))  # os.replace onto a directory fails
        with pytest.raises(OSError):
            presets.write(path, _store_of("a"))
        # No stray temp file left behind next to the config.
        assert [n for n in os.listdir(tmp_path) if ".tmp-" in n] == []

    def test_writes_the_current_version_not_the_legacy_one(self, tmp_path):
        """A legacy file loads, but what we write back is FILE_VERSION. The bump exists for the
        DOWNGRADE: a build predating unlimited presets accepts a version-1 file, truncates it to
        3 and its next save drops the rest with no copy kept. An unknown version makes that build
        read empty AND preserve the file as `.corrupt` instead."""
        path = _cfg_path(tmp_path)
        raw = json.dumps(
            {"version": 1, "active": 0, "presets": [{"name": "a"}, None, {"name": "c"}]}
        )
        _write_raw(tmp_path, raw)
        presets.write(path, presets.load(path))
        data = _read_json(presets.presets_path(path))
        assert data["version"] == presets.FILE_VERSION == 2
        assert [p["name"] for p in data["presets"]] == ["a", "c"]

    def test_file_shape(self, tmp_path):
        path = _cfg_path(tmp_path)
        presets.write(path, _store_of("a"))
        data = _read_json(presets.presets_path(path))
        assert data["version"] == presets.FILE_VERSION
        assert data["active"] == 0
        assert len(data["presets"]) == 1
        assert None not in data["presets"], "the on-disk list is dense — no holes"


class TestSeedAndMatch:
    def test_seeded_captures_the_config_and_activates_it(self):
        store = presets.seeded(SAMPLE_CFG)
        assert store.active == 0
        assert store.current().name == presets.DEFAULT_NAME
        assert store.current().agents["oracle"]["model"] == "openai/gpt-5.5"
        assert len(store.presets) == 1, "the seed is ONE preset; you add the rest"

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

    def test_matching_index_of_an_empty_store(self):
        assert presets.matching_index(presets.Store(), SAMPLE_CFG) is None

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
            ("a\nb", "a b"),  # a newline would render as a SECOND row in the card
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
        raw = json.dumps({"version": presets.FILE_VERSION, "presets": [{"name": "[/b]x"}]})
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
        assert [p.name for p in presets.load(path).presets] == ["fresh"]

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
