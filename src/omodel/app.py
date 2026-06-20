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
                            row matching the launch-time on-disk assignment is prefixed '● '.
  * Static#hints           — pane-aware key hint bar (bottom row). Content switches on focus
                            + highlighted row (see _render_hints); modals carry their own
                            one-line hint instead.

Each pane is a bordered card; the focused pane (`#targets`/`#candidates`) brightens its border
to `$accent`, while blurred panes and the never-focused `#detail` use `$primary`.
`#providers`/`#hints`/`#detail` don't focus.

KEYS: ↑↓ move within the focused pane · ←/→ focus targets/candidates (gated to the base
screen via check_action) · enter set (dispatch by row: cand:add → add-model modal, else set
model + default variant) · v variant · e add · x clear · a add sub-target · s save
(diff+confirm) · r refresh (live re-fetch off-thread + rebuild cache; also retries after
CatalogUnavailable) · q quit (confirm if dirty). The live keys are always shown in
Static#hints (and per-modal hint lines).
Add-model modal: one-line Input 'provider/model' + live preview; full provider/model used
verbatim (split on FIRST '/'); bare id auto-prefixed via resolve_prefix if available, else
'⚠ unknown — add a provider/' and enter is BLOCKED until qualified.
"""
from __future__ import annotations

import asyncio
import copy
from typing import Optional

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from . import cache as cache_mod
from . import catalog as catalog_mod
from . import config_io
from . import suggestions as suggestions_mod
from .catalog import Catalog, CatalogUnavailable
from .resolve import Resolver
from .suggestions import Suggestions

# Sub-targets an agent may carry beyond its top-level `model`.
_SUBKINDS = ("ultrawork", "compaction")

# Agents omo locks to a single model family. Hephaestus is GPT-exclusive: omo's
# `no-hephaestus-non-gpt` hook reassigns the session to Sisyphus for any non-GPT model. We
# mirror that — the chain + add-model are both restricted to GPT models for these agents.
_GPT_ONLY_AGENTS = frozenset({"hephaestus"})


def _is_gpt_model(model_id: str) -> bool:
    """omo's `isGptModel` (model-core): the model name (after the LAST '/'), lowercased,
    contains 'gpt'. Used to gate the add-model modal for GPT-only agents (Hephaestus)."""
    return "gpt" in model_id.rsplit("/", 1)[-1].lower()


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


class AddModelModal(ModalScreen):
    """`e` / cand:add — one-line Input for `provider/model` + live preview of what saves.

    Full `provider/model` (a '/' present) → used verbatim, split on the FIRST '/'.
    Bare id → auto-prefixed via resolver.resolve_prefix if available, else
    '⚠ unknown — add a provider/' and `enter` is BLOCKED until qualified.
    Dismisses with the staged candidate-row dict (source 'add') on accept, or None on cancel.
    """

    BINDINGS = [
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

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Add model — type provider/model (or a bare id):")
            yield Input(placeholder="provider/model", id="add-input")
            yield Static("", id="add-preview")
            yield Static("enter add · esc cancel", id="add-hints", classes="modal-hints")

    def on_mount(self) -> None:
        self.query_one("#add-input", Input).focus()
        self._recompute("")

    def _build_row(self, text: str):
        """Return (row_or_None, preview_text, accept_ok) for the current Input value."""
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

        # GPT-only target: block a non-GPT model (omo would reassign the agent to Sisyphus).
        if self._require_gpt and not _is_gpt_model(model):
            return None, "⚠ Hephaestus is GPT-only — the model name must contain 'gpt'", False

        warn = []
        if not self._resolver.catalog.providers_for(model):
            warn.append("unavailable")
        # An add row is user-typed ("you asked for it"); warn flags availability but never
        # blocks. It is not a chain substitute, so substitute_for stays None. Registry only
        # validates variants (designates no default), so variant stays unset.
        row = {
            "source": "add",
            "model": model,
            "provider": provider,
            "variant": None,
            "entry": None,
            "substitute_for": None,
            "warn": warn,
        }
        preview = f"saves: {provider}/{model}" + _warn_str(warn)
        return row, preview, True

    def _recompute(self, text: str) -> None:
        row, preview, ok = self._build_row(text)
        self._staged = row if ok else None
        self.query_one("#add-preview", Static).update(preview)

    @on(Input.Changed, "#add-input")
    def _on_changed(self, event: Input.Changed) -> None:
        self._recompute(event.value)

    @on(Input.Submitted, "#add-input")
    def _on_submitted(self, event: Input.Submitted) -> None:
        # enter is BLOCKED until qualified (staged is None ⇒ no-op).
        if self._staged is not None:
            self.dismiss(self._staged)

    def action_cancel(self) -> None:
        self.dismiss(None)


class VariantModal(ModalScreen):
    """`v` — pick from the family's valid variants + '(none)'.  Dismisses with the chosen
    variant string, the sentinel '' for (none), or None on cancel."""

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
            ol = OptionList(id="variant-list")
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
    palette-loss warning) and Yes/No.  Dismisses True on accept, False otherwise."""

    BINDINGS = [
        Binding("escape", "decline", "No", show=False),
        Binding("y", "accept", "Yes", show=False),
        Binding("n", "decline", "No", show=False),
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
            yield Static(self._body, id="confirm-body")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes", variant="primary", id="confirm-yes")
                yield Button("No", id="confirm-no")
            yield Static("y yes · n no · esc cancel", id="confirm-hints", classes="modal-hints")

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


class OModelApp(App):
    """Two-pane master-detail TUI to set OMO models.  See module docstring for the stable
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
        border: solid $primary;
    }
    #right {
        width: 1fr;
    }
    #detail {
        height: auto;
        min-height: 4;
        padding: 0 1;
        border: solid $primary;
    }
    #candidates {
        height: 1fr;
        border: solid $primary;
    }
    #targets:focus, #candidates:focus {
        border: solid $accent;
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
        Binding("v", "variant", "variant"),
        Binding("e", "add_model", "add"),
        Binding("x", "clear", "clear"),
        Binding("a", "add_sub", "sub"),
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
        # Snapshot of the on-disk config at launch (the oh-my-openagent.jsonc that becomes
        # .backup/original.jsonc). Frozen for the session; used to mark (●) the candidate row
        # that matches what your config currently has — staging edits self.cfg but not this.
        self._saved_cfg = copy.deepcopy(cfg)
        # In-memory edit state.
        self.dirty = False
        # Cache of the candidate-row dicts currently rendered, keyed by target id. Each cache
        # entry may include staged "+ custom" rows from the add-model modal.
        self._rows: dict = {}
        # The target id currently shown in the right pane.
        self._current_target: Optional[str] = None
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
            yield OptionList(id="targets")
            with Vertical(id="right"):
                yield Static("", id="detail")
                yield OptionList(id="candidates")
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

    def _populate_targets(self) -> None:
        targets = self.query_one("#targets", OptionList)
        # Preserve highlight across rebuilds (e.g. after `a` adds a sub-target).
        prior = None
        if targets.highlighted is not None:
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

    def _node_for(self, target: str, cfg: "Optional[dict]" = None):
        """Return the dict node holding {model, variant} for `target` in `cfg`
        (default self.cfg), or None if its parent agent/category isn't present. Does NOT
        create nodes. Pass self._saved_cfg to read the launch-time on-disk assignment."""
        if cfg is None:
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

    def _saved_model(self, target: str) -> str:
        """The 'provider/model' string `target` had on disk at launch (self._saved_cfg), or
        '' if unset. Fixed for the session — used to ● the candidate row matching your
        current oh-my-openagent.jsonc."""
        node = self._node_for(target, self._saved_cfg)
        if not isinstance(node, dict):
            return ""
        return node.get("model", "") or ""

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
        """Candidate rows for `target`: resolver.candidates(target) when a resolver exists,
        else just the current assignment (degraded mode). Cached per target so staged edits
        survive re-highlight."""
        if target in self._rows:
            return self._rows[target]
        rows: list = []
        if self.resolver is not None:
            try:
                rows = list(self.resolver.candidates(target))
            except CatalogUnavailable:
                rows = []
        self._rows[target] = rows
        return rows

    def _render_detail(self, target: str) -> None:
        detail = self.query_one("#detail", Static)
        model, variant = self._current_assignment(target)
        lines = [f"[b]{target}[/b]"]
        if model:
            lines.append(f"model: {model}")
            lines.append("variant: " + (variant if variant else "—"))
            # Detail line from catalog (display only); bare model id is after the first '/'.
            # Cache hit → render now; miss → schedule a background fetch and render without it
            # (the line pops in when the worker finishes — never blocks highlighting).
            bare = model.split("/", 1)[1] if "/" in model else model
            info = self._detail_info(target, bare)
            if info:
                lines.append(self._detail_line(info))
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
        # Mark (●) the row matching what oh-my-openagent.jsonc has on disk for this target.
        saved = self._saved_model(target)
        for i, row in enumerate(rows):
            matched = bool(saved) and f"{row['provider']}/{row['model']}" == saved
            label = ("● " if matched else "  ") + _row_label(row)
            cands.add_option(Option(label, id=f"cand:{i}"))
        cands.add_option(Option("+ add model…", id="cand:add"))

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
        cands = self.query_one("#candidates", OptionList)
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
                text = "↑↓ move · ← targets · enter add · s save · r refresh · q quit"
            else:
                text = ("↑↓ move · ← targets · enter set · v variant · e add · "
                        "x clear · s save · r · q")
        else:
            # Left pane (targets): `a sub` only applies to an agent row, not a category.
            tgt = self._current_target or ""
            sub = "a sub · " if tgt.startswith("agent:") else ""
            text = f"↑↓ move · → candidates · {sub}s save · r refresh · q quit"
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
        changes which keys apply (enter set vs enter add, v/x relevance) — refresh hints."""
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

    def _stage_row(self, target: str, row: dict) -> None:
        """Write the chosen candidate row into the cfg node and mark dirty."""
        node = self._ensure_node(target)
        node["model"] = f"{row['provider']}/{row['model']}"
        if row.get("variant"):
            node["variant"] = row["variant"]
        else:
            node.pop("variant", None)
        self.dirty = True
        self._refresh_right(target)

    def _set_candidate(self, idx: int) -> None:
        if self._current_target is None:
            return
        rows = self._build_rows(self._current_target)
        if not (0 <= idx < len(rows)):
            return
        self._stage_row(self._current_target, rows[idx])

    # ----- actions / keybindings -------------------------------------------------------

    def action_focus_targets(self) -> None:
        """`←` — focus the targets (left) pane."""
        self.query_one("#targets", OptionList).focus()

    def action_focus_candidates(self) -> None:
        """`→` — focus the candidates (right) pane."""
        self.query_one("#candidates", OptionList).focus()

    def check_action(self, action: str, parameters) -> bool:
        """Gate the pane-crossing arrows to the base screen: a ModalScreen manages its own
        focus, and `←` inside e.g. the variant modal must not reach down to the (hidden)
        #targets list. (Defense-in-depth: Textual already truncates the binding chain at a
        modal, so these app bindings can't fire while one is up — and the add-model Input's own
        ←/→ cursor bindings take precedence regardless.) All other actions stay enabled."""
        if action in ("focus_targets", "focus_candidates"):
            return len(self.screen_stack) <= 1
        return True

    def action_clear(self) -> None:
        """`x` — clear the current target's assignment (delete model/variant)."""
        target = self._current_target
        if target is None:
            return
        node = self._node_for(target)
        if isinstance(node, dict) and ("model" in node or "variant" in node):
            node.pop("model", None)
            node.pop("variant", None)
            self.dirty = True
        self._refresh_right(target)

    def action_variant(self) -> None:
        """`v` — push the family's valid variants for the highlighted candidate's model."""
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
        fam = self.suggestions.detect_family(row["model"])
        variants = list(fam.variants) if fam and fam.variants else list(self.suggestions.known_variants)

        def _apply(result) -> None:
            if result is None:
                return
            chosen = result or None  # '' sentinel → clear
            row["variant"] = chosen
            self._rows[target][idx] = row
            # `v` adjusts the highlighted candidate's variant but must NOT create an
            # assignment (DESIGN §Events: only Enter sets a model). Restage only when this
            # row is already the staged assignment; otherwise just re-render so the chosen
            # variant rides along if the user later picks the row with Enter.
            model, _ = self._current_assignment(target)
            if model and model.split("/", 1)[-1] == row["model"]:
                self._stage_row(target, row)
            else:
                self._render_candidates(target)

        self.push_screen(VariantModal(variants), _apply)

    def action_add_model(self) -> None:
        """`e` — open the add-model modal."""
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
            # Insert the typed row as a selected "+ custom" candidate and stage it.
            self._rows.setdefault(target, [])
            self._rows[target].append(row)
            self._render_candidates(target)
            self._stage_row(target, row)

        self.push_screen(
            AddModelModal(self.resolver, self.suggestions, require_gpt=self._gpt_only(target)),
            _accept,
        )

    def action_add_sub(self) -> None:
        """`a` — add an ultrawork/compaction sub-target to the highlighted agent."""
        target = self._current_target
        if target is None or not target.startswith("agent:"):
            return
        rest = target[len("agent:"):]
        name = rest.split(".", 1)[0]
        present = set(self._agent_subtargets(name))
        to_add = next((k for k in _SUBKINDS if k not in present), None)
        if to_add is None:
            self.bell()
            return
        # Create an empty sub-object so it shows as a sub-row. Do NOT mark dirty: an empty
        # ultrawork/compaction is not a real edit — serialize() drops empty sub-objects, and
        # only staging a model into it (which sets dirty) counts as a change.
        self._ensure_node(f"agent:{name}.{to_add}")
        self._populate_targets()
        # Highlight the freshly added sub-target.
        targets = self.query_one("#targets", OptionList)
        try:
            targets.highlighted = self._index_of_option(targets, f"agent:{name}.{to_add}")
        except Exception:
            pass

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
                "⚠ First save deletes the commented-out palette (saved verbatim to "
                ".backup/original.jsonc, always restorable).\n\n" + diff
            )

        def _confirm(ok) -> None:
            if not ok:
                return
            try:
                result = config_io.save(self.cfg, self.config_path)
            except Exception as exc:  # surface, don't crash the app
                self.notify(f"Save failed: {exc}", severity="error")
                return
            if result.changed:
                self.dirty = False
                self.notify("Saved.")
            else:
                self.notify("Nothing to save.")

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
        self._rows.clear()
        self._detail_cache.clear()
        self._detail_generation += 1
        self._render_providers()
        if self._current_target is not None:
            self._refresh_right(self._current_target)
        self.notify(f"Refreshed — {len(new_catalog.connected)} providers.")

    def action_quit_confirm(self) -> None:
        """`q` — quit; confirm if there are unsaved edits."""
        if not self.dirty:
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
