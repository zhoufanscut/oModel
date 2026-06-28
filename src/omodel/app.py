"""Textual two-pane App.  DESIGN.md §Textual two-pane contract / §Layout.

FROZEN CONTRACT — owned by the TUI specialist. Consumes catalog / suggestions / resolve /
config_io against their frozen signatures. Implements the App class `OModelApp` and the
module entrypoint `run_app`.

STABLE WIDGET IDs (pilot tests in tests/test_app_pilot.py depend on these — do not rename):
  * Static#providers      — "Providers: <id · id · …>" from catalog.connected (first-seen);
                            on CatalogUnavailable shows the banner + `r` retry instead.
  * OptionList#targets     — AGENTS then CATEGORIES. Option IDs: 'agent:<name>',
                            'agent:<name>.ultrawork' / '.compaction' (indented sub-rows),
                            'cat:<name>'.
  * Static#detail          — current model/variant + catalog.detail() line. The detail()
                            line is a ~3s opencode subprocess, so it is fetched in a
                            background worker (cached per model) and appears when ready —
                            highlighting renders the rest of the pane instantly.
  * OptionList#candidates  — option IDs 'cand:<i>'; LAST row 'cand:add' (+ add model…). The
                            row matching the current assignment (follows your pick) is '● '. If
                            that assignment is off-chain (a custom/hand-set model not in the
                            chain), it's surfaced as its own 'cand:<i>' row just before
                            'cand:add' (see _build_rows), so what's configured is always shown
                            and re-selectable. The highlighted (cursor) row is remembered per
                            target by model identity and restored on re-render, so it survives a
                            target switch and `r` refresh (see _cand_choice / _restore_cand_highlight).
  * Static#hints           — pane-aware key hint bar (bottom row). Content switches on focus
                            + highlighted row (see _render_hints); modals carry their own
                            one-line hint instead.

Each pane is a bordered card; the focused pane (`#targets`/`#candidates`) brightens its border
to `$primary`, while blurred panes and the never-focused `#detail` use a muted gray (`#808080`,
a literal — `$border-blurred` renders near-black on a dark terminal background).
`#providers`/`#hints`/`#detail` don't focus.

KEYS: ↑↓ (or vim j/k) move within the focused pane · ←/→ (or vim h/l) focus
targets/candidates (gated to the base screen via check_action) · enter set (dispatch by row:
cand:add → add-model modal, else set
model + default variant) · v variant · x clear (on an ultrawork/compaction sub-target row: delete
the whole row) · a pane-contextual (candidates + targets category
rows → add/edit-model modal; targets agent rows → add sub-target chooser) · u undo / ctrl+r redo
(in-session undo of EVERY edit — set/clear/variant/add-model/add-sub/delete-sub — for mis-press recovery;
snapshot stack in history.py, also gated to the base screen) · s save
(diff+confirm) · r refresh (live re-fetch off-thread + rebuild cache; also retries after
CatalogUnavailable) · q quit (confirm if dirty). The pane keys are shown in Static#hints (and
per-modal hint lines); undo/redo appear there only when available; r is advertised in the
Static#providers header instead, not the hint bar.

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
cached verbose) it's added immediately (variant None). GPT-only targets filter the list to GPT models.
Add-sub (`a` on an agent): an agent that supports more than one sub-kind (only Sisyphus — ultrawork
+ compaction) opens a chooser modal, an OptionList (`#sub-list`, IDs 'sub:ultrawork' /
'sub:compaction') with one row per kind valid for that agent (see _ULTRAWORK_AGENTS), naming each
kind + what it's for; a kind already on the agent is disabled ('✓ added'); `u`/`c` shortcut or enter
picks one, esc cancels. An agent with a single sub-kind (every non-Sisyphus agent → compaction only)
has no choice, so `a` adds it directly — no modal. Every supported kind present → `a` bells.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.fuzzy import Matcher
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from . import cache as cache_mod
from . import catalog as catalog_mod
from . import config_io
from . import suggestions as suggestions_mod
from .catalog import Catalog, CatalogUnavailable
from .history import History
from .resolve import Resolver
from .suggestions import Suggestions

# Sub-targets an agent may carry beyond its top-level `model`.
_SUBKINDS = ("ultrawork", "compaction")

# Agents omo locks to a single model family. Hephaestus is GPT-exclusive: omo's
# `no-hephaestus-non-gpt` hook reassigns the session to Sisyphus for any non-GPT model. We
# mirror that — the chain + add-model are both restricted to GPT models for these agents.
_GPT_ONLY_AGENTS = frozenset({"hephaestus"})

# Agents for which omo actually honors an `ultrawork` sub-model. The `ultrawork`/`ulw` keyword
# only swaps the model on Sisyphus; on any other agent an `ultrawork` block is dead config (omo
# never reads it). We mirror that — only Sisyphus can add an `ultrawork` sub-target (and so only it
# gets an add-sub chooser; every other agent has just `compaction`, which `a` adds directly).
# `compaction` is valid on every agent. Hard-coded agent key, like `_GPT_ONLY_AGENTS`, not a data field.
_ULTRAWORK_AGENTS = frozenset({"sisyphus"})


def _is_gpt_model(model_id: str) -> bool:
    """omo's `isGptModel` (model-core): the model name (after the LAST '/'), lowercased,
    contains 'gpt'. Used to gate the add-model modal for GPT-only agents (Hephaestus)."""
    return "gpt" in model_id.rsplit("/", 1)[-1].lower()


def _subkinds_for(name: str) -> tuple:
    """Sub-target kinds addable to agent `name`, in `_SUBKINDS` order: `compaction` for every
    agent; `ultrawork` only for the agents omo honors it on (`_ULTRAWORK_AGENTS` — Sisyphus)."""
    return tuple(k for k in _SUBKINDS if k != "ultrawork" or name in _ULTRAWORK_AGENTS)


def _warn_str(warn: list) -> str:
    """Render the candidate-row warn list as trailing ⚠ markers."""
    if not warn:
        return ""
    return "  ⚠ " + " ".join(warn)


def _row_label(row: dict) -> str:
    """One-line rendering of a candidate-row dict for OptionList#candidates.
    A same-line substitute (substitute_for set) is suffixed `(≈ omo <id>)` so it reads
    as a stand-in for the model omo actually named."""
    variant = row.get("variant")
    vtext = f" ({variant})" if variant else ""
    sub = row.get("substitute_for")
    subtext = f"  (≈ omo {sub})" if sub else ""
    return f"{row['provider']}/{row['model']}{vtext}{subtext}{_warn_str(row['warn'])}"


class VimOptionList(OptionList):
    """OptionList with vim `j`/`k` aliased to cursor down/up (alongside the inherited ↑↓).

    Every list in the app uses this (targets, candidates, and the modal pickers) so j/k move
    the highlight anywhere a list is focused, including inside a modal. Textual merges BINDINGS
    across the MRO, so the parent's ↑↓ / enter / home / end still apply. `h`/`l` are NOT here:
    they cross panes via App-level focus_targets/focus_candidates actions (gated to the base
    screen) — a list inside a modal must not grab the hidden base-screen panes. Printable keys
    only reach these bindings when this list is focused; a focused Input eats j/k as text first
    (so the add-model modal's id field is unaffected)."""

    BINDINGS = [
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
    ids 'var:<v>' / 'var:__none__'). A model opencode lists with no variants (kimi, glm-5) — or
    whose verbose isn't cached anywhere — skips this phase and adds immediately. Esc returns to the
    model phase (restores + focuses the Input); the model phase's Esc cancels the modal.

    Dismisses with the staged candidate-row dict (source 'add') on accept, or None on cancel — the
    frozen CONTRACTS.md candidate-row shape (`variant` was always a field; this modal just stops
    forcing it to None).
    """

    BINDINGS = [
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
    _MODEL_HINTS = "↑↓ move · tab fill · enter add · esc cancel"
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
        self._staged: Optional[dict] = None
        self._phase = "model"
        # Candidate-row dicts currently in #add-candidates, parallel to its options (so a
        # highlighted/selected option index maps straight back to a row).
        self._candidate_rows: list = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._MODEL_TITLE, id="add-title")
            yield Input(placeholder="provider/model", id="add-input")
            yield OptionList(id="add-candidates")
            yield VimOptionList(id="add-variants")
            yield Static("", id="add-preview")
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
        # (provider, model) pairs, so they stay warn-free; only a typed mismatch warns.
        if provider not in self._resolver.catalog.providers_for(model):
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
            cands.add_option(Option(label, id=f"add-cand:{i}"))

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

    def _choose_model(self, row: Optional[dict]) -> None:
        """Commit the chosen model: enter the variant phase iff opencode reports variants for its
        (provider, model) — catalog.variants_for, the cached `--verbose` map — else dismiss
        immediately with variant left None (kimi / glm-5 / any model opencode lists with no
        variants, or whose verbose isn't cached anywhere)."""
        if row is None:
            return
        self._staged = row
        variants = self._resolver.catalog.variants_for(row["provider"], row["model"])
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
            variants_list.add_option(Option(v, id=f"var:{v}"))
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
    """`v` — pick from the variants opencode reports for the model + '(none)'.  Dismisses with the
    chosen variant string, the sentinel '' for (none), or None on cancel."""

    BINDINGS = [
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
            ol.add_option(Option(v, id=f"var:{v}"))
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


class ConfirmModal(ModalScreen):
    """Generic confirm modal — shows `body` (e.g. the save diff, with the first-save
    palette-loss warning) and Yes/No.  Dismisses True on accept, False otherwise.

    The body lives in a `VerticalScroll` capped at `max-height`, so a long save diff is fully
    scrollable: ↑/↓ + j/k, PageUp/PageDown, Home/End.  Those are screen-level bindings (not the
    scroller's own), so they scroll even while the Yes button keeps focus — leaving Enter to
    confirm the focused button as before."""

    BINDINGS = [
        Binding("escape", "decline", "No", show=False),
        Binding("y", "accept", "Yes", show=False),
        Binding("n", "decline", "No", show=False),
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
    ConfirmModal .modal-hints {
        margin-top: 1;
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title)
            with VerticalScroll(id="confirm-body") as body:
                # Non-focusable so default focus stays on the Yes button (Enter still confirms);
                # scrolling is driven by this screen's own bindings, not the scroller's focus.
                body.can_focus = False
                yield Static(self._body, id="confirm-body-text")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes", variant="primary", id="confirm-yes")
                yield Button("No", id="confirm-no")
            yield Static(
                "↑↓/jk scroll · y yes · n no · esc cancel",
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


class AddSubModal(ModalScreen):
    """`a` — pick which sub-target to add to an agent.  Shown only when the agent supports more
    than one kind (so there's an actual choice — only Sisyphus, with `ultrawork` + `compaction`);
    a single-kind agent skips this and `a` adds straight away (see `OModelApp._add_sub`).  Offers
    only the kinds valid for that agent (`_subkinds_for`): every agent gets `compaction`, but
    `ultrawork` only Sisyphus.  Each row names the kind and one line on what omo uses it for; a
    kind already on the agent is shown disabled (`✓ added`).  Dismisses with the chosen kind
    ('ultrawork'|'compaction') — via the `u`/`c` shortcut or enter on the row — or None on cancel."""

    BINDINGS = [
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
    _BLURB = {
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
            ol.add_option(Option(label, id=f"sub:{kind}", disabled=added))
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


class OModelApp(App):
    """Two-pane list-detail TUI to set OMO models.  See module docstring for the stable
    widget/option IDs the pilot tests depend on."""

    TITLE = "oModel"

    CSS = """
    #providers {
        height: 1;
        background: $panel;
        color: $text;
        padding: 0 1;
    }
    #main {
        height: 1fr;
    }
    #targets {
        width: 32;
        border: solid #808080;
    }
    #right {
        width: 1fr;
    }
    #detail {
        height: auto;
        min-height: 4;
        padding: 0 1;
        border: solid #808080;
    }
    #candidates {
        height: 1fr;
        border: solid #808080;
    }
    #targets:focus, #candidates:focus {
        border: solid $primary;
    }
    #hints {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [
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
        Binding("q", "quit_confirm", "quit"),
    ]

    def __init__(
        self,
        catalog: Catalog,
        suggestions: Suggestions,
        resolver: Optional[Resolver],
        cfg: dict,
        config_path: str,
        catalog_error: Optional[BaseException] = None,
    ) -> None:
        super().__init__()
        self.catalog = catalog
        self.suggestions = suggestions
        self.resolver = resolver
        self.cfg = cfg
        self.config_path = config_path
        self.catalog_error = catalog_error
        # In-session undo/redo (mis-press recovery). `_history` holds cfg snapshots; every
        # config mutation routes through `_record` (and `_stage_row`), which pushes one, so any
        # operation can be reverted with `u` / re-applied with `ctrl+r` (see history.py).
        # `_saved_text` is the serialization last written to (or loaded from) disk: dirtiness is
        # computed against it (`_is_dirty`), NOT a bool flag — so undoing back to the saved
        # state reads as clean, and a structural-but-unserialized change (an empty
        # ultrawork/compaction sub-object) is undoable yet never marks the file dirty.
        self._history = History(cfg)
        self._saved_text = config_io.serialize(cfg)
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
        # The target id currently shown in the right pane.
        self._current_target: Optional[str] = None
        # Per-target memory of the highlighted candidate, keyed by target id → the row's stable
        # provider/model identity (or the sentinel 'cand:add'). Restored on every candidate
        # re-render (_restore_cand_highlight) so the cursor returns to your last pick when you
        # revisit a target or after `r`. Keyed by identity (not row index) so it still resolves
        # once the chain re-resolves against refreshed availability. Deliberately NOT cleared by
        # _refresh_catalog (unlike _rows / _detail_cache), which is what makes it survive refresh.
        self._cand_choice: dict = {}
        # Detail-pane enrichment (catalog.detail()) is a ~3s, ~320 MB `opencode … --verbose`
        # subprocess. It runs in a background worker and is cached by bare model id, so
        # highlighting never blocks the UI thread. Crucially only ONE fetch runs at a time
        # (_detail_fetching): asyncio.to_thread can't kill a spawned process, so stacking
        # fetches would pile up 320 MB each. On completion the worker re-renders the *current*
        # target, which schedules the next fetch if it's still uncached ("chase the cursor").
        # _detail_cache: bare → info dict | None (None = known-empty); _detail_timer debounces.
        self._detail_cache: dict = {}
        self._detail_fetching = False
        self._detail_timer = None
        # Bumped by a refresh (r) so an in-flight detail fetch can tell its result is stale.
        self._detail_generation = 0

    # ----- composition -----------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("", id="providers")
        with Horizontal(id="main"):
            yield VimOptionList(id="targets")
            with Vertical(id="right"):
                yield Static("", id="detail")
                yield VimOptionList(id="candidates")
        yield Static("", id="hints")

    def on_mount(self) -> None:
        self._render_providers()
        self._populate_targets()
        self._render_hints()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """Re-render the hint bar whenever focus crosses panes (Tab / ←→ / click) so it
        reflects the now-focused pane."""
        self._render_hints()

    # ----- header ----------------------------------------------------------------------

    def _render_providers(self) -> None:
        header = self.query_one("#providers", Static)
        if self.catalog_error is not None:
            header.update("⚠ couldn't read models — press r to retry")
        elif self.catalog.connected:
            line = "Providers: " + " · ".join(self.catalog.connected)
            # Surface cache staleness so `r` (refresh) is discoverable. No suffix when the
            # list wasn't served from cache (e.g. the in-memory test catalog).
            age = cache_mod.age_seconds("models")
            if age is not None:
                line += f"   (cached {self._fmt_age(age)} · r to refresh)"
            header.update(line)
        else:
            header.update("Providers: (none — opencode not found; suggestions/add only)")

    @staticmethod
    def _fmt_age(seconds: float) -> str:
        """Coarse 'cached X ago' label for the providers header."""
        if seconds < 90:
            return "just now"
        if seconds < 3600:
            return f"{int(seconds // 60)}m ago"
        if seconds < 86400:
            return f"{int(seconds // 3600)}h ago"
        return f"{int(seconds // 86400)}d ago"

    # ----- left pane: targets ----------------------------------------------------------

    def _agent_subtargets(self, name: str) -> list:
        """Present sub-target kinds for an agent (in config), as ('ultrawork'|'compaction')."""
        agent = (self.cfg.get("agents") or {}).get(name) or {}
        return [k for k in _SUBKINDS if isinstance(agent.get(k), dict)]

    def _populate_targets(self, select: Optional[str] = None) -> None:
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
        for name in self.suggestions.agents.keys():
            targets.add_option(Option(f"  {name}", id=f"agent:{name}"))
            for kind in self._agent_subtargets(name):
                targets.add_option(Option(f"    ↳ {kind}", id=f"agent:{name}.{kind}"))
        targets.add_option(Option("CATEGORIES", id="hdr:categories", disabled=True))
        for name in self.suggestions.categories.keys():
            targets.add_option(Option(f"  {name}", id=f"cat:{name}"))

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

    # ----- target → cfg node helpers ---------------------------------------------------

    def _node_for(self, target: str):
        """Return the dict node holding {model, variant} for `target` in self.cfg, or None if
        its parent agent/category isn't present. Does NOT create nodes."""
        cfg = self.cfg
        if target.startswith("agent:"):
            rest = target[len("agent:"):]
            if "." in rest:
                name, kind = rest.split(".", 1)
                agent = (cfg.get("agents") or {}).get(name)
                if not isinstance(agent, dict):
                    return None
                sub = agent.get(kind)
                return sub if isinstance(sub, dict) else None
            return (cfg.get("agents") or {}).get(rest)
        if target.startswith("cat:"):
            name = target[len("cat:"):]
            return (cfg.get("categories") or {}).get(name)
        return None

    def _ensure_node(self, target: str) -> dict:
        """Return (creating if needed) the cfg node for `target`. agents/categories maps and
        the agent object / sub-object are created on demand so staged edits can land."""
        if target.startswith("agent:"):
            rest = target[len("agent:"):]
            agents = self.cfg.setdefault("agents", {})
            if "." in rest:
                name, kind = rest.split(".", 1)
                agent = agents.setdefault(name, {})
                return agent.setdefault(kind, {})
            return agents.setdefault(rest, {})
        # cat:
        name = target[len("cat:"):]
        cats = self.cfg.setdefault("categories", {})
        return cats.setdefault(name, {})

    def _current_assignment(self, target: str):
        """(model_str, variant) currently assigned in cfg for `target`; ('', None) if unset.
        model_str is the full 'provider/model' as stored."""
        node = self._node_for(target)
        if not isinstance(node, dict):
            return "", None
        return node.get("model", "") or "", node.get("variant")

    @staticmethod
    def _gpt_only(target: str) -> bool:
        """True if `target` (incl. its sub-targets) belongs to a GPT-exclusive agent —
        currently Hephaestus (see _GPT_ONLY_AGENTS). Such agents hide the add-model escape
        hatch and show a tip; the fallbackChain is the only valid source."""
        if not target.startswith("agent:"):
            return False
        name = target[len("agent:"):].split(".", 1)[0]
        return name in _GPT_ONLY_AGENTS

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
        rows: list = []
        if self.resolver is not None:
            try:
                rows = list(self.resolver.candidates(target))
            except CatalogUnavailable:
                rows = []
        # Re-merge session-added custom rows (typed in the add-model modal) the chain doesn't
        # already cover, so a typed model stays a pickable row — _custom_rows is the store
        # (snapshotted with the undo history); this cache and the resolver list rebuild around it.
        existing = {f"{r['provider']}/{r['model']}" for r in rows}
        for cr in self._custom_rows.get(target, []):
            key = f"{cr['provider']}/{cr['model']}"
            if key not in existing:
                rows.append(cr)
                existing.add(key)
        # Surface the target's CURRENT off-chain assignment as its own pickable row when neither
        # the chain nor a typed custom already covers it — e.g. a model set in a prior session, a
        # hand-edited config, or one that has since dropped off the chain. Derived straight from
        # cfg (the source of truth), so it always reflects what's set: it carries the `●` marker,
        # is re-selectable, and — because the per-target cache is dropped whenever the assignment
        # changes — appears/vanishes in lockstep with cfg across set/clear/undo/redo. Appended
        # LAST so it renders right before `+ add model…`. Skips a bare id with no `provider/` (a
        # malformed value) rather than rendering it as `/model`.
        current, current_variant = self._current_assignment(target)
        if current and "/" in current and current not in existing:
            provider, model = current.split("/", 1)
            # ⚠ unavailable only when the catalog is readable and no connected provider serves the
            # model; never in degraded mode (empty `connected`), where availability is unknown and
            # an unqualified ⚠ would mislead. source 'add' = off-chain pick (CONTRACTS enum).
            warn = []
            if self.catalog.connected and provider not in self.catalog.providers_for(model):
                warn.append("unavailable")
            rows.append({
                "source": "add",
                "model": model,
                "provider": provider,
                "variant": current_variant,
                "entry": None,
                "substitute_for": None,
                "warn": warn,
            })
        self._rows[target] = rows
        return rows

    def _render_detail(self, target: str) -> None:
        detail = self.query_one("#detail", Static)
        model, variant = self._current_assignment(target)
        # Header mirrors the `label: value` spacing below it — give `agent:`/`cat:` the same
        # space after the colon as `model: `/`variant: ` so the values line up.
        lines = [f"[b]{target.replace(':', ': ', 1)}[/b]"]
        if model:
            lines.append(f"model: {model}")
            lines.append("variant: " + (variant if variant else "—"))
            # Detail line from catalog (display only); bare model id is after the first '/'.
            # Always reserve this row so switching targets refreshes its text in place rather
            # than adding/removing a line (which makes the pane jump). Cache hit → render the
            # line now; miss → a dim placeholder holds the slot while the ~3s background fetch
            # runs, and the worker's re-render swaps in the real content (or blank if none).
            bare = model.split("/", 1)[1] if "/" in model else model
            info = self._detail_info(target, bare)
            if info:
                lines.append(self._detail_line(info))
            elif bare in self._detail_cache:
                lines.append("")  # fetch done, no detail available — keep the slot blank
            else:
                lines.append("[dim]…[/dim]")  # fetch pending — keep the slot, fill on arrival
        else:
            lines.append("model: — (unset)")
            lines.append("variant: —")
        if self._gpt_only(target):
            lines.append("⚑ GPT-only: Hephaestus needs a GPT model (omo) — non-GPT is blocked.")
        detail.update("\n".join(lines))

    def _detail_info(self, target: str, bare: str):
        """Cached `catalog.detail(bare)` for the detail pane. Returns the info dict (or None)
        when already known; on a cache miss schedules a background fetch and returns None so
        the base detail renders immediately. `catalog.detail()` is a ~3s `opencode --verbose`
        subprocess — it must never run on the UI thread (highlighting has to stay smooth)."""
        if bare in self._detail_cache:
            return self._detail_cache[bare]
        # No connected provider serves it → detail() would no-op; cache None, skip the worker.
        if not self.catalog.providers_for(bare):
            self._detail_cache[bare] = None
            return None
        self._schedule_detail_fetch(target, bare)
        return None

    def _schedule_detail_fetch(self, target: str, bare: str) -> None:
        """Debounce (~0.2s) so scrolling doesn't fetch per row, and never start a second
        fetch while one is in flight — the running fetch re-renders the current target on
        completion, which reschedules from here if it's still uncached."""
        if self._detail_fetching or bare in self._detail_cache:
            return
        if self._detail_timer is not None:
            self._detail_timer.stop()
        self._detail_timer = self.set_timer(
            0.2, lambda: self._fetch_detail(target, bare)
        )

    @work(group="detail")
    async def _fetch_detail(self, target: str, bare: str) -> None:
        """Background worker: run the blocking ~320 MB `catalog.detail()` subprocess off the
        event loop, cache it, then re-render the CURRENT target. At most one fetch runs at a
        time (the `_detail_fetching` gate, since a to_thread subprocess can't be killed); the
        post-render reschedules the next fetch if the current target still needs one."""
        if self._detail_fetching or bare in self._detail_cache:
            return
        generation = self._detail_generation
        self._detail_fetching = True
        try:
            info = await asyncio.to_thread(self.catalog.detail, bare)
        except Exception:
            info = None
        finally:
            self._detail_fetching = False
        # If a refresh (r) cleared the cache while we were off-thread, this result describes
        # the pre-refresh catalog — drop it instead of repopulating the cleared cache. The
        # re-render below still runs, so the current target schedules a fresh fetch.
        if self._detail_generation == generation:
            self._detail_cache[bare] = info
        # Re-render whatever is current NOW: shows the line if this was it, and (via
        # _detail_info → _schedule_detail_fetch) kicks off the next fetch if still uncached.
        if self._current_target is not None:
            self._render_detail(self._current_target)

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
            cands.add_option(Option(label, id=f"cand:{i}"))
        cands.add_option(Option("+ add model…", id="cand:add"))
        # clear_options() reset the cursor to None; put it back where this target last had it.
        self._restore_cand_highlight(target, rows)

    @staticmethod
    def _cand_identity(rows: list, option_id: Optional[str]):
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
            except Exception:
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
        self._render_hints()

    def _render_hints(self) -> None:
        """Update Static#hints to the keys valid for the focused pane + highlighted row
        (DESIGN §Layout — pane-aware so the bar stays one line and only advertises keys that
        do something right now). Modals carry their own hint line, so skip while one is up."""
        if len(self.screen_stack) > 1:
            return
        hints = self.query_one("#hints", Static)
        # Global tail (both panes): undo/redo are shown ONLY when there's something to
        # undo/redo, keeping the one-line bar minimal until the keys actually do something
        # (same philosophy as the pane-aware keys); then the always-present save/quit.
        tail = []
        if self._history.can_undo:
            tail.append("u undo")
        if self._history.can_redo:
            tail.append("⌃r redo")
        tail += ["s save", "q quit"]
        tail_text = " · ".join(tail)
        cands = self.query_one("#candidates", OptionList)
        # A sub-target row (agent:<name>.<kind>) deletes with `x` (clear == delete there);
        # base agent/category rows clear the model. The label/hint follows so it's never
        # misleading — and so the delete capability is discoverable on the left pane.
        target = self._current_target or ""
        is_sub = target.startswith("agent:") and "." in target[len("agent:"):]
        if self.focused is cands:
            # Right pane: the '+ add model…' row repurposes enter and drops v/x.
            hi = cands.highlighted
            on_add = False
            if hi is not None:
                try:
                    on_add = cands.get_option_at_index(hi).id == "cand:add"
                except Exception:
                    on_add = False
            if on_add:
                text = f"↑↓ move · ← targets · enter add · {tail_text}"
            else:
                x_label = "x delete" if is_sub else "x clear"
                text = ("↑↓ move · ← targets · enter set · v variant · a edit · "
                        f"{x_label} · {tail_text}")
        else:
            # Left pane (targets): `a` is `sub` on an agent row, `edit` on a category row
            # (categories have no sub-targets, so `a` opens the add/edit-model modal there); a
            # sub-target row also advertises `x delete`.
            if target.startswith("agent:"):
                a_hint = "a sub · "
            elif target.startswith("cat:"):
                a_hint = "a edit · "
            else:
                a_hint = ""
            x_hint = "x delete · " if is_sub else ""
            text = f"↑↓ move · → candidates · {a_hint}{x_hint}{tail_text}"
        hints.update(text)

    # ----- events ----------------------------------------------------------------------

    @on(OptionList.OptionHighlighted, "#targets")
    def _target_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        oid = event.option_id
        if not oid or oid.startswith("hdr:"):
            return
        self._refresh_right(oid)

    @on(OptionList.OptionHighlighted, "#candidates")
    def _candidate_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Right-pane ↑↓ doesn't change focus, but moving onto/off the '+ add model…' row
        changes which keys apply (enter set vs enter add, v/x relevance) — refresh hints.
        Also remember this target's highlighted candidate so it survives a re-render / refresh
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
        self._render_hints()

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
        'cat:deep' → 'deep'."""
        for prefix in ("agent:", "cat:"):
            if target.startswith(prefix):
                return target[len(prefix):]
        return target

    def _is_dirty(self) -> bool:
        """True iff the in-memory cfg would change the saved file — `serialize(cfg)` differs
        from the text last written/loaded (`_saved_text`). Used by quit (`q`). NB: an empty
        ultrawork/compaction sub-object serializes away, so adding one is undoable (it's in the
        history) but does NOT count as dirty — there's nothing to save."""
        return config_io.serialize(self.cfg) != self._saved_text

    def _record(self, label: str) -> None:
        """Snapshot the current cfg into the undo history under `label` (a no-op if nothing
        actually changed) and refresh the hint bar so `u undo` appears. Call after ANY cfg
        mutation — this single chokepoint is what makes every operation undoable. The current
        `_custom_rows` (off-chain typed models) rides along as the entry's `aux` so a restore
        moves typed rows in lockstep with cfg — undoing an add-model drops its row, not just its
        assignment."""
        if self._history.push(self.cfg, label, aux=self._custom_rows):
            self._render_hints()

    def _stage_row(self, target: str, row: dict, label: str) -> None:
        """Write the chosen candidate row into the cfg node, re-render, and record an undo
        snapshot under `label`."""
        node = self._ensure_node(target)
        node["model"] = f"{row['provider']}/{row['model']}"
        if row.get("variant"):
            node["variant"] = row["variant"]
        else:
            node.pop("variant", None)
        # The assignment changed, so _build_rows' synthesized current-off-chain row may no longer
        # apply (picked a chain model) or now describes a different model — drop the cache so it
        # rebuilds from the new cfg value.
        self._rows.pop(target, None)
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

    def check_action(self, action: str, parameters) -> bool:
        """Gate the pane-crossing keys (`←`/`→` and their vim aliases `h`/`l`, all bound to
        these two actions) to the base screen: a ModalScreen manages its own focus, and `←`
        inside e.g. the variant modal must not reach down to the (hidden)
        #targets list. (Defense-in-depth: Textual already truncates the binding chain at a
        modal, so these app bindings can't fire while one is up — and the add-model Input's own
        ←/→ cursor bindings take precedence regardless.) All other actions stay enabled."""
        if action == "command_palette" and isinstance(self.screen, AddModelModal):
            # Ctrl-P drives the add-model fuzzy list (up) while that modal is open, so suppress the
            # App's *priority* command-palette binding there (a priority binding is checked from the
            # App down, before the key reaches the modal — only check_action can gate it). The
            # palette stays available everywhere else; Ctrl-N is not an app binding.
            return False
        if action in ("focus_targets", "focus_candidates", "undo", "redo"):
            # Pane-crossing focus AND undo/redo are base-screen-only: a ModalScreen manages its
            # own focus and keys (e.g. AddSubModal binds `u` to pick ultrawork), so the app's
            # `u`/`ctrl+r` must not reach down through a modal. (Textual already truncates the
            # binding chain at a modal; this is the explicit, matching guard.)
            return len(self.screen_stack) <= 1
        return True

    def action_clear(self) -> None:
        """`x` — clear/delete the current target. On a base agent/category row it clears the
        assignment (drops model/variant, keeps the row). On an ↳ ultrawork/compaction SUB-target
        it deletes the whole row: a cleared sub-object serializes away anyway (config_io drops
        empty sub-objects), so a model-less placeholder isn't worth keeping — for a sub-target
        clear == delete, which is also how you undo a stray `a` add without reaching for `u`.
        Undoable (`u`) either way — a fat-fingered `x` is one keystroke from being reverted."""
        target = self._current_target
        if target is None:
            return
        if target.startswith("agent:") and "." in target[len("agent:"):]:
            name, kind = target[len("agent:"):].split(".", 1)
            self._delete_subtarget(target, name, kind)
            return
        node = self._node_for(target)
        if isinstance(node, dict) and ("model" in node or "variant" in node):
            node.pop("model", None)
            node.pop("variant", None)
        # Drop the cache so _build_rows stops synthesizing the now-cleared off-chain row.
        self._rows.pop(target, None)
        self._refresh_right(target)
        self._record(f"clear {self._target_label(target)}")

    def _delete_subtarget(self, target: str, name: str, kind: str) -> None:
        """`x` on an ↳ ultrawork/compaction row — remove the sub-target outright, dropping the
        cfg node (along with any model it held) plus its off-chain typed rows and cached resolver
        rows, so re-adding the same sub-target later starts clean rather than resurrecting a stale
        ⚠ row. Rebuilds the left pane onto the parent agent and records an undoable snapshot — the
        `_custom_rows` ride along as the entry's `aux`, so `u` restores the row in lockstep with cfg."""
        agent = (self.cfg.get("agents") or {}).get(name)
        if isinstance(agent, dict):
            agent.pop(kind, None)
        self._custom_rows.pop(target, None)
        self._rows.pop(target, None)
        parent = f"agent:{name}"
        self._populate_targets(select=parent)
        self._refresh_right(parent)
        self._record(f"delete {kind} sub-target from {name}")

    def action_variant(self) -> None:
        """`v` — pick from the variants opencode reports for the highlighted candidate's
        (provider, model) (catalog.variants_for — the cached `--verbose` map). A model opencode
        lists with no variants (kimi) — or whose verbose isn't cached anywhere — has nothing to
        pick, so `v` just bells (no fallback: variant validity is opencode's, not the heuristic's)."""
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
        variants = self.catalog.variants_for(row["provider"], row["model"])
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
                self._render_candidates(target)

        self.push_screen(VariantModal(variants), _apply)

    def action_edit_or_sub(self) -> None:
        """`a` — pane-contextual, one key (see _render_hints / DESIGN §Textual contract). Only a
        #targets *agent* row does "sub" (add an ultrawork/compaction sub-target); everywhere else
        — #candidates, or a #targets *category* row (categories have no sub-targets) — `a` opens
        the add/edit-model modal ("edit")."""
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
            self.bell()
            return
        target = self._current_target
        if target is None:
            return

        def _accept(row) -> None:
            if row is None:
                return
            # Persist the typed row in _custom_rows (durable across undo/redo) and invalidate the
            # per-target row cache so _build_rows re-merges it as a selectable candidate, then
            # stage it.
            self._custom_rows.setdefault(target, []).append(row)
            self._rows.pop(target, None)
            self._render_candidates(target)
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
            self._ensure_node(f"agent:{name}.{kind}")
            self._populate_targets()
            # Highlight the freshly added sub-target.
            targets = self.query_one("#targets", OptionList)
            try:
                targets.highlighted = self._index_of_option(targets, f"agent:{name}.{kind}")
            except Exception:
                pass
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

    def _restore_state(self, state: dict) -> None:
        """Swap self.cfg for a restored history snapshot and re-render everything that depends
        on it: the LEFT pane (sub-targets appear/vanish with the cfg) and the RIGHT pane
        (detail + the `●` current-pick marker). Mirrors the per-session cache handling a
        refresh does, minus the catalog rebuild — the candidate rows' `●` follows cfg, so the
        per-target row cache is dropped and rebuilt; `_cand_choice` (highlight memory) and
        `_detail_cache` (keyed by model id) are unaffected and kept. `_custom_rows` IS restored
        (from the entry's `aux`) so typed off-chain rows move in lockstep with undo/redo."""
        self.cfg = state
        self._rows.clear()  # resolver rows rebuild around the restored _custom_rows below
        # Move typed (off-chain) rows in lockstep with undo/redo: load the _custom_rows snapshot
        # this cfg state was pushed with, so undoing an add-model drops its row and redoing brings
        # it back — not just the bare cfg value. (Empty {} for entries pushed before any add.)
        self._custom_rows = self._history.current_aux()
        # Pick a target that still exists after the restore: a sub-target whose node is gone
        # (an undone add-sub) falls back to its parent agent; top-level agent/category rows
        # always exist (they come from suggestions, not cfg).
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
        else:
            self._render_hints()

    def action_save(self) -> None:
        """`s` — diff + confirm modal (incl. first-save palette-loss warning) → config_io.save."""
        diff = config_io.diff_text(self.cfg, self.config_path)
        if not diff.strip():
            self.notify("Nothing to save.")
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
                result = config_io.save(self.cfg, self.config_path)
            except Exception as exc:  # surface, don't crash the app
                self.notify(f"Save failed: {exc}", severity="error")
                return
            # Re-baseline dirtiness to what's now on disk (== serialize(cfg) either way). The
            # undo history is intentionally preserved across a save, so you can still undo a
            # just-saved edit (and re-save to persist the reverted state).
            self._saved_text = config_io.serialize(self.cfg)
            self.notify("Saved." if result.changed else "Nothing to save.")

        self.push_screen(ConfirmModal("Save changes?", body), _confirm)

    def action_refresh(self) -> None:
        """`r` — force a live re-fetch + rebuild cache, OFF the UI thread. Doubles as the
        CatalogUnavailable retry and the manual staleness refresh (DESIGN §CLI/refresh)."""
        self._refresh_catalog()

    @work(exclusive=True, group="refresh")
    async def _refresh_catalog(self) -> None:
        """Background worker: run `opencode models --refresh` (network, ~3s+) off the event
        loop via catalog.refresh(), then rebuild the resolver and re-render. Exclusive so a
        second `r` supersedes an in-flight refresh."""
        self.query_one("#providers", Static).update("Refreshing models… (opencode --refresh)")
        try:
            new_catalog = await asyncio.to_thread(catalog_mod.refresh)
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
        # _custom_rows is dropped too (a typed model's stored availability ⚠ is now stale); also
        # wipe the history's aux snapshots so undo/redo can't resurrect a pre-refresh typed row.
        self._rows.clear()
        self._custom_rows.clear()
        self._history.clear_aux()
        self._detail_cache.clear()
        self._detail_generation += 1
        self._render_providers()
        if self._current_target is not None:
            self._refresh_right(self._current_target)
        self.notify(f"Refreshed — {len(new_catalog.connected)} providers.")

    def action_quit_confirm(self) -> None:
        """`q` — quit; confirm if there are unsaved edits (`_is_dirty`: serialize(cfg) differs
        from what's on disk — so undoing back to the saved state quits without a prompt)."""
        if not self._is_dirty():
            self.exit()
            return

        def _confirm(ok) -> None:
            if ok:
                self.exit()

        self.push_screen(
            ConfirmModal("Quit?", "You have unsaved changes. Quit without saving?"), _confirm
        )


def run_app(config_path: str = None) -> None:
    """Build catalog / suggestions / resolver / config and run OModelApp().
    Called by cli.main() for the default (no-subcommand) invocation.

    Degrades gracefully: on CatalogUnavailable the app still launches with a banner + `r`
    retry and suggestions/add-model only (no resolver candidates until catalog is back)."""
    suggestions = suggestions_mod.load()
    cfg, resolved_path = config_io.load_config(config_path)

    catalog_error: Optional[BaseException] = None
    try:
        catalog = catalog_mod.load()
    except CatalogUnavailable as exc:
        catalog_error = exc
        catalog = Catalog(available={}, connected=[])

    resolver: Optional[Resolver] = None
    if catalog_error is None:
        try:
            resolver = Resolver.build(catalog, suggestions)
        except Exception:
            resolver = None

    app = OModelApp(
        catalog=catalog,
        suggestions=suggestions,
        resolver=resolver,
        cfg=cfg,
        config_path=resolved_path,
        catalog_error=catalog_error,
    )
    app.run()
