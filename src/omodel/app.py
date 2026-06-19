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
  * Static#detail          — current model/variant + catalog.detail() line.
  * OptionList#candidates  — option IDs 'cand:<i>'; LAST row 'cand:add' (+ add model…).

KEYS: ↑↓ move · enter set (dispatch by row: cand:add → add-model modal, else set model +
default variant) · v variant · p prefix (cycle providers_for) · e add · x clear ·
a add sub-target · s save (diff+confirm) · q quit (confirm if dirty).
Add-model modal: one-line Input 'provider/model' + live preview; full provider/model used
verbatim (split on FIRST '/'); bare id auto-prefixed via resolve_prefix if available, else
'⚠ unknown — add a provider/' and enter is BLOCKED until qualified.
"""
from __future__ import annotations

from typing import Optional

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from . import catalog as catalog_mod
from . import config_io
from . import suggestions as suggestions_mod
from .catalog import Catalog, CatalogUnavailable
from .resolve import Resolver
from .suggestions import Suggestions

# Sub-targets an agent may carry beyond its top-level `model`.
_SUBKINDS = ("ultrawork", "compaction")


def _tag_str(tags: list) -> str:
    """Render the candidate-row tags list ('★'/'✓') as a fixed-width 2-glyph cell."""
    has_star = "★" in tags
    has_check = "✓" in tags
    return ("★" if has_star else " ") + ("✓" if has_check else " ")


def _warn_str(warn: list) -> str:
    """Render the candidate-row warn list as trailing ⚠ markers."""
    if not warn:
        return ""
    return "  ⚠ " + " ".join(warn)


def _row_label(row: dict) -> str:
    """One-line rendering of a candidate-row dict for OptionList#candidates."""
    variant = row.get("variant")
    vtext = f" ({variant})" if variant else ""
    return f"{_tag_str(row['tags'])} {row['provider']}/{row['model']}{vtext}{_warn_str(row['warn'])}"


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
    """

    def __init__(self, resolver: Resolver, suggestions: Suggestions) -> None:
        super().__init__()
        self._resolver = resolver
        self._suggestions = suggestions
        self._staged: Optional[dict] = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Add model — type provider/model (or a bare id):")
            yield Input(placeholder="provider/model", id="add-input")
            yield Static("", id="add-preview")

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

        warn = []
        if not self._resolver.catalog.providers_for(model):
            warn.append("unavailable")
        # An add row is user-typed; tag it ✓ ("you asked for it") and let warn flag availability.
        # Registry only validates variants (designates no default), so variant stays unset.
        row = {
            "source": "add",
            "model": model,
            "provider": provider,
            "variant": None,
            "entry": None,
            "tags": ["✓"],
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
    """

    def __init__(self, variants: list) -> None:
        super().__init__()
        self._variants = variants

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Variant:")
            ol = OptionList(id="variant-list")
            yield ol

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
        border-right: solid $panel;
    }
    #right {
        width: 1fr;
    }
    #detail {
        height: auto;
        min-height: 4;
        padding: 0 1;
        border-bottom: solid $panel;
    }
    #candidates {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("v", "variant", "variant"),
        Binding("p", "prefix", "prefix"),
        Binding("e", "add_model", "add"),
        Binding("x", "clear", "clear"),
        Binding("a", "add_sub", "sub"),
        Binding("s", "save", "save"),
        Binding("r", "retry", "retry"),
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
        # In-memory edit state.
        self.dirty = False
        # Cache of the candidate-row dicts currently rendered, keyed by target id. Each cache
        # entry may include staged "+ custom" rows and per-row prefix overrides (from `p`).
        self._rows: dict = {}
        # The target id currently shown in the right pane.
        self._current_target: Optional[str] = None

    # ----- composition -----------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("", id="providers")
        with Horizontal(id="main"):
            yield OptionList(id="targets")
            with Vertical(id="right"):
                yield Static("", id="detail")
                yield OptionList(id="candidates")

    def on_mount(self) -> None:
        self._render_providers()
        self._populate_targets()

    # ----- header ----------------------------------------------------------------------

    def _render_providers(self) -> None:
        header = self.query_one("#providers", Static)
        if self.catalog_error is not None:
            header.update("⚠ couldn't read models — press r to retry")
        elif self.catalog.connected:
            header.update("Providers: " + " · ".join(self.catalog.connected))
        else:
            header.update("Providers: (none — opencode not found; suggestions/add only)")

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

    def _node_for(self, target: str):
        """Return the cfg dict node holding {model, variant} for `target`, or None if its
        parent agent/category isn't in cfg yet. Does NOT create nodes."""
        if target.startswith("agent:"):
            rest = target[len("agent:"):]
            if "." in rest:
                name, kind = rest.split(".", 1)
                agent = (self.cfg.get("agents") or {}).get(name)
                if not isinstance(agent, dict):
                    return None
                sub = agent.get(kind)
                return sub if isinstance(sub, dict) else None
            return (self.cfg.get("agents") or {}).get(rest)
        if target.startswith("cat:"):
            name = target[len("cat:"):]
            return (self.cfg.get("categories") or {}).get(name)
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

    # ----- right pane: detail + candidates ---------------------------------------------

    def _build_rows(self, target: str) -> list:
        """Candidate rows for `target`: resolver.candidates(target) when a resolver exists,
        else just the current assignment (degraded mode). Cached per target so staged edits
        and prefix overrides survive re-highlight."""
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
            bare = model.split("/", 1)[1] if "/" in model else model
            try:
                info = self.catalog.detail(bare)
            except Exception:
                info = None
            if info:
                lines.append(self._detail_line(info))
        else:
            lines.append("model: — (unset)")
            lines.append("variant: —")
        detail.update("\n".join(lines))

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
        for i, row in enumerate(rows):
            cands.add_option(Option(_row_label(row), id=f"cand:{i}"))
        cands.add_option(Option("+ add model…", id="cand:add"))

    def _refresh_right(self, target: str) -> None:
        self._current_target = target
        self._render_detail(target)
        self._render_candidates(target)

    # ----- events ----------------------------------------------------------------------

    @on(OptionList.OptionHighlighted, "#targets")
    def _target_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        oid = event.option_id
        if not oid or oid.startswith("hdr:"):
            return
        self._refresh_right(oid)

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
            # Re-render the row in place and, if it's the staged assignment, restage.
            self._rows[target][idx] = row
            self._stage_row(target, row)

        self.push_screen(VariantModal(variants), _apply)

    def action_prefix(self) -> None:
        """`p` — cycle the highlighted candidate's prefix across providers_for(model)."""
        target = self._current_target
        if target is None or self.resolver is None:
            return
        idx = self._highlighted_candidate_index()
        if idx is None:
            return
        rows = self._build_rows(target)
        if not (0 <= idx < len(rows)):
            return
        row = rows[idx]
        cands = self.catalog.providers_for(row["model"])
        if not cands:
            return
        try:
            pos = cands.index(row["provider"])
        except ValueError:
            pos = -1
        row["provider"] = cands[(pos + 1) % len(cands)]
        self._rows[target][idx] = row
        # Re-render candidates so the new prefix shows; restage if this row is current.
        self._render_candidates(target)
        # Keep the highlight on the same row.
        try:
            self.query_one("#candidates", OptionList).highlighted = idx
        except Exception:
            pass
        # If the cycled row matches what's staged, update the staged prefix too.
        model, _ = self._current_assignment(target)
        if model and model.split("/", 1)[-1] == row["model"]:
            self._stage_row(target, row)

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

        self.push_screen(AddModelModal(self.resolver, self.suggestions), _accept)

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
        # Create an empty sub-object so it shows as a sub-row (and serializes when set).
        self._ensure_node(f"agent:{name}.{to_add}")
        self.dirty = True
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

    def action_retry(self) -> None:
        """`r` — re-run catalog.load() after a CatalogUnavailable banner; rebuild the resolver."""
        if self.catalog_error is None:
            return
        try:
            new_catalog = catalog_mod.load()
        except CatalogUnavailable as exc:
            self.catalog_error = exc
            self._render_providers()
            return
        self.catalog = new_catalog
        self.catalog_error = None
        try:
            self.resolver = Resolver.build(new_catalog, self.suggestions)
        except Exception:
            self.resolver = None
        self._rows.clear()
        self._render_providers()
        if self._current_target is not None:
            self._refresh_right(self._current_target)

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
