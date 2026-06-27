"""test_history.py — unit tests for the in-session undo/redo stack (history.py).

Pure-data, no Textual. Covers change detection (no-op pushes), deep-copy isolation in BOTH
directions, undo/redo cursor movement + labels, redo-tail truncation, and the memory cap.
"""
from __future__ import annotations

from omodel.history import History


class TestBasics:
    def test_initial_state_has_no_undo_or_redo(self):
        h = History({"agents": {}})
        assert h.can_undo is False
        assert h.can_redo is False
        assert h.undo_label is None
        assert h.redo_label is None
        assert len(h) == 1

    def test_push_enables_undo(self):
        h = History({"x": 1})
        assert h.push({"x": 2}, "set x=2") is True
        assert h.can_undo is True
        assert h.can_redo is False
        assert h.undo_label == "set x=2"
        assert len(h) == 2

    def test_push_noop_when_unchanged(self):
        h = History({"x": 1})
        assert h.push({"x": 1}, "noop") is False  # structurally equal → no entry
        assert h.can_undo is False
        assert len(h) == 1

    def test_push_noop_is_order_independent(self):
        # dict == is order-independent, so a reordered-but-equal cfg is not a change.
        h = History({"a": 1, "b": 2})
        assert h.push({"b": 2, "a": 1}, "reordered") is False
        assert len(h) == 1

    def test_structural_change_is_recorded(self):
        # An added empty sub-object (the app's empty ultrawork/compaction case) IS a change
        # to the cfg object even though it would serialize away — it must be undoable.
        h = History({"agents": {"s": {"model": "m"}}})
        assert h.push({"agents": {"s": {"model": "m", "ultrawork": {}}}}, "add sub") is True
        assert h.can_undo is True


class TestUndoRedo:
    def test_undo_returns_prior_state_and_undone_label(self):
        h = History({"x": 1})
        h.push({"x": 2}, "set x=2")
        result = h.undo()
        assert result is not None
        state, label = result
        assert state == {"x": 1}
        assert label == "set x=2"      # label of the op being reverted
        assert h.can_undo is False
        assert h.can_redo is True
        assert h.redo_label == "set x=2"

    def test_redo_reapplies(self):
        h = History({"x": 1})
        h.push({"x": 2}, "set x=2")
        h.undo()
        state, label = h.redo()
        assert state == {"x": 2}
        assert label == "set x=2"
        assert h.can_redo is False

    def test_undo_at_bottom_returns_none(self):
        assert History({"x": 1}).undo() is None

    def test_redo_at_top_returns_none(self):
        h = History({"x": 1})
        h.push({"x": 2}, "set")
        assert h.redo() is None

    def test_multi_step_undo_redo(self):
        h = History({"n": 0})
        for i in range(1, 4):
            h.push({"n": i}, f"set n={i}")
        assert h.undo()[0] == {"n": 2}
        assert h.undo()[0] == {"n": 1}
        assert h.redo()[0] == {"n": 2}


class TestRedoTailTruncation:
    def test_push_after_undo_drops_redo_tail(self):
        h = History({"n": 0})
        h.push({"n": 1}, "a")
        h.push({"n": 2}, "b")
        h.undo()                       # back to n=1; redo would give n=2
        assert h.can_redo is True
        h.push({"n": 9}, "c")          # new branch → the redo tail (n=2) is discarded
        assert h.can_redo is False
        assert h.redo() is None
        assert h.undo()[0] == {"n": 1}  # undo now returns to n=1, never the dropped n=2


class TestIsolation:
    def test_initial_dict_is_deep_copied(self):
        live = {"x": 1}
        h = History(live)
        live["x"] = 999                # mutate the caller's dict after construction
        assert h.current_state() == {"x": 1}

    def test_pushed_dict_is_deep_copied(self):
        h = History({"x": 0})
        payload = {"x": 1}
        h.push(payload, "set")
        payload["x"] = 999             # mutate the dict handed to push
        assert h.current_state() == {"x": 1}

    def test_returned_state_mutation_does_not_corrupt_history(self):
        h = History({"d": {"k": "v"}})
        h.push({"d": {"k": "v2"}}, "set")
        state, _ = h.undo()
        state["d"]["k"] = "tampered"   # caller mutates the returned snapshot
        assert h.redo()[0] == {"d": {"k": "v2"}}     # stored top still pristine
        h.undo()
        assert h.current_state() == {"d": {"k": "v"}}  # stored bottom still pristine


class TestAux:
    """The optional `aux` companion snapshot (app.py stores _custom_rows there) rides each entry
    so out-of-cfg state moves in lockstep with undo/redo."""

    def test_aux_defaults_to_empty_dict(self):
        h = History({"x": 1})
        assert h.current_aux() == {}                 # no aux stored → {}, not None
        h.push({"x": 2}, "set")                      # push without aux
        assert h.current_aux() == {}

    def test_aux_travels_with_undo_redo(self):
        h = History({"x": 0})
        h.push({"x": 1}, "add", aux={"t": [{"model": "m"}]})
        assert h.current_aux() == {"t": [{"model": "m"}]}
        h.undo()                                     # back to the pre-add entry
        assert h.current_aux() == {}                 # typed row is gone alongside the cfg value
        h.redo()
        assert h.current_aux() == {"t": [{"model": "m"}]}  # and returns on redo

    def test_aux_is_deep_copied_in_and_out(self):
        payload = {"t": [{"model": "m"}]}
        h = History({"x": 0})
        h.push({"x": 1}, "add", aux=payload)
        payload["t"].append({"model": "other"})      # mutate the dict handed to push
        got = h.current_aux()
        got["t"].append({"model": "tampered"})        # mutate the returned snapshot
        assert h.current_aux() == {"t": [{"model": "m"}]}  # stored aux pristine in both directions

    def test_clear_aux_wipes_all_entries(self):
        h = History({"x": 0}, aux={"t": [{"model": "init"}]})
        h.push({"x": 1}, "add", aux={"t": [{"model": "m"}]})
        h.clear_aux()
        assert h.current_aux() == {}
        h.undo()
        assert h.current_aux() == {}                  # earlier entries wiped too
        assert h.current_state() == {"x": 0}          # cfg states + cursor untouched


class TestLimit:
    def test_cap_drops_oldest_and_keeps_cursor_valid(self):
        h = History({"n": 0}, limit=3)
        for i in range(1, 6):          # 5 pushes onto a cap of 3
            h.push({"n": i}, f"n={i}")
        assert len(h) == 3
        assert h.current_state() == {"n": 5}   # newest stays current
        assert h.undo()[0] == {"n": 4}
        assert h.undo()[0] == {"n": 3}
        assert h.undo() is None                # entries older than the window were dropped

    def test_limit_floor_allows_at_least_one_undo(self):
        h = History({"n": 0}, limit=1)         # clamped up to 2
        h.push({"n": 1}, "set")
        assert h.can_undo is True
