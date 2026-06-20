"""In-session undo/redo of config edits.  DESIGN.md §history.py.

A linear snapshot stack of `cfg` states. `app.py` routes EVERY config mutation through it
(see app's `_record` / `_stage_row`), so any operation — set model, clear, variant, add-model,
add sub-target — can be undone with one key (`u`) and re-applied with `ctrl+r`. The point is
mis-press recovery: a fat-fingered `x` (clear) or a wrong pick is one keystroke away from being
reverted.

Pure data, no Textual dependency — unit-tested in isolation (`tests/test_history.py`). The cfg
is plain JSON (dict/list/str/num/bool/None), so snapshots are `copy.deepcopy` (cheap; a config
is small) and fully isolated: the caller may mutate a returned state or a pushed dict without
corrupting history.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class HistoryEntry:
    """One point in the timeline: the cfg snapshot AFTER an operation + a human label for it."""
    state: dict
    label: str


class History:
    """Linear undo/redo stack of cfg snapshots.

    Entry 0 is the initial (loaded) state; each accepted edit appends a snapshot and advances
    the cursor. `undo()` steps the cursor back and returns the prior state plus the label of
    the operation being reverted; `redo()` steps forward. A new edit after an undo truncates
    the redo tail (standard undo semantics). Snapshots are deep-copied on the way IN (push) and
    OUT (current_state/undo/redo), so neither the caller's live cfg nor a previously returned
    state can mutate the stored timeline.
    """

    def __init__(self, initial: dict, label: str = "loaded", limit: int = 200) -> None:
        # limit caps memory for very long sessions; >=2 so there's always room for one undo.
        self._limit = max(2, limit)
        self._entries = [HistoryEntry(copy.deepcopy(initial), label)]
        self._index = 0

    # ----- inspection -------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._entries) - 1

    @property
    def undo_label(self) -> Optional[str]:
        """Label of the operation `undo()` would revert (the entry at the cursor), or None."""
        return self._entries[self._index].label if self.can_undo else None

    @property
    def redo_label(self) -> Optional[str]:
        """Label of the operation `redo()` would re-apply (the next entry), or None."""
        return self._entries[self._index + 1].label if self.can_redo else None

    def current_state(self) -> dict:
        """A deep copy of the state at the cursor — safe for the caller to mutate."""
        return copy.deepcopy(self._entries[self._index].state)

    def matches_current(self, state: dict) -> bool:
        """True if `state` is structurally equal to the snapshot at the cursor — i.e. nothing
        actually changed (dict `==` is deep + order-independent, which is what we want: a no-op
        edit must not create a history entry)."""
        return self._entries[self._index].state == state

    # ----- mutation ---------------------------------------------------------------------

    def push(self, state: dict, label: str) -> bool:
        """Record `state` as a new entry under `label`, dropping any redo tail first. Returns
        False (a no-op) when `state` is unchanged from the current snapshot, so callers can
        push unconditionally after an edit without manufacturing empty entries."""
        if self.matches_current(state):
            return False
        del self._entries[self._index + 1:]  # truncate the redo tail
        self._entries.append(HistoryEntry(copy.deepcopy(state), label))
        self._index = len(self._entries) - 1
        # Cap memory: drop the oldest entries beyond the limit, keeping the cursor valid.
        overflow = len(self._entries) - self._limit
        if overflow > 0:
            del self._entries[:overflow]
            self._index -= overflow
        return True

    def undo(self) -> Optional[Tuple[dict, str]]:
        """Step back one entry. Returns `(restored_state, undone_label)` — the state to load
        and the label of the operation being reverted — or None at the bottom of the stack."""
        if not self.can_undo:
            return None
        undone_label = self._entries[self._index].label
        self._index -= 1
        return self.current_state(), undone_label

    def redo(self) -> Optional[Tuple[dict, str]]:
        """Step forward one entry. Returns `(restored_state, redone_label)` — the state to load
        and the label of the operation being re-applied — or None at the top of the stack."""
        if not self.can_redo:
            return None
        self._index += 1
        return self.current_state(), self._entries[self._index].label
