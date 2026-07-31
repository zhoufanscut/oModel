"""Textual two-pane App.  DESIGN.md §Textual two-pane contract / §Layout.

FROZEN CONTRACT — owned by the TUI specialist. Implements the App class `OModelApp` and the
module entrypoint `run_app` (== `create_app(config_path).run()` — `create_app` is the testable
construction half, building the session without launching the Textual event loop).

The editable state is NOT owned here: `self.session` (session.py) holds cfg / catalog /
suggestions / resolver / the presets store and performs every cfg mutation and every write,
because `cli.py` edits through the same object — the TUI and the agent surface must not drift
into two answers for "what may I set here?" or "what does a save write?". What stays in this
module is everything that is only meaningful inside a running UI: the undo `History`, the
per-target row cache, `_custom_rows`, and all rendering. `cfg` / `_store` / `_saved_text` /
`_saved_store_fp` are PROPERTIES onto the session (see §session state).

STABLE WIDGET IDs (pilot tests in tests/test_app_pilot.py depend on these — do not rename):
  * Static#providers      — "oModel: <id · id · …>" from catalog.connected (first-seen);
                            on CatalogUnavailable shows the banner + `r` retry instead.
  * OptionList#targets     — AGENTS then CATEGORIES. Option IDs: 'agent:<name>',
                            'agent:<name>.ultrawork' / '.compaction' (indented sub-rows),
                            'cat:<name>'.
  * OptionList#presets     — the named presets (decision #17 / presets.py), a card under
                            #targets inside Vertical#left that grows with the list and caps at
                            half the column. Option IDs 'preset:0' … 'preset:<n-1>' plus the
                            trailing 'preset:new' (+ add preset…). Rows '● 1 <name>' ('●' = the
                            ACTIVE preset — your edits go into it and `s` publishes it).
                            UNLIMITED and DENSE: no empty rows, and a delete renumbers.
                            Heading is the border TITLE; the highlighted row's summary goes to
                            the border SUBTITLE (never #detail — see _preset_highlighted).
  * Static#detail          — current model/variant + catalog.detail() line. The detail()
                            line is a ~3s opencode subprocess, so it is fetched in a
                            background worker (cached per (provider, model)) and appears when ready —
                            highlighting renders the rest of the pane instantly.
  * OptionList#candidates  — option IDs 'cand:<i>'; LAST row 'cand:add' (+ add model…). The
                            row matching the current assignment (follows your pick) is '● '. If
                            that assignment is off-chain (a custom/hand-set model not in the
                            chain), it's surfaced as its own 'cand:<i>' row just before
                            'cand:add' (see _build_rows), so what's configured is always shown
                            and re-selectable. The highlighted (cursor) row is remembered per
                            target by model identity and restored on re-render, so it survives a
                            target switch and `r` refresh (see _cand_choice / _restore_cand_highlight).
  * Static#hints           — minimal, STATIC key hint bar (LEFT of the bottom row): `s save · q quit · ? help`
                            (see _HINT_BAR). Every other key lives in the `?` help overlay
                            (HelpModal); modals carry their own one-line hint instead.
  * Static#hints-version   — app version `v<version>`, right-aligned at the tail of the bottom row (its
                            own Static in the #hints-bar Horizontal; #hints is width:1fr so it pushes
                            this Static to the right edge).

Each pane is a bordered card; the focused pane (`#targets`/`#candidates`) brightens its border
to `$primary`, while blurred panes and the never-focused `#detail` use a muted `$surface-lighten-3`
border — a theme token, so it tracks the active theme. (Deliberately NOT `$border-blurred`: the
default textual-dark theme resolves that to ~`#191919`, invisible against the `#1E1E1E` surface.)
`#providers`/`#hints`/`#detail` don't focus.

KEYS: ↑↓ (or vim j/k) move within the focused pane · ←/→ (or vim h/l) focus
targets/candidates (gated to the base screen via check_action) · tab / shift+tab cycle all three
panes including #presets (Textual's own Screen traversal — NOT an app binding; the only way into
the presets card, which ←/→ deliberately skip) · enter set (dispatch by row:
cand:add → add-model modal, else set
model + default variant) · v variant · x clear (on an ultrawork/compaction sub-target row: delete
the whole row; on a #candidates row YOU ADDED — a _custom_rows entry, and only with #candidates
FOCUSED — delete that row instead, taking the assignment with it iff the row is what's assigned;
chain rows keep the clear meaning) · a pane-contextual (candidates + targets category
rows → add/edit-model modal; targets agent rows → add sub-target chooser) ·
on a #presets row: enter SWITCHES to that preset — a staged, undoable replace that banks
your edits into the one you leave; a ADDS a preset from the current models + names it +
switches there (row-blind — it always appends, never overwrites what the cursor is on);
r renames it; x deletes it behind a confirm, refused on the active one; v bells. The
trailing `+ add preset…` row (id preset:new) takes enter to do the same as `a` — presets are
unlimited and the list is dense, so a delete renumbers the rest · u undo / ctrl+r redo
(in-session undo of EVERY edit — set/clear/variant/add-model/add-sub/delete-sub — for mis-press recovery;
snapshot stack in history.py, also gated to the base screen) · s save
(diff+confirm) · r refresh (live re-fetch off-thread + rebuild cache; also retries after
CatalogUnavailable) — EXCEPT on a #presets row, where r RENAMES · ? help (open HelpModal — the
base-screen keys, grouped by pane; base-screen-only) ·
q quit (when dirty: a three-way save & quit / discard / cancel — `s` writes the config AND
the presets file, so a discard drops both). In the two BUTTON modals (QuitModal, ConfirmModal)
←/→ and vim h/l walk the button row, wrapping like tab (`app.focus_next`/`focus_previous`) — the
App's own ←/→ are check_action-gated to the base screen, so they are free inside a modal; in
ConfirmModal j/k stay on the scrolling diff body. The hint bar (Static#hints) is minimal and static — only
`s save · q quit · ? help`; every other key is documented in the `?` overlay (HelpModal) and,
where relevant, in per-modal hint lines. (r is also advertised in the Static#providers header.)

Every cfg mutation routes through `_record` (snapshots into `_history`) and dirtiness is computed
by `_is_dirty` (serialize(cfg) vs `_saved_text`, the last-saved/loaded text) rather than a flag —
so undo back to the saved state reads as clean, and an empty sub-object (which serializes away) is
undoable but not dirty. `_restore_state` reloads a snapshot and re-renders both panes.
Add-model modal: a two-phase picker with stable IDs '#add-input' (the Input — accepts typed
text), '#add-candidates' (fuzzy `provider/model` list), '#add-variants' (variant list),
'#add-title', '#add-preview', '#add-hints'. MODEL phase: opens with NO list (type-to-search — the
empty-query browse dump is intentionally not rendered, so open is instant); typing fuzzy-filters
'#add-candidates' from catalog.available (dedicated-first, capped at _MAX_CANDIDATES); ↑↓ (or emacs
Ctrl-P/Ctrl-N) move the list while the Input keeps focus, Tab fills the highlighted pair into the
Input, enter chooses the
highlighted (or, when the list is empty, the validated typed) row. A full provider/model is used verbatim (split on FIRST '/'); a bare id
is auto-prefixed via resolve_prefix if available, else '⚠ unknown — add a provider/' and enter is
BLOCKED until qualified; a typed full id that fuzzy-matches nothing appears as a synthetic "use as
typed" row (a half-typed fragment that still matches falls back to the fuzzy list, no ⚠ row).
VARIANT phase: iff opencode reports variants for the chosen (provider, model)
(catalog.variants_for — cached `--verbose`), pick one or '(none)'; otherwise (kimi/glm-5, or no
cached verbose) it's added immediately (variant None). A "none" opencode may list is dropped as a
duplicate of '(none)' (_is_no_variant) — never offered, never written. GPT-only targets filter the
list to GPT models.
Add-sub (`a` on an agent): an agent that supports more than one sub-kind (only Sisyphus — ultrawork
+ compaction) opens a chooser modal, an OptionList (`#sub-list`, IDs 'sub:ultrawork' /
'sub:compaction') with one row per kind valid for that agent (see session.ULTRAWORK_AGENTS), naming each
kind + what it's for; a kind already on the agent is disabled ('✓ added'); `u`/`c` shortcut or enter
picks one, esc cancels. An agent with a single sub-kind (every non-Sisyphus agent → compaction only)
has no choice, so `a` adds it directly — no modal. Every supported kind present → `a` bells.
"""
from __future__ import annotations

import asyncio
import threading
from typing import ClassVar

from rich.cells import cell_len
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.fuzzy import Matcher
from textual.markup import escape as _escape_markup
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from . import __version__, config_io
from . import catalog as catalog_mod
from . import presets as presets_mod
from . import session as session_mod
from .catalog import Catalog, CatalogUnavailable
from .history import History
from .resolve import Resolver

# The editable state, the guards, and the target-id vocabulary live in session.py now — the
# headless core `cli.py` edits through too, so the TUI and the agent surface can't drift into two
# answers for "what may I set here?". Aliased to their historical private names: the rest of this
# module (and its pilot tests) address them that way, and only the OWNER moved.
from .session import SUBKINDS as _SUBKINDS
from .session import Session
from .session import is_gpt_model as _is_gpt_model
from .session import is_no_variant as _is_no_variant
from .session import subkinds_for as _subkinds_for
from .suggestions import Suggestions

# Cells a preset name may occupy in a row: the 32-wide pane, less its border (2), less
# OptionList's own DEFAULT_CSS `padding: 0 1` (2), less the always-reserved scrollbar gutter (2)
# — MEASURED: scrollable_content_region.width is 26 — less the `● 1 ` prefix (4). Names are
# capped at presets.MAX_NAME CHARACTERS on save; this is the render-time guard for wide (CJK)
# characters, which cost 2 cells each — 22 of them would otherwise wrap every row onto two
# lines, doubling the height of a card that is capped at half the column. Used only as the
# fallback before layout (and adjusted there for a wider row number); _populate_presets prefers
# the widget's measured width, so a padding/CSS change can't silently reintroduce the wrap.
# See _fit_cells.
_PRESET_NAME_CELLS = 22

# Option id of the trailing `+ add preset…` row in `#presets` — a stable id the pilot tests
# address (like `cand:add`, whose idiom it borrows), kept as `preset:new` even though the row now
# reads "add" because the option ids are a contract (module docstring). It is NOT a preset:
# `_preset_index` returns None for it and every caller branches on it explicitly. `a` no longer
# needs the distinction (it appends from any row); `enter` and `x` still do.
PRESET_NEW = "preset:new"

# Stored in a history entry's `aux["active"]` once the preset it named has been deleted. Any
# out-of-range int would do; a named sentinel keeps `_restore_state`'s "was deleted" branch
# readable, and negative can never collide with a real index.
_DELETED = -1


def _retarget_active(active, was: int, now: int):
    """Move a stored active-preset index from `was` to `now`, leaving every other value alone.

    Used by the two actions that change which preset is active WITHOUT pushing a history entry
    (a fork, and a switch between presets holding identical models): the entries recorded on the
    preset you were sitting on have to follow you, or the next `u` quietly moves the `●` back.
    Deliberately NOT `set_aux_key`, which stamps ONE value onto every entry — that erases both
    genuinely different indices (so an older switch could no longer be undone) and the `_DELETED`
    sentinels a prior delete wrote, which is what silently voided the "that preset was deleted"
    warning."""
    return now if active == was else active


def _shift_active(active, removed: int):
    """Remap a stored active-preset index after the preset at `removed` was deleted.

    Presets are a dense list, so deleting one renumbers everything after it. `removed` itself
    becomes `_DELETED` — the entry pointed at a preset that no longer exists, and pretending it
    means the one that slid into that number would move a user's models without a word."""
    if not isinstance(active, int):
        return active
    if active == removed:
        return _DELETED
    return active - 1 if active > removed else active


def _warn_str(warn: list) -> str:
    """Render the candidate-row warn list as trailing ⚠ markers."""
    if not warn:
        return ""
    return "  ⚠ " + " ".join(warn)


# ----- rendering user data safely ---------------------------------------------------------
#
# Textual parses *content markup* in every plain `str` it renders — `Static.update`, a widget's
# initial content, an `Option` prompt, `App.notify`. Almost nothing we render is ours: model and
# provider ids come from `opencode` and from the config, agent/category names are config KEYS,
# preset names and the add-model box are typed by hand. A `[` in any of them is an opening tag,
# and an unmatched close (`acme/[/b]`) raises `MarkupError` *from inside the render pass* — which
# is not catchable at the call site and takes the whole app down. The add-model box made that one
# keystroke away; a config or preset holding such an id made it fatal on every launch.
#
# Two mechanisms, and the widget-level one is preferred:
#   * `markup=False` at construction — a property of the WIDGET, so it covers every present and
#     future `.update()` on it. Used for every Static/Label carrying data (`#detail` excepted).
#   * `_lit()` / `_esc()` below — for the two places a widget flag can't reach: `Option` prompts
#     (`OptionList` has no such flag) and `#detail`, the one widget that renders markup on purpose.


def _lit(text: str) -> Content:
    """`text` as a literal `Content` — a Visual, so Textual renders it verbatim instead of
    parsing it. Every `Option` prompt built from data goes through this."""
    return Content(str(text))


def _esc(text: str) -> str:
    """`text` escaped for a widget that DOES render markup — i.e. `#detail` alone (`[b]` header,
    `[dim]` pending-fetch placeholder). Prefer `markup=False` on the widget where you can: a flag
    can't be forgotten at the next `.update()`, an escape call can."""
    return _escape_markup(str(text))


def _row_label(row: dict) -> str:
    """One-line rendering of a candidate-row dict for OptionList#candidates.
    A same-line substitute (substitute_for set) is suffixed `(≈ omo <id>)` so it reads
    as a stand-in for the model omo actually named."""
    variant = row.get("variant")
    vtext = f" ({variant})" if variant else ""
    sub = row.get("substitute_for")
    subtext = f"  (≈ omo {sub})" if sub else ""
    return f"{row['provider']}/{row['model']}{vtext}{subtext}{_warn_str(row['warn'])}"


async def _to_thread_daemon(func, *args, **kwargs):
    """Like `asyncio.to_thread`, but `func` runs on a `threading.Thread(daemon=True)` instead of
    the loop's default (non-daemon) executor. Non-daemon executor threads are joined at
    interpreter shutdown — since a spawned `opencode` subprocess can't be killed once started,
    that joins `q` (quit) to however long the in-flight `--verbose`/`--refresh` call takes (up to
    20s/90s; a 6s-sleeping stub measured a matching 6s hang on quit). A daemon thread instead lets
    the process exit immediately; the orphaned opencode child finishing on its own afterwards is
    harmless — nothing is left awaiting its result. Used by `_fetch_detail` and
    `_refresh_catalog` in place of `asyncio.to_thread`."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def _set_result(result) -> None:
        # The awaiting worker may have been cancelled (cancelling `future` with it) by the time
        # this callback runs — set_result/set_exception on an already-done future raises
        # InvalidStateError, so skip once that's happened.
        if not future.done():
            future.set_result(result)

    def _set_exception(exc: BaseException) -> None:
        if not future.done():
            future.set_exception(exc)

    def _run() -> None:
        try:
            result = func(*args, **kwargs)
        except Exception as exc:  # propagate to the awaiter, like asyncio.to_thread
            try:
                loop.call_soon_threadsafe(_set_exception, exc)
            except RuntimeError:
                pass  # event loop already closed by delivery time — nothing to deliver to
        else:
            try:
                loop.call_soon_threadsafe(_set_result, result)
            except RuntimeError:
                pass

    threading.Thread(target=_run, daemon=True).start()
    return await future


class VimOptionList(OptionList):
    """OptionList with vim `j`/`k` aliased to cursor down/up (alongside the inherited ↑↓).

    Every list in the app uses this (targets, candidates, and the modal pickers) so j/k move
    the highlight anywhere a list is focused, including inside a modal. Textual merges BINDINGS
    across the MRO, so the parent's ↑↓ / enter / home / end still apply. `h`/`l` are NOT here:
    they cross panes via App-level focus_targets/focus_candidates actions (gated to the base
    screen) — a list inside a modal must not grab the hidden base-screen panes. Printable keys
    only reach these bindings when this list is focused; a focused Input eats j/k as text first
    (so the add-model modal's id field is unaffected)."""

    BINDINGS: ClassVar[list] = [
        Binding("j", "cursor_down", "down", show=False),
        Binding("k", "cursor_up", "up", show=False),
    ]


class AddModelModal(ModalScreen):
    """`a` / cand:add — two-phase model picker: fuzzy `provider/model`, then variant-if-supported.

    MODEL PHASE — the Input (#add-input) filters a fuzzy list (#add-candidates) of the
    `provider/model` pairs you actually have (catalog.available, dedicated-first). The list is
    type-to-search: it opens EMPTY (no browse dump — keeps open instant with hundreds of models)
    and appears only once you type. ↑↓ (or emacs Ctrl-P/Ctrl-N) move the list highlight while the
    Input keeps focus and keeps filtering; Tab fills the highlighted pair
    into the Input (cursor to end); Enter chooses the highlighted pair, or — when the list is empty
    — the validated typed text; Esc cancels. A full `provider/model` you type that fuzzy-matches
    nothing is offered as a synthetic "use as typed" row, so custom / unavailable ids still work; a
    half-typed fragment that still fuzzy-matches just shows those matches (no ⚠ row); a bare unknown
    id yields no row and Enter is a no-op (still blocked). For a GPT-only target
    (Hephaestus) the fuzzy list is filtered to GPT models, and a typed non-GPT id stays blocked.

    VARIANT PHASE — iff opencode reports variants for the chosen (provider, model)
    (catalog.variants_for — the cached `--verbose` map), pick one, or `(none)` ⇒ variant None (a
    fresh add, NOT VariantModal's '' clear sentinel), from #add-variants (a VimOptionList, option
    ids 'var:<v>' / 'var:__none__'). A "none" opencode may list is dropped as a duplicate of the
    synthetic `(none)` (_is_no_variant). A model opencode lists with no (real) variants (kimi,
    glm-5) — or whose verbose isn't cached anywhere — skips this phase and adds immediately. Esc
    returns to the model phase (restores + focuses the Input); the model phase's Esc cancels the modal.

    Dismisses with the staged candidate-row dict (source 'add') on accept, or None on cancel — the
    frozen CONTRACTS.md candidate-row shape (`variant` was always a field; this modal just stops
    forcing it to None).
    """

    BINDINGS: ClassVar[list] = [
        # Only ↑/↓ (+ emacs Ctrl-P/Ctrl-N aliases) and Esc are bound on the screen; the Input keeps
        # h/j/k/l and ←/→ as literal text / cursor moves (do NOT bind those). Tab is intercepted in
        # on_key. In the model phase ↑/↓/Ctrl-P/Ctrl-N bubble from the (un-binding) Input to drive
        # the unfocused #add-candidates; in the variant phase the focused #add-variants handles ↑/↓
        # itself and Ctrl-P/Ctrl-N route to it via action_list_*. (Ctrl-P is normally the App's
        # priority command-palette binding — OModelApp.check_action suppresses it while this modal
        # is open so it drives the list instead.)
        Binding("up", "list_up", "up", show=False),
        Binding("down", "list_down", "down", show=False),
        Binding("ctrl+p", "list_up", "up", show=False),
        Binding("ctrl+n", "list_down", "down", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    DEFAULT_CSS = """
    AddModelModal {
        align: center middle;
    }
    AddModelModal > Vertical {
        width: 70;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    AddModelModal #add-candidates, AddModelModal #add-variants {
        height: auto;
        max-height: 12;
        display: none;
    }
    AddModelModal #add-preview {
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }
    AddModelModal .modal-hints {
        margin-top: 1;
        color: $text-muted;
    }
    """

    _MODEL_TITLE = "Add model — type to search (or provider/model):"
    # ⌃p/⌃n earn their place HERE and only here: this is the one phase where your hands are on the
    # home row typing a query and j/k are literal text, so the emacs aliases are the only way to
    # move the list without leaving it. (They work in the variant phase too, but that list is
    # focused and already advertises ↑↓/jk — a third alias would just lengthen the line.) The `?`
    # overlay stays out of it: each dialog states its own keys — see HelpModal._BODY.
    _MODEL_HINTS = "↑↓/⌃p⌃n move · tab fill · enter add · esc cancel"
    # Cap the rendered fuzzy list so a broad query (e.g. one common letter) can't re-introduce the
    # render lag this type-to-search design removes. Top matches by score; type more to narrow.
    _MAX_CANDIDATES = 50

    def __init__(
        self, resolver: Resolver, suggestions: Suggestions, require_gpt: bool = False
    ) -> None:
        super().__init__()
        self._resolver = resolver
        self._suggestions = suggestions
        # GPT-only target (Hephaestus): a non-GPT model is BLOCKED (enter disabled), since omo
        # would reject it and reassign the agent to Sisyphus.
        self._require_gpt = require_gpt
        self._staged: dict | None = None
        self._phase = "model"
        # Candidate-row dicts currently in #add-candidates, parallel to its options (so a
        # highlighted/selected option index maps straight back to a row).
        self._candidate_rows: list = []

    def compose(self) -> ComposeResult:
        with Vertical():
            # markup=False: every update to this Label embeds a provider/model id (see the
            # "rendering user data safely" note at the top of the module).
            yield Label(self._MODEL_TITLE, id="add-title", markup=False)
            yield Input(placeholder="provider/model", id="add-input")
            yield OptionList(id="add-candidates")
            yield VimOptionList(id="add-variants")
            yield Static("", id="add-preview", markup=False)  # shows what you typed
            yield Static(self._MODEL_HINTS, id="add-hints", classes="modal-hints")

    def on_mount(self) -> None:
        # The fuzzy list is driven from the Input (↑↓ via screen bindings, Tab via on_key) and
        # never takes focus, so the Input keeps eating printable keys (h/j/k/l stay literal text).
        self.query_one("#add-candidates", OptionList).can_focus = False
        self.query_one("#add-input", Input).focus()
        self._render_candidates("")

    # ----- validation (shared by typed + fuzzy paths) ----------------------------------

    def _validate_row(self, provider: str, model: str):
        """Shared validator for the typed path AND the fuzzy-list path: apply the GPT-gate, flag
        availability, and assemble the candidate-row dict. Returns (row_or_None, preview, ok).
        `provider`/`model` are already split + stripped."""
        # GPT-only target: block a non-GPT model (omo would reassign the agent to Sisyphus).
        if self._require_gpt and not _is_gpt_model(model):
            return None, "⚠ Hephaestus is GPT-only — the model name must contain 'gpt'", False

        warn = []
        # Pair-level availability: warn unless THIS provider serves the model. Model-level
        # ("some provider serves it") would hide a typed mismatch like openai/glm-5 — and the
        # synthetic typed row makes that a one-keystroke commit. Fuzzy rows come from real
        # (provider, model) pairs, so they stay warn-free; only a typed mismatch warns. Skipped
        # entirely in degraded mode (empty catalog.connected, e.g. after a CatalogUnavailable
        # launch): availability is UNKNOWN there, so an unqualified ⚠ would mislead — mirrors
        # _build_rows' identical reasoning for the current off-chain assignment's row.
        catalog = self._resolver.catalog
        if catalog.connected and provider not in catalog.providers_for(model):
            warn.append("unavailable")
        # An add row is user-typed ("you asked for it"); warn flags availability but never
        # blocks. It is not a chain substitute, so substitute_for stays None. variant is filled
        # by the variant phase (None when the family declares none / is unknown).
        row = {
            "source": "add",
            "model": model,
            "provider": provider,
            "variant": None,
            "entry": None,
            "substitute_for": None,
            "warn": warn,
        }
        return row, self._saves_line(row), True

    def _build_row(self, text: str):
        """Return (row_or_None, preview_text, accept_ok) for raw Input text — split a full
        `provider/model` on the FIRST '/', else auto-prefix a bare id via resolve_prefix, then
        delegate to _validate_row. EXACT signature is a pilot-test contract (it is called
        directly)."""
        text = text.strip()
        if not text:
            return None, "(type a model id)", False

        if "/" in text:
            provider, model = text.split("/", 1)
            provider = provider.strip()
            model = model.strip()
            if not provider or not model:
                return None, "⚠ incomplete — provider/model", False
        else:
            model = text
            provider = self._resolver.resolve_prefix(model, "add", None)
            if not provider:
                return None, "⚠ unknown — add a provider/", False

        return self._validate_row(provider, model)

    @staticmethod
    def _saves_line(row: dict) -> str:
        """The `saves: provider/model (+ ⚠)` preview line for a candidate row."""
        return f"saves: {row['provider']}/{row['model']}" + _warn_str(row["warn"])

    # ----- model phase: fuzzy list -----------------------------------------------------

    def _fuzzy_rows(self, text: str) -> list:
        """Candidate-row dicts for the fuzzy list: the `provider/model` pairs you actually have
        (catalog.available), filtered by `text` and — for a GPT-only target — to GPT models. An
        empty `text` returns ALL pairs (dedicated-first); note the modal's render path no longer
        calls this for an empty query (type-to-search — _render_candidates shows nothing until you
        type), but the empty branch is kept (it's exercised directly + guards Matcher("")). Scored
        on the full `provider/model` string so you can filter by either side; BOTH branches are
        dedicated-first (single-vendor provider before a gateway) then first-seen. Every pair comes
        from availability, so warn is []."""
        catalog = self._resolver.catalog
        gateways = self._resolver.gateways
        pairs = [(p, m) for p in catalog.connected for m in catalog.available.get(p, [])]
        if self._require_gpt:
            pairs = [(p, m) for (p, m) in pairs if _is_gpt_model(m)]

        text = text.strip()
        if not text:
            # Browse mode — list everything, dedicated-first then first-seen (the SAME tie-break
            # as the scored branch, so both are dedicated-first). `pairs` is already first-seen,
            # and sorted() is stable, so keying on `provider in gateways` (False < True) sorts
            # every dedicated pair ahead of every gateway pair. Never construct Matcher("") — it
            # raises (FuzzySearch unpacks an empty query).
            scored = sorted(pairs, key=lambda pm: pm[0] in gateways)
        else:
            matcher = Matcher(text)
            order = {pair: i for i, pair in enumerate(pairs)}
            ranked = []
            for p, m in pairs:
                score = matcher.match(f"{p}/{m}")
                if score > 0:
                    ranked.append((score, p, m))
            # Highest score first; tie-break dedicated-first (provider not in gateways) then
            # first-seen (original pair index — catalog.connected order).
            ranked.sort(key=lambda t: (-t[0], t[1] in gateways, order[(t[1], t[2])]))
            scored = [(p, m) for _score, p, m in ranked]

        rows = []
        for p, m in scored:
            row, _preview, ok = self._validate_row(p, m)
            if ok and row is not None:
                rows.append(row)
        return rows

    def _render_candidates(self, text: str) -> None:
        """Rebuild #add-candidates from the fuzzy matches for `text`. Type-to-search: an EMPTY
        query shows NO list at all (hidden, nothing staged) so opening the modal stays instant even
        with hundreds of available models, and a reflexive Enter is a no-op. A non-empty query
        builds the fuzzy list — or, ONLY when nothing fuzzy-matches, a single synthetic "use as
        typed" row for a full provider/model that validates (custom / unavailable ids) — capped at
        _MAX_CANDIDATES, shows it, and auto-highlights the top row for quick-select. A typed string
        that still fuzzy-matches a model you have (e.g. a Tab-filled id after a backspace) falls back
        to those matches, never an ⚠-unavailable synth row for the half-typed text. A non-empty query
        that neither matches anything nor validates hides the list and shows the typed-path preview
        (e.g. the bare-unknown block message)."""
        cands = self.query_one("#add-candidates", OptionList)
        preview = self.query_one("#add-preview", Static)

        if not text.strip():
            # No browse dump — type to search. Don't build/render the full available list (a
            # gateway can serve hundreds of models → a visible lag on open); show nothing until the
            # user types. Also never constructs Matcher("") (which raises on an empty query).
            self._candidate_rows = []
            cands.clear_options()
            cands.display = False
            self._staged = None
            preview.update("type to search")
            return

        fuzzy = self._fuzzy_rows(text)
        typed_row, typed_preview, typed_ok = self._build_row(text)

        rows = fuzzy
        # Synthetic "use as typed" row — ONLY when nothing fuzzy-matched. A typed string that DOES
        # fuzzy-match is a mid-edit fragment of a model you have (Tab fills the full
        # `provider/model`, then a backspace leaves "zhipuai/glm-", still a subsequence of
        # "zhipuai/glm-5"): fall back to those fuzzy matches, don't lead with an "⚠ unavailable" row
        # for the half-typed text. With no fuzzy match the typed string is a genuinely novel custom
        # / unavailable id and this synth row is its only way to commit. (No case-insensitive dedup
        # needed any more: an available id is itself a fuzzy hit, so this branch can't duplicate one
        # — a mixed-case ZHIPUAI/GLM-5 matches zhipuai/glm-5 and so takes the fuzzy path.)
        if not fuzzy and typed_ok and typed_row is not None and "/" in text:
            rows = [typed_row]

        rows = rows[: self._MAX_CANDIDATES]  # bound the per-keystroke render cost
        self._candidate_rows = rows
        cands.clear_options()
        for i, row in enumerate(rows):
            label = f"{row['provider']}/{row['model']}" + _warn_str(row["warn"])
            cands.add_option(Option(_lit(label), id=f"add-cand:{i}"))

        if not rows:
            cands.display = False
            self._staged = None
            preview.update(typed_preview)
        else:
            # Typed query with matches: show the list + auto-highlight the top so a single Enter
            # quick-selects it.
            cands.display = True
            cands.highlighted = 0
            self._staged = rows[0]
            preview.update(self._saves_line(rows[0]))

    @on(Input.Changed, "#add-input")
    def _on_changed(self, event: Input.Changed) -> None:
        if self._phase == "model":
            self._render_candidates(event.value)

    @on(OptionList.OptionHighlighted, "#add-candidates")
    def _on_cand_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        idx = event.option_index
        if idx is not None and 0 <= idx < len(self._candidate_rows):
            self._staged = self._candidate_rows[idx]
            self.query_one("#add-preview", Static).update(self._saves_line(self._staged))

    @on(OptionList.OptionSelected, "#add-candidates")
    def _on_cand_selected(self, event: OptionList.OptionSelected) -> None:
        # Mouse click on a row funnels to the same chooser as Enter.
        idx = event.option_index
        if idx is not None and 0 <= idx < len(self._candidate_rows):
            self._choose_model(self._candidate_rows[idx])

    @on(Input.Submitted, "#add-input")
    def _on_submitted(self, event: Input.Submitted) -> None:
        # Enter chooses the staged (highlighted) row; a no-op when nothing is staged (e.g. a bare
        # unknown id → no fuzzy hit and no synth row → still blocked).
        if self._staged is not None:
            self._choose_model(self._staged)

    def _active_list(self) -> OptionList:
        """The list ↑↓ / Ctrl-P / Ctrl-N move: the fuzzy #add-candidates in the model phase, the
        #add-variants list in the variant phase."""
        sel = "#add-variants" if self._phase == "variant" else "#add-candidates"
        return self.query_one(sel, OptionList)

    def action_list_down(self) -> None:
        """↓ / Ctrl-N — move the active list's highlight. Model phase: the Input keeps focus and the
        unfocused #add-candidates is driven here (a no-op when it is empty/hidden). Variant phase:
        ↑↓ are handled by the focused #add-variants natively; Ctrl-P/Ctrl-N route here."""
        self._active_list().action_cursor_down()

    def action_list_up(self) -> None:
        self._active_list().action_cursor_up()

    def on_key(self, event: events.Key) -> None:
        """Tab (model phase) fills the highlighted pair into the Input — intercepted here, before
        Textual's focus traversal (the candidates list is can_focus=False, so there is nowhere to
        focus anyway). Setting the value re-filters via Input.Changed. Every other key falls
        through to the bindings / the focused Input."""
        if event.key == "tab" and self._phase == "model":
            event.stop()
            event.prevent_default()
            if self._staged is not None:
                inp = self.query_one("#add-input", Input)
                inp.value = f"{self._staged['provider']}/{self._staged['model']}"
                inp.cursor_position = len(inp.value)

    # ----- variant phase ---------------------------------------------------------------

    def _choose_model(self, row: dict | None) -> None:
        """Commit the chosen model: enter the variant phase iff opencode reports variants for its
        (provider, model) — catalog.variants_for, the cached `--verbose` map — else dismiss
        immediately with variant left None (kimi / glm-5 / any model opencode lists with no
        variants, or whose verbose isn't cached anywhere)."""
        if row is None:
            return
        self._staged = row
        # Drop a "none" variant opencode may list — it duplicates the synthetic "(none)" clear row
        # _enter_variant_phase appends, so offering both is redundant (_is_no_variant). If that
        # leaves nothing pickable, add the model straight away (variant left None), like kimi/glm-5.
        variants = [
            v for v in self._resolver.catalog.variants_for(row["provider"], row["model"])
            if not _is_no_variant(v)
        ]
        if not variants:
            self.dismiss(row)
            return
        self._enter_variant_phase(variants)

    def _enter_variant_phase(self, variants: list) -> None:
        self._phase = "variant"
        row = self._staged
        variants_list = self.query_one("#add-variants", VimOptionList)
        variants_list.clear_options()
        for v in variants:
            variants_list.add_option(Option(_lit(v), id=f"var:{v}"))
        variants_list.add_option(Option("(none)", id="var:__none__"))
        variants_list.display = True
        # Hide the model-phase widgets now that the variant list can take focus.
        self.query_one("#add-input", Input).display = False
        self.query_one("#add-candidates", OptionList).display = False
        self.query_one("#add-title", Label).update(
            f"Variant for {row['provider']}/{row['model']}:"
        )
        self.query_one("#add-preview", Static).update(self._saves_line(row))
        self.query_one("#add-hints", Static).update("↑↓/jk move · enter choose · esc back")
        variants_list.focus()

    @on(OptionList.OptionSelected, "#add-variants")
    def _on_variant_selected(self, event: OptionList.OptionSelected) -> None:
        oid = event.option_id or ""
        row = self._staged
        if row is None or not oid.startswith("var:"):
            return
        # (none) ⇒ variant None — a fresh add, NOT VariantModal's '' clear sentinel.
        row["variant"] = None if oid == "var:__none__" else oid[len("var:"):]
        self.dismiss(row)

    def _return_to_model_phase(self) -> None:
        self._phase = "model"
        self.query_one("#add-variants", VimOptionList).display = False
        self.query_one("#add-input", Input).display = True
        self.query_one("#add-title", Label).update(self._MODEL_TITLE)
        self.query_one("#add-hints", Static).update(self._MODEL_HINTS)
        inp = self.query_one("#add-input", Input)
        inp.focus()
        # _render_candidates owns #add-candidates visibility (shown only when the current text
        # yields matches), so it is not force-shown here.
        self._render_candidates(inp.value)

    def action_cancel(self) -> None:
        """Esc — the variant phase returns to the model phase; the model phase cancels the modal."""
        if self._phase == "variant":
            self._return_to_model_phase()
        else:
            self.dismiss(None)


class VariantModal(ModalScreen):
    """`v` — pick from the variants opencode reports for the model + '(none)'.  A "none" opencode
    may list is dropped by the caller as a duplicate of '(none)' (_is_no_variant).  Dismisses with
    the chosen variant string, the sentinel '' for (none), or None on cancel."""

    BINDINGS: ClassVar[list] = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    DEFAULT_CSS = """
    VariantModal {
        align: center middle;
    }
    VariantModal > Vertical {
        width: 50;
        height: auto;
        max-height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    VariantModal .modal-hints {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, variants: list) -> None:
        super().__init__()
        self._variants = variants

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Variant:")
            ol = VimOptionList(id="variant-list")
            yield ol
            yield Static(
                "↑↓ move · enter choose · esc cancel",
                id="variant-hints",
                classes="modal-hints",
            )

    def on_mount(self) -> None:
        ol = self.query_one("#variant-list", OptionList)
        for v in self._variants:
            ol.add_option(Option(_lit(v), id=f"var:{v}"))
        ol.add_option(Option("(none)", id="var:__none__"))
        ol.focus()

    @on(OptionList.OptionSelected, "#variant-list")
    def _on_selected(self, event: OptionList.OptionSelected) -> None:
        oid = event.option_id or ""
        if oid == "var:__none__":
            self.dismiss("")  # explicit clear
        elif oid.startswith("var:"):
            self.dismiss(oid[len("var:"):])

    def action_cancel(self) -> None:
        self.dismiss(None)


class PresetNameModal(ModalScreen):
    """Name a preset — for both things that need a name.

    * **add** (`a` anywhere in `#presets`, or `enter` on `+ add preset…`; no args): empty box,
      "Save new preset".
    * **rename** (`r` on an existing row, `index`+`existing` given): prefilled with that preset's
      name, and it changes nothing but the name.

    There is deliberately NO third "overwrite this preset" mode. `a` used to replace the
    highlighted preset's models, with this modal doubling as the confirm — a destructive,
    non-undoable action under the key that means *add* in every other pane, one habitual `a` +
    reflexive `enter` away. `a` appends now, so the two modes above are the whole surface and
    `existing is not None` ⟺ rename.

    Dismisses with the RAW typed text (the caller sanitizes via presets.sanitize_name) or None
    on cancel."""

    BINDINGS: ClassVar[list] = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    DEFAULT_CSS = """
    PresetNameModal {
        align: center middle;
    }
    PresetNameModal > Vertical {
        width: 54;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    PresetNameModal .modal-hints {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, index: int | None = None, existing=None) -> None:
        super().__init__()
        self._index = index          # None == a NEW preset (appended); no number to show yet
        self._existing = existing    # not None == rename (the only mode that names a preset)

    def compose(self) -> ComposeResult:
        rename = self._existing is not None
        number = "" if self._index is None else f" {self._index + 1}"
        if rename:
            title = f'Rename preset{number} "{self._existing.name}"'
        else:
            title = "Save new preset"
        with Vertical():
            yield Label(title, id="preset-name-title", markup=False)  # quotes a preset name
            yield Input(
                value=self._existing.name if rename else "",
                placeholder="preset name",
                max_length=presets_mod.MAX_NAME,
                id="preset-name-input",
            )
            # "+ switch" on the one that MOVES you: adding leaves you editing the new preset,
            # which the title alone doesn't say and which decides where your next edit goes.
            hint = "rename" if rename else "save + switch"
            yield Static(
                f"enter {hint} · esc cancel",
                id="preset-name-hints",
                classes="modal-hints",
            )

    def on_mount(self) -> None:
        self.query_one("#preset-name-input", Input).focus()

    @on(Input.Submitted, "#preset-name-input")
    def _on_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen):
    """Generic confirm modal — shows `body` (e.g. the save diff, with the first-save
    palette-loss warning) and Yes/No.  Dismisses True on accept, False otherwise.

    The body lives in a `VerticalScroll` capped at `max-height`, so a long save diff is fully
    scrollable: ↑/↓ + j/k, PageUp/PageDown, Home/End.  Those are screen-level bindings (not the
    scroller's own), so they scroll even while the Yes button keeps focus — leaving Enter to
    confirm the focused button as before."""

    BINDINGS: ClassVar[list] = [
        Binding("escape", "cancel", "No", show=False),
        Binding("y", "accept", "Yes", show=False),
        Binding("n", "decline", "No", show=False),
        # Yes/No sit side by side, so move between them side to side (see QuitModal). The
        # vertical keys stay on the scrolling body — j/k scroll the diff, h/l pick the answer,
        # the same split the base screen uses.
        Binding("left", "app.focus_previous", "Previous", show=False),
        Binding("right", "app.focus_next", "Next", show=False),
        Binding("h", "app.focus_previous", "Previous", show=False),
        Binding("l", "app.focus_next", "Next", show=False),
        Binding("up", "scroll(-1)", "Scroll up", show=False),
        Binding("k", "scroll(-1)", "Scroll up", show=False),
        Binding("down", "scroll(1)", "Scroll down", show=False),
        Binding("j", "scroll(1)", "Scroll down", show=False),
        Binding("pageup", "scroll_page(-1)", "Page up", show=False),
        Binding("pagedown", "scroll_page(1)", "Page down", show=False),
        Binding("home", "scroll_ends(-1)", "Top", show=False),
        Binding("end", "scroll_ends(1)", "Bottom", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }
    ConfirmModal > Vertical {
        width: 90%;
        max-width: 100;
        height: auto;
        max-height: 90%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    ConfirmModal #confirm-body {
        height: auto;
        max-height: 20;
        margin-bottom: 1;
        scrollbar-size-vertical: 1;
    }
    ConfirmModal #confirm-buttons {
        height: auto;
        align: center middle;
    }
    ConfirmModal Button {
        margin: 0 1;
    }
    /* Emphasis follows focus rather than sitting permanently on Yes — see QuitModal's note. */
    ConfirmModal Button:focus {
        background: $primary;
    }
    ConfirmModal .modal-hints {
        margin-top: 1;
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(
        self,
        title: str,
        body: str,
        yes_label: str = "Yes",
        no_label: str = "No",
        hints: str | None = None,
        escape_cancels: bool = False,
    ) -> None:
        super().__init__()
        self._title = title
        self._body = body
        # Custom labels let a non-yes/no decision reuse this modal (the launch sync prompt names
        # its two directions); `y`/`n` still work, since they are the screen's own bindings.
        self._yes_label = yes_label
        self._no_label = no_label
        # …and then the hint line has to be overridable, or it advertises "y yes · n no" for
        # buttons that say something else. `escape_cancels` dismisses None instead of False, so
        # a two-direction question gets a real third answer rather than silently picking "no" —
        # which for the sync prompt would mean rewriting the user's config on an `esc`.
        self._hints = hints
        self._escape_cancels = escape_cancels

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title)
            with VerticalScroll(id="confirm-body") as body:
                # Non-focusable so default focus stays on the Yes button (Enter still confirms);
                # scrolling is driven by this screen's own bindings, not the scroller's focus.
                body.can_focus = False
                # markup=False: the save diff quotes model ids straight from the config.
                yield Static(self._body, id="confirm-body-text", markup=False)
            with Horizontal(id="confirm-buttons"):
                yield Button(self._yes_label, id="confirm-yes")  # focus is the emphasis
                yield Button(self._no_label, id="confirm-no")
            yield Static(
                self._hints or "↑↓/jk scroll · y yes · n no · esc cancel",
                id="confirm-hints",
                classes="modal-hints",
            )

    @on(Button.Pressed, "#confirm-yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#confirm-no")
    def _no(self) -> None:
        self.dismiss(False)

    def action_accept(self) -> None:
        self.dismiss(True)

    def action_decline(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        """`esc` — False by default (a plain yes/no has no third answer), or None when the
        caller asked for one (`escape_cancels`)."""
        self.dismiss(None if self._escape_cancels else False)

    def _body_scroll(self) -> VerticalScroll:
        return self.query_one("#confirm-body", VerticalScroll)

    def action_scroll(self, direction: int) -> None:
        """↑/k, ↓/j — scroll the diff body one line (no-op when it already fits)."""
        body = self._body_scroll()
        (body.scroll_down if direction > 0 else body.scroll_up)(animate=False)

    def action_scroll_page(self, direction: int) -> None:
        """PageUp / PageDown — scroll the diff body one page."""
        body = self._body_scroll()
        (body.scroll_page_down if direction > 0 else body.scroll_page_up)(animate=False)

    def action_scroll_ends(self, direction: int) -> None:
        """Home / End — jump to the top / bottom of the diff body (instant, no animation, so a
        big diff lands immediately rather than smooth-scrolling for a second)."""
        body = self._body_scroll()
        (body.scroll_end if direction > 0 else body.scroll_home)(animate=False)


class QuitModal(ModalScreen):
    """`q` with unsaved changes — THREE ways out, not two.

    Discarding now costs preset work as well as config work (decision #17: the presets file and
    the config are written together, by `s` alone), so the exit that loses both must not be the
    only exit. Dismisses `'save'` (run the normal diff+confirm, then quit), `'discard'`, or
    None (cancel)."""

    BINDINGS: ClassVar[list] = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("c", "cancel", "Cancel", show=False),
        Binding("s", "save", "Save & quit", show=False),
        Binding("d", "discard", "Discard", show=False),
        # Move along the button row the way the buttons are laid out — horizontally. `tab` alone
        # (Textual's only default) is a poor fit for a row you read left-to-right, and `←`/`→`
        # are dead keys here otherwise: the App's own ←/→ are gated to the base screen
        # (check_action), so nothing else claims them. Same two actions `tab`/`shift+tab` use, so
        # they wrap identically. `h`/`l` mirror them, as everywhere else in the app.
        Binding("left", "app.focus_previous", "Previous", show=False),
        Binding("right", "app.focus_next", "Next", show=False),
        Binding("h", "app.focus_previous", "Previous", show=False),
        Binding("l", "app.focus_next", "Next", show=False),
    ]

    DEFAULT_CSS = """
    QuitModal {
        align: center middle;
    }
    QuitModal > Vertical {
        width: 66;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    QuitModal #quit-buttons {
        height: auto;
        align: center middle;
        margin-top: 1;
    }
    QuitModal Button {
        margin: 0 1;
    }
    /* The emphasis has to FOLLOW focus. `variant="primary"` paints one button blue PERMANENTLY,
       which competes with Textual's focus styling — two buttons look emphasized at once and the
       eye reads the static blue one as selected, so moving along the row appears to do nothing.
       Dropping the variant and colouring `:focus` instead makes the fill the cursor. NB Textual
       focuses buttons with `text-style: b reverse` and that wins over anything set here, so this
       renders as $primary TEXT on a light fill, not the other way round — either way it is one
       unmistakable block, and it moves. */
    QuitModal Button:focus {
        background: $primary;
    }
    QuitModal .modal-hints {
        margin-top: 1;
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(self, body: str) -> None:
        super().__init__()
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Quit?")
            yield Static(self._body, id="quit-body", markup=False)
            with Horizontal(id="quit-buttons"):
                # No `variant="primary"` on any of them — see the DEFAULT_CSS note.
                yield Button("Save & quit", id="quit-save")
                yield Button("Discard", id="quit-discard")
                yield Button("Cancel", id="quit-cancel")
            yield Static(
                "s save & quit · d discard · esc cancel", id="quit-hints", classes="modal-hints"
            )

    @on(Button.Pressed, "#quit-save")
    def _save(self) -> None:
        self.dismiss("save")

    @on(Button.Pressed, "#quit-discard")
    def _discard(self) -> None:
        self.dismiss("discard")

    @on(Button.Pressed, "#quit-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        self.dismiss("save")

    def action_discard(self) -> None:
        self.dismiss("discard")

    def action_cancel(self) -> None:
        self.dismiss(None)


class AddSubModal(ModalScreen):
    """`a` — pick which sub-target to add to an agent.  Shown only when the agent supports more
    than one kind (so there's an actual choice — only Sisyphus, with `ultrawork` + `compaction`);
    a single-kind agent skips this and `a` adds straight away (see `OModelApp._add_sub`).  Offers
    only the kinds valid for that agent (`_subkinds_for`): every agent gets `compaction`, but
    `ultrawork` only Sisyphus.  Each row names the kind and one line on what omo uses it for; a
    kind already on the agent is shown disabled (`✓ added`).  Dismisses with the chosen kind
    ('ultrawork'|'compaction') — via the `u`/`c` shortcut or enter on the row — or None on cancel."""

    BINDINGS: ClassVar[list] = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("u", "pick('ultrawork')", "ultrawork", show=False),
        Binding("c", "pick('compaction')", "compaction", show=False),
    ]

    DEFAULT_CSS = """
    AddSubModal {
        align: center middle;
    }
    AddSubModal > Vertical {
        width: 64;
        height: auto;
        max-height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    AddSubModal .modal-hints {
        margin-top: 1;
        color: $text-muted;
    }
    """

    # One line on what each sub-model is for (display only). Mirrors omo: ultrawork swaps the
    # model on a keyworded message; compaction is the model used to summarize the session.
    _BLURB: ClassVar[dict] = {
        "ultrawork": "model swapped in when you type 'ultrawork' / 'ulw'",
        "compaction": "model used for automatic context summaries",
    }

    def __init__(self, kinds, present) -> None:
        super().__init__()
        self._kinds = tuple(kinds)  # the kinds valid for this agent (see _subkinds_for)
        self._present = set(present)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Add sub-target:")
            yield VimOptionList(id="sub-list")
            yield Static("", id="sub-hints", classes="modal-hints")

    def on_mount(self) -> None:
        ol = self.query_one("#sub-list", OptionList)
        for kind in self._kinds:
            added = kind in self._present
            tag = "   ✓ added" if added else ""
            label = f"{kind[0]}  {kind:<10} — {self._BLURB[kind]}{tag}"
            ol.add_option(Option(_lit(label), id=f"sub:{kind}", disabled=added))
        # Hint names only the shortcuts that exist for this agent ("u/c" on Sisyphus, "c" else).
        keys = "/".join(k[0] for k in self._kinds)
        self.query_one("#sub-hints", Static).update(
            f"↑↓ move · {keys} or enter add · esc cancel"
        )
        ol.focus()

    def action_pick(self, kind: str) -> None:
        # Shortcut path (`u`/`c`): ignore a kind not valid here or already present (the row would
        # be absent or disabled anyway — `u` on a non-Sisyphus agent is a no-op).
        if kind in self._kinds and kind not in self._present:
            self.dismiss(kind)

    @on(OptionList.OptionSelected, "#sub-list")
    def _on_selected(self, event: OptionList.OptionSelected) -> None:
        oid = event.option_id or ""
        if oid.startswith("sub:"):
            self.dismiss(oid[len("sub:"):])

    def action_cancel(self) -> None:
        self.dismiss(None)


# The bottom hint bar (Static#hints) is deliberately MINIMAL and STATIC: only the three keys you
# won't discover by convention and that act regardless of focus — `s` save (the app's whole point),
# the `?` help overlay (which documents the base-screen keys), and `q` quit. Every other
# base-screen key lives in HelpModal, so the bar never grows past one line and never has to track
# pane / row / undo state. Keep this in sync with HelpModal._BODY; dialogs advertise their own keys
# on their own hint line (`Static.modal-hints`) and are not repeated in either.
_HINT_BAR = "s save · q quit · ? help"


class HelpModal(ModalScreen):
    """`?` — the base-screen keys the hint bar doesn't show, grouped by pane (the same key means
    different things on a #presets row). Not an exhaustive reference: `s`/`q`/`?` stay on the bar
    behind it, dialogs carry their own hint line, and conventions like enter/esc/y/n go unsaid.
    Read-only and scrollable (same body pattern as ConfirmModal, in case it outgrows a short
    terminal); closes with `?`, `esc`, or `q`."""

    BINDINGS: ClassVar[list] = [
        Binding("question_mark", "close", "Close", show=False),
        Binding("escape", "close", "Close", show=False),
        Binding("q", "close", "Close", show=False),
        Binding("up", "scroll(-1)", "Scroll up", show=False),
        Binding("k", "scroll(-1)", "Scroll up", show=False),
        Binding("down", "scroll(1)", "Scroll down", show=False),
        Binding("j", "scroll(1)", "Scroll down", show=False),
        Binding("pageup", "scroll_page(-1)", "Page up", show=False),
        Binding("pagedown", "scroll_page(1)", "Page down", show=False),
        Binding("home", "scroll_ends(-1)", "Top", show=False),
        Binding("end", "scroll_ends(1)", "Bottom", show=False),
    ]

    DEFAULT_CSS = """
    HelpModal {
        align: center middle;
    }
    HelpModal > Vertical {
        width: 62;
        height: auto;
        max-height: 90%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    HelpModal .modal-hints {
        margin-top: 1;
        color: $text-muted;
    }
    """

    # The keys you can't guess, grouped BY PANE — because `enter`/`a`/`r`/`x` each mean something
    # different on a #presets row, and that contrast is the whole reason this overlay exists.
    # Deliberately NOT a full reference: `s`/`q`/`?` are on the hint bar behind this modal, every
    # dialog carries its own hint line (`Static.modal-hints`), and universal conventions
    # (enter confirms, esc cancels, y/n) are left unsaid — one line per key that earns it.
    # Descriptions start at a fixed column (15) so the keys line up; lines stay ≤54 cells — the
    # 62-cell panel leaves 56 of content, and a short terminal spends 2 of those on the scrollbar,
    # which would WRAP a 55-cell line and cost back the height it just saved. Verified by
    # test_help_body_stays_light. Mirror any change here in the module KEYS docstring and
    # DESIGN §Layout / §Textual contract. The list form keeps this aligned help table readable and
    # diffable; FLY002 would collapse it into one long line for zero runtime gain.
    _BODY = "\n".join(  # noqa: FLY002 - see comment above
        [
            "Move",
            "  ↑↓  jk       within a pane",
            "  ←→  hl       targets ⇄ candidates",
            "  tab          cycles all three panes, incl. presets",
            "",
            "Models",
            "  enter        set the highlighted model",
            "  v            pick a variant",
            "  a            add / edit a model  (agent: sub-target)",
            "  x            clear (sub-target / added row: delete)",
            "  r            refresh models from opencode",
            "",
            "Presets  (tab to reach)",
            "  enter        switch — banks your edits first",
            "  a            add one from the current models",
            "  r            rename",
            "  x            delete (not the active one)",
            "",
            "Undo",
            "  u   ⌃r       undo / redo the last edit",
        ]
    )

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Keys")
            with VerticalScroll(id="help-body") as body:
                # Non-focusable so this screen's own scroll bindings drive it (mirrors ConfirmModal);
                # the overlay is a read-only reference, so nothing inside needs focus.
                body.can_focus = False
                yield Static(self._BODY, id="help-body-text")
            yield Static("↑↓/jk scroll · ?/esc/q close", id="help-hints", classes="modal-hints")

    def action_close(self) -> None:
        self.dismiss(None)

    def _body_scroll(self) -> VerticalScroll:
        return self.query_one("#help-body", VerticalScroll)

    def action_scroll(self, direction: int) -> None:
        """↑/k, ↓/j — scroll one line (no-op when the reference already fits)."""
        body = self._body_scroll()
        (body.scroll_down if direction > 0 else body.scroll_up)(animate=False)

    def action_scroll_page(self, direction: int) -> None:
        """PageUp / PageDown — scroll one page."""
        body = self._body_scroll()
        (body.scroll_page_down if direction > 0 else body.scroll_page_up)(animate=False)

    def action_scroll_ends(self, direction: int) -> None:
        """Home / End — jump to top / bottom (instant, no animation)."""
        body = self._body_scroll()
        (body.scroll_end if direction > 0 else body.scroll_home)(animate=False)


class OModelApp(App):
    """Two-pane list-detail TUI to set OMO models.  See module docstring for the stable
    widget/option IDs the pilot tests depend on."""

    TITLE = "oModel"

    CSS = """
    #providers {
        height: 1;
        background: $surface-lighten-1;  /* neutral bar fill — theme token, deliberately not the blue-gray $panel (the "90s DOS status bar" tint) */
        color: $text;
        padding: 0 1;
    }
    #main {
        height: 1fr;
    }
    #left {
        width: 32;                       /* the width moved here from #targets: the left column now stacks the targets list over the presets card */
    }
    #targets {
        height: 1fr;
        border: solid $surface-lighten-3;  /* muted blurred border — theme token (not a literal); NOT $border-blurred, which textual-dark resolves to ~#191919, invisible on the #1E1E1E surface. See class docstring. */
    }
    #presets {
        height: auto;                    /* grows with however many presets you keep (unlimited) … */
        max-height: 50%;                 /* … but never past half the column: #targets keeps the rest, and the card scrolls internally beyond that (DESIGN decision #17). */
        scrollbar-gutter: stable;        /* ALWAYS reserve the 2-cell gutter, so the name budget _populate_presets measures can't be invalidated by the scrollbar appearing (see _fit_cells). */
        border: solid $surface-lighten-3;
    }
    #right {
        width: 1fr;
    }
    #detail {
        height: auto;
        min-height: 4;
        padding: 0 1;
        border: solid $surface-lighten-3;  /* muted blurred border — theme token (not a literal); NOT $border-blurred, which textual-dark resolves to ~#191919, invisible on the #1E1E1E surface. See class docstring. */
    }
    #candidates {
        height: 1fr;
        border: solid $surface-lighten-3;  /* muted blurred border — theme token (not a literal); NOT $border-blurred, which textual-dark resolves to ~#191919, invisible on the #1E1E1E surface. See class docstring. */
    }
    #targets:focus, #presets:focus, #candidates:focus {
        border: solid $primary;
    }
    #hints-bar {
        height: 1;
        background: $surface-lighten-1;  /* neutral bar fill — theme token, deliberately not the blue-gray $panel (the "90s DOS status bar" tint) */
    }
    #hints {
        width: 1fr;                      /* keys, left — grows to fill so the version is pushed to the tail (right edge) */
        color: $text-muted;
        padding: 0 1;
    }
    #hints-version {
        width: auto;                     /* app version, right-aligned at the tail of the bar */
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS: ClassVar[list] = [
        Binding("left", "focus_targets", "targets", show=False),
        Binding("right", "focus_candidates", "candidates", show=False),
        Binding("h", "focus_targets", "targets", show=False),
        Binding("l", "focus_candidates", "candidates", show=False),
        Binding("v", "variant", "variant"),
        Binding("x", "clear", "clear"),
        Binding("a", "edit_or_sub", "edit/sub"),
        Binding("u", "undo", "undo"),
        Binding("ctrl+r", "redo", "redo"),
        Binding("s", "save", "save"),
        Binding("r", "refresh", "refresh"),
        Binding("question_mark", "help", "help", show=False),
        Binding("q", "quit_confirm", "quit"),
    ]

    def __init__(
        self,
        catalog: Catalog | None = None,
        suggestions: Suggestions | None = None,
        resolver: Resolver | None = None,
        cfg: dict | None = None,
        config_path: str | None = None,
        catalog_error: BaseException | None = None,
        session: Session | None = None,
    ) -> None:
        super().__init__()
        # The headless core (session.py): cfg + catalog + suggestions + resolver + the presets
        # store, plus every cfg mutation and the save. `cli.py` edits through the same object, so
        # the TUI can't drift from the agent surface. Accepting a prebuilt `session` is what
        # `create_app` uses; building one from loose parts is the test/embedding path, and both
        # run the identical presets load / seed / launch-reconcile (Session.__post_init__).
        self.session = session if session is not None else Session(
            catalog=catalog,
            suggestions=suggestions,
            resolver=resolver,
            cfg=cfg,
            config_path=config_path,
            catalog_error=catalog_error,
        )
        # `suggestions` and `config_path` are never reassigned after construction, so aliasing
        # them is safe. `catalog` / `resolver` / `catalog_error` are NOT — `r` (_refresh_catalog)
        # replaces all three — so they are properties onto the session below, or a refresh would
        # leave the app fresh and the session stale and `_build_rows` (which delegates to
        # `session.rows`) would keep resolving against the pre-refresh catalog.
        self.suggestions = self.session.suggestions
        self.config_path = self.session.config_path
        self._sync_conflict = self.session.sync_conflict
        # In-session undo/redo (mis-press recovery) — a UI concern, so it stays here rather than
        # moving into Session: a CLI process edits once and exits, with no stack to unwind.
        # `_history` holds cfg snapshots; every config mutation routes through `_record` (and
        # `_stage_row`), which pushes one, so any operation can be reverted with `u` / re-applied
        # with `ctrl+r` (see history.py). Dirtiness is computed against `_saved_text` (the
        # session's baseline), NOT a bool flag — so undoing back to the saved state reads as
        # clean, and a structural-but-unserialized change (an empty ultrawork/compaction
        # sub-object) is undoable yet never marks the file dirty.
        # Cache of the candidate-row dicts currently rendered, keyed by target id; rebuilt from
        # the resolver (+ merged _custom_rows) on a cache miss. Dropped by a refresh AND a
        # state restore (undo/redo), since the `●` current-pick depends on cfg.
        self._rows: dict = {}
        # Per-target store of models typed in the add-model modal (off-chain picks), keyed by
        # target id. Merged into _build_rows so a typed model stays a pickable row. Snapshotted
        # into the undo history alongside cfg (as each entry's `aux`, via _record) and restored by
        # _restore_state, so it moves in lockstep with undo/redo: undoing an add-model drops its
        # row, redoing brings it back. A refresh clears it (stored availability ⚠ would be stale).
        self._custom_rows: dict = {}
        # `v` picks on a row that is NOT the current assignment, kept as
        # {target: {"provider/model": variant|None}} and re-applied by _build_rows.
        #
        # Such a pick is deliberately not a cfg edit (only Enter assigns — DESIGN §Events), so it
        # has to live somewhere until you press Enter. It used to live as an IN-PLACE mutation of
        # the row dict inside `_rows`, which quietly made `_rows` load-bearing state rather than a
        # cache — and anything that rebuilt it silently reverted the pick to omo's suggested
        # variant. That was survivable while only cfg mutations rebuilt rows; it stopped being so
        # once a landing background fetch did too (it fires on its own schedule, so the pick
        # vanished with no user action at all, and a later Enter wrote omo's variant instead of
        # the chosen one). Holding the pick OUTSIDE the cache makes `_rows` a true cache again,
        # which is what lets the fetch worker invalidate it freely.
        #
        # Cleared exactly where the pick stops being pending: _stage_row (it reached cfg),
        # _restore_state (undo/redo owns the staged state) and _refresh_catalog (same reasoning as
        # _custom_rows — the model may not even survive the re-resolve).
        self._pending_variants: dict = {}
        # Built last: entry 0's `aux` needs `_custom_rows` and the reconciled `_store` (see
        # _aux — the active preset index rides with every undo step). Reads self.cfg, not the
        # `cfg` parameter, which is None on the prebuilt-session path.
        self._history = History(self.cfg, aux=self._aux())
        # The target id currently shown in the right pane.
        self._current_target: str | None = None
        # Per-target memory of the highlighted candidate, keyed by target id → the row's stable
        # provider/model identity (or the sentinel 'cand:add'). Restored on every candidate
        # re-render (_restore_cand_highlight) so the cursor returns to your last pick when you
        # revisit a target or after `r`. Keyed by identity (not row index) so it still resolves
        # once the chain re-resolves against refreshed availability. Deliberately NOT cleared by
        # _refresh_catalog (unlike _rows / _detail_cache), which is what makes it survive refresh.
        self._cand_choice: dict = {}
        # Detail-pane enrichment (catalog.detail()) is a ~3s, ~320 MB `opencode … --verbose`
        # subprocess. It runs in a background worker and is cached per (provider, model), so
        # highlighting never blocks the UI thread. Crucially only ONE fetch runs at a time
        # (_detail_fetching): a spawned opencode process can't be killed, so stacking fetches
        # would pile up 320 MB each. On completion the worker re-renders the *current*
        # target, which schedules the next fetch if it's still uncached ("chase the cursor").
        # _detail_cache: 'provider/bare' (see _detail_key) → info dict | None (None =
        # known-empty); _detail_timer debounces.
        self._detail_cache: dict = {}
        self._detail_fetching = False
        self._detail_timer = None
        # Bumped by a refresh (r) so an in-flight detail fetch can tell its result is stale.
        self._detail_generation = 0
        # Single-flight guard for `r` (action_refresh): @work(exclusive=True) on _refresh_catalog
        # cancels a PRIOR ASYNCIO TASK on a second `r`, but not the underlying opencode
        # subprocess/thread it's awaiting (which can't be killed — see _to_thread_daemon), so two
        # concurrent `opencode models --refresh` calls would race cache.clear()/cache.write()
        # (last finisher wins regardless of press order). action_refresh checks this instead of
        # spawning a second worker; set/cleared by _refresh_catalog itself (try/finally).
        self._refresh_inflight = False

    # ----- session state -----------------------------------------------------------------
    #
    # The four pieces of editable state live on `self.session`, not here, so `cli.py` mutates
    # exactly what the TUI mutates. These properties keep the historical attribute names working
    # unchanged across the ~50 sites (and the pilot tests) that use them. `cfg` and `_store` are
    # both REASSIGNED in places — `_restore_state` swaps cfg for a history snapshot, a preset
    # switch and a store write replace the store — so neither can simply be aliased at
    # construction; the setters have to reach through to the session or the two would fork.

    @property
    def cfg(self) -> dict:
        return self.session.cfg

    @cfg.setter
    def cfg(self, value: dict) -> None:
        self.session.cfg = value

    # `r` (_refresh_catalog) replaces all three of these mid-session. They MUST reach the session
    # rather than shadow it: `_build_rows` delegates to `session.rows()`, so an app-only refresh
    # would redraw the header with the new providers while the pick list kept resolving against
    # the old catalog — the one thing `r` exists to do, silently not done.

    @property
    def catalog(self) -> Catalog:
        return self.session.catalog

    @catalog.setter
    def catalog(self, value: Catalog) -> None:
        self.session.catalog = value

    @property
    def resolver(self):
        return self.session.resolver

    @resolver.setter
    def resolver(self, value) -> None:
        self.session.resolver = value

    @property
    def catalog_error(self):
        return self.session.catalog_error

    @catalog_error.setter
    def catalog_error(self, value) -> None:
        self.session.catalog_error = value

    @property
    def _store(self):
        return self.session.store

    @_store.setter
    def _store(self, value) -> None:
        self.session.store = value

    @property
    def _saved_text(self) -> str:
        return self.session.saved_text

    @_saved_text.setter
    def _saved_text(self, value: str) -> None:
        self.session.saved_text = value

    @property
    def _saved_store_fp(self) -> str:
        return self.session.saved_store_fp

    @_saved_store_fp.setter
    def _saved_store_fp(self, value: str) -> None:
        self.session.saved_store_fp = value

    # ----- composition -----------------------------------------------------------------

    def compose(self) -> ComposeResult:
        # markup=False: provider names come from `opencode` (see the module-top note).
        yield Static("", id="providers", markup=False)
        with Horizontal(id="main"):
            # Left column: the targets list (1fr) over the presets card, which grows with the
            # list and is capped at half the column (scrolling past that), so presets are
            # reachable without scrolling past ~21 target rows and #targets is never squeezed.
            with Vertical(id="left"):
                yield VimOptionList(id="targets")
                yield VimOptionList(id="presets")
            with Vertical(id="right"):
                yield Static("", id="detail")
                yield VimOptionList(id="candidates")
        with Horizontal(id="hints-bar"):
            yield Static("", id="hints")
            yield Static(f"v{__version__}", id="hints-version")

    def on_mount(self) -> None:
        self._render_providers()
        self._populate_targets()
        # The card's heading lives in its border (not an in-list disabled row like #targets'
        # AGENTS/CATEGORIES headers), so no line of the bounded card is spent on a label.
        self.query_one("#presets", OptionList).border_title = "PRESETS"
        self._populate_presets()
        if self._sync_conflict:
            self._ask_sync()
        # The hint bar is static (see _HINT_BAR) — set it once. Everything it used to advertise
        # pane-by-pane now lives in the `?` help overlay (HelpModal).
        self.query_one("#hints", Static).update(_HINT_BAR)

    # ----- header ----------------------------------------------------------------------

    def _render_providers(self) -> None:
        # Just the connected list — no cache-age / "r to refresh" suffix. Refresh (`r`) is
        # documented in the `?` help overlay, and the bare list reads cleaner (decision: keep the
        # header aesthetic and uncluttered; staleness isn't worth a permanent suffix).
        header = self.query_one("#providers", Static)
        if self.catalog_error is not None:
            header.update("⚠ couldn't read models — press r to retry")
        elif self.catalog.connected:
            header.update("oModel: " + " · ".join(self.catalog.connected))
        else:
            header.update("oModel: (none — opencode not found; suggestions/add only)")

    # ----- left pane: targets ----------------------------------------------------------

    def _agent_subtargets(self, name: str) -> list:
        """Present sub-target kinds for an agent (in config), as ('ultrawork'|'compaction').

        `read_map`, not `(cfg.get("agents") or {})`: that rescues `null` but not a TRUTHY
        non-dict, so a hand-edited `"agents": "oops"` reached `.get` on a str and killed the app
        during the first render — while `omodel check` called the same config healthy."""
        agent = session_mod.read_map(self.cfg, "agents").get(name)
        if not isinstance(agent, dict):
            return []
        return [k for k in _SUBKINDS if isinstance(agent.get(k), dict)]

    def _populate_targets(self, select: str | None = None) -> None:
        targets = self.query_one("#targets", OptionList)
        # Preserve highlight across rebuilds (e.g. after `a` adds a sub-target). `select`, when
        # given, overrides — restore the cursor straight to that option id (used by undo/redo so
        # the pane lands on the final target without first highlighting a fallback row, which
        # would queue a stale OptionHighlighted for the wrong target).
        prior = select
        if prior is None and targets.highlighted is not None:
            try:
                opt = targets.get_option_at_index(targets.highlighted)
                prior = opt.id
            except Exception:
                prior = None

        targets.clear_options()
        targets.add_option(Option("AGENTS", id="hdr:agents", disabled=True))
        for name in self.suggestions.agents:
            targets.add_option(Option(_lit(f"  {name}"), id=f"agent:{name}"))
            for kind in self._agent_subtargets(name):
                targets.add_option(Option(_lit(f"    ↳ {kind}"), id=f"agent:{name}.{kind}"))
        targets.add_option(Option("CATEGORIES", id="hdr:categories", disabled=True))
        for name in self.suggestions.categories:
            targets.add_option(Option(_lit(f"  {name}"), id=f"cat:{name}"))

        # Restore highlight to the prior id if it still exists, else first selectable row.
        restored = False
        if prior is not None:
            try:
                idx = self._index_of_option(targets, prior)
                targets.highlighted = idx
                restored = True
            except Exception:
                restored = False
        if not restored:
            # First non-header row.
            for i in range(targets.option_count):
                opt = targets.get_option_at_index(i)
                if opt.id and not opt.id.startswith("hdr:"):
                    targets.highlighted = i
                    break

    @staticmethod
    def _index_of_option(option_list: OptionList, option_id: str) -> int:
        for i in range(option_list.option_count):
            if option_list.get_option_at_index(i).id == option_id:
                return i
        raise KeyError(option_id)

    # ----- left pane (bottom): presets --------------------------------------------------

    def _aux(self) -> dict:
        """The out-of-cfg companion snapshot that must ride with every undo step: the typed
        off-chain rows, AND which preset is active. The second one is load-bearing — undoing a
        preset switch has to put the `●` back too, or the restored models would be folded into
        the preset you switched TO (see _projected_store)."""
        return {"custom_rows": self._custom_rows, "active": self._store.active}

    def _projected_store(self) -> presets_mod.Store:
        """The store as it would be WRITTEN: a copy whose ACTIVE entry carries the live cfg.
        → `Session.projected_store` (the invariant it upholds is documented there)."""
        return self.session.projected_store()

    def _store_is_dirty(self) -> bool:
        return self.session.store_is_dirty()

    @staticmethod
    def _fit_cells(text: str, limit: int) -> str:
        """Truncate to `limit` terminal CELLS (not code points — 24 CJK chars are 48 cells).
        A row that overflows the 32-wide pane wraps onto a second line, doubling the height of a
        card that is bounded at half the left column."""
        if cell_len(text) <= limit:
            return text
        out = ""
        for ch in text:
            if cell_len(out + ch) > limit - 1:
                break
            out += ch
        return out + "…"

    def _populate_presets(self, select: int | None = None) -> None:
        """Render one row per preset (`preset:0` … `preset:<n-1>`) plus the trailing
        `+ add preset…` row (`preset:new`).  `● ` marks the **active** preset — the one your edits
        are going into and the one `s` publishes to the config.  Called on mount and after every
        cfg mutation (via _record / _rerender_all), since a switch moves the marker.

        There is no "(empty)" row any more: the list is dense and unbounded, so a preset either
        exists or isn't there, and `+ add preset…` is where `enter` makes another — the same
        idiom (and now the same wording) as `+ add model…` in the candidate pane."""
        try:
            lst = self.query_one("#presets", OptionList)
        except Exception:
            return  # not mounted yet (a cfg edit during construction) — on_mount renders it
        prior = lst.highlighted if select is None else select
        lst.clear_options()
        # Name budget = the width rows are actually WRAPPED against, less the `● 12 ` prefix.
        # That width is `scrollable_content_region`, NOT `size` — `size.width` is the content
        # region and does not subtract the scrollbar, so measuring it overflowed every row by 2
        # cells as soon as the card scrolled (verified: 15 rendered lines for 13 rows). Reading
        # it before `clear_options()` is NOT a fix either: at the moment the list first outgrows
        # the card the scrollbar isn't there yet, so the pre-clear read is the pre-scrollbar
        # width. `scrollbar-gutter: stable` in the CSS is what makes this reliable — the gutter
        # is always reserved, so this number is the same whether the card scrolls or not.
        # The prefix grows a cell at 10 presets, hence deriving it from the count.
        prefix = 3 + len(str(max(len(self._store.presets), 1)))
        measured = lst.scrollable_content_region.width
        # Pre-layout (first render, width 0) the constant stands in; it is stated for a 4-cell
        # prefix, so a wider one comes out of the name's share rather than off the end.
        fallback = _PRESET_NAME_CELLS - (prefix - 4)
        room = max(8, measured - prefix) if measured else max(8, fallback)
        for i, preset in enumerate(self._store.presets):
            active = i == self._store.active
            name = self._fit_cells(preset.name, room)
            lst.add_option(
                Option(_lit(f"{'● ' if active else '  '}{i + 1} {name}"), id=f"preset:{i}")
            )
        lst.add_option(Option("+ add preset…", id=PRESET_NEW))
        if prior is None and lst.option_count:
            # Seed the cursor when the rows are built, not on focus: `tab` is the only way in and
            # Textual's OptionList does not auto-highlight on focus — an unseeded card swallows
            # `enter`/`a`/`x` entirely.
            prior = 0
        if prior is not None and 0 <= prior < lst.option_count:
            lst.highlighted = prior

    def _preset_index(self, option_id):
        """`preset:<i>` → i, or None for anything else — `preset:new` included, which every
        caller has to handle separately (it addresses no preset)."""
        if not option_id or not option_id.startswith("preset:"):
            return None
        try:
            idx = int(option_id[len("preset:"):])
        except ValueError:
            return None
        return idx if 0 <= idx < len(self._store.presets) else None

    def _highlighted_preset_id(self):
        """The option id under the cursor in `#presets` — `preset:<i>`, `preset:new`, or None."""
        try:
            lst = self.query_one("#presets", OptionList)
        except Exception:
            return None
        hi = lst.highlighted
        if hi is None:
            return None
        try:
            return lst.get_option_at_index(hi).id
        except Exception:
            return None

    def _highlighted_preset_index(self):
        return self._preset_index(self._highlighted_preset_id())

    def _presets_focused(self) -> bool:
        """Whether `#presets` owns focus — `a`/`x`/`v` dispatch on this (they were focus-blind
        before this pane existed)."""
        try:
            return self.focused is self.query_one("#presets", OptionList)
        except Exception:
            return False

    def _ask_sync(self) -> None:
        """Launch reconciliation, the one case the invariant can't cover on its own: the config
        on disk matches NO preset, because something outside oModel wrote it.

        Both answers end at `s` — neither writes anything here, so the app never changes a file
        you didn't ask it to. Adopting is already true in memory (the active preset's content IS
        cfg), so it just needs saving; restoring replaces cfg with the preset's models, which
        then shows up as an ordinary staged edit with a diff you can read before it lands."""
        preset = self._store.current()
        if preset is None:
            return

        def _choice(adopt) -> None:
            if adopt is None:
                # esc — decide later. Adopting is already true in memory (the active preset's
                # content IS cfg), so this leaves everything exactly as it is.
                return
            if adopt:
                self.notify(
                    f"'{preset.name}' will take your config's models — press s to save.",
                )
                return
            agents, categories = presets_mod.assignments(preset)
            self.cfg["agents"] = agents
            self.cfg["categories"] = categories
            if self._record(f"restore preset '{preset.name}' over the config"):
                self._restore_state(self._history.current_state())
            else:
                self._rerender_all()
            self.notify(f"Config will be rewritten from '{preset.name}' — press s to save.")

        self.push_screen(
            ConfirmModal(
                "Your config changed outside oModel",
                f"It no longer matches any preset. The one you were using is "
                f"'{preset.name}'.\n\n"
                "  • Adopt the config — that preset takes the models now in the file.\n"
                "  • Restore the preset — the file goes back to the preset's models.\n\n"
                "Nothing is written either way until you press s.",
                yes_label="Adopt the config",
                no_label="Restore the preset",
                hints="y adopt · n restore · esc decide later",
                escape_cancels=True,
            ),
            _choice,
        )

    def _switch_preset(self, index: int) -> None:
        """`enter` — make preset `index` the one you're editing.

        The edits you made while on the OLD preset are folded into it first (via
        _projected_store), so switching back and forth never loses work. Then cfg is REPLACED by
        this preset's assignments — a target it doesn't define is cleared, because a preset is a
        complete state, not an overlay. Staged only: the config on disk still reflects the
        preset you came from until you press `s`, which is what keeps "the config always equals
        one of the presets" true at rest."""
        preset = self._store.presets[index]
        if index == self._store.active:
            self.notify(f"Already editing '{preset.name}'.")
            return
        came_from = self._store.active
        self.session.switch_preset(index)  # banks the in-flight edits into the preset you leave
        # `_record` no-ops when the two presets hold identical models; the active index has
        # still changed, so re-render either way — and only take the aux-restoring path when a
        # snapshot was actually pushed (a no-op push would leave the old active in aux).
        if self._record(f"switch to preset '{preset.name}'"):
            self._restore_state(self._history.current_state())
        else:
            # Identical models, so nothing was pushed — but the switch IS an action, and an
            # untruncated redo tail would let `ctrl+r` resurrect an undone edit AND jump the `●`
            # to a preset the user just left.
            self._history.drop_redo()
            self._history.map_aux_key(
                "active", lambda a: _retarget_active(a, came_from, self._store.active)
            )
            self._rerender_all()
        self.notify(f"Now editing '{preset.name}' — s writes it to your config.")

    def _add_preset(self) -> None:
        """`a` — copy the models you're looking at into a NEW preset, name it, and switch to it.
        This is how presets 2..n come into being: add one from the state you're in, then diverge.

        Row-blind on purpose — it always APPENDS, whatever the cursor is on. `a` used to overwrite
        the highlighted preset instead (the name modal doubling as the confirm), which put a
        destructive, non-undoable action under the key that means *add* in every other pane: one
        habitual `a` then a reflexive `enter` and that preset's models were gone. Replacing a
        preset's models now means switching to it and editing, which `u` can walk back.

        Staged like everything else — `s` persists it, quitting without saving drops it."""

        def _named(text) -> None:
            if text is None:
                return
            came_from = self._store.active
            self._store = self._projected_store()  # bank in-flight edits into the old preset
            at = len(self._store.presets)
            name = presets_mod.sanitize_name(text, at)
            self._store.presets.append(presets_mod.capture(name, self.cfg))
            self._store.active = at
            # Adding changes `active` without changing cfg, so it pushes no history entry — the
            # entries recorded on the preset you were sitting on have to follow you here, or the
            # next `u` would quietly move the `●` back (and fold the restored models into the
            # preset you'd left). Only those entries: see `_retarget_active`.
            self._history.map_aux_key("active", lambda a: _retarget_active(a, came_from, at))
            self._populate_presets(select=at)
            self.notify(f"Now editing '{name}' (preset {at + 1}) — s to save.")

        self.push_screen(PresetNameModal(), _named)

    def _rename_preset(self, index: int) -> None:
        """`r` on a preset row — change its name, nothing else.

        Deliberately does NOT restamp `saved_at`: that stamp answers "when were these models
        banked", and renaming banks nothing. Staged like every other preset edit; the ACTIVE
        preset's name is read back through `_projected_store` (which rebuilds it from cfg under
        `current.name`), so renaming the one you're on sticks too."""
        preset = self._store.presets[index]

        def _named(text) -> None:
            if text is None:
                return
            name = presets_mod.sanitize_name(text, index)
            if name == preset.name:
                return
            preset.name = name
            self._populate_presets(select=index)
            self.notify(f"Renamed preset {index + 1} to '{name}' — s to save.")

        self.push_screen(PresetNameModal(index, preset), _named)

    def _delete_preset(self, index: int) -> None:
        """`x` — drop preset `index`, behind a confirm.

        REFUSED on the active preset: the config on disk mirrors it, so deleting it would strand
        the config as a state matching no preset — exactly the orphan the whole design exists to
        prevent. Switch somewhere else first. (That also guarantees you can never reach zero
        presets — with one left it IS the active one, so `x` refuses.)"""
        preset = self._store.presets[index]
        if index == self._store.active:
            self.bell()
            self.notify(
                f"'{preset.name}' is the preset you're editing — switch to another one first.",
                severity="warning",
            )
            return

        def _confirm(ok) -> None:
            if not ok:
                return
            del self._store.presets[index]
            if self._store.active > index:
                self._store.active -= 1
            presets_mod.normalize_active(self._store)
            # The list is DENSE, so removing an entry renumbers every later preset — and the undo
            # history stores which preset was active per entry. Remap them in the same breath, or
            # a later `u` would restore models into whichever preset slid into that number.
            # Deliberately NOT `set_aux_key` (one value everywhere): entries legitimately differ,
            # and an entry pointing at the preset that just went away must stay recognizable —
            # `_DELETED` is what lets `_restore_state` say so instead of picking silently.
            self._history.map_aux_key("active", lambda a: _shift_active(a, index))
            # Land on the row that took this one's place, or the new last preset when you
            # deleted the tail — never on `+ add preset…`, which is not a preset and would leave
            # `x`/`r` inert under a cursor that just did something.
            self._populate_presets(select=min(index, len(self._store.presets) - 1))
            self.notify(f"Deleted preset {index + 1} — s to save.")

        count = presets_mod.model_count(preset)
        self.push_screen(
            ConfirmModal(
                f"Delete preset {index + 1}?",
                f"'{preset.name}' — {count} model{'' if count == 1 else 's'}, saved "
                f"{preset.saved_at or 'unknown'}.\n\n"
                "Takes effect on your next save; quitting without saving keeps it.",
            ),
            _confirm,
        )

    # ----- target → cfg node helpers ---------------------------------------------------

    def _node_for(self, target: str):
        """The dict node holding {model, variant} for `target` in cfg, or None if its parent
        agent/category isn't present. Does NOT create nodes. → `Session.node_for`."""
        return self.session.node_for(target)

    def _ensure_node(self, target: str) -> dict:
        """The cfg node for `target`, creating it if needed. → `Session.ensure_node`."""
        return self.session.ensure_node(target)

    def _current_assignment(self, target: str):
        """(model_str, variant) currently assigned in cfg for `target`; ('', None) if unset.
        model_str is the full 'provider/model' as stored. → `Session.assignment`."""
        return self.session.assignment(target)

    @staticmethod
    def _gpt_only(target: str) -> bool:
        """True if `target` (incl. its sub-targets) belongs to a GPT-exclusive agent —
        currently Hephaestus (see session.GPT_ONLY_AGENTS). Such agents hide the add-model escape
        hatch and show a tip; the fallbackChain is the only valid source."""
        return session_mod.gpt_only(target)

    # ----- right pane: detail + candidates ---------------------------------------------

    def _build_rows(self, target: str) -> list:
        """Candidate rows for `target`: resolver.candidates(target) when a resolver exists (the
        chain-only pick list), plus session-typed custom rows, plus the current off-chain
        assignment (so a model that's set but not in the chain is still shown). In degraded mode
        (no resolver) the chain is empty, leaving just those last two. Cached per target so staged
        edits survive re-highlight; the cache is dropped whenever the assignment changes (e.g.
        _stage_row, action_clear, sub-target delete, state restore, refresh) so the synthesized row
        below tracks cfg."""
        if target in self._rows:
            return self._rows[target]
        # `_custom_rows` is the TUI's own store (snapshotted with the undo history), so it is
        # passed IN rather than owned by the session — a CLI process has no add-model modal.
        # The per-target cache below is likewise a UI concern: it exists so staged edits survive
        # a re-highlight, and it is what makes the synthesized current-assignment row appear and
        # vanish in lockstep with cfg (every mutation drops it).
        rows = self.session.rows(target, self._custom_rows.get(target, []))
        # Re-apply any pending `v` pick (see _pending_variants). session.rows() rebuilds chain
        # rows from omo's fallbackChain, so without this the pick is reverted on every rebuild.
        # `warn` is deliberately left alone: it is recomputed per (provider, model) by resolve
        # and this only overrides the variant, matching what the in-place mutation used to do.
        pending = self._pending_variants.get(target)
        if pending:
            for row in rows:
                key = f"{row['provider']}/{row['model']}"
                if key in pending:
                    row["variant"] = pending[key]
        self._rows[target] = rows
        return rows

    def _render_detail(self, target: str) -> None:
        detail = self.query_one("#detail", Static)
        model, variant = self._current_assignment(target)
        # Header mirrors the `label: value` spacing below it — give `agent:`/`cat:` the same
        # space after the colon as `model: `/`variant: ` so the values line up.
        # This is the ONE widget rendered with markup on (`[b]`, `[dim]`), so every value spliced
        # in is `_esc`aped: the target is a config KEY and the model/variant are config VALUES —
        # a `[` in any of them would be parsed as a tag (module-top note).
        lines = [f"[b]{_esc(target.replace(':', ': ', 1))}[/b]"]
        if model:
            lines.append(f"model: {_esc(model)}")
            lines.append("variant: " + (_esc(variant) if variant else "—"))
            # Detail line from catalog (display only); the assignment splits on the first '/'
            # into provider + bare id, and the PROVIDER rides along so the pane describes the
            # (provider, model) actually assigned — an `opencode/x` assignment shows the
            # gateway's record (cost can differ per provider), never silently the dedicated
            # provider's. Always reserve this row so switching targets refreshes its text in
            # place rather than adding/removing a line (which makes the pane jump). Cache hit →
            # render the line now; miss → a dim placeholder holds the slot while the ~3s
            # background fetch runs, and the worker's re-render swaps in the real content.
            prov = model.split("/", 1)[0] if "/" in model else None
            bare = model.split("/", 1)[1] if "/" in model else model
            info = self._detail_info(target, prov, bare)
            if info:
                lines.append(_esc(self._detail_line(info)))
            elif self._detail_key(prov, bare) in self._detail_cache:
                lines.append("")  # fetch done, no detail available — keep the slot blank
            else:
                lines.append("[dim]…[/dim]")  # fetch pending — keep the slot, fill on arrival
        else:
            lines.append("model: — (unset)")
            lines.append("variant: —")
        if self._gpt_only(target):
            lines.append("⚑ GPT-only: Hephaestus needs a GPT model (omo) — non-GPT is blocked.")
        detail.update("\n".join(lines))

    @staticmethod
    def _detail_key(provider: str | None, bare: str) -> str:
        """_detail_cache key: the provider-qualified 'provider/bare' — the pane describes a
        (provider, model) pair, and the same model's record can differ per provider (gateway
        vs dedicated cost) — or the bare id alone for a prefix-less (hand-edited) assignment."""
        return f"{provider}/{bare}" if provider else bare

    def _detail_info(self, target: str, provider: str | None, bare: str):
        """Cached `catalog.detail(bare, provider=provider)` for the detail pane. Returns the
        info dict (or None) when already known; on a cache miss schedules a background fetch
        and returns None so the base detail renders immediately. `catalog.detail()` is a ~3s
        `opencode --verbose` subprocess — it must never run on the UI thread (highlighting has
        to stay smooth)."""
        key = self._detail_key(provider, bare)
        if key in self._detail_cache:
            return self._detail_cache[key]
        # No connected provider serves it → detail() would no-op; cache None, skip the worker.
        if not self.catalog.providers_for(bare):
            self._detail_cache[key] = None
            return None
        self._schedule_detail_fetch(target, provider, bare)
        return None

    def _schedule_detail_fetch(self, target: str, provider: str | None, bare: str) -> None:
        """Debounce (~0.2s) so scrolling doesn't fetch per row, and never start a second
        fetch while one is in flight — the running fetch re-renders the current target on
        completion, which reschedules from here if it's still uncached."""
        if self._detail_fetching or self._detail_key(provider, bare) in self._detail_cache:
            return
        if self._detail_timer is not None:
            self._detail_timer.stop()
        self._detail_timer = self.set_timer(
            0.2, lambda: self._fetch_detail(target, provider, bare)
        )

    @work(group="detail")
    async def _fetch_detail(self, target: str, provider: str | None, bare: str) -> None:
        """Background worker: run the blocking ~320 MB `catalog.detail()` subprocess off the
        event loop, cache it, then re-render the CURRENT target. At most one fetch runs at a
        time (the `_detail_fetching` gate, since a spawned subprocess can't be killed); the
        post-render reschedules the next fetch if the current target still needs one."""
        key = self._detail_key(provider, bare)
        if self._detail_fetching or key in self._detail_cache:
            return
        generation = self._detail_generation
        self._detail_fetching = True
        failed = False
        try:
            info = await _to_thread_daemon(self.catalog.detail, bare, provider=provider)
        except Exception:
            info = None
            failed = True
        finally:
            self._detail_fetching = False
        # If a refresh (r) cleared the cache while we were off-thread, this result describes
        # the pre-refresh catalog — drop it instead of repopulating the cleared cache. The
        # re-render below still runs, so the current target schedules a fresh fetch. A
        # TRANSIENT failure (the `except` above — catalog.detail raised) is likewise never
        # cached, so the next highlight retries rather than blanking this model's detail line
        # for the rest of the session; a genuine `None` RETURN (no record for this model / no
        # providers) still caches as "known-empty", same as before. Remaining limitation: a
        # timeout swallowed INSIDE catalog.detail's own try/except still returns None and is
        # indistinguishable from "known-empty" here — only an exception that escapes catalog.detail
        # is treated as transient.
        if not failed and self._detail_generation == generation:
            self._detail_cache[key] = info
        # Re-render whatever is current NOW: shows the line if this was it, and (via
        # _detail_info → _schedule_detail_fetch) kicks off the next fetch if still uncached.
        #
        # The CANDIDATE rows go too, not just the detail line: this call also rewrote
        # `verbose-<prov>`, which is where `catalog.variants_for` reads the variant sets that
        # resolve validates omo's suggestion against (the `⚠ variant` marker) and that `v`
        # offers. Rows resolved before it landed were computed against whatever the cache held
        # then, and `_rows` would otherwise pin that until the next mutation dropped it — so the
        # correction surfaced when you pressed enter, reading as "the list changed under me".
        # Clear ALL targets (the new verbose covers every model of this provider, not just the
        # one fetched); they rebuild lazily on highlight, and _restore_cand_highlight puts the
        # cursor back by identity so a re-render under you is invisible.
        #
        # …but NOT under an open VariantModal. `v`'s callback (action_variant._apply) holds a row
        # dict captured before the modal opened and RETURNS EARLY if `_rows[target][idx]` is no
        # longer that same OBJECT — its way of yielding to an `r` refresh. Rebuilding rows from
        # under it trips that guard, and the pick is dropped before it can reach
        # `_pending_variants` (a pilot test covers exactly this).
        #
        # VariantModal specifically, NOT "any modal": that identity guard is the only holder of a
        # cached row across a callback, and gating on the whole screen stack meant a fetch landing
        # under, say, the `?` overlay skipped the rebuild — and then nothing ever retried it,
        # because the completed fetch is cached so no further fetch is scheduled and a re-highlight
        # hits the still-stale `_rows`. Rows stayed wrong for the rest of the session, which is the
        # very bug this re-render exists to fix. The residual window is now one open VariantModal,
        # and picking or dismissing it re-renders anyway.
        #
        # All of it is cosmetic, and this worker outlives the UI: `q` during an in-flight fetch
        # tears the widgets down while the daemon thread is still in opencode (it can't be
        # killed — see _to_thread_daemon), so the panes can be gone by the time we get here.
        # `query_one` then raises NoMatches, and an exception out of a @work worker surfaces as
        # WorkerFailed rather than being swallowed. Nothing to draw on is a no-op, not an error.
        if self._current_target is not None:
            try:
                self._render_detail(self._current_target)
                if not any(isinstance(s, VariantModal) for s in self.screen_stack):
                    self._rows.clear()
                    self._render_candidates(self._current_target)
            except NoMatches:
                return

    @staticmethod
    def _detail_line(info: dict) -> str:
        parts = []
        ctx = info.get("context")
        if ctx:
            parts.append(f"ctx {ctx // 1000}k" if ctx >= 1000 else f"ctx {ctx}")
        cost = info.get("cost") or {}
        if isinstance(cost, dict) and ("input" in cost or "output" in cost):
            parts.append(f"${cost.get('input', 0)}/${cost.get('output', 0)}")
        if info.get("reasoning"):
            parts.append("reasoning")
        if info.get("image"):
            parts.append("image")
        return " · ".join(parts) if parts else ""

    def _render_candidates(self, target: str) -> None:
        cands = self.query_one("#candidates", OptionList)
        cands.clear_options()
        rows = self._build_rows(target)
        # Mark (●) the row matching the current assignment for this target: at launch that's
        # what oh-my-openagent.jsonc has on disk, and it follows your pick as you stage edits.
        current, _ = self._current_assignment(target)
        for i, row in enumerate(rows):
            matched = bool(current) and f"{row['provider']}/{row['model']}" == current
            label = ("● " if matched else "  ") + _row_label(row)
            cands.add_option(Option(_lit(label), id=f"cand:{i}"))
        cands.add_option(Option("+ add model…", id="cand:add"))
        # clear_options() reset the cursor to None; put it back where this target last had it.
        self._restore_cand_highlight(target, rows)

    @staticmethod
    def _cand_identity(rows: list, option_id: str | None):
        """Stable identity for a candidate option — index-independent so it still resolves after
        a refresh re-orders/adds/drops chain rows. The '+ add model…' row → the sentinel
        'cand:add'; a model row 'cand:<i>' → its 'provider/model'. None if it maps to neither."""
        if option_id == "cand:add":
            return "cand:add"
        if not option_id or not option_id.startswith("cand:"):
            return None
        try:
            i = int(option_id[len("cand:"):])
        except ValueError:
            return None
        if 0 <= i < len(rows):
            row = rows[i]
            return f"{row['provider']}/{row['model']}"
        return None

    def _restore_cand_highlight(self, target: str, rows: list) -> None:
        """Re-highlight the candidate `target` last had under the cursor (kept in _cand_choice),
        matched by identity. No remembered choice, or it's gone from the list now (e.g. the model
        dropped off the chain after a refresh) → leave the pane un-highlighted, as on a fresh
        target. Row index == option index by construction, so set `highlighted` directly."""
        ident = self._cand_choice.get(target)
        if ident is None:
            return
        cands = self.query_one("#candidates", OptionList)
        if ident == "cand:add":
            try:
                cands.highlighted = self._index_of_option(cands, "cand:add")
            except Exception:  # noqa: S110 - cosmetic highlight; a failure must not break the key
                pass
            return
        for i, row in enumerate(rows):
            if f"{row['provider']}/{row['model']}" == ident:
                cands.highlighted = i
                return

    def _refresh_right(self, target: str) -> None:
        self._current_target = target
        self._render_detail(target)
        self._render_candidates(target)

    def action_help(self) -> None:
        """`?` — open the full key reference overlay. Base-screen-only (gated in check_action):
        a modal already carries its own hint line, and `esc` closes it first. The hint bar
        advertises only save/help/quit, so this overlay is where every other key is documented."""
        self.push_screen(HelpModal())

    # ----- events ----------------------------------------------------------------------

    @on(OptionList.OptionHighlighted, "#targets")
    def _target_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        oid = event.option_id
        if not oid or oid.startswith("hdr:"):
            return
        self._refresh_right(oid)

    @on(OptionList.OptionHighlighted, "#candidates")
    def _candidate_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Remember this target's highlighted candidate so it survives a re-render / refresh
        (see _restore_cand_highlight)."""
        cands = self.query_one("#candidates", OptionList)
        target = self._current_target
        # Record only the *settled* highlight of the *current* render. OptionHighlighted is
        # queued (posted by watch_highlighted), so a stale one can arrive after a newer move —
        # or after a fast target switch re-rendered the pane for a different target (key-repeat
        # can queue two #targets moves before either is handled, advancing _current_target while
        # this event still describes the prior render). Both show as option_index != the live
        # highlighted; skipping them keeps one target's row from being stamped onto another's
        # memory. A bare re-render leaves the cursor at None (no event), so this never no-ops a
        # genuine move.
        if target is not None and event.option_index == cands.highlighted:
            ident = self._cand_identity(self._build_rows(target), event.option_id)
            if ident is not None:
                self._cand_choice[target] = ident

    @on(OptionList.OptionHighlighted, "#presets")
    def _preset_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Summarise the highlighted preset in THIS CARD's border subtitle — deliberately not in
        `#detail`, which has two ASYNC writers (the detail worker's tail and _refresh_catalog)
        that re-render whatever `_current_target` is at completion time and would silently
        clobber it.  The subtitle has exactly one writer, and a preset row schedules no
        catalog.detail fetch, so the one-concurrent-fetch rule (cache.py) is untouched."""
        lst = self.query_one("#presets", OptionList)
        idx = self._preset_index(event.option_id)
        # Read through the projection so the ACTIVE row's count reflects your in-flight edits
        # (its content is cfg, not what the store last held).
        store = self._projected_store()
        preset = store.presets[idx] if idx is not None else None
        if preset is None:
            lst.border_subtitle = ""
            return
        # '2026-07-26T09:14:03Z' → '07-26'; a hand-written/absent stamp falls through as-is.
        # _esc: a border subtitle is parsed as markup too, and saved_at comes off a JSON file a
        # user can hand-edit (module-top note).
        stamp = preset.saved_at[5:10] if len(preset.saved_at) >= 10 else preset.saved_at
        count = presets_mod.model_count(preset)
        prefix = f"saved {_esc(stamp)} · " if stamp else ""
        lst.border_subtitle = f"{prefix}{count} model{'' if count == 1 else 's'}"

    @on(OptionList.OptionSelected, "#presets")
    def _preset_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id == PRESET_NEW:
            # Same as `a` here: `enter` on `+ add preset…` is the obvious gesture, and the row
            # would otherwise be inert under the key that activates every other row.
            self._add_preset()
            return
        idx = self._preset_index(event.option_id)
        if idx is not None:
            self._switch_preset(idx)

    @on(OptionList.OptionSelected, "#candidates")
    def _candidate_selected(self, event: OptionList.OptionSelected) -> None:
        oid = event.option_id
        if oid == "cand:add":
            self._open_add_modal()
        elif oid and oid.startswith("cand:"):
            try:
                idx = int(oid[len("cand:"):])
            except ValueError:
                return
            self._set_candidate(idx)

    # ----- staging ---------------------------------------------------------------------

    def _candidates_focused(self) -> bool:
        """Whether `#candidates` owns focus. `x` dispatches on this so its row-scoped meaning
        (drop the row you added) can only fire with a candidate actually under the cursor — from
        `#targets` the same key still means clear-this-target, where no candidate is in play."""
        try:
            return self.focused is self.query_one("#candidates", OptionList)
        except Exception:
            return False

    def _highlighted_candidate_index(self):
        cands = self.query_one("#candidates", OptionList)
        hi = cands.highlighted
        if hi is None:
            return None
        try:
            opt = cands.get_option_at_index(hi)
        except Exception:
            return None
        oid = opt.id or ""
        if not oid.startswith("cand:") or oid == "cand:add":
            return None
        try:
            return int(oid[len("cand:"):])
        except ValueError:
            return None

    @staticmethod
    def _target_label(target: str) -> str:
        """Short human name for a target id, for undo/redo notifications:
        'agent:sisyphus' → 'sisyphus', 'agent:sisyphus.ultrawork' → 'sisyphus.ultrawork',
        'cat:deep' → 'deep'. → `session.target_label`."""
        return session_mod.target_label(target)

    def _is_dirty(self) -> bool:
        """True iff a save would change anything on disk — the config OR the presets file. Both,
        because `s` writes both and quitting discards both. Used by quit (`q`).
        → `Session.is_dirty`."""
        return self.session.is_dirty()

    def _record(self, label: str) -> bool:
        """Snapshot the current cfg into the undo history under `label` (a no-op if nothing
        actually changed). Call after ANY cfg mutation — this single chokepoint is what makes
        every operation undoable. `_custom_rows` (off-chain typed models) and the active preset
        index ride along as the entry's `aux` (see `_aux`), so a restore moves both in lockstep
        with cfg — undoing an add-model drops its row, and undoing a preset switch moves the `●`
        back with the models.

        Returns whether a snapshot was actually pushed. `_switch_preset` needs that: a no-op
        push leaves the previous entry's aux in place, and restoring from it would undo the very
        switch that called this."""
        pushed = self._history.push(self.cfg, label, aux=self._aux())
        # Your edits go into the preset you're on, and its row shows their model count — so the
        # pane follows every staged edit, through the one chokepoint they all pass through.
        self._populate_presets()
        return pushed

    def _stage_row(self, target: str, row: dict, label: str) -> None:
        """Write the chosen candidate row into the cfg node, re-render, and record an undo
        snapshot under `label`."""
        self.session.set_row(target, row)
        # The assignment changed, so _build_rows' synthesized current-off-chain row may no longer
        # apply (picked a chain model) or now describes a different model — drop the cache so it
        # rebuilds from the new cfg value. The pending `v` picks go with it: the variant is in cfg
        # now (undo/redo carries it from there), so re-applying them would pin a stale override
        # over whatever the assignment becomes next.
        self._rows.pop(target, None)
        self._pending_variants.pop(target, None)
        self._refresh_right(target)
        self._record(label)

    def _set_candidate(self, idx: int) -> None:
        if self._current_target is None:
            return
        rows = self._build_rows(self._current_target)
        if not (0 <= idx < len(rows)):
            return
        row = rows[idx]
        self._stage_row(
            self._current_target,
            row,
            f"set {self._target_label(self._current_target)} → {row['provider']}/{row['model']}",
        )

    # ----- actions / keybindings -------------------------------------------------------

    def action_focus_targets(self) -> None:
        """`←` / `h` — focus the targets (left) pane."""
        self.query_one("#targets", OptionList).focus()

    def action_focus_candidates(self) -> None:
        """`→` / `l` — focus the candidates (right) pane."""
        self.query_one("#candidates", OptionList).focus()

    # NOTE: `#presets` has NO dedicated focus action/key. `tab` / `shift+tab` (Textual's own
    # Screen traversal, DOM order targets → presets → candidates, wrapping) already reach every
    # pane in both directions, so a `p` shortcut was pure duplication — one more key to learn and
    # to keep out of the way of a future binding. `←`/`→` stay targets-vs-candidates only.

    def check_action(self, action: str, parameters) -> bool:
        """Gate the base-screen-only keys — pane-crossing (`←`/`→` + vim `h`/`l`), undo/redo, and
        the `?` help overlay — to the base screen: a ModalScreen manages its own focus and keys, so
        e.g. `←` inside the variant modal must not reach down to the (hidden) #targets list. (Defense-
        in-depth: Textual already truncates the binding chain at a modal, so these app bindings can't
        fire while one is up — and the add-model Input's own ←/→ cursor bindings take precedence
        regardless.) All other actions stay enabled."""
        if action == "command_palette" and isinstance(self.screen, AddModelModal):
            # Ctrl-P drives the add-model fuzzy list (up) while that modal is open, so suppress the
            # App's *priority* command-palette binding there (a priority binding is checked from the
            # App down, before the key reaches the modal — only check_action can gate it). The
            # palette stays available everywhere else; Ctrl-N is not an app binding.
            return False
        if action in (
            "focus_targets",
            "focus_candidates",
            "undo",
            "redo",
            "help",
        ):
            # Pane-crossing focus, undo/redo, AND the `?` help overlay are base-screen-only: a
            # ModalScreen manages its own focus and keys (e.g. AddSubModal binds `u` to pick
            # ultrawork), so the app's `u`/`ctrl+r` must not reach down through a modal; and `?`
            # over a modal is pointless (esc closes the modal first). (Textual already truncates
            # the binding chain at a modal; this is the explicit, matching guard.)
            return len(self.screen_stack) <= 1
        return True

    def notify(self, message: str, **kwargs) -> None:
        """Every toast, rendered LITERALLY (`markup=False` unless a caller insists otherwise).

        A one-line choke point rather than escaping at ~20 call sites: the messages quote model
        ids, preset names, undo labels and `str(exc)` (which carries file paths), so any of them
        can contain `[` — and a toast that raises `MarkupError` takes the app down while merely
        reporting something. See the "rendering user data safely" note at the top of the module."""
        kwargs.setdefault("markup", False)
        super().notify(message, **kwargs)

    def action_help_quit(self) -> None:
        """`ctrl+c` — Textual's built-in "you probably meant to quit" toast, re-worded for us.

        The base implementation notifies ``f"Press [b]{key}[/b] to quit the app"`` — content
        markup, which the `markup=False` default above (rightly) does not parse, so the tags
        showed up as literal `[b]`/`[/b]`. It also names whichever binding runs `quit`, i.e.
        Textual's own `ctrl+q`, which drops straight into `App.action_quit` and exits WITHOUT
        the unsaved-changes prompt. `q` (`action_quit_confirm`) is the quit that offers to save
        config + presets first, so that is the key to point a reflex `ctrl+c` at. Our key, our
        wording, no markup — do NOT re-introduce tags here."""
        self.notify("Press q to quit the app", title="Do you want to quit?")

    def action_clear(self) -> None:
        """`x` — get rid of the thing under the cursor. On a `#candidates` row YOU added it drops
        that row (`_remove_custom_row`). Otherwise it clears/deletes the current TARGET: on a base
        agent/category row it clears the assignment (drops model/variant, keeps the row); on an ↳
        ultrawork/compaction SUB-target it deletes the whole row — a cleared sub-object serializes
        away anyway (config_io drops empty sub-objects), so a model-less placeholder isn't worth
        keeping — for a sub-target clear == delete, which is also how you undo a stray `a` add
        without reaching for `u`. Undoable (`u`) — a fat-fingered `x` is one keystroke from being
        reverted.

        The added-row branch comes FIRST, and is gated on `#candidates` having focus: a row you
        typed is the one thing in that pane you can delete, and reading the cursor is the only way
        `x` doesn't clear a model you aren't pointing at (it used to — see _remove_custom_row).
        Chain rows keep the clear meaning: they're omo's data, not yours to remove.

        On a `#presets` row it deletes that PRESET instead, behind a confirm — the one `x` that
        is not undoable (the presets file lives outside the cfg history); on `+ add preset…` there
        is nothing to delete, so it bells. This action was focus-blind before the card existed."""
        if self._presets_focused():
            idx = self._highlighted_preset_index()
            if idx is None:
                self.bell()
            else:
                self._delete_preset(idx)
            return
        target = self._current_target
        if target is None:
            return
        if self._candidates_focused() and self._remove_custom_row(target):
            return
        if target.startswith("agent:") and "." in target[len("agent:"):]:
            name, kind = target[len("agent:"):].split(".", 1)
            self._delete_subtarget(target, name, kind)
            return
        self.session.clear(target)
        # Drop the cache so _build_rows stops synthesizing the now-cleared off-chain row.
        self._rows.pop(target, None)
        self._refresh_right(target)
        self._record(f"clear {self._target_label(target)}")

    def _remove_custom_row(self, target: str) -> bool:
        """`x` on a `#candidates` row you added yourself — drop that row. Returns whether it
        fired, so `action_clear` falls through to its clear/delete meanings for every other row.

        Only `_custom_rows` entries qualify: models typed into the add-model modal, kept as rows
        so one stays pickable after you try something else. That persistence is what made this
        the one row with no way out — the chain rebuilds from omo's data and `_build_rows`'
        synthesized off-chain row is derived from cfg (it vanishes on its own when the assignment
        moves, and clearing is what removes it), but a typed row outlived every key in the app.
        Meanwhile `x` read only `_current_target`, so pressing it here cleared whatever model was
        assigned — a model you weren't pointing at — and left the row sitting there.

        When the row IS the assignment the two go together, clear == delete as on a sub-target
        row: a model left set on a target whose row just disappeared is the one state this pane
        must not show. That branch touches cfg, so it records an undo entry. Dropping a row that
        ISN'T assigned changes nothing that will ever be written — it is pane state, not an edit —
        so it deliberately records none (`History.push` is cfg-only by contract, and `a` re-adds
        the row in one keystroke)."""
        customs = self._custom_rows.get(target) or []
        idx = self._highlighted_candidate_index()
        if not customs or idx is None:
            return False
        rows = self._build_rows(target)
        if not 0 <= idx < len(rows):
            return False
        row = rows[idx]
        # Match on OBJECT IDENTITY, not `provider/model`: `_build_rows` appends the very dicts
        # held in `_custom_rows`, so `is` says exactly "this rendered row came from there".
        # Comparing ids would misfire on a model you added that the chain ALSO offers — the two
        # dedupe to one CHAIN row (History.push's docstring calls out that case), and `x` on it
        # would silently drop the shadowed entry instead of clearing, which is the chain-row
        # behaviour this fix promises to leave alone.
        keep = [r for r in customs if r is not row]
        if len(keep) == len(customs):
            return False  # a chain row, or the synthesized off-chain one — not yours to delete
        ident = f"{row['provider']}/{row['model']}"
        if keep:
            self._custom_rows[target] = keep
        else:
            self._custom_rows.pop(target, None)
        clears = self._current_assignment(target)[0] == ident
        if clears:
            node = self._node_for(target)
            if isinstance(node, dict):
                node.pop("model", None)
                node.pop("variant", None)
        # The remembered cursor names a row that is about to stop existing. Re-aim it at whatever
        # is assigned (its `●` row) so the pane doesn't come back un-highlighted under your hand;
        # nothing assigned (we just cleared it) → forget it, and it renders like a fresh target.
        if self._cand_choice.get(target) == ident:
            still = self._current_assignment(target)[0]
            if still:
                self._cand_choice[target] = still
            else:
                self._cand_choice.pop(target, None)
        self._rows.pop(target, None)
        self._refresh_right(target)
        if clears:
            self._record(f"clear {self._target_label(target)}")
        return True

    def _delete_subtarget(self, target: str, name: str, kind: str) -> None:
        """`x` on an ↳ ultrawork/compaction row — remove the sub-target outright, dropping the
        cfg node (along with any model it held) plus its off-chain typed rows and cached resolver
        rows, so re-adding the same sub-target later starts clean rather than resurrecting a stale
        ⚠ row. Rebuilds the left pane onto the parent agent and records an undoable snapshot — the
        `_custom_rows` ride along as the entry's `aux`, so `u` restores the row in lockstep with cfg."""
        self.session.delete_subtarget(name, kind)
        self._custom_rows.pop(target, None)
        # Pending `v` picks go for the same "starts clean" reason as the typed rows above: the
        # target is gone, so nothing is pending on it — and re-adding the sub-target later would
        # otherwise resurrect the pick and re-apply it to the rebuilt row.
        self._pending_variants.pop(target, None)
        self._rows.pop(target, None)
        parent = f"agent:{name}"
        self._populate_targets(select=parent)
        self._refresh_right(parent)
        self._record(f"delete {kind} sub-target from {name}")

    def action_variant(self) -> None:
        """`v` — pick from the variants opencode reports for the highlighted candidate's
        (provider, model) (catalog.variants_for — the cached `--verbose` map). A model opencode
        lists with no variants (kimi) — or whose verbose isn't cached anywhere — has nothing to
        pick, so `v` just bells (no fallback: variant validity is opencode's, not the heuristic's).

        Bells on a `#presets` row too: a variant is meaningless there, and this action reads the
        (now hidden) candidate pane's highlight, so unguarded it would silently retarget it."""
        if self._presets_focused():
            self.bell()
            return
        target = self._current_target
        if target is None:
            return
        idx = self._highlighted_candidate_index()
        if idx is None:
            return
        rows = self._build_rows(target)
        if not (0 <= idx < len(rows)):
            return
        row = rows[idx]
        # Drop a "none" variant opencode may list — the VariantModal already appends a synthetic
        # "(none)" clear row, so listing both is redundant (_is_no_variant).
        variants = [
            v for v in self.catalog.variants_for(row["provider"], row["model"])
            if not _is_no_variant(v)
        ]
        if not variants:
            # No variants opencode-reported for this (provider, model). Bell + a notify (the
            # detail pane may still show a stray `variant:` from an older config — surface why `v`
            # is a no-op; to clear such a stray, re-pick the row with Enter or `x`).
            self.bell()
            self.notify(f"No variants for {row['provider']}/{row['model']}.")
            return

        def _apply(result) -> None:
            if result is None:
                return
            # A background `r` refresh finishing while this modal was open clears/rebuilds the
            # per-target row cache (with fresh row dicts). If the row we captured is no longer the
            # object at `idx`, that refresh supersedes this edit — drop it (refresh already
            # re-rendered) rather than indexing a cleared/reshaped cache.
            cached = self._rows.get(target)
            if cached is None or not (0 <= idx < len(cached)) or cached[idx] is not row:
                return
            chosen = result or None  # '' sentinel → clear
            row["variant"] = chosen  # row is cached[idx] (guarded above) → mutation persists
            # `v` adjusts the highlighted candidate's variant but must NOT create or switch an
            # assignment (DESIGN §Events: only Enter sets a model). Restage only when THIS row —
            # by full provider/model, the same test the `●` marker uses — IS the current
            # assignment; matching the model alone would let `v` on a same-model/other-provider
            # row silently switch the provider. Otherwise just re-render so the chosen variant
            # rides along if the user later picks the row with Enter.
            model, _ = self._current_assignment(target)
            if model and model == f"{row['provider']}/{row['model']}":
                self._stage_row(
                    target,
                    row,
                    f"set {self._target_label(target)} variant → {chosen or '(none)'}",
                )
            else:
                # Not the assignment, so this pick reaches cfg only if you later press Enter —
                # until then it is pending state, and it has to survive a rebuild of `_rows`
                # (which is a cache, not a store). See _pending_variants.
                self._pending_variants.setdefault(target, {})[
                    f"{row['provider']}/{row['model']}"
                ] = chosen
                self._render_candidates(target)

        self.push_screen(VariantModal(variants), _apply)

    def action_edit_or_sub(self) -> None:
        """`a` — pane-contextual, one key (see HelpModal / DESIGN §Textual contract). Only a
        #targets *agent* row does "sub" (add an ultrawork/compaction sub-target); everywhere else
        — #candidates, or a #targets *category* row (categories have no sub-targets) — `a` opens
        the add/edit-model modal ("edit").

        In `#presets` it means "add" too: it APPENDS a new preset holding the current assignments,
        names it and switches there — there is no cap. Deliberately row-blind (see `_add_preset`):
        it used to overwrite whatever row the cursor sat on, which made `a` the one destructive,
        non-undoable key in the app while reading as the additive one everywhere else."""
        if self._presets_focused():
            self._add_preset()
            return
        on_targets_agent = (
            self.focused is self.query_one("#targets", OptionList)
            and (self._current_target or "").startswith("agent:")
        )
        if on_targets_agent:
            self._add_sub()
        else:
            self._open_add_modal()

    def _open_add_modal(self) -> None:
        if self.resolver is None:
            # create_app/run_app builds the resolver unconditionally, even in degraded
            # (CatalogUnavailable) mode — so this is only reachable if Resolver.build() itself
            # raised (e.g. corrupt bundled suggestions data). Surface why, don't just bell.
            self.bell()
            self.notify("Add-model unavailable — the model resolver failed to build.", severity="error")
            return
        target = self._current_target
        if target is None:
            return

        def _accept(row) -> None:
            if row is None:
                return
            # A background `r` refresh completing while this modal was open replaces
            # self.catalog/self.resolver with fresh objects — the modal computed row['warn']
            # against the resolver/catalog it was constructed with, now stale (the same class of
            # bug action_variant's restage guards against, but there's no index into a cleared
            # cache to guard here — the row itself just needs re-validating). Recompute against
            # the LIVE catalog rather than discarding the user's typed pick: same rule as
            # _validate_row / _build_rows' off-chain row — [] (unknown, don't mislead) when
            # catalog.connected is empty, else 'unavailable' iff no connected provider serves it.
            warn = []
            if self.catalog.connected and row["provider"] not in self.catalog.providers_for(
                row["model"]
            ):
                warn.append("unavailable")
            row["warn"] = warn
            # Persist the typed row in _custom_rows (durable across undo/redo) and invalidate the
            # per-target row cache so _build_rows re-merges it as a selectable candidate, then
            # stage it (which re-renders via _refresh_right).
            self._custom_rows.setdefault(target, []).append(row)
            self._rows.pop(target, None)
            self._stage_row(
                target,
                row,
                f"add {self._target_label(target)} → {row['provider']}/{row['model']}",
            )

        self.push_screen(
            AddModelModal(self.resolver, self.suggestions, require_gpt=self._gpt_only(target)),
            _accept,
        )

    def _add_sub(self) -> None:
        """`a` in #targets — add a sub-target to the highlighted agent. When the agent supports
        more than one kind (only Sisyphus — `ultrawork` + `compaction`) this opens AddSubModal to
        pick; an agent with a single kind (every non-Sisyphus agent → `compaction` only) has no
        choice, so `a` adds it directly — no modal (`_subkinds_for`). Either way the picked kind
        becomes an empty sub-row. Bell when the row isn't an agent or every supported kind already
        exists (nothing to add)."""
        target = self._current_target
        if target is None or not target.startswith("agent:"):
            return
        name = target[len("agent:"):].split(".", 1)[0]
        allowed = _subkinds_for(name)
        present = set(self._agent_subtargets(name))
        if all(k in present for k in allowed):
            self.bell()  # every kind this agent supports is already added — nothing to choose
            return

        def _add(kind) -> None:
            if not kind or kind in present:
                return
            # Empty sub-object → shows as a sub-row but is NOT dirty: serialize() drops empty
            # ultrawork/compaction, so there's nothing to save until a model is staged into it.
            # It IS recorded in the undo history, though, so a mis-added sub-target can be
            # removed with `u` (its row vanishes again).
            sub_target = f"agent:{name}.{kind}"
            self._ensure_node(sub_target)
            # Repopulate the left pane straight onto the new sub-target (select=), then render
            # the right pane synchronously — mirrors _restore_state: don't rely on the queued
            # OptionHighlighted event the highlight move posts (which may be handled later, or
            # not at all if some other change coalesces with it first), so the right pane can
            # never show a stale target in the meantime.
            self._populate_targets(select=sub_target)
            self._current_target = sub_target
            self._refresh_right(sub_target)
            self._record(f"add {kind} sub-target to {name}")

        # Single supported kind (non-Sisyphus → compaction only): no choice to make, so skip the
        # chooser and add it straight away. Sisyphus (ultrawork + compaction) opens the modal.
        if len(allowed) == 1:
            _add(allowed[0])
            return

        self.push_screen(AddSubModal(allowed, present), _add)

    def action_undo(self) -> None:
        """`u` — revert the last edit (mis-press recovery). Steps back through the in-session
        history (set / clear / variant / add-model / add sub-target / delete sub-target) and
        notifies what was undone; at the bottom of the stack it just says so."""
        result = self._history.undo()
        if result is None:
            self.notify("Nothing to undo.")
            return
        state, label = result
        self._restore_state(state)
        self.notify(f"Undo: {label}")

    def action_redo(self) -> None:
        """`ctrl+r` — re-apply the last undone edit (vim-style redo; distinct from `r` refresh)."""
        result = self._history.redo()
        if result is None:
            self.notify("Nothing to redo.")
            return
        state, label = result
        self._restore_state(state)
        self.notify(f"Redo: {label}")

    def _lands_elsewhere(self, state: dict) -> bool:
        """Whether restoring `state` would actually CHANGE the active preset's models.

        Gates the "that preset was deleted" warning, which is about models arriving somewhere the
        user didn't choose. When the restored state is what the active preset already holds,
        nothing arrives anywhere and the warning is pure noise — reachable because a fork
        retargets the entries recorded on the preset it copied, so undoing all the way back to a
        state the current preset also holds would otherwise announce a move that didn't happen."""
        target = self._store.current()
        if target is None:
            return True
        return presets_mod.fingerprint(
            target.agents, target.categories
        ) != presets_mod.fingerprint(state.get("agents"), state.get("categories"))

    def _restore_state(self, state: dict) -> None:
        """Swap self.cfg for a restored history snapshot and re-render everything that depends
        on it: the LEFT pane (sub-targets appear/vanish with the cfg) and the RIGHT pane
        (detail + the `●` current-pick marker). Mirrors the per-session cache handling a
        refresh does, minus the catalog rebuild — the candidate rows' `●` follows cfg, so the
        per-target row cache is dropped and rebuilt; `_cand_choice` (highlight memory) and
        `_detail_cache` (keyed by model id) are unaffected and kept. `_custom_rows` AND the
        active preset are restored (from the entry's `aux`) so typed off-chain rows and the `●`
        move in lockstep with undo/redo."""
        self.cfg = state
        # Move the out-of-cfg companions in lockstep with undo/redo: the _custom_rows snapshot
        # this state was pushed with (so undoing an add-model drops its row and redoing brings it
        # back), and which preset was active (so undoing a switch moves the `●` back with the
        # models — otherwise the restored models would be folded into the preset you switched TO).
        aux = self._history.current_aux() or {}
        self._custom_rows = aux.get("custom_rows") or {}
        # Pending `v` picks are NOT snapshotted into aux (they aren't cfg, and a `v` on a
        # non-assigned row pushes no history entry) — so undo/redo has no matching value to
        # restore and the honest move is to drop them, exactly as a refresh does. Keeping them
        # would re-apply a pick made against a cfg state you have just stepped away from.
        self._pending_variants.clear()
        active = aux.get("active")
        if isinstance(active, int):
            if 0 <= active < len(self._store.presets):
                self._store.active = active
            elif active == _DELETED and self._lands_elsewhere(state):
                # This state was recorded while a preset that has since been DELETED was active
                # (`_shift_active` marked it). The models still come back — undo must undo — but
                # they land in whichever preset is active now, so say so rather than editing it
                # silently. Every OTHER stale index was renumbered, not orphaned.
                target = self._store.current()
                self.notify(
                    "That preset was deleted — these models are now in "
                    f"'{target.name if target else 'the active preset'}'.",
                    severity="warning",
                )
        self._rerender_all()

    def _rerender_all(self) -> None:
        """Re-render everything that depends on cfg / the store: the row cache, the presets
        card, the LEFT pane (sub-targets appear and vanish with cfg) and the RIGHT pane."""
        self._rows.clear()  # resolver rows rebuild around the restored _custom_rows
        self._populate_presets()
        # Pick a target that still exists: a sub-target whose node is gone (an undone add-sub,
        # or one the preset you switched to doesn't define) falls back to its parent agent;
        # top-level agent/category rows always exist (they come from suggestions, not cfg).
        target = self._current_target
        if target and "." in target and self._node_for(target) is None:
            target = "agent:" + self._target_label(target).split(".", 1)[0]
        # Repopulate the left pane straight to the final target (select=), so no intermediate
        # fallback highlight queues a stale render for the wrong target.
        self._populate_targets(select=target)
        # Authoritative, synchronous render of the chosen target (don't rely on the queued
        # OptionHighlighted event, which may not fire if the highlight index is unchanged).
        self._current_target = target
        if target is not None:
            self._refresh_right(target)

    def _write_store(self, store) -> bool:
        """Persist the presets file and re-baseline its dirtiness. Best-effort in the sense that
        a failure is REPORTED, never swallowed (presets.write raises by contract) — but it does
        not roll the config back: see `_save` for why the config goes first."""
        try:
            self.session.write_store(store)
        except Exception as exc:
            self.notify(f"Presets could not be written: {exc}", severity="error")
            return False
        self._populate_presets()
        return True

    def action_save(self) -> None:
        """`s` — write the config AND the presets file, together (decision #17's one write
        rule)."""
        self._save()

    def _save(self, then=None) -> None:
        """The save flow. `then` is invoked after a successful save (quit-and-save uses it).

        `s` publishes BOTH files, because the invariant is that the config on disk equals the
        active preset: letting one land without the other is exactly the orphan state the design
        exists to prevent. A presets-only change (fork, delete, rename, or adopting an
        out-of-band config edit) has no config diff to confirm, so it writes straight out."""
        diff = config_io.diff_text(self.cfg, self.config_path)
        store = self._projected_store()
        store_dirty = presets_mod.store_fingerprint(store) != self._saved_store_fp
        if not diff.strip():
            if not store_dirty:
                self.notify("Nothing to save.")
                return
            # Presets-only: the config isn't being touched, so there's nothing to diff-confirm.
            if self._write_store(store) and then is not None:
                then()
            return
        body = diff
        # First save deletes the commented-out palette (decision #13) — warn explicitly.
        import os

        original = os.path.join(
            os.path.dirname(os.path.abspath(self.config_path)), ".backup", "original.jsonc"
        )
        if not os.path.exists(original):
            body = (
                "⚠ First save rewrites agents/categories clean, dropping their commented-out "
                "palette. Everything outside those two (other keys, comments, commented-out "
                "config) is kept verbatim; the whole original is saved to .backup/original.jsonc, "
                "always restorable.\n\n" + diff
            )

        def _confirm(ok) -> None:
            if not ok:
                return
            try:
                # Re-baselines dirtiness to what's now on disk. The undo history is intentionally
                # preserved across a save, so you can still undo a just-saved edit (and re-save
                # to persist the reverted state).
                result = self.session.save_config()
            except Exception as exc:  # surface, don't crash the app
                self.notify(f"Save failed: {exc}", severity="error")
                return
            # Config first — it is the artifact with the backup and the diff you just approved,
            # so a failure there aborts before the presets file moves. If the presets write then
            # fails, the config is ahead of the store: `_write_store` says so plainly, and a
            # second `s` takes the presets-only path above and heals it.
            wrote_store = self._write_store(store)
            if not wrote_store:
                # Don't contradict the error toast _write_store just raised: the config landed,
                # the store didn't, and `_is_dirty()` stays True so a second `s` heals it.
                self.notify(
                    "Config saved — the presets file did not. Press s again.",
                    severity="warning",
                )
            else:
                self.notify("Saved." if result.changed else "Nothing to save.")
            if wrote_store and then is not None:
                then()

        self.push_screen(ConfirmModal("Save changes?", body), _confirm)

    def action_refresh(self) -> None:
        """`r` — force a live re-fetch + rebuild cache, OFF the UI thread. Doubles as the
        CatalogUnavailable retry and the manual staleness refresh (DESIGN §CLI/refresh).
        Single-flight (`_refresh_inflight`): a second `r` while one is already running just
        notifies rather than spawning a second worker — see the field's comment in __init__ for
        why `@work(exclusive=True)` alone isn't enough.

        On a `#presets` row `r` RENAMES that preset instead — the fourth key to dispatch on focus
        (with `a`/`x`/`v`). Refreshing the model list is a whole-app action you'd never want while
        looking at the presets card, and `r` for rename is the obvious letter; the card is one
        `tab` away from anywhere, so the refresh is never more than that."""
        if self._presets_focused():
            idx = self._highlighted_preset_index()
            if idx is None:
                self.bell()  # the `+ new preset…` row — nothing to rename
            else:
                self._rename_preset(idx)
            return
        if self._refresh_inflight:
            self.notify("Refresh already running…")
            return
        self._refresh_catalog()

    @work(exclusive=True, group="refresh")
    async def _refresh_catalog(self) -> None:
        """Background worker: run `opencode models --refresh` (network, ~3s+) off the event
        loop via catalog.refresh(), then rebuild the resolver and re-render. `exclusive=True`
        cancels a prior in-flight ASYNCIO TASK, but action_refresh's `_refresh_inflight` check is
        what actually stops a second `r` from spawning a second worker (and racing the opencode
        subprocess underneath — see the field's __init__ comment); set here so it covers the
        worker's entire body regardless of which branch returns."""
        self._refresh_inflight = True
        try:
            self.query_one("#providers", Static).update("Refreshing models… (opencode --refresh)")
            try:
                new_catalog = await _to_thread_daemon(catalog_mod.refresh)
            except CatalogUnavailable as exc:
                self.catalog_error = exc
                self._render_providers()
                self.notify("Refresh failed — couldn't read models.", severity="error")
                return
            self.catalog = new_catalog
            self.catalog_error = None
            try:
                self.resolver = Resolver.build(new_catalog, self.suggestions)
            except Exception:
                self.resolver = None
            # Drop every per-session cache so the refreshed availability shows everywhere. Any
            # in-flight detail fetch finishes on its own and resets _detail_fetching; bumping the
            # generation makes it discard its (now stale) result rather than re-populating here.
            # _custom_rows is dropped too (a typed model's stored availability ⚠ is now stale);
            # also wipe the history's aux snapshots so undo/redo can't resurrect a pre-refresh
            # typed row.
            self._rows.clear()
            self._custom_rows.clear()
            # Same reasoning for pending `v` picks: they were chosen from the PRE-refresh variant
            # sets, and the row they annotate may not even survive the re-resolve.
            self._pending_variants.clear()
            # Keep the active-preset index: unlike the typed rows, it doesn't go stale with the
            # catalog, and dropping it would let an undo move the models without moving the `●`.
            self._history.clear_aux(keep=("active",))
            self._detail_cache.clear()
            self._detail_generation += 1
            self._render_providers()
            if self._current_target is not None:
                self._refresh_right(self._current_target)
            self.notify(f"Refreshed — {len(new_catalog.connected)} providers.")
        finally:
            self._refresh_inflight = False

    def action_quit_confirm(self) -> None:
        """`q` — quit; if anything is unsaved (`_is_dirty`: the config OR the presets file),
        offer three ways out rather than two. Discarding now drops preset work as well as config
        work — they are written together — so "save & quit" has to be on the same screen as the
        exit that loses both. Undoing back to the saved state still quits without a prompt."""
        if not self._is_dirty():
            self.exit()
            return

        def _choice(choice) -> None:
            if choice == "discard":
                self.exit()
            elif choice == "save":
                # Runs the normal diff+confirm; exits only once the save actually lands, so a
                # declined confirm or a failed write leaves you in the app with your edits.
                self._save(then=self.exit)

        self.push_screen(
            QuitModal(
                "Unsaved changes to your models and presets.\n\n"
                "Saving writes both your config and the presets file. Discarding leaves both "
                "exactly as they are on disk."
            ),
            _choice,
        )


def create_app(config_path: str | None = None) -> OModelApp:
    """Build the session and construct (but do NOT run) an OModelApp — the production wiring,
    factored out of `run_app` so it's directly testable without launching the Textual event loop.

    The loading itself is `Session.build` (session.py), which `cli.py` also calls: the TUI and
    the agent surface open the same state the same way. It degrades gracefully — on
    CatalogUnavailable the app still launches with a banner + `r` retry and suggestions/
    add-model only, and the resolver is built even over the empty catalog so add-model (the only
    route to a model while degraded) stays live."""
    return OModelApp(session=Session.build(config_path))


def run_app(config_path: str | None = None) -> None:
    """Build and run OModelApp() for the default (no-subcommand) CLI invocation.
    Called by cli.main(). See create_app() for the construction details."""
    create_app(config_path).run()
