"""test_app_pilot.py — headless Textual pilot: select agent, set model, save.

DESIGN §Textual two-pane contract / §Verification check #7 (UI half).

OModelApp.__init__ takes: catalog, suggestions, resolver, cfg, config_path, catalog_error=None.
Tests build these explicitly from the test catalog + real bundled suggestions.

Interaction pattern confirmed for Textual 8.x:
  - pilot.click("#widget-id") works for stable widget IDs (no colon in the ID).
  - OptionList option IDs contain ':' which is invalid in CSS selectors for pilot.click.
  - Instead: set OptionList.highlighted = get_option_index(option_id), focus, press enter.
  - OptionList.OptionHighlighted fires on highlight change; OptionList.OptionSelected fires
    when the focused OptionList receives 'enter' via action_select.
  - Save flow: 's' opens ConfirmModal; confirm with 'y' (keybinding Binding("y","accept")).
  - Sub-targets agent:<name>.ultrawork/.compaction inherit the parent agent's chain.

All tests use tmp_path only — the real ~/.config/opencode/... is never touched.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import glob
import json
import os
import subprocess
import threading
import time
import types

import pytest
from rich.cells import cell_len
from textual.content import Content
from textual.widgets import Button, Input, OptionList, Static

import omodel
from omodel import presets as presets_mod
from omodel.app import (
    ConfirmModal,
    HelpModal,
    OModelApp,
    QuitModal,
    VariantModal,
    _to_thread_daemon,
)
from omodel.catalog import Catalog
from omodel.config_io import list_backups
from omodel.resolve import Resolver


@pytest.fixture(autouse=True)
def _no_real_opencode(monkeypatch):
    """Hard rule: no test calls the real opencode CLI. The detail pane now fetches
    `opencode models <prov> --verbose` from a worker thread (~320 MB per process), so an
    un-stubbed pilot run would spawn real opencode subprocesses that outlive the test and
    pile up — that OOM'd a dev machine. Stub subprocess.run so the TUI stays hermetic."""
    def _stub(*args, **kwargs):
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _stub)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_app(cfg_path: str) -> OModelApp:
    """Hermetic DI constructor — no live opencode binary.
    Catalog is hardcoded so deepseek/deepseek-v4-pro exists deterministically,
    opencode is a multi-vendor gateway, and the dedicated providers match the
    §Verification check #2 expectation (dedicated-first resolution).
    This is CI-safe: no subprocess calls."""
    from omodel import config_io as _config_io
    from omodel import suggestions as suggestions_mod

    suggestions = suggestions_mod.load()
    catalog = Catalog(
        available={
            "opencode": ["claude-opus-4-7", "kimi-k2.5", "glm-5", "gpt-5.5"],
            "deepseek": ["deepseek-v4-pro"],
            "moonshotai-cn": ["kimi-k2.5"],
            "zhipuai": ["glm-5"],
            "openai": ["gpt-5.5"],
        },
        connected=["opencode", "deepseek", "moonshotai-cn", "zhipuai", "openai"],
    )
    resolver = Resolver.build(catalog, suggestions)
    cfg, resolved = _config_io.load_config(cfg_path)
    return OModelApp(
        catalog=catalog,
        suggestions=suggestions,
        resolver=resolver,
        cfg=cfg,
        config_path=resolved,
    )


def _build_app_with(cfg_path: str, catalog: Catalog) -> OModelApp:
    """Hermetic constructor for tests that need a bespoke Catalog (e.g. a qwen / empty-variants
    family). Same wiring as _build_app, just an injected catalog."""
    from omodel import config_io as _config_io
    from omodel import suggestions as suggestions_mod

    suggestions = suggestions_mod.load()
    resolver = Resolver.build(catalog, suggestions)
    cfg, resolved = _config_io.load_config(cfg_path)
    return OModelApp(
        catalog=catalog,
        suggestions=suggestions,
        resolver=resolver,
        cfg=cfg,
        config_path=resolved,
    )


# Canonical fake-verbose cache seeder — shared across test files (tests/_helpers.py).
from _helpers import seed_verbose as _seed_verbose


async def _select_target(pilot, option_id: str) -> None:
    """Highlight a target by ID in OptionList#targets, then fire OptionSelected via enter.
    OptionList option IDs contain ':' which is invalid in CSS selectors, so we use
    get_option_index + set highlighted directly."""
    targets = pilot.app.query_one("#targets", OptionList)
    try:
        idx = targets.get_option_index(option_id)
    except Exception:
        pytest.fail(f"Option {option_id!r} not found in #targets")
    targets.highlighted = idx
    targets.focus()
    await pilot.press("enter")
    await pilot.pause()


async def _select_candidate(pilot, model_fragment: str) -> str:
    """Highlight the first candidate whose label contains model_fragment; return option ID.
    Focuses #candidates and fires OptionSelected via enter. Returns the found option ID.

    model_fragment should be specific enough to match the desired 'provider/model' string
    in the rendered row label (e.g. 'zhipuai/glm-5' not just 'glm-5'), so the dedicated-first
    resolved prefix is pinned unambiguously.
    """
    candidates = pilot.app.query_one("#candidates", OptionList)
    found_id = None
    found_idx = None
    for i in range(candidates.option_count):
        opt = candidates.get_option_at_index(i)
        oid = opt.id or ""
        if oid.startswith("hdr:") or oid == "cand:add":
            continue
        label = str(opt.prompt)
        if model_fragment in label:
            found_id = oid
            found_idx = i
            break
    if found_id is None:
        return None
    candidates.highlighted = found_idx
    candidates.focus()
    await pilot.press("enter")
    await pilot.pause()
    return found_id


async def _highlight_candidate(pilot, model_fragment: str) -> str:
    """Highlight (do NOT select) the first #candidates row whose label contains model_fragment,
    and focus the pane; returns its option id, or None. Unlike _select_candidate this presses
    nothing — for keys that act on the highlighted candidate (e.g. `v`)."""
    candidates = pilot.app.query_one("#candidates", OptionList)
    for i in range(candidates.option_count):
        opt = candidates.get_option_at_index(i)
        oid = opt.id or ""
        if oid.startswith("hdr:") or oid == "cand:add":
            continue
        if model_fragment in str(opt.prompt):
            candidates.highlighted = i
            candidates.focus()
            await pilot.pause()
            return oid
    return None


async def _save_and_confirm(pilot) -> None:
    """Press 's' to open the ConfirmModal, then 'y' to confirm."""
    await pilot.press("s")
    await pilot.pause()
    await pilot.press("y")
    await pilot.pause()


# ---------------------------------------------------------------------------
# Config fixture
# ---------------------------------------------------------------------------

PILOT_JSONC = """\
// hand-curated header — outside agents/categories, must survive
{
  "$schema": "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/assets/oh-my-opencode.schema.json",
  "agents": {
    "sisyphus": {
      "model": "opencode/claude-opus-4-7"
      // "model": "moonshotai-cn/kimi-k2.5"
    }
  },
  "categories": {},
  "team_mode": true,
  "experimental": {"featureY": false},
  "claude_code": {
    "enabled": true,
    "model": "opencode/claude-opus-4-8"
    // "skills": false
  }
}
"""


@pytest.fixture
def pilot_config(tmp_path):
    """Write realistic JSONC to a temp dir. Returns (cfg_path, str(tmp_path)).
    Never touches ~/.config/opencode/..."""
    cfg_path = str(tmp_path / "oh-my-openagent.jsonc")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(PILOT_JSONC)
    return cfg_path, str(tmp_path)


# ---------------------------------------------------------------------------
# Pilot test 1: full set + save cycle (§Verification check #7)
# ---------------------------------------------------------------------------

def test_pilot_set_model_and_save(pilot_config):
    """Full headless pilot:
    1. Build OModelApp with test catalog + real suggestions + temp config.
    2. Select agent:sisyphus via OptionList index + enter.
    3. In #candidates, highlight zhipuai/glm-5 + enter to set it.
    4. Press 's'; confirm ConfirmModal with 'y'.
    5. Re-json5.load the config and assert all contracts hold.
    """
    import json5

    cfg_path, tmp_dir = pilot_config

    async def _run():
        app = _build_app(cfg_path)

        async with app.run_test() as pilot:
            # 1. Select agent:sisyphus to populate the right pane
            await _select_target(pilot, "agent:sisyphus")

            # 2. Find and select zhipuai/glm-5 in candidates. glm-5 is a sisyphus chain
            # entry served by opencode(gateway) + zhipuai(dedicated); the full
            # 'zhipuai/glm-5' fragment pins the dedicated row (resolve_prefix: dedicated wins).
            found_id = await _select_candidate(pilot, "zhipuai/glm-5")
            assert found_id is not None, (
                "zhipuai/glm-5 must appear as a candidate for agent:sisyphus under the "
                "zhipuai/ dedicated provider (resolve_prefix: dedicated wins)."
            )

            # 3. Save and confirm
            await _save_and_confirm(pilot)

    asyncio.run(_run())

    # Assert on-disk result
    with open(cfg_path, encoding="utf-8") as f:
        saved = json5.load(f)

    # Model updated; zhipuai is dedicated → wins over opencode gateway
    assert saved["agents"]["sisyphus"]["model"] == "zhipuai/glm-5", (
        f"Expected zhipuai/glm-5, got {saved['agents']['sisyphus']['model']!r}"
    )

    # Non-model sections preserved BY VALUE
    assert saved["team_mode"] is True
    assert saved["experimental"] == {"featureY": False}
    assert saved["claude_code"]["enabled"] is True
    assert saved["claude_code"]["model"] == "opencode/claude-opus-4-8"

    # Edit-in-place save: agents/categories rewritten clean, everything else byte-for-byte.
    with open(cfg_path, encoding="utf-8") as f:
        raw_text = f.read()
    # Comments OUTSIDE agents/categories survive verbatim …
    assert raw_text.startswith("// hand-curated header"), (
        "the top comment outside agents/categories must be preserved verbatim"
    )
    assert '// "skills": false' in raw_text, (
        "a comment inside the non-model claude_code block must be preserved"
    )
    # … but the commented palette INSIDE agents is dropped, and no oModel header is injected
    # over the file's own top matter.
    assert "moonshotai-cn/kimi-k2.5" not in raw_text, "inside-agents palette must be dropped"
    assert "Generated by oModel" not in raw_text, (
        "the header must not be injected over an existing file's top matter"
    )

    # Timestamped snapshot exists
    backup_dir = os.path.join(tmp_dir, ".backup")
    timestamped = glob.glob(os.path.join(backup_dir, "[0-9]*.jsonc"))
    assert len(timestamped) >= 1, "At least one .backup/<ts>.jsonc must exist"

    # original.jsonc pinned verbatim (palette comments intact)
    orig_path = os.path.join(backup_dir, "original.jsonc")
    assert os.path.exists(orig_path), ".backup/original.jsonc must be created on first save"
    with open(orig_path, encoding="utf-8") as f:
        orig_text = f.read()
    assert "//" in orig_text, "original.jsonc must preserve the palette comments verbatim"


# ---------------------------------------------------------------------------
# Pilot test 3: Providers header shows connected providers in first-seen order
# ---------------------------------------------------------------------------

def test_pilot_providers_header_visible(pilot_config):
    """Static#providers renders 'oModel: <id · id · …>' from catalog.connected."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)

        async with app.run_test() as pilot:
            providers_widget = pilot.app.query_one("#providers", Static)
            # Static.content is the canonical way to read the current display value
            text = str(providers_widget.content)
            assert "oModel:" in text, f"Missing 'oModel:' in header: {text!r}"
            # Test catalog has opencode as the first connected provider
            assert "opencode" in text, f"opencode missing from providers header: {text!r}"
            # deepseek is also in connected
            assert "deepseek" in text, f"deepseek missing from providers header: {text!r}"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test 4: second save adds second snapshot; list_backups newest-first
# ---------------------------------------------------------------------------

def test_pilot_second_save_adds_snapshot(pilot_config):
    """A second save adds a second timestamped snapshot. list_backups returns newest-first."""
    cfg_path, tmp_dir = pilot_config

    async def _do_save(model_fragment: str):
        app = _build_app(cfg_path)

        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            await _select_candidate(pilot, model_fragment)
            await _save_and_confirm(pilot)

    # Use full 'provider/model' fragments to pick the right dedicated-provider rows.
    # Both are sisyphus chain entries resolved to their dedicated providers.
    asyncio.run(_do_save("zhipuai/glm-5"))
    time.sleep(0.02)  # ensure distinct UTC timestamps
    asyncio.run(_do_save("moonshotai-cn/kimi-k2.5"))

    backup_dir = os.path.join(tmp_dir, ".backup")
    timestamped = glob.glob(os.path.join(backup_dir, "[0-9]*.jsonc"))
    assert len(timestamped) >= 2, (
        f"Expected >=2 timestamped snapshots after two saves, got {len(timestamped)}"
    )

    # list_backups must list them newest-first
    backups = list_backups(cfg_path)
    ts_entries = [b for b in backups if not b.is_original]
    if len(ts_entries) >= 2:
        names = [b.name for b in ts_entries]
        assert names == sorted(names, reverse=True), (
            f"list_backups must return newest-first; got {names}"
        )


# ---------------------------------------------------------------------------
# Pilot test 5: sub-target inherits parent chain
# ---------------------------------------------------------------------------

def test_pilot_sub_target_inherits_parent_chain(pilot_config):
    """agent:sisyphus.ultrawork's pick list is IDENTICAL to the parent agent's (it inherits
    the same fallbackChain). TUI-track: 'a' key creates the sub-target."""
    cfg_path, _ = pilot_config

    def _real_candidate_ids(pilot):
        candidates = pilot.app.query_one("#candidates", OptionList)
        return [
            candidates.get_option_at_index(i).id
            for i in range(candidates.option_count)
            if candidates.get_option_at_index(i).id not in (None, "cand:add")
            and not (candidates.get_option_at_index(i).id or "").startswith("hdr:")
        ]

    async def _run():
        app = _build_app(cfg_path)

        async with app.run_test() as pilot:
            # Populate the parent's pick list and record it.
            await _select_target(pilot, "agent:sisyphus")
            parent_ids = _real_candidate_ids(pilot)
            assert len(parent_ids) > 0, "parent sisyphus must have candidates"

            # Highlight sisyphus, then 'a' opens the chooser and 'u' adds + highlights ultrawork.
            targets = pilot.app.query_one("#targets", OptionList)
            targets.highlighted = targets.get_option_index("agent:sisyphus")
            targets.focus()
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("u")  # chooser → ultrawork
            await pilot.pause()

            uw_present = any(
                targets.get_option_at_index(i).id == "agent:sisyphus.ultrawork"
                for i in range(targets.option_count)
            )
            assert uw_present, (
                "agent:sisyphus.ultrawork must be present after 'a' + 'u' — the sub-target "
                "chooser is fully wired, so this is a real regression, not a pending feature"
            )

            # Sub-target's pick list must equal the parent's (same chain, same rows).
            await _select_target(pilot, "agent:sisyphus.ultrawork")
            sub_ids = _real_candidate_ids(pilot)
            assert sub_ids == parent_ids, (
                f"sub-target must inherit the parent's pick list; "
                f"parent={parent_ids} sub={sub_ids}"
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test 5b: `a` opens a chooser; the picked kind (not a fixed cycle) is added
# ---------------------------------------------------------------------------

def test_pilot_add_sub_chooser(pilot_config):
    """`a` opens the sub-target chooser instead of blindly adding: `c` adds compaction first
    (proving the choice is honored), a second `a`+`u` adds ultrawork, and once both exist `a`
    is a no-op that opens no modal."""
    cfg_path, _ = pilot_config

    def _ids(targets):
        return [targets.get_option_at_index(i).id for i in range(targets.option_count)]

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            targets = pilot.app.query_one("#targets", OptionList)

            def _highlight_agent():
                targets.highlighted = targets.get_option_index("agent:sisyphus")

            _highlight_agent()
            targets.focus()
            await pilot.pause()

            # First `a` → chooser → `c`: compaction is added, ultrawork is NOT (not a cycle).
            await pilot.press("a")
            await pilot.pause()
            assert len(pilot.app.screen_stack) > 1, "`a` on an agent must open the chooser modal"
            await pilot.press("c")
            await pilot.pause()
            assert "agent:sisyphus.compaction" in _ids(targets)
            assert "agent:sisyphus.ultrawork" not in _ids(targets)

            # Second `a` → chooser → `u`: ultrawork joins it.
            _highlight_agent()
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("u")
            await pilot.pause()
            assert "agent:sisyphus.ultrawork" in _ids(targets)

            # Both present → `a` opens nothing (bell) and adds no row.
            _highlight_agent()
            await pilot.pause()
            before = _ids(targets)
            await pilot.press("a")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 1, "both kinds present → no chooser"
            assert _ids(targets) == before

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test 5b': `ultrawork` is Sisyphus-only — a non-Sisyphus agent adds compaction directly
# ---------------------------------------------------------------------------
def test_pilot_ultrawork_is_sisyphus_only(pilot_config):
    """omo only honors the `ultrawork`/`ulw` swap on Sisyphus, so every other agent has a single
    addable sub-kind: `compaction`. With no choice to make, `a` on a non-Sisyphus agent (oracle)
    skips the chooser and adds compaction **directly** — no modal, never an `ultrawork` block —
    and once compaction is present `a` just bells. Sisyphus, which supports both kinds, still opens
    the chooser (test_pilot_add_sub_chooser)."""
    cfg_path, _ = pilot_config

    def _ids(ol):
        return [ol.get_option_at_index(i).id for i in range(ol.option_count)]

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            targets = pilot.app.query_one("#targets", OptionList)

            def _highlight_oracle():
                targets.highlighted = targets.get_option_index("agent:oracle")
                targets.focus()

            _highlight_oracle()
            await pilot.pause()

            # `a` adds compaction directly — no chooser modal opens, and never an ultrawork row.
            await pilot.press("a")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 1, (
                "a single-kind agent must skip the chooser — `a` adds compaction directly"
            )
            assert "agent:oracle.compaction" in _ids(targets)
            assert "agent:oracle.ultrawork" not in _ids(targets)
            assert "ultrawork" not in pilot.app.cfg["agents"].get("oracle", {})

            # compaction is the only kind oracle supports → a second `a` just bells (no modal,
            # no new row).
            _highlight_oracle()
            await pilot.pause()
            before = _ids(targets)
            await pilot.press("a")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 1, "nothing left to add → no chooser"
            assert _ids(targets) == before

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test 5c: `a` on a #targets *category* row opens the model modal, not the chooser
# ---------------------------------------------------------------------------

def test_pilot_category_a_opens_add_modal(pilot_config):
    """A category has no sub-targets, so `a` on a #targets category row opens the add/edit-model
    modal (the same modal `a` opens in #candidates) — never the agent-only sub-target chooser."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            cat_name = next(iter(pilot.app.suggestions.categories.keys()))
            await _select_target(pilot, f"cat:{cat_name}")
            pilot.app.query_one("#targets", OptionList).focus()  # category row, left pane
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            assert len(pilot.app.screen_stack) > 1, "`a` on a category must open a modal"
            # It's the add-model modal (its #add-input Input), not the sub-target chooser.
            assert pilot.app.screen.query_one("#add-input", Input)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test 6: the on-disk (oh-my-openagent.jsonc) pick is marked ● in the list
# ---------------------------------------------------------------------------

def test_pilot_saved_model_marked(pilot_config):
    """The candidate row matching what oh-my-openagent.jsonc has on disk is prefixed with ●;
    other rows are not. Saved sisyphus = zhipuai/glm-5 (a chain entry in the pilot catalog)."""
    cfg_path, _ = pilot_config
    # Overwrite the config so sisyphus' on-disk model is a known in-list candidate.
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write('{ "agents": { "sisyphus": { "model": "zhipuai/glm-5" } } }')

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            cands = pilot.app.query_one("#candidates", OptionList)
            labels = []
            for i in range(cands.option_count):
                opt = cands.get_option_at_index(i)
                oid = opt.id or ""
                if oid == "cand:add" or oid.startswith("hdr:"):
                    continue
                labels.append(str(opt.prompt))

            glm = [s for s in labels if "zhipuai/glm-5" in s]
            assert len(glm) == 1, f"expected one zhipuai/glm-5 row, got {glm}"
            assert "●" in glm[0], f"saved row must be marked with ●: {glm[0]!r}"
            others = [s for s in labels if "zhipuai/glm-5" not in s]
            assert others, "expected other (unmarked) candidate rows too"
            assert all("●" not in o for o in others), f"only the saved row may be marked: {others}"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test 6b: the ● marker follows the current selection, not the on-disk one
# ---------------------------------------------------------------------------

def test_pilot_marker_follows_selection(pilot_config):
    """The ● tracks the *current* assignment, not the launch-time on-disk model: after picking
    a different candidate the ● moves to it and leaves the originally-marked row."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            cands = pilot.app.query_one("#candidates", OptionList)

            def _marked():
                out = []
                for i in range(cands.option_count):
                    opt = cands.get_option_at_index(i)
                    oid = opt.id or ""
                    if oid == "cand:add" or oid.startswith("hdr:"):
                        continue
                    if "●" in str(opt.prompt):
                        out.append(str(opt.prompt))
                return out

            # At launch the ● sits on the on-disk model (opencode/claude-opus-4-7).
            before = _marked()
            assert len(before) == 1 and "claude-opus-4-7" in before[0], (
                f"expected ● on the on-disk model at launch, got {before!r}"
            )

            # Pick a different in-list candidate; the ● must move to it.
            found = await _select_candidate(pilot, "zhipuai/glm-5")
            assert found is not None, "zhipuai/glm-5 must be a candidate row"
            after = _marked()
            assert len(after) == 1 and "zhipuai/glm-5" in after[0], (
                f"● must move to the selected model, got {after!r}"
            )
            assert "claude-opus-4-7" not in after[0], (
                "● must leave the old on-disk row once another model is picked"
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test 6d: an off-chain assignment already on disk is shown before + add model…
# ---------------------------------------------------------------------------

def test_pilot_off_chain_assignment_shown_before_add(pilot_config):
    """A custom model already set on disk that isn't in the chain (not typed this session) is
    surfaced as its own candidate row: ●-marked, placed immediately before `+ add model…`, and
    ⚠-flagged unavailable when no connected provider serves it — so what's configured is always
    visible and re-selectable, never silently dropped."""
    cfg_path, _ = pilot_config
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write('{ "agents": { "sisyphus": { "model": "myprovider/custom-model" } } }')

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            cands = pilot.app.query_one("#candidates", OptionList)

            ids = [cands.get_option_at_index(i).id for i in range(cands.option_count)]
            assert ids[-1] == "cand:add", f"+ add model… must stay last: {ids}"
            # The off-chain row is the cand:<i> immediately before the add row.
            before_add = str(cands.get_option_at_index(cands.option_count - 2).prompt)
            assert "myprovider/custom-model" in before_add, (
                f"off-chain assignment must be the row before + add model…: {before_add!r}"
            )
            assert "●" in before_add, f"configured off-chain model must be ●-marked: {before_add!r}"
            assert "⚠" in before_add and "unavailable" in before_add, (
                f"a model no provider serves must warn unavailable: {before_add!r}"
            )

            # Exactly one such row (no duplicate vs the chain) and it's the only ●.
            all_labels = [str(cands.get_option_at_index(i).prompt) for i in range(cands.option_count)]
            custom = [s for s in all_labels if "myprovider/custom-model" in s]
            assert len(custom) == 1, f"exactly one off-chain row: {custom}"
            assert sum("●" in s for s in all_labels) == 1, f"only the off-chain row is ●: {all_labels}"

            # Re-selectable: enter on it round-trips the same value through _set_candidate.
            found = await _select_candidate(pilot, "myprovider/custom-model")
            assert found is not None, "off-chain row must be selectable"
            assert pilot.app.cfg["agents"]["sisyphus"]["model"] == "myprovider/custom-model"

    asyncio.run(_run())


def test_pilot_off_chain_row_tracks_assignment_through_set_and_undo(pilot_config):
    """The synthesized off-chain row strictly mirrors the current cfg assignment (the per-target
    cache is dropped when it changes): picking an in-chain model drops the off-chain row, and undo
    restores both the off-chain assignment and its ●-marked row."""
    cfg_path, _ = pilot_config
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write('{ "agents": { "sisyphus": { "model": "myprovider/custom-model" } } }')

    def _labels(cands):
        return [str(cands.get_option_at_index(i).prompt) for i in range(cands.option_count)]

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            cands = pilot.app.query_one("#candidates", OptionList)
            assert any("myprovider/custom-model" in s for s in _labels(cands))

            # Pick an in-chain model → the off-chain row is no longer the assignment, so it drops.
            found = await _select_candidate(pilot, "zhipuai/glm-5")
            assert found is not None, "zhipuai/glm-5 must be a chain candidate"
            assert pilot.app.cfg["agents"]["sisyphus"]["model"] == "zhipuai/glm-5"
            assert not any("myprovider/custom-model" in s for s in _labels(cands)), (
                f"off-chain row must drop once an in-chain model is picked: {_labels(cands)}"
            )

            # Undo restores the off-chain assignment AND its ●-marked row.
            await pilot.press("u")
            await pilot.pause()
            assert pilot.app.cfg["agents"]["sisyphus"]["model"] == "myprovider/custom-model"
            assert any("●" in s and "myprovider/custom-model" in s for s in _labels(cands)), (
                f"undo must restore the off-chain ●-marked row, not just cfg: {_labels(cands)}"
            )

    asyncio.run(_run())


def test_pilot_off_chain_assignment_available_has_no_warn(pilot_config):
    """An off-chain model the *assigned* provider actually serves is surfaced before
    `+ add model…` and ●-marked, but WITHOUT the ⚠ unavailable flag — the
    `provider in providers_for(model)` branch of the synthesized row. `myprovider/custom-model`
    is off-chain (a made-up id no omo chain names) yet served by `myprovider`, so it's available."""
    cfg_path, _ = pilot_config
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write('{ "agents": { "sisyphus": { "model": "myprovider/custom-model" } } }')

    catalog = Catalog(
        available={
            "opencode": ["claude-opus-4-7", "glm-5", "gpt-5.5"],
            "myprovider": ["custom-model"],
        },
        connected=["opencode", "myprovider"],
    )

    async def _run():
        app = _build_app_with(cfg_path, catalog)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            cands = pilot.app.query_one("#candidates", OptionList)
            ids = [cands.get_option_at_index(i).id for i in range(cands.option_count)]
            assert ids[-1] == "cand:add", f"+ add model… must stay last: {ids}"
            before_add = str(cands.get_option_at_index(cands.option_count - 2).prompt)
            assert "myprovider/custom-model" in before_add, (
                f"available off-chain assignment must be the row before + add model…: {before_add!r}"
            )
            assert "●" in before_add, f"configured off-chain model must be ●-marked: {before_add!r}"
            assert "⚠" not in before_add, (
                f"a model its assigned provider serves must NOT warn unavailable: {before_add!r}"
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test 6c: the highlighted candidate is remembered per target + across refresh
# ---------------------------------------------------------------------------

def test_pilot_candidate_highlight_remembered_per_target(pilot_config):
    """Each target remembers its own highlighted candidate: navigate one target's list, switch
    to another target and back, and the cursor returns to where you left it (kept per target by
    provider/model identity, restored on re-render)."""
    cfg_path, _ = pilot_config

    def _idx_with(cands, fragment):
        for i in range(cands.option_count):
            if fragment in str(cands.get_option_at_index(i).prompt):
                return i
        return None

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            # Target A (sisyphus): put the cursor on zhipuai/glm-5 (without picking it).
            await _select_target(pilot, "agent:sisyphus")
            cands = pilot.app.query_one("#candidates", OptionList)
            a_idx = _idx_with(cands, "zhipuai/glm-5")
            assert a_idx is not None, "zhipuai/glm-5 must be a sisyphus candidate row"
            cands.focus()
            cands.highlighted = a_idx
            await pilot.pause()

            # Switch to another target (re-renders the pane → cursor would normally reset to None).
            await _select_target(pilot, "agent:hephaestus")
            await pilot.pause()
            cands = pilot.app.query_one("#candidates", OptionList)
            assert cands.highlighted is None, (
                "a target never navigated must start with no candidate cursor"
            )

            # Back to A: the cursor is restored to zhipuai/glm-5.
            await _select_target(pilot, "agent:sisyphus")
            cands = pilot.app.query_one("#candidates", OptionList)
            assert cands.highlighted is not None, "the remembered cursor must be restored"
            assert "zhipuai/glm-5" in str(cands.get_option_at_index(cands.highlighted).prompt), (
                "the cursor must return to the candidate this target last had highlighted"
            )

    asyncio.run(_run())


def test_pilot_candidate_highlight_survives_refresh(pilot_config, monkeypatch):
    """`r` (refresh) must NOT clear the candidate cursor: the highlighted row is restored by
    provider/model identity after the chain re-resolves against refreshed availability."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        # `r` runs catalog.refresh() off-thread; the empty subprocess stub would make it raise
        # CatalogUnavailable (zero lines parsed), so hand the worker a fresh equivalent catalog
        # to exercise the post-refresh re-render path.
        from omodel import app as app_mod

        fresh = Catalog(
            available={
                "opencode": ["claude-opus-4-7", "kimi-k2.5", "glm-5", "gpt-5.5"],
                "deepseek": ["deepseek-v4-pro"],
                "moonshotai-cn": ["kimi-k2.5"],
                "zhipuai": ["glm-5"],
                "openai": ["gpt-5.5"],
            },
            connected=["opencode", "deepseek", "moonshotai-cn", "zhipuai", "openai"],
        )
        monkeypatch.setattr(app_mod.catalog_mod, "refresh", lambda *a, **k: fresh)

        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            cands = pilot.app.query_one("#candidates", OptionList)
            target_idx = None
            for i in range(cands.option_count):
                if "zhipuai/glm-5" in str(cands.get_option_at_index(i).prompt):
                    target_idx = i
                    break
            assert target_idx is not None
            cands.focus()
            cands.highlighted = target_idx
            await pilot.pause()

            # Refresh and wait for the off-thread worker to finish + re-render.
            await pilot.press("r")
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

            cands = pilot.app.query_one("#candidates", OptionList)
            assert cands.highlighted is not None, "refresh must not clear the candidate cursor"
            assert "zhipuai/glm-5" in str(cands.get_option_at_index(cands.highlighted).prompt), (
                "the cursor must return to the same model after refresh"
            )

    asyncio.run(_run())


def test_pilot_candidate_highlight_ignores_stale_event(pilot_config):
    """A stale/queued OptionHighlighted — one whose option_index no longer matches the live
    cursor (e.g. a fast #targets key-repeat re-rendered the pane for another target before the
    event drained) — must NOT stamp the current target's memory. This guards _candidate_highlighted
    against the cross-target mis-record; the index mismatch is the exact condition it keys on."""
    import types as _types

    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            cands = pilot.app.query_one("#candidates", OptionList)

            live_idx = next(
                i for i in range(cands.option_count)
                if "zhipuai/glm-5" in str(cands.get_option_at_index(i).prompt)
            )
            cands.highlighted = live_idx
            await pilot.pause()
            recorded = dict(pilot.app._cand_choice)
            assert recorded.get("agent:sisyphus", "").endswith("glm-5"), "precondition: live row recorded"

            # Stale event for a DIFFERENT index than the live cursor → ignored (memory unchanged).
            other_idx = next(
                i for i in range(cands.option_count)
                if i != live_idx and (cands.get_option_at_index(i).id or "") != "cand:add"
            )
            stale = _types.SimpleNamespace(
                option_index=other_idx,
                option_id=cands.get_option_at_index(other_idx).id,
            )
            pilot.app._candidate_highlighted(stale)
            assert pilot.app._cand_choice == recorded, (
                "a stale OptionHighlighted (index != live cursor) must not overwrite memory"
            )

            # A live-matching event (index == cursor) still records normally.
            fresh = _types.SimpleNamespace(
                option_index=live_idx,
                option_id=cands.get_option_at_index(live_idx).id,
            )
            pilot.app._candidate_highlighted(fresh)
            assert "zhipuai/glm-5" in pilot.app._cand_choice.get("agent:sisyphus", ""), (
                "a live-matching highlight must be recorded"
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test 7: Hephaestus is GPT-only — no add-model row + a tip
# ---------------------------------------------------------------------------

def test_pilot_hephaestus_gpt_only(pilot_config):
    """Hephaestus (omo: GPT-exclusive) keeps the '+ add model…' row (the add modal is gated to
    GPT models) and shows a GPT-only tip in the detail pane."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            # Both agents keep the add-model row; Hephaestus additionally shows the tip.
            await _select_target(pilot, "agent:sisyphus")
            cands = pilot.app.query_one("#candidates", OptionList)
            sis_ids = [cands.get_option_at_index(i).id for i in range(cands.option_count)]
            assert "cand:add" in sis_ids, f"sisyphus must keep add-model: {sis_ids}"

            await _select_target(pilot, "agent:hephaestus")
            cands = pilot.app.query_one("#candidates", OptionList)
            hep_ids = [cands.get_option_at_index(i).id for i in range(cands.option_count)]
            assert "cand:add" in hep_ids, f"hephaestus keeps add-model (gated): {hep_ids}"

            detail = str(pilot.app.query_one("#detail", Static).content)
            assert "GPT-only" in detail, f"hephaestus detail must carry the GPT-only tip: {detail!r}"

    asyncio.run(_run())


def test_addmodal_gpt_only_gating():
    """AddModelModal(require_gpt=True) blocks a non-GPT model (enter disabled) and accepts a
    GPT one; without the flag the same non-GPT model is accepted (other agents unaffected)."""
    from omodel import suggestions as suggestions_mod
    from omodel.app import AddModelModal

    suggestions = suggestions_mod.load()
    catalog = Catalog(
        available={"openai": ["gpt-5.5", "gpt-5"], "zhipuai": ["glm-5"]},
        connected=["openai", "zhipuai"],
    )
    resolver = Resolver.build(catalog, suggestions)

    # A Textual screen creates an asyncio.Lock at construction; on Python 3.9 that needs a
    # CURRENT event loop (3.10+ binds lazily). The app only ever builds a modal inside its
    # running loop (via push_screen), so construct inside asyncio.run here too — otherwise this
    # bare construction raises "no current event loop" on 3.9.
    async def _run():
        gated = AddModelModal(resolver, suggestions, require_gpt=True)
        row, _preview, ok = gated._build_row("openai/gpt-5")
        assert ok and row is not None and row["model"] == "gpt-5", "GPT model must be accepted"
        row, preview, ok = gated._build_row("zhipuai/glm-5")
        assert not ok and row is None, "non-GPT model must be blocked"
        assert "GPT" in preview, f"block preview should explain GPT-only: {preview!r}"

        ungated = AddModelModal(resolver, suggestions, require_gpt=False)
        row, _preview, ok = ungated._build_row("zhipuai/glm-5")
        assert ok and row is not None, "non-GPT model accepted when not GPT-gated"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test 9: minimal static hint bar + `?` help overlay + ←/→ pane crossing
# ---------------------------------------------------------------------------

def test_pilot_hint_bar_minimal_and_help(pilot_config):
    """Static#hints is minimal and STATIC (`s save · q quit · ? help`, keys left) with the app
    version (`#hints-version`, right-aligned at the tail) regardless of pane or highlighted row; `?`
    opens the full-reference HelpModal (which documents the keys the bar no longer shows) and toggles
    it closed; ←/→ still cross panes."""
    cfg_path, _ = pilot_config
    EXPECTED = "s save · q quit · ? help"
    # Keys that used to live in the pane-aware bar and now belong only in the `?` overlay.
    MOVED_OUT = ("enter set", "enter add", "v variant", "x clear", "x delete",
                 "a sub", "a edit", "u undo", "⌃r redo", "→ candidates", "← targets")

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            def bar():
                return str(pilot.app.query_one("#hints", Static).content)

            # Left pane, an AGENT highlighted → the minimal bar, none of the moved-out keys.
            await _select_target(pilot, "agent:sisyphus")
            assert EXPECTED in bar(), f"hint bar must be the minimal static line: {bar()!r}"
            version = str(pilot.app.query_one("#hints-version", Static).content)
            assert f"v{omodel.__version__}" in version, (
                f"version must show at the tail (#hints-version): {version!r}"
            )
            # …and it is RIGHT-ALIGNED: keys on the left, version flush to the bar's right edge.
            bar_region = pilot.app.query_one("#hints-bar").region
            keys_region = pilot.app.query_one("#hints", Static).region
            ver_region = pilot.app.query_one("#hints-version", Static).region
            assert keys_region.x < ver_region.x, f"keys must sit left of the version: {keys_region} vs {ver_region}"
            assert ver_region.right == bar_region.right, (
                f"version must be flush to the tail (right edge): ver={ver_region} bar={bar_region}"
            )
            for gone in MOVED_OUT:
                assert gone not in bar(), f"{gone!r} must not be in the minimal bar: {bar()!r}"

            # → crosses to the candidates pane; the bar is unchanged (it no longer tracks focus).
            await pilot.press("right")
            await pilot.pause()
            cands = pilot.app.query_one("#candidates", OptionList)
            assert pilot.app.focused is cands, "→ must move focus to the candidates pane"
            await pilot.press("down")
            await pilot.pause()
            assert EXPECTED in bar(), f"bar must not change when focus/row changes: {bar()!r}"

            # ← crosses back to targets (focus crossing still works even though the bar is static).
            await pilot.press("left")
            await pilot.pause()
            assert pilot.app.focused is pilot.app.query_one("#targets", OptionList), (
                "← must move focus back to the targets pane"
            )

            # An edit populates the undo history and an undo opens a redo — neither may grow
            # the bar an `u undo` / `⌃r redo` token (it is minimal by design; `?` carries them).
            await _select_candidate(pilot, "zhipuai/glm-5")
            assert pilot.app._history.can_undo, "the edit should be undoable"
            assert EXPECTED in bar() and "u undo" not in bar(), f"bar grew after an edit: {bar()!r}"
            await pilot.press("u")
            await pilot.pause()
            assert pilot.app._history.can_redo, "undo should open a redo"
            assert EXPECTED in bar() and "⌃r redo" not in bar(), f"bar grew after an undo: {bar()!r}"

            # `?` opens the help overlay, which documents the keys the bar dropped.
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(pilot.app.screen, HelpModal), "`?` must open the HelpModal overlay"
            body = str(pilot.app.screen.query_one("#help-body-text", Static).content)
            for key in ("enter", "variant", "undo", "redo", "refresh"):
                assert key in body, f"help overlay must document {key!r}: {body!r}"

            # `?` again toggles it closed, back to the base screen.
            await pilot.press("question_mark")
            await pilot.pause()
            assert not isinstance(pilot.app.screen, HelpModal), "`?` must toggle the overlay closed"

    asyncio.run(_run())


def test_help_body_stays_light():
    """The `?` overlay is a short prompt, not a manual (DESIGN §Textual contract): it must fit an
    ordinary terminal without scrolling and without wrapping. Guards the two ways it re-bloats —
    growing past the panel's 56-cell content width, and re-listing keys that are already on screen
    (the hint bar behind it, each dialog's own hint line) or that need no telling (esc, y/n)."""
    lines = HelpModal._BODY.splitlines()
    # +2 chrome rows (title, hint) + 4 border/padding, against a 24-row terminal.
    assert len(lines) <= 22, f"help body must stay under ~22 lines, got {len(lines)}"
    widest = max(lines, key=cell_len)
    # 54, not the panel's 56 cells of content: on a short terminal the scrollbar eats 2, and a
    # 55-cell line would then wrap — costing back the height the trim just bought.
    assert cell_len(widest) <= 54, f"help line would wrap beside a scrollbar: {widest!r}"
    # The keys the overlay exists for: contextual on both panes, plus the ones you can't guess.
    for key in ("Move", "Models", "Presets", "Undo", "tab", "jk", "enter", "v", "a", "x", "r", "⌃r"):
        assert key in HelpModal._BODY, f"help must still document {key!r}"
    # …and the redundancy it deliberately drops.
    for gone in ("In dialogs", "yes / no", "esc", "  s  ", "  q  "):
        assert gone not in HelpModal._BODY, f"{gone!r} is on screen already — keep it out of help"


# ---------------------------------------------------------------------------
# Pilot test 10: ←/→ guardrail — the add-model Input keeps its cursor arrows
# ---------------------------------------------------------------------------

def test_pilot_addmodal_arrows_keep_input_cursor(pilot_config):
    """Inside the add-model modal, ← must move the Input cursor (not steal focus to the
    hidden #targets list), and the modal shows its own hint line."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            # `a` is pane-contextual: it opens the add/edit-model modal only from #candidates
            # (from #targets it would open the sub-target chooser instead).
            pilot.app.query_one("#candidates", OptionList).focus()
            await pilot.pause()
            await pilot.press("a")  # open the add-model modal
            await pilot.pause()
            # The active modal is its own screen — query it, not the base screen.
            inp = pilot.app.screen.query_one("#add-input", Input)
            assert pilot.app.focused is inp, "add-model modal must focus its Input"

            inp.value = "openai/gpt-5"
            inp.cursor_position = len(inp.value)
            await pilot.pause()
            await pilot.press("left")
            await pilot.pause()

            assert pilot.app.focused is inp, "← must not steal focus from the add-model Input"
            assert inp.cursor_position == len("openai/gpt-5") - 1, "← must move the Input cursor"

            modal_hint = str(pilot.app.screen.query_one("#add-hints", Static).content)
            assert "esc cancel" in modal_hint, f"add modal must show its own hint: {modal_hint!r}"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test 11: hjkl vim movement (aliases ↑↓←→) + add-model Input guardrail
# ---------------------------------------------------------------------------

def test_pilot_vim_movement(pilot_config):
    """`j`/`k` move the highlight within the focused list (like ↓/↑) and `l`/`h` cross to the
    candidates / targets pane (like →/←). Inside the add-model modal, h/j/k/l are typed into
    the Input as literal text — they must NOT move the highlight or steal pane focus."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            targets = pilot.app.query_one("#targets", OptionList)
            cands = pilot.app.query_one("#candidates", OptionList)
            targets.focus()
            await pilot.pause()

            # j/k move within the focused (targets) pane, like ↓/↑ (skips disabled headers).
            start = targets.highlighted
            await pilot.press("j")
            await pilot.pause()
            assert targets.highlighted is not None and targets.highlighted > start, (
                "j must move the targets highlight down"
            )
            await pilot.press("k")
            await pilot.pause()
            assert targets.highlighted == start, "k must move the targets highlight back up"

            # l crosses to candidates (like →); h crosses back (like ←).
            await pilot.press("l")
            await pilot.pause()
            assert pilot.app.focused is cands, "l must focus the candidates pane"
            await pilot.press("h")
            await pilot.pause()
            assert pilot.app.focused is targets, "h must focus the targets pane"

            # j/k also move within the candidates pane.
            cands.focus()
            await pilot.pause()
            before = cands.highlighted
            await pilot.press("j")
            await pilot.pause()
            assert cands.highlighted is not None
            if before is not None:
                assert cands.highlighted > before, "j must move the candidates highlight down"

            # Guardrail: inside the add-model modal h/j/k/l are literal text — the focused
            # Input eats printable keys before any binding, so focus stays put and they insert.
            cands.focus()
            await pilot.pause()
            await pilot.press("a")  # open the add-model modal from #candidates
            await pilot.pause()
            inp = pilot.app.screen.query_one("#add-input", Input)
            assert pilot.app.focused is inp, "add-model modal must focus its Input"
            for ch in ("h", "j", "k", "l"):
                await pilot.press(ch)
            await pilot.pause()
            assert pilot.app.focused is inp, (
                "hjkl must type into the modal Input, not move focus / highlight"
            )
            assert inp.value == "hjkl", f"hjkl must be inserted as text: {inp.value!r}"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test 12: in-session undo / redo (mis-press recovery) — DESIGN §history.py
# ---------------------------------------------------------------------------

def test_pilot_undo_redo_set_model(pilot_config):
    """`u` reverts a model pick to the prior assignment and `ctrl+r` re-applies it."""
    cfg_path, _ = pilot_config

    def _model(pilot):
        return pilot.app.cfg["agents"]["sisyphus"].get("model")

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            assert _model(pilot) == "opencode/claude-opus-4-7"  # on-disk launch value

            found = await _select_candidate(pilot, "zhipuai/glm-5")
            assert found is not None, "zhipuai/glm-5 must be a candidate row"
            assert _model(pilot) == "zhipuai/glm-5"

            await pilot.press("u")  # undo the set (focus is on #candidates → bubbles to app)
            await pilot.pause()
            assert _model(pilot) == "opencode/claude-opus-4-7", "undo must restore the prior model"

            await pilot.press("ctrl+r")  # redo
            await pilot.pause()
            assert _model(pilot) == "zhipuai/glm-5", "ctrl+r must re-apply the undone set"

    asyncio.run(_run())


def test_pilot_undo_clear(pilot_config):
    """A fat-fingered `x` (clear) is one keystroke from recovery — `u` restores the model."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            assert pilot.app.cfg["agents"]["sisyphus"].get("model") == "opencode/claude-opus-4-7"

            await pilot.press("x")  # clear
            await pilot.pause()
            assert "model" not in pilot.app.cfg["agents"]["sisyphus"], "x must clear the model"

            await pilot.press("u")  # undo the clear
            await pilot.pause()
            assert pilot.app.cfg["agents"]["sisyphus"].get("model") == "opencode/claude-opus-4-7", (
                "undo must bring the cleared model back"
            )

    asyncio.run(_run())


def test_pilot_undo_add_sub_target(pilot_config):
    """Adding a sub-target via `a` is undoable: the first `u` is the chooser's ultrawork
    shortcut (modal binding); the second `u` is app-level undo, which removes the new sub-row."""
    cfg_path, _ = pilot_config

    def _ids(targets):
        return [targets.get_option_at_index(i).id for i in range(targets.option_count)]

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            targets = pilot.app.query_one("#targets", OptionList)
            targets.highlighted = targets.get_option_index("agent:sisyphus")
            targets.focus()
            await pilot.pause()

            await pilot.press("a")  # open the sub-target chooser modal
            await pilot.pause()
            await pilot.press("u")  # modal's `u` shortcut → add ultrawork
            await pilot.pause()
            assert "agent:sisyphus.ultrawork" in _ids(targets), "add-sub must create the sub-row"

            await pilot.press("u")  # app-level undo → remove the just-added sub-target
            await pilot.pause()
            assert "agent:sisyphus.ultrawork" not in _ids(targets), (
                "undo must remove the mis-added sub-target row"
            )

    asyncio.run(_run())


def test_pilot_x_deletes_sub_target(pilot_config):
    """`x` on an ↳ ultrawork/compaction sub-target row deletes the WHOLE row (clear == delete
    there — an empty sub-object never saves, so there's no model-less placeholder to keep), and
    the parent agent regains the highlight. `u` brings the row back (the delete is an undoable
    snapshot). This is the direct way to remove a stray `a`-added sub-target."""
    cfg_path, _ = pilot_config

    def _ids(targets):
        return [targets.get_option_at_index(i).id for i in range(targets.option_count)]

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            targets = pilot.app.query_one("#targets", OptionList)
            targets.highlighted = targets.get_option_index("agent:sisyphus")
            targets.focus()
            await pilot.pause()

            # Add the ultrawork sub-target (chooser's `u` shortcut leaves it highlighted).
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("u")
            await pilot.pause()
            assert "agent:sisyphus.ultrawork" in _ids(targets)

            # `x` on the sub-row deletes it outright and lands the highlight on the parent agent.
            await pilot.press("x")
            await pilot.pause()
            assert "agent:sisyphus.ultrawork" not in _ids(targets), (
                "x on a sub-target must remove the whole row, not leave an empty placeholder"
            )
            assert "ultrawork" not in pilot.app.cfg["agents"].get("sisyphus", {}), (
                "the cfg sub-object must be gone after delete"
            )
            assert pilot.app._current_target == "agent:sisyphus", (
                "deleting a sub-target lands the highlight on its parent agent"
            )

            # Undo brings the sub-row back.
            await pilot.press("u")
            await pilot.pause()
            assert "agent:sisyphus.ultrawork" in _ids(targets), (
                "undo must restore the deleted sub-target row"
            )

    asyncio.run(_run())


def test_pilot_x_delete_sub_target_with_model_is_undoable(pilot_config):
    """Deleting a sub-target that already holds a model drops the model too (the whole sub-object
    goes, not just its `model` field); `u` restores both the row and the model it held."""
    cfg_path, _ = pilot_config

    def _ids(targets):
        return [targets.get_option_at_index(i).id for i in range(targets.option_count)]

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            targets = pilot.app.query_one("#targets", OptionList)
            targets.highlighted = targets.get_option_index("agent:sisyphus")
            targets.focus()
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("u")  # chooser → ultrawork
            await pilot.pause()

            # Assign a model into the new sub-target (inherits the parent chain).
            await _select_target(pilot, "agent:sisyphus.ultrawork")
            assert await _select_candidate(pilot, "zhipuai/glm-5") is not None
            assert pilot.app.cfg["agents"]["sisyphus"]["ultrawork"].get("model") == "zhipuai/glm-5"

            # Delete the model-bearing sub-row with `x`.
            await _select_target(pilot, "agent:sisyphus.ultrawork")
            await pilot.press("x")
            await pilot.pause()
            assert "ultrawork" not in pilot.app.cfg["agents"]["sisyphus"], (
                "x must delete the whole sub-object, model and all"
            )
            assert "agent:sisyphus.ultrawork" not in _ids(targets)

            # Undo restores the row AND the model it held.
            await pilot.press("u")
            await pilot.pause()
            assert pilot.app.cfg["agents"]["sisyphus"]["ultrawork"].get("model") == "zhipuai/glm-5", (
                "undo of a sub-target delete must restore its model assignment"
            )

    asyncio.run(_run())


async def _add_ultrawork_sub(pilot) -> None:
    """Highlight `agent:sisyphus`, focus #targets, and add its ultrawork sub-target via the
    chooser's `u` shortcut (leaving the new sub-row highlighted)."""
    targets = pilot.app.query_one("#targets", OptionList)
    targets.highlighted = targets.get_option_index("agent:sisyphus")
    targets.focus()
    await pilot.pause()
    await pilot.press("a")  # open the sub-target chooser
    await pilot.pause()
    await pilot.press("u")  # → ultrawork
    await pilot.pause()


def test_pilot_re_add_after_delete_does_not_resurrect_custom_row(pilot_config):
    """Deleting a sub-target drops its off-chain typed rows (_custom_rows) and cached resolver
    rows (_rows), so re-adding the same sub-target starts clean: a model TYPED into the first
    incarnation does not reappear as a candidate in the second. This is the case that exercises
    the `_custom_rows.pop` / `_rows.pop` in `_delete_subtarget` (the existing tests don't)."""
    cfg_path, _ = pilot_config

    def _labels(cands):
        return [str(cands.get_option_at_index(i).prompt) for i in range(cands.option_count)]

    def _ids(targets):
        return [targets.get_option_at_index(i).id for i in range(targets.option_count)]

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _add_ultrawork_sub(pilot)

            # Type a custom (off-chain) model into the ultrawork sub-target.
            inp = await _open_add_modal(pilot, "agent:sisyphus.ultrawork")
            inp.value = "openrouter/zzz-custom"  # full provider/model → used verbatim
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            cands = pilot.app.query_one("#candidates", OptionList)
            assert any("zzz-custom" in s for s in _labels(cands)), "typed custom row must be present"

            # Delete the sub-target straight from the right pane (focus is #candidates), then
            # re-add a fresh one.
            await pilot.press("x")
            await pilot.pause()
            targets = pilot.app.query_one("#targets", OptionList)
            assert "agent:sisyphus.ultrawork" not in _ids(targets)
            await _add_ultrawork_sub(pilot)

            # The fresh sub-target must NOT inherit the deleted incarnation's typed row.
            await _select_target(pilot, "agent:sisyphus.ultrawork")
            cands = pilot.app.query_one("#candidates", OptionList)
            assert not any("zzz-custom" in s for s in _labels(cands)), (
                f"re-added sub-target must not resurrect the deleted custom row: {_labels(cands)}"
            )

    asyncio.run(_run())


def test_pilot_x_delete_then_undo_restores_custom_sub_target_row(pilot_config):
    """Undo of a sub-target delete restores its off-chain typed row via the history `aux`
    snapshot, not just the cfg value: a CUSTOM model assigned to the sub-target reappears as a
    ●-marked candidate after `u` (it isn't in the chain, so ONLY a restored _custom_rows can
    render it — a plain cfg restore wouldn't)."""
    cfg_path, _ = pilot_config

    def _labels(cands):
        return [str(cands.get_option_at_index(i).prompt) for i in range(cands.option_count)]

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _add_ultrawork_sub(pilot)

            inp = await _open_add_modal(pilot, "agent:sisyphus.ultrawork")
            inp.value = "openrouter/zzz-custom"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            node = pilot.app.cfg["agents"]["sisyphus"]["ultrawork"]
            assert node.get("model") == "openrouter/zzz-custom"

            await pilot.press("x")  # delete the (custom-model-bearing) sub-target
            await pilot.pause()
            assert "ultrawork" not in pilot.app.cfg["agents"]["sisyphus"]

            await pilot.press("u")  # undo the delete → row + custom model restored via aux
            await pilot.pause()
            assert pilot.app.cfg["agents"]["sisyphus"]["ultrawork"].get("model") == "openrouter/zzz-custom", (
                "undo must restore the sub-target's custom model assignment"
            )
            await _select_target(pilot, "agent:sisyphus.ultrawork")
            cands = pilot.app.query_one("#candidates", OptionList)
            assert any("●" in s and "zzz-custom" in s for s in _labels(cands)), (
                f"undo must restore the custom ●-row via aux, not just cfg: {_labels(cands)}"
            )

    asyncio.run(_run())


def test_pilot_save_after_delete_drops_sub_target_from_disk(tmp_path):
    """End-to-end: a sub-target deleted with `x` is gone from the SAVED file (render rewrites the
    agents span clean from cfg), while config OUTSIDE agents/categories is preserved verbatim."""
    cfg_path = str(tmp_path / "oh-my-openagent.jsonc")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(
            "{\n"
            '  "agents": {\n'
            '    "sisyphus": {\n'
            '      "model": "opencode/claude-opus-4-7",\n'
            '      "ultrawork": {"model": "zhipuai/glm-5"}\n'
            "    }\n"
            "  },\n"
            '  "categories": {},\n'
            '  "team_mode": true\n'
            "}\n"
        )

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            targets = pilot.app.query_one("#targets", OptionList)
            ids = [targets.get_option_at_index(i).id for i in range(targets.option_count)]
            assert "agent:sisyphus.ultrawork" in ids, "pre-existing ultrawork must show as a row"

            await _select_target(pilot, "agent:sisyphus.ultrawork")
            await pilot.press("x")  # delete the sub-target
            await pilot.pause()
            assert "ultrawork" not in pilot.app.cfg["agents"]["sisyphus"]

            await _save_and_confirm(pilot)
            with open(cfg_path, encoding="utf-8") as fh:
                saved = fh.read()
            assert "ultrawork" not in saved, f"deleted sub-target must not persist to disk: {saved}"
            assert '"model": "opencode/claude-opus-4-7"' in saved, "the base model must remain"
            assert "team_mode" in saved, "config outside agents/categories must be preserved"

    asyncio.run(_run())


def test_pilot_undo_restores_clean_state(pilot_config):
    """Dirtiness is computed (serialize vs on-disk), so undoing back to the launch state reads
    as clean (quit won't prompt) and redoing re-dirties."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            assert pilot.app._is_dirty() is False
            await _select_target(pilot, "agent:sisyphus")
            await _select_candidate(pilot, "zhipuai/glm-5")
            assert pilot.app._is_dirty() is True, "a pick must mark the config dirty"

            await pilot.press("u")
            await pilot.pause()
            assert pilot.app._is_dirty() is False, "undo back to the on-disk state must be clean"

            await pilot.press("ctrl+r")
            await pilot.pause()
            assert pilot.app._is_dirty() is True, "redo must re-dirty"

    asyncio.run(_run())


def test_pilot_undo_noop_when_empty(pilot_config):
    """Pressing `u` with an empty history is a harmless no-op (notifies, never crashes)."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            before = pilot.app.cfg["agents"]["sisyphus"].get("model")
            await pilot.press("u")
            await pilot.pause()
            assert pilot.app.cfg["agents"]["sisyphus"].get("model") == before
            assert pilot.app._is_dirty() is False

    asyncio.run(_run())


def test_pilot_undo_gated_under_modal(pilot_config):
    """check_action disables app-level undo/redo while a modal is open — the modal owns its
    keys (e.g. AddSubModal binds `u` to pick ultrawork), so app `u`/`ctrl+r` must not leak in."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            assert pilot.app.check_action("undo", None) is True  # base screen → enabled

            await pilot.press("right")  # focus candidates
            await pilot.pause()
            await pilot.press("a")  # open the add-model modal
            await pilot.pause()
            assert len(pilot.app.screen_stack) > 1, "`a` must open a modal"
            assert pilot.app.check_action("undo", None) is False
            assert pilot.app.check_action("redo", None) is False

    asyncio.run(_run())


def test_pilot_undo_survives_save(pilot_config):
    """The undo history is preserved across a save: after saving a pick, `u` still reverts it,
    and the config goes dirty again (disk now differs from the reverted in-memory state, which
    the user could re-save). Proves dirtiness is computed against disk, not cleared by undo."""
    import json5

    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            await _select_candidate(pilot, "zhipuai/glm-5")
            await _save_and_confirm(pilot)
            assert pilot.app._is_dirty() is False, "a save must leave the config clean"

            with open(cfg_path, encoding="utf-8") as f:
                assert json5.load(f)["agents"]["sisyphus"]["model"] == "zhipuai/glm-5"

            await pilot.press("u")  # undo the just-saved edit
            await pilot.pause()
            assert pilot.app.cfg["agents"]["sisyphus"]["model"] == "opencode/claude-opus-4-7"
            assert pilot.app._is_dirty() is True, "undo after save must re-dirty (disk differs)"

    asyncio.run(_run())


def test_pilot_undo_redo_moves_custom_added_row_in_lockstep(pilot_config):
    """A model typed in the add-model modal is an off-chain row stored in _custom_rows, which is
    snapshotted into the undo history (aux) and so moves in lockstep with undo/redo: after add it
    is a `●`-marked candidate row; undo drops BOTH the assignment AND the row (not just the cfg
    value); redo brings the assignment AND the row back."""
    cfg_path, _ = pilot_config

    def _labels(cands):
        return [str(cands.get_option_at_index(i).prompt) for i in range(cands.option_count)]

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            cands = pilot.app.query_one("#candidates", OptionList)
            cands.focus()
            await pilot.pause()

            await pilot.press("a")  # open the add-model modal
            await pilot.pause()
            inp = pilot.app.screen.query_one("#add-input", Input)
            inp.value = "openrouter/zzz-custom"  # full provider/model → used verbatim
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert pilot.app.cfg["agents"]["sisyphus"]["model"] == "openrouter/zzz-custom"
            labels = _labels(cands)
            assert any("●" in s and "openrouter/zzz-custom" in s for s in labels), (
                f"typed model must be a ●-marked row: {labels}"
            )

            await pilot.press("u")  # undo the add → assignment AND the typed row revert
            await pilot.pause()
            assert pilot.app.cfg["agents"]["sisyphus"]["model"] == "opencode/claude-opus-4-7"
            labels = _labels(cands)
            assert not any("zzz-custom" in s for s in labels), (
                f"undo of an add-model must drop the typed row, not just the assignment: {labels}"
            )

            await pilot.press("ctrl+r")  # redo → typed model + its row return
            await pilot.pause()
            assert pilot.app.cfg["agents"]["sisyphus"]["model"] == "openrouter/zzz-custom"
            labels = _labels(cands)
            assert any("●" in s and "openrouter/zzz-custom" in s for s in labels), (
                f"redo must restore the typed model's ●-marked row, not just cfg: {labels}"
            )

    asyncio.run(_run())


def test_pilot_undo_sub_target_under_non_first_agent(pilot_config):
    """Undoing an add-sub on a NON-first agent lands the cursor on its parent agent (the
    vanished-sub → parent fallback), exercising the index path the sisyphus(index-0) test
    can't. After undo the sub-row is gone and #targets highlights the parent."""
    cfg_path, _ = pilot_config

    def _ids(targets):
        return [targets.get_option_at_index(i).id for i in range(targets.option_count)]

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            second = list(pilot.app.suggestions.agents.keys())[1]  # not index 0
            target = f"agent:{second}"
            targets = pilot.app.query_one("#targets", OptionList)
            targets.highlighted = targets.get_option_index(target)
            targets.focus()
            await pilot.pause()

            # `second` is non-Sisyphus → single-kind, so `a` adds compaction directly (no chooser);
            # this test exercises the sub-row index/undo path, which is kind-agnostic.
            await pilot.press("a")
            await pilot.pause()
            assert f"{target}.compaction" in _ids(targets)

            await pilot.press("u")  # app-level undo → remove the sub-target
            await pilot.pause()
            assert f"{target}.compaction" not in _ids(targets)
            assert pilot.app._current_target == target, "undo must fall back to the parent agent"
            hi = targets.highlighted
            assert hi is not None and targets.get_option_at_index(hi).id == target, (
                "the targets cursor must land on the parent agent"
            )

    asyncio.run(_run())


def test_pilot_confirm_modal_diff_scrolls(pilot_config):
    """Regression: a save diff taller than the modal cap (#confirm-body max-height: 20) must be
    fully scrollable, not clipped at the top. The body is a VerticalScroll driven by the modal's
    own bindings (↑↓/jk, PageUp/PageDown, Home/End), so it scrolls while the Yes button keeps
    focus — leaving Enter to confirm as before."""
    from textual.containers import VerticalScroll

    from omodel.app import ConfirmModal

    cfg_path, _ = pilot_config
    long_body = "\n".join(f"+ added line {i:02d}" for i in range(40))  # 40 rows > 20-row cap

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            result = {}
            app.push_screen(
                ConfirmModal("Save changes?", long_body),
                lambda v: result.__setitem__("v", v),
            )
            await pilot.pause()

            body = app.screen.query_one("#confirm-body", VerticalScroll)
            assert body.max_scroll_y > 0, "long diff must overflow the cap (i.e. be scrollable)"
            assert not body.focusable, "scroller stays non-focusable so the Yes button keeps focus"
            assert app.focused is not None and app.focused.id == "confirm-yes", (
                "default focus is the Yes button so Enter still confirms"
            )

            await pilot.press("j")  # one line down (vim)
            await pilot.pause()
            assert round(body.scroll_y) >= 1, "j must scroll the body down"
            await pilot.press("end")  # jump to bottom
            await pilot.pause()
            assert round(body.scroll_y) == body.max_scroll_y, "End reaches the last diff line"
            await pilot.press("home")  # back to top
            await pilot.pause()
            assert round(body.scroll_y) == 0, "Home returns to the first diff line"

            await pilot.press("enter")  # focused Yes button still confirms
            await pilot.pause()
            assert result.get("v") is True, "Enter must still confirm the modal"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test 13: add-model modal — fuzzy model picker + inline variant step
# ---------------------------------------------------------------------------


async def _open_add_modal(pilot, target: str = "agent:sisyphus"):
    """Select `target`, focus #candidates, and press `a` to open the add-model modal (from
    #candidates `a` is the add/edit-model modal, not the sub-target chooser). Returns the
    modal's #add-input."""
    await _select_target(pilot, target)
    pilot.app.query_one("#candidates", OptionList).focus()
    await pilot.pause()
    await pilot.press("a")
    await pilot.pause()
    return pilot.app.screen.query_one("#add-input", Input)


def _add_candidate_labels(pilot):
    cands = pilot.app.screen.query_one("#add-candidates", OptionList)
    return [str(cands.get_option_at_index(i).prompt) for i in range(cands.option_count)]


def test_pilot_addmodal_fuzzy_filter(pilot_config):
    """Typing fuzzy-filters #add-candidates to matching provider/model pairs from
    catalog.available: 'glm' surfaces zhipuai/glm-5 and opencode/glm-5, excluding deepseek-v4-pro."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "glm"
            await pilot.pause()
            labels = _add_candidate_labels(pilot)
            assert any("zhipuai/glm-5" in s for s in labels), labels
            assert any("opencode/glm-5" in s for s in labels), labels
            assert not any("deepseek-v4-pro" in s for s in labels), labels

    asyncio.run(_run())


def test_pilot_addmodal_empty_query_shows_no_list(pilot_config):
    """Type-to-search: opening the modal (empty input) renders NO candidate list — the browse dump
    is intentionally not built, so open stays instant. The list is hidden, nothing is staged, and
    Matcher('') is never constructed (it raises). The list appears only once you type."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            assert inp.value == ""
            scr = pilot.app.screen
            cands = scr.query_one("#add-candidates", OptionList)
            assert cands.option_count == 0, "empty query must render no rows (type-to-search)"
            assert not cands.display, "the candidate list stays hidden until you type"
            assert scr._staged is None, "nothing is staged on open"

            # Typing surfaces the fuzzy list.
            inp.value = "glm"
            await pilot.pause()
            assert cands.option_count > 0 and cands.display, "typing surfaces matches"

    asyncio.run(_run())


def test_pilot_addmodal_tab_fills_input(pilot_config):
    """Tab fills the highlighted provider/model pair into #add-input (cursor to end)."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "glm"
            await pilot.pause()
            cands = pilot.app.screen.query_one("#add-candidates", OptionList)
            assert cands.highlighted is not None, "a fuzzy hit must be highlighted"
            highlighted = str(cands.get_option_at_index(cands.highlighted).prompt)

            await pilot.press("tab")
            await pilot.pause()
            assert inp.value == highlighted, (
                f"tab must fill the highlighted pair: {inp.value!r} vs {highlighted!r}"
            )
            assert inp.value == "zhipuai/glm-5", (
                "dedicated-first puts zhipuai/glm-5 at the top, so tab fills it"
            )

    asyncio.run(_run())


def test_pilot_addmodal_ctrl_p_n_navigate_list(pilot_config):
    """Ctrl-P / Ctrl-N navigate the fuzzy list like ↑/↓ (emacs-style). Ctrl-P must NOT open the
    App command palette while the modal is open (OModelApp.check_action suppresses that priority
    binding so the key drives the list instead). This modal's own hint line is where they're
    advertised — the `?` overlay documents base-screen keys only, so if the hint drops them the
    key becomes undiscoverable."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            hints = str(pilot.app.screen.query_one("#add-hints", Static).content)
            assert "⌃p" in hints and "⌃n" in hints, f"model-phase hint must show ⌃p/⌃n: {hints!r}"
            inp.value = "glm"  # ≥2 matches: zhipuai/glm-5 (row 0, dedicated), opencode/glm-5 (row 1)
            await pilot.pause()
            cands = pilot.app.screen.query_one("#add-candidates", OptionList)
            assert cands.option_count >= 2 and cands.highlighted == 0

            await pilot.press("ctrl+n")
            await pilot.pause()
            assert cands.highlighted == 1, "Ctrl-N moves the highlight down"
            assert len(pilot.app.screen_stack) == 2, "Ctrl-N must not open another screen"
            staged = pilot.app.screen._staged
            assert (staged["provider"], staged["model"]) == ("opencode", "glm-5"), (
                "Ctrl-N restages the newly-highlighted row"
            )

            await pilot.press("ctrl+p")
            await pilot.pause()
            assert cands.highlighted == 0, "Ctrl-P moves the highlight up"
            assert len(pilot.app.screen_stack) == 2, (
                "Ctrl-P must navigate the list, NOT open the command palette"
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# `x` on a candidate row you added — row-scoped delete
# ---------------------------------------------------------------------------

def _cand_labels(pilot):
    cands = pilot.app.query_one("#candidates", OptionList)
    return [str(cands.get_option_at_index(i).prompt) for i in range(cands.option_count)]


async def _highlight_cand(pilot, fragment: str) -> int:
    """Park the #candidates cursor on the first model row whose label contains `fragment`."""
    cands = pilot.app.query_one("#candidates", OptionList)
    for i in range(cands.option_count):
        opt = cands.get_option_at_index(i)
        if (opt.id or "").startswith("cand:") and opt.id != "cand:add" and fragment in str(opt.prompt):
            cands.highlighted = i
            await pilot.pause()
            return i
    pytest.fail(f"no #candidates row matching {fragment!r}: {_cand_labels(pilot)}")


async def _add_offchain_model(pilot, typed: str = "deepseek") -> None:
    """Add an off-chain model through the add-model modal, accepting the top fuzzy match.
    deepseek has no seeded `--verbose`, so there's no variant phase — one enter commits it."""
    inp = await _open_add_modal(pilot)
    inp.value = typed
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


def _sisyphus_model(pilot):
    return pilot.app.cfg["agents"]["sisyphus"].get("model")


def test_pilot_x_removes_the_row_you_added(pilot_config):
    """The reported bug. Add a model, pick a DIFFERENT one, then press `x` on the row you added:
    it must delete that row and leave the assignment alone.

    `x` was target-scoped — `action_clear` read only `_current_target` and never the cursor — so
    it cleared whatever was assigned (a model you weren't pointing at) while the added row stayed
    put, since nothing ever removed a single `_custom_rows` entry."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _add_offchain_model(pilot)
            assert _sisyphus_model(pilot) == "deepseek/deepseek-v4-pro", "the add must set it"

            # …then pick a chain model, so the added row is no longer the assignment.
            await _highlight_cand(pilot, "zhipuai/glm-5")
            await pilot.press("enter")
            await pilot.pause()
            assert _sisyphus_model(pilot) == "zhipuai/glm-5"
            assert any("deepseek" in lbl for lbl in _cand_labels(pilot)), (
                "a typed row stays pickable after you try something else"
            )

            # `x` on the added row: that row goes, the assignment does NOT.
            await _highlight_cand(pilot, "deepseek")
            await pilot.press("x")
            await pilot.pause()
            assert not any("deepseek" in lbl for lbl in _cand_labels(pilot)), (
                f"`x` must delete the added row: {_cand_labels(pilot)}"
            )
            assert _sisyphus_model(pilot) == "zhipuai/glm-5", (
                "`x` on a row you're pointing at must not clear a model you aren't"
            )
            # The cursor re-aims at what's assigned rather than vanishing with the deleted row.
            cands = pilot.app.query_one("#candidates", OptionList)
            assert cands.highlighted is not None, "the cursor must land somewhere after the delete"
            assert "glm-5" in str(cands.get_option_at_index(cands.highlighted).prompt), (
                "the cursor should land on the assigned (●) row"
            )

    asyncio.run(_run())


def test_pilot_x_on_added_row_that_is_assigned_clears_too(pilot_config):
    """`x` on an added row that IS the assignment takes both — clear == delete, as on a sub-target
    row: a model left set on a target whose row just disappeared is the state this pane must not
    show. That branch touches cfg, so `u` puts the model AND the row back (the `_custom_rows`
    snapshot rides in the history entry's aux)."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _add_offchain_model(pilot)
            await _highlight_cand(pilot, "deepseek")
            await pilot.press("x")
            await pilot.pause()
            assert _sisyphus_model(pilot) is None, "the assignment goes with its row"
            assert not any("deepseek" in lbl for lbl in _cand_labels(pilot))

            await pilot.press("u")
            await pilot.pause()
            assert _sisyphus_model(pilot) == "deepseek/deepseek-v4-pro", "undo restores the model"
            assert any("deepseek" in lbl for lbl in _cand_labels(pilot)), (
                "undo restores the row in lockstep (aux carries _custom_rows)"
            )

    asyncio.run(_run())


def test_pilot_x_on_chain_row_still_clears(pilot_config):
    """Unchanged for rows you didn't add: `x` on a chain row clears the target's assignment (the
    documented meaning) and leaves the added row alone — omo's chain isn't yours to delete."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _add_offchain_model(pilot)
            await _highlight_cand(pilot, "zhipuai/glm-5")
            await pilot.press("enter")
            await pilot.pause()

            await _highlight_cand(pilot, "opencode/kimi")  # a chain row, not the assignment
            await pilot.press("x")
            await pilot.pause()
            assert _sisyphus_model(pilot) is None, "`x` on a chain row still clears"
            assert any("deepseek" in lbl for lbl in _cand_labels(pilot)), (
                "…and must not take the added row with it"
            )

    asyncio.run(_run())


def test_pilot_x_on_chain_row_shadowing_an_added_one_still_clears(pilot_config):
    """Adding a model the chain ALREADY offers leaves a `_custom_rows` entry that `_build_rows`
    dedupes away behind the chain row (History.push's docstring calls out that case). `x` on that
    single visible row is a chain-row press and must still clear — hence the object-identity match
    in `_remove_custom_row`; matching on `provider/model` would silently eat the hidden entry and
    do nothing the user can see."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _add_offchain_model(pilot, "zhipuai/glm-5")  # already a chain row
            assert _sisyphus_model(pilot) == "zhipuai/glm-5"
            assert pilot.app._custom_rows.get("agent:sisyphus"), "the add still records the row"
            assert sum("zhipuai/glm-5" in lbl for lbl in _cand_labels(pilot)) == 1, (
                "it must dedupe to ONE row, not render twice"
            )

            # Move the assignment elsewhere, so clearing is observably different from "the row
            # I'm on happens to be what's set" — this is what pins the identity match.
            await _highlight_cand(pilot, "openai/gpt-5.5")
            await pilot.press("enter")
            await pilot.pause()
            assert _sisyphus_model(pilot) == "openai/gpt-5.5"

            await _highlight_cand(pilot, "zhipuai/glm-5")
            await pilot.press("x")
            await pilot.pause()
            assert _sisyphus_model(pilot) is None, (
                "`x` on the chain row must still clear the target, not quietly drop the hidden "
                "_custom_rows entry that shares its id"
            )
            assert any("zhipuai/glm-5" in lbl for lbl in _cand_labels(pilot)), (
                "…and the chain row itself stays in the list"
            )

    asyncio.run(_run())


def test_pilot_x_from_targets_pane_ignores_the_candidate_cursor(pilot_config):
    """The row-scoped delete is gated on #candidates having focus. With the candidate cursor
    parked on an added row but focus back on #targets, `x` means clear-this-target again — the
    left pane must never reach across and eat a row."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _add_offchain_model(pilot)
            await _highlight_cand(pilot, "deepseek")
            await pilot.press("left")  # focus #targets, cursor still on the added row
            await pilot.pause()
            assert pilot.app.focused is pilot.app.query_one("#targets", OptionList)

            await pilot.press("x")
            await pilot.pause()
            assert _sisyphus_model(pilot) is None, "`x` on #targets still clears the target"
            assert any("deepseek" in lbl for lbl in _cand_labels(pilot)), (
                "…and leaves the candidate row the cursor happened to be on"
            )

    asyncio.run(_run())


def test_pilot_addmodal_select_enters_variant_phase(pilot_config):
    """Choosing a model opencode reports variants for enters the variant phase: #add-variants is
    visible + focused listing opencode's variant keys + (none); picking one sets the assignment's
    variant alongside the resolved provider/model. Variants come from cached `--verbose`
    (catalog.variants_for), seeded here for openai/gpt-5.5."""
    cfg_path, _ = pilot_config

    async def _run():
        _seed_verbose("openai", {"gpt-5.5": ["low", "medium", "high"]})
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "openai/gpt-5.5"
            await pilot.pause()
            await pilot.press("enter")  # choose openai/gpt-5.5 → variant phase
            await pilot.pause()

            variants = pilot.app.screen.query_one("#add-variants", OptionList)
            assert variants.display, "variant list must be visible in the variant phase"
            assert pilot.app.focused is variants, "variant list must be focused"
            vids = [variants.get_option_at_index(i).id for i in range(variants.option_count)]
            assert vids == ["var:low", "var:medium", "var:high", "var:__none__"], vids

            variants.highlighted = vids.index("var:high")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert len(pilot.app.screen_stack) == 1, "picking a variant must close the modal"
            node = pilot.app.cfg["agents"]["sisyphus"]
            assert node["model"] == "openai/gpt-5.5", node
            assert node.get("variant") == "high", node

    asyncio.run(_run())


def test_pilot_addmodal_kimi_no_variant_phase(pilot_config):
    """Regression for the reported bug: kimi has NO variants, so adding it must skip the variant
    phase — even though the old heuristic family registry wrongly listed [low,medium,high] for
    kimi. opencode's cached `--verbose` reports kimi-k2.5 with an EMPTY variants object on every
    serving provider, so catalog.variants_for returns [] and a single Enter adds it with no
    variant key (no #add-variants phase)."""
    cfg_path, _ = pilot_config

    async def _run():
        # opencode's real-world shape: kimi reports an empty variants object everywhere.
        _seed_verbose("moonshotai-cn", {"kimi-k2.5": []})
        _seed_verbose("opencode", {"kimi-k2.5": []})
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "moonshotai-cn/kimi-k2.5"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 1, "kimi has no variants → no variant phase"
            node = pilot.app.cfg["agents"]["sisyphus"]
            assert node["model"] == "moonshotai-cn/kimi-k2.5", node
            assert "variant" not in node, f"kimi must be added with no variant: {node}"

    asyncio.run(_run())


def test_pilot_addmodal_drops_none_variant(pilot_config):
    """A "none" variant opencode lists is dropped as a duplicate of the synthetic "(none)" clear
    row — the add-model variant list must show the REAL variants + var:__none__ only, never a
    var:none. Picking (none) then writes NO variant key ("none" ≡ (none) ≡ unset)."""
    cfg_path, _ = pilot_config

    async def _run():
        # opencode reports "none" alongside the real variants (a GPT/reasoning model shape).
        _seed_verbose("openai", {"gpt-5.5": ["none", "low", "medium", "high"]})
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "openai/gpt-5.5"
            await pilot.pause()
            await pilot.press("enter")  # choose openai/gpt-5.5 → variant phase
            await pilot.pause()

            variants = pilot.app.screen.query_one("#add-variants", OptionList)
            vids = [variants.get_option_at_index(i).id for i in range(variants.option_count)]
            assert vids == ["var:low", "var:medium", "var:high", "var:__none__"], vids
            assert "var:none" not in vids, "the literal 'none' must be dropped, not offered"

            variants.highlighted = vids.index("var:__none__")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            node = pilot.app.cfg["agents"]["sisyphus"]
            assert node["model"] == "openai/gpt-5.5", node
            assert "variant" not in node, f"(none) must remove the variant key: {node}"

    asyncio.run(_run())


def test_pilot_addmodal_only_none_variant_skips_phase(pilot_config):
    """A model whose ONLY opencode-reported variant is "none" has nothing real to pick once the
    duplicate is dropped — so the add-model flow skips the variant phase entirely and adds it
    immediately with no variant key (same path as kimi/glm-5)."""
    cfg_path, _ = pilot_config

    async def _run():
        _seed_verbose("openai", {"gpt-5.5": ["none"]})
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "openai/gpt-5.5"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 1, "a 'none'-only model must skip the variant phase"
            node = pilot.app.cfg["agents"]["sisyphus"]
            assert node["model"] == "openai/gpt-5.5", node
            assert "variant" not in node, f"must be added with no variant: {node}"

    asyncio.run(_run())


def test_pilot_vkey_drops_none_variant(pilot_config):
    """`v` on a candidate opens VariantModal listing the variants opencode reports for that
    (provider, model) — catalog.variants_for (cached `--verbose`), seeded here — plus the (none)
    clear row. A "none" opencode lists is likewise dropped as a duplicate of that synthetic row,
    so the list is the REAL variants + var:__none__ only, never a var:none."""
    cfg_path, _ = pilot_config

    async def _run():
        _seed_verbose("openai", {"gpt-5.5": ["none", "low", "high"]})
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            oid = await _highlight_candidate(pilot, "openai/gpt-5.5")
            assert oid is not None, "openai/gpt-5.5 must be a sisyphus candidate"
            await pilot.press("v")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 2, "v must open the VariantModal"
            vlist = pilot.app.screen.query_one("#variant-list", OptionList)
            vids = [vlist.get_option_at_index(i).id for i in range(vlist.option_count)]
            assert vids == ["var:low", "var:high", "var:__none__"], vids
            assert "var:none" not in vids, "the literal 'none' must be dropped, not offered"

    asyncio.run(_run())


def test_pilot_repick_offchain_clears_stale_none_variant(tmp_path):
    """A pre-existing on-disk `variant: "none"` (e.g. hand-edited, or written by an older omodel)
    rides along on the synthesized off-chain candidate row. Re-picking that row with Enter must
    DROP the stale key ("none" ≡ (none) ≡ no variant) rather than round-trip `variant: "none"`."""
    cfg_path = str(tmp_path / "oh-my-openagent.jsonc")
    with open(cfg_path, "w", encoding="utf-8") as f:
        # deepseek/deepseek-v4-pro is available but OFF sisyphus's chain → it surfaces as the
        # synthesized current-assignment row, which carries the cfg variant verbatim.
        f.write(
            '{\n'
            '  "agents": {\n'
            '    "sisyphus": {"model": "deepseek/deepseek-v4-pro", "variant": "none"}\n'
            '  },\n'
            '  "categories": {}\n'
            '}\n'
        )

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            # sanity: the stale "none" is what's loaded before we touch it
            assert app.cfg["agents"]["sisyphus"].get("variant") == "none"
            oid = await _highlight_candidate(pilot, "deepseek/deepseek-v4-pro")
            assert oid is not None, "the off-chain current assignment must be a pickable row"
            await pilot.press("enter")  # re-pick the same model → _stage_row normalizes
            await pilot.pause()
            node = app.cfg["agents"]["sisyphus"]
            assert node["model"] == "deepseek/deepseek-v4-pro", node
            assert "variant" not in node, f"re-picking must clear the stale 'none': {node}"

    asyncio.run(_run())


def test_pilot_vkey_no_variants_bells(pilot_config):
    """`v` on a model opencode reports no variants for (kimi) opens NO modal — the old
    `known_variants` 'always offer something' fallback is gone; variant validity is opencode's,
    so with an empty variants object everywhere `v` just bells (screen stack unchanged)."""
    cfg_path, _ = pilot_config

    async def _run():
        _seed_verbose("moonshotai-cn", {"kimi-k2.5": []})
        _seed_verbose("opencode", {"kimi-k2.5": []})
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            oid = await _highlight_candidate(pilot, "moonshotai-cn/kimi-k2.5")
            assert oid is not None, "moonshotai-cn/kimi-k2.5 must be a sisyphus candidate"
            bell_calls = []
            pilot.app.bell = lambda: bell_calls.append(1)
            await pilot.press("v")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 1, "kimi has no variants → v opens no modal"
            assert bell_calls, "v on a no-variant model must bell"

    asyncio.run(_run())


def test_pilot_vkey_on_assigned_row_stages_variant(pilot_config):
    """`v` on the currently-assigned row stages the chosen variant onto that assignment (the
    restage branch of action_variant._apply)."""
    cfg_path, _ = pilot_config
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write('{ "agents": { "sisyphus": { "model": "openai/gpt-5.5" } } }')

    async def _run():
        _seed_verbose("openai", {"gpt-5.5": ["low", "medium", "high"]})
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            assert await _highlight_candidate(pilot, "openai/gpt-5.5") is not None
            await pilot.press("v")
            await pilot.pause()
            vlist = pilot.app.screen.query_one("#variant-list", OptionList)
            vids = [vlist.get_option_at_index(i).id for i in range(vlist.option_count)]
            vlist.highlighted = vids.index("var:high")
            await pilot.press("enter")
            await pilot.pause()
            node = pilot.app.cfg["agents"]["sisyphus"]
            assert node["model"] == "openai/gpt-5.5", node
            assert node.get("variant") == "high", f"variant must be staged onto the assignment: {node}"

    asyncio.run(_run())


async def _vkey_pick(pilot, variant: str) -> None:
    """Press `v` on the highlighted candidate and choose `variant` from the modal."""
    await pilot.press("v")
    await pilot.pause()
    vlist = pilot.app.screen.query_one("#variant-list", OptionList)
    vids = [vlist.get_option_at_index(i).id for i in range(vlist.option_count)]
    vlist.highlighted = vids.index(f"var:{variant}")
    await pilot.press("enter")
    await pilot.pause()


def _cand_labels(pilot) -> list:
    c = pilot.app.query_one("#candidates", OptionList)
    return [str(c.get_option_at_index(i).prompt) for i in range(c.option_count)]


async def _land_detail_fetch(pilot, target: str, provider: str, bare: str) -> None:
    """Drive one detail fetch to completion, as the debounce timer's callback would.

    Clears `_detail_cache` first and asserts the worker really ran: `_fetch_detail` returns
    IMMEDIATELY when the key is already cached (the app's own launch fetch usually cached it),
    and a silent no-op would make "the pending pick survived" pass for the wrong reason — there
    would have been no rebuild to survive."""
    pilot.app._detail_cache.clear()
    pilot.app._detail_fetching = False
    calls = {"n": 0}
    inner = pilot.app.catalog.detail
    pilot.app.catalog.detail = lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), inner(*a, **k))[1]
    pilot.app._fetch_detail(target, provider, bare)
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()
    pilot.app.catalog.detail = inner
    assert calls["n"] == 1, f"the fetch must actually have run, else the test proves nothing: {calls}"


def test_pilot_vkey_pick_on_nonassigned_row_survives_a_landing_fetch(pilot_config):
    """A `v` pick on a row that is NOT the assignment reaches cfg only on Enter, so until then it
    is pending state — and it must survive a rebuild of `_rows`, which is a CACHE.

    It used to live as an in-place mutation of the cached row dict, so anything that rebuilt the
    rows reverted it to omo's suggested variant. Harmless while only cfg mutations rebuilt them;
    a regression once a landing background detail fetch did too, since that fires on its own
    schedule: the pick vanished with no user action, and a later Enter wrote omo's variant
    instead of the chosen one. Now held in `_pending_variants` and re-applied by `_build_rows`."""
    cfg_path, _ = pilot_config
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write('{ "agents": { "sisyphus": { "model": "opencode/gpt-5.5" } } }')

    async def _run():
        _seed_verbose("openai", {"gpt-5.5": ["low", "medium", "high"]})
        app = _build_app(cfg_path)
        app.catalog.detail = lambda *a, **k: {
            "context": 1, "cost": None, "reasoning": False, "image": False
        }
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            # openai/gpt-5.5 — same model as the assignment but a DIFFERENT provider, so it is
            # not the assignment and `v` stages nothing to cfg.
            assert await _highlight_candidate(pilot, "openai/gpt-5.5") is not None
            await _vkey_pick(pilot, "high")
            assert any("openai/gpt-5.5 (high)" in s for s in _cand_labels(pilot)), _cand_labels(pilot)

            # A detail fetch lands, rebuilding the rows. The pick must still be there.
            await _land_detail_fetch(pilot, "agent:sisyphus", "opencode", "gpt-5.5")
            after = _cand_labels(pilot)
            assert any("openai/gpt-5.5 (high)" in s for s in after), (
                f"a landing fetch must not revert the pending `v` pick: {after}"
            )

            # …and Enter still writes what was picked, not omo's suggestion.
            await _select_candidate(pilot, "openai/gpt-5.5")
            node = pilot.app.cfg["agents"]["sisyphus"]
            assert node == {"model": "openai/gpt-5.5", "variant": "high"}, node

    asyncio.run(_run())


def test_pilot_pending_variant_is_dropped_once_it_reaches_cfg(pilot_config):
    """`_stage_row` must drop the pending pick: it is in cfg now, so an override left behind
    would outrank cfg on the next rebuild.

    Sequence: `v(high)` on a NON-assigned row → Enter (it becomes the assignment, cfg gets
    `high`) → `v` it again, now that it IS the assignment, and clear the variant. Without the
    pop, cfg is correctly variantless but the `●` row still renders `(high)` off the stale
    override — and Enter writes `high` straight back."""
    cfg_path, _ = pilot_config
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write('{ "agents": { "sisyphus": { "model": "opencode/gpt-5.5" } } }')

    async def _run():
        _seed_verbose("openai", {"gpt-5.5": ["low", "medium", "high"]})
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            assert await _highlight_candidate(pilot, "openai/gpt-5.5") is not None
            await _vkey_pick(pilot, "high")                 # pending: not the assignment yet
            await _select_candidate(pilot, "openai/gpt-5.5")  # Enter → cfg, pending must clear
            assert pilot.app.cfg["agents"]["sisyphus"] == {
                "model": "openai/gpt-5.5", "variant": "high"
            }, pilot.app.cfg
            assert not pilot.app._pending_variants.get("agent:sisyphus"), (
                f"the pick reached cfg — nothing may stay pending: {pilot.app._pending_variants}"
            )

            # Now clear the variant via `v` on the row that IS the assignment.
            assert await _highlight_candidate(pilot, "openai/gpt-5.5") is not None
            await pilot.press("v")
            await pilot.pause()
            vlist = pilot.app.screen.query_one("#variant-list", OptionList)
            vids = [vlist.get_option_at_index(i).id for i in range(vlist.option_count)]
            vlist.highlighted = vids.index("var:__none__")   # the synthetic "(none)" clear row
            await pilot.press("enter")
            await pilot.pause()

            node = pilot.app.cfg["agents"]["sisyphus"]
            assert node == {"model": "openai/gpt-5.5"}, node
            labels = _cand_labels(pilot)
            assert not any("openai/gpt-5.5 (high)" in s for s in labels), (
                f"a stale override must not outlive the cfg value it was staged into: {labels}"
            )

    asyncio.run(_run())


def test_pilot_pending_variant_is_dropped_by_undo_and_by_refresh(pilot_config, monkeypatch):
    """The other two clear-sites. `_restore_state`: pending picks are not in the undo aux, so
    undo/redo has no matching value to restore and keeping one would re-apply a choice made
    against a cfg state you just stepped away from. `_refresh_catalog`: the pick was chosen from
    PRE-refresh variant sets and its row may not survive the re-resolve — same reasoning that
    already drops `_custom_rows`."""
    cfg_path, _ = pilot_config
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write('{ "agents": { "sisyphus": { "model": "opencode/gpt-5.5" } } }')

    async def _run():
        _seed_verbose("openai", {"gpt-5.5": ["low", "medium", "high"]})
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")

            # --- undo path: stage something undoable, then a pending pick, then `u`.
            await _select_candidate(pilot, "zhipuai/glm-5")
            assert await _highlight_candidate(pilot, "openai/gpt-5.5") is not None
            await _vkey_pick(pilot, "high")
            assert pilot.app._pending_variants.get("agent:sisyphus"), "precondition: pick pending"
            await pilot.press("u")
            await pilot.pause()
            assert pilot.app._pending_variants == {}, (
                f"undo must not carry a pick it cannot restore: {pilot.app._pending_variants}"
            )

            # --- refresh path.
            assert await _highlight_candidate(pilot, "openai/gpt-5.5") is not None
            await _vkey_pick(pilot, "high")
            assert pilot.app._pending_variants.get("agent:sisyphus"), "precondition: pick pending"
            # `r` runs catalog.refresh() off-thread; the autouse stub returns empty stdout,
            # which would raise CatalogUnavailable — hand the worker an equivalent catalog so
            # the post-refresh path actually runs (the file's existing refresh idiom).
            from omodel import app as app_mod
            monkeypatch.setattr(
                app_mod.catalog_mod, "refresh",
                lambda *a, **k: Catalog(available={"openai": ["gpt-5.5"],
                                                   "opencode": ["gpt-5.5"]},
                                        connected=["opencode", "openai"]),
            )
            await pilot.press("r")
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
            assert pilot.app._pending_variants == {}, (
                f"a refresh must drop picks made against pre-refresh sets: "
                f"{pilot.app._pending_variants}"
            )

    asyncio.run(_run())


def test_pilot_pending_variant_dropped_with_the_subtarget(pilot_config):
    """`_delete_subtarget` drops the pending pick along with the sub-target's typed rows and
    cached rows — its stated contract is that re-adding the sub-target "starts clean rather than
    resurrecting a stale ⚠ row", and a surviving pick would come back applied to the rebuilt
    row (`v` → `x` → `a` re-adds it reading `(high)`, and Enter writes that)."""
    cfg_path, _ = pilot_config
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write('{ "agents": { "sisyphus": { "model": "opencode/gpt-5.5",'
                ' "compaction": { "model": "opencode/gpt-5.5" } } } }')

    async def _run():
        _seed_verbose("openai", {"gpt-5.5": ["low", "medium", "high"]})
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            sub = "agent:sisyphus.compaction"
            await _select_target(pilot, sub)
            assert await _highlight_candidate(pilot, "openai/gpt-5.5") is not None
            await _vkey_pick(pilot, "high")
            assert pilot.app._pending_variants.get(sub), "precondition: pick pending on the sub"

            pilot.app._delete_subtarget(sub, "sisyphus", "compaction")
            await pilot.pause()
            assert sub not in pilot.app._pending_variants, (
                f"the sub-target is gone — nothing may stay pending on it: "
                f"{pilot.app._pending_variants}"
            )

    asyncio.run(_run())


def test_pilot_fetch_landing_under_variant_modal_keeps_the_pick(pilot_config):
    """The one case the re-render must stand down for. `action_variant._apply` holds a row dict
    captured before the modal opened and returns early if `_rows[target][idx]` is no longer that
    same object (its way of yielding to an `r` refresh) — so rebuilding rows while the modal is
    open drops the pick before `_pending_variants` ever sees it. Pins the guard directly rather
    than relying on the 0.2s debounce happening to fire inside another test's `pilot.pause()`."""
    cfg_path, _ = pilot_config
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write('{ "agents": { "sisyphus": { "model": "opencode/gpt-5.5" } } }')

    async def _run():
        _seed_verbose("openai", {"gpt-5.5": ["low", "medium", "high"]})
        app = _build_app(cfg_path)
        app.catalog.detail = lambda *a, **k: {
            "context": 1, "cost": None, "reasoning": False, "image": False
        }
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            assert await _highlight_candidate(pilot, "openai/gpt-5.5") is not None
            await pilot.press("v")
            await pilot.pause()
            assert isinstance(pilot.app.screen, VariantModal), pilot.app.screen

            # The fetch completes with the modal still up.
            await _land_detail_fetch(pilot, "agent:sisyphus", "opencode", "gpt-5.5")

            vlist = pilot.app.screen.query_one("#variant-list", OptionList)
            vids = [vlist.get_option_at_index(i).id for i in range(vlist.option_count)]
            vlist.highlighted = vids.index("var:high")
            await pilot.press("enter")
            await pilot.pause()
            after = _cand_labels(pilot)
            assert any("openai/gpt-5.5 (high)" in s for s in after), (
                f"a fetch landing under the modal must not cost the pick: {after}"
            )

    asyncio.run(_run())


def test_pilot_fetch_landing_under_a_non_variant_modal_still_rerenders(pilot_config):
    """…and the guard is VariantModal-specific, not "any modal". Gating on the whole screen stack
    meant a fetch landing under the `?` overlay skipped the rebuild — and nothing ever retried:
    the completed fetch is cached so no further fetch is scheduled, and a re-highlight hits the
    still-stale `_rows`. The rows stayed wrong for the rest of the session, which is the very bug
    the re-render exists to fix."""
    cfg_path, _ = pilot_config
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write('{ "agents": { "sisyphus": { "model": "opencode/gpt-5.5" } } }')

    async def _run():
        app = _build_app(cfg_path)
        seen = {"n": 0}
        real = app._render_candidates
        app._render_candidates = lambda t: (seen.__setitem__("n", seen["n"] + 1), real(t))[1]
        app.catalog.detail = lambda *a, **k: {
            "context": 1, "cost": None, "reasoning": False, "image": False
        }
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            await pilot.press("question_mark")
            await pilot.pause()
            assert len(pilot.app.screen_stack) > 1, "the ? overlay must be open"

            before = seen["n"]
            stale = pilot.app._build_rows("agent:sisyphus")   # the dicts cached pre-fetch
            await _land_detail_fetch(pilot, "agent:sisyphus", "opencode", "gpt-5.5")
            assert seen["n"] > before, (
                "a non-VariantModal overlay must not suppress the candidate re-render"
            )
            # …and the rows were REBUILT, not merely redrawn. A call count alone would still
            # pass with `_rows.clear()` dropped — i.e. with the stale dicts re-rendered — so
            # assert on object identity, which only a real re-resolve changes.
            rebuilt = pilot.app._build_rows("agent:sisyphus")
            assert all(r is not s for r, s in zip(rebuilt, stale)), (
                "the row cache must be rebuilt from the new verbose, not re-rendered as-is"
            )

    asyncio.run(_run())


def test_pilot_vkey_other_provider_row_does_not_switch_provider(pilot_config):
    """`v` on a candidate that shares the assigned model but under a DIFFERENT provider must not
    silently switch the provider. Sisyphus is assigned opencode/gpt-5.5; varianting the
    openai/gpt-5.5 row (same model, other provider, NOT the assignment) leaves the on-disk
    opencode/gpt-5.5 untouched — only Enter sets a model. (Regression: the old model-only match
    restaged openai/gpt-5.5, switching the provider.)"""
    cfg_path, _ = pilot_config
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write('{ "agents": { "sisyphus": { "model": "opencode/gpt-5.5" } } }')

    async def _run():
        _seed_verbose("openai", {"gpt-5.5": ["low", "medium", "high"]})
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            assert await _highlight_candidate(pilot, "openai/gpt-5.5") is not None
            await pilot.press("v")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 2, "v must open the VariantModal"
            vlist = pilot.app.screen.query_one("#variant-list", OptionList)
            vids = [vlist.get_option_at_index(i).id for i in range(vlist.option_count)]
            vlist.highlighted = vids.index("var:high")
            await pilot.press("enter")
            await pilot.pause()
            node = pilot.app.cfg["agents"]["sisyphus"]
            assert node["model"] == "opencode/gpt-5.5", (
                f"v on a non-assigned row must not switch the provider: {node}"
            )
            assert "variant" not in node, (
                f"v on a non-assigned row must not create an assignment/variant on disk: {node}"
            )

    asyncio.run(_run())


def test_pilot_vkey_apply_survives_rows_cache_cleared(pilot_config):
    """If a background refresh clears the per-target row cache while the VariantModal is open,
    applying the picked variant must not crash on the now-stale idx — the edit is dropped and the
    assignment is left as-is. (Regression: _apply did self._rows[target][idx] = row unguarded,
    KeyError-ing once a refresh cleared the cache.)"""
    cfg_path, _ = pilot_config
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write('{ "agents": { "sisyphus": { "model": "openai/gpt-5.5" } } }')

    async def _run():
        _seed_verbose("openai", {"gpt-5.5": ["low", "medium", "high"]})
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            assert await _highlight_candidate(pilot, "openai/gpt-5.5") is not None
            await pilot.press("v")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 2
            # Simulate a background `r` refresh completing under the open modal: it clears _rows.
            pilot.app._rows.clear()
            vlist = pilot.app.screen.query_one("#variant-list", OptionList)
            vids = [vlist.get_option_at_index(i).id for i in range(vlist.option_count)]
            vlist.highlighted = vids.index("var:high")
            await pilot.press("enter")  # must not raise (guarded against the cleared cache)
            await pilot.pause()
            node = pilot.app.cfg["agents"]["sisyphus"]
            assert node["model"] == "openai/gpt-5.5", node
            assert node.get("variant") is None, f"variant edit must be dropped after cache clear: {node}"

    asyncio.run(_run())


def test_pilot_addmodal_variant_skipped_for_familyless(pilot_config):
    """A model opencode reports no variants for skips the variant phase: a single Enter adds it
    with no variant key. Nothing is seeded into the `--verbose` cache here, so catalog.variants_for
    returns [] for both a custom id (openrouter/zzz-custom) and a real model (alibaba/qwen-3-max)."""
    cfg_path, _ = pilot_config

    async def _run():
        # Custom id (detect_family → None) via the standard harness.
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "openrouter/zzz-custom"  # full provider/model → synthetic "use as typed"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 1, (
                "a family-less id must add immediately (no variant phase)"
            )
            node = pilot.app.cfg["agents"]["sisyphus"]
            assert node["model"] == "openrouter/zzz-custom", node
            assert "variant" not in node, f"no variant key for a family-less add: {node}"

        # qwen id (family 'qwen', variants == []) via a bespoke catalog.
        catalog = Catalog(
            available={"alibaba": ["qwen-3-max"], "zhipuai": ["glm-5"]},
            connected=["alibaba", "zhipuai"],
        )
        app = _build_app_with(cfg_path, catalog)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "alibaba/qwen-3-max"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 1, (
                "a variant-less family (qwen) must add immediately"
            )
            node = pilot.app.cfg["agents"]["sisyphus"]
            assert node["model"] == "alibaba/qwen-3-max", node
            assert "variant" not in node, f"qwen declares no variants: {node}"

    asyncio.run(_run())


def test_pilot_addmodal_esc_returns_then_cancels(pilot_config):
    """Esc in the variant phase returns to the model phase — Input visible + focused with its
    typed value intact, candidate list back, variant list hidden — and a second Esc cancels the
    modal, leaving the assignment untouched."""
    cfg_path, _ = pilot_config

    async def _run():
        _seed_verbose("openai", {"gpt-5.5": ["low", "medium", "high"]})
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            before = pilot.app.cfg["agents"]["sisyphus"].get("model")
            inp.value = "openai/gpt-5.5"
            await pilot.pause()
            await pilot.press("enter")  # → variant phase
            await pilot.pause()
            scr = pilot.app.screen
            variants = scr.query_one("#add-variants", OptionList)
            assert variants.display and pilot.app.focused is variants

            await pilot.press("escape")  # back to the model phase
            await pilot.pause()
            inp = scr.query_one("#add-input", Input)
            assert inp.display, "Esc from the variant phase must restore the Input"
            assert pilot.app.focused is inp, "the model phase must re-focus the Input"
            assert inp.value == "openai/gpt-5.5", (
                f"the typed value must survive esc-back: {inp.value!r}"
            )
            assert scr.query_one("#add-candidates", OptionList).display, "candidate list back"
            assert not scr.query_one("#add-variants", OptionList).display, "variant list hidden"

            await pilot.press("escape")  # cancel the modal
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 1, "a second Esc must close the modal"
            assert pilot.app.cfg["agents"]["sisyphus"].get("model") == before, (
                "cancel must assign nothing"
            )

    asyncio.run(_run())


def test_addmodal_gpt_filter_fuzzy_rows():
    """AddModelModal(require_gpt=True)._fuzzy_rows filters to GPT models only (a non-GPT pick is a
    foot-gun, not a warning); a typed non-GPT full id still stays blocked by _build_row."""
    from omodel import suggestions as suggestions_mod
    from omodel.app import AddModelModal

    suggestions = suggestions_mod.load()
    catalog = Catalog(
        available={
            "opencode": ["claude-opus-4-7", "kimi-k2.5", "glm-5", "gpt-5.5"],
            "deepseek": ["deepseek-v4-pro"],
            "zhipuai": ["glm-5"],
            "openai": ["gpt-5.5"],
        },
        connected=["opencode", "deepseek", "zhipuai", "openai"],
    )
    resolver = Resolver.build(catalog, suggestions)

    # Construct inside a running loop (see test_addmodal_gpt_only_gating: a Textual screen needs a
    # current event loop on Python 3.9).
    async def _run():
        gated = AddModelModal(resolver, suggestions, require_gpt=True)
        ids = [f"{r['provider']}/{r['model']}" for r in gated._fuzzy_rows("")]
        assert "openai/gpt-5.5" in ids, ids
        assert "opencode/gpt-5.5" in ids, ids
        assert all("gpt" in i.rsplit("/", 1)[-1].lower() for i in ids), ids
        assert not any(("glm" in i or "kimi" in i or "deepseek" in i) for i in ids), ids

        row, preview, ok = gated._build_row("zhipuai/glm-5")
        assert not ok and row is None and "GPT" in preview, (preview, ok)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test 14: add-model modal — adversarial / edge cases (tester)
#
# These exercise the two-phase picker beyond the happy path: empty catalog,
# de-dup, provider-name fuzzy match, the GPT gate through the *pushed* modal,
# esc-back value retention, and three behaviours flagged to the lead as
# footguns/warts (bare-Enter, mixed-case dup, model-level warn). They assert
# the ACTUAL behaviour (characterization), so a future change that alters any
# of them trips here.
# ---------------------------------------------------------------------------


def test_pilot_addmodal_empty_catalog(pilot_config):
    """Empty catalog (available={}, connected=[]): browse mode shows an empty list with no
    exception and nothing staged; Tab on the empty list is a no-op (input untouched); typing a
    full provider/model still stages a synthetic row — WITHOUT an 'unavailable' warn, since an
    empty catalog.connected means availability is UNKNOWN (degraded mode: opencode missing / a
    CatalogUnavailable launch), not a confirmed miss — mirrors _build_rows' identical reasoning
    for the off-chain current-assignment row — and Enter still adds it."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app_with(cfg_path, Catalog(available={}, connected=[]))
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            scr = pilot.app.screen
            cands = scr.query_one("#add-candidates", OptionList)
            # Browse mode over an empty catalog: zero rows, nothing staged, no crash.
            assert cands.option_count == 0, _add_candidate_labels(pilot)
            assert scr._staged is None

            # Tab with an empty list must not crash and must not change the input.
            await pilot.press("tab")
            await pilot.pause()
            assert inp.value == ""
            assert pilot.app.focused is inp, "Tab on empty list keeps focus on the input"

            # A typed full id still stages a row — warn-free, since with catalog.connected empty
            # there is no readable catalog to confirm it's actually unavailable.
            inp.value = "openrouter/zzz-custom"
            await pilot.pause()
            assert cands.option_count == 1, _add_candidate_labels(pilot)
            assert "unavailable" not in _add_candidate_labels(pilot)[0]
            assert scr._staged is not None and scr._staged["warn"] == []

            await pilot.press("enter")  # family-less → no variant phase → immediate add
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 1, "family-less id adds with a single Enter"
            node = pilot.app.cfg["agents"]["sisyphus"]
            assert node["model"] == "openrouter/zzz-custom", node
            assert "variant" not in node, node

    asyncio.run(_run())


def test_pilot_addmodal_synthetic_row_dedup(pilot_config):
    """A typed full id that IS available appears exactly once: the fuzzy hit is NOT also
    duplicated by a synthetic 'use as typed' row (the synth row is suppressed when the pair is
    already a fuzzy match)."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "zhipuai/glm-5"  # exactly an available pair
            await pilot.pause()
            labels = _add_candidate_labels(pilot)
            matches = [s for s in labels if s.startswith("zhipuai/glm-5")]
            assert len(matches) == 1, f"available id must appear once, not duplicated: {labels}"
            # The single row carries no warning (it is genuinely available).
            assert "unavailable" not in matches[0], matches

    asyncio.run(_run())


def test_pilot_addmodal_backspace_after_tab_falls_back_to_fuzzy(pilot_config):
    """Tab-fill then backspace falls back to the fuzzy matches — NOT a synthetic '⚠ unavailable'
    row for the half-typed text. Repro: type 'glm' → Tab fills 'zhipuai/glm-5' → backspace leaves
    'zhipuai/glm-' (still a subsequence of 'zhipuai/glm-5'). The synth row is offered ONLY when
    nothing fuzzy-matches, so here the list stays the warn-free fuzzy hit and a reflexive Enter is
    safe."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "glm"
            await pilot.pause()
            await pilot.press("tab")  # fills the highlighted dedicated-first pair
            await pilot.pause()
            assert inp.value == "zhipuai/glm-5"

            await pilot.press("backspace")  # → "zhipuai/glm-": a fragment of the available pair
            await pilot.pause()
            assert inp.value == "zhipuai/glm-"

            scr = pilot.app.screen
            cands = scr.query_one("#add-candidates", OptionList)
            labels = _add_candidate_labels(pilot)
            # Fell back to fuzzy: the lone warn-free hit, no "use as typed" ⚠ row for "zhipuai/glm-".
            assert not any("unavailable" in s for s in labels), labels
            assert len(labels) == 1 and labels[0].startswith("zhipuai/glm-5"), labels
            assert cands.display and cands.highlighted == 0
            assert scr._staged is not None and scr._staged["warn"] == [], scr._staged
            assert (scr._staged["provider"], scr._staged["model"]) == ("zhipuai", "glm-5")

    asyncio.run(_run())


def test_pilot_addmodal_provider_name_fuzzy(pilot_config):
    """Fuzzy scores the whole 'provider/model' string, so typing a PROVIDER name surfaces that
    provider's rows and excludes unrelated models."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "openai"
            await pilot.pause()
            labels = _add_candidate_labels(pilot)
            assert any("openai/gpt-5.5" in s for s in labels), labels
            assert not any(("glm" in s or "kimi" in s or "deepseek" in s) for s in labels), labels

    asyncio.run(_run())


def test_pilot_addmodal_gpt_only_typed_blocked_via_modal(pilot_config):
    """Through the PUSHED modal on a GPT-only agent (Hephaestus, require_gpt via _gpt_only): the
    browse list is GPT-only, a typed non-GPT full id stays blocked (Enter is a no-op, no
    assignment, modal stays open), and a typed GPT id is accepted (staged)."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _open_add_modal(pilot, "agent:hephaestus")
            scr = pilot.app.screen
            inp = scr.query_one("#add-input", Input)

            # Type-to-search: the list is empty until you type. A query that WOULD match non-GPT
            # models ("5" matches glm-5 / kimi-k2.5) surfaces only GPT rows — proving the filter.
            inp.value = "5"
            await pilot.pause()
            labels = _add_candidate_labels(pilot)
            assert labels, "a matching query must surface hephaestus' GPT models"
            assert all("gpt" in s.lower() for s in labels), f"GPT-only list leaked non-GPT: {labels}"

            # Typed non-GPT full id: blocked. Nothing staged; Enter is a no-op; modal stays.
            inp.value = "zhipuai/glm-5"
            await pilot.pause()
            assert scr._staged is None, "non-GPT id must not stage under require_gpt"
            await pilot.press("enter")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 2, "blocked Enter must not close the modal"
            assert pilot.app.cfg["agents"].get("hephaestus", {}).get("model") is None

            # Typed GPT id: accepted (staged).
            inp.value = "openai/gpt-5.5"
            await pilot.pause()
            assert scr._staged is not None, "GPT id must stage under require_gpt"
            assert "gpt" in scr._staged["model"].lower(), scr._staged

    asyncio.run(_run())


def test_pilot_addmodal_open_then_type_selects(pilot_config):
    """Type-to-search F1: opening renders no list and stages nothing, so a reflexive Enter right
    after opening is a no-op (modal stays, no assignment). Typing surfaces the fuzzy list and
    auto-stages the top (dedicated-first) row; Enter selects it — deepseek/deepseek-v4-pro has
    variants (seeded into the cached `--verbose`), so it enters the variant phase, and picking one
    commits."""
    cfg_path, _ = pilot_config

    async def _run():
        _seed_verbose("deepseek", {"deepseek-v4-pro": ["low", "medium", "high", "max"]})
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _open_add_modal(pilot)
            scr = pilot.app.screen
            cands = scr.query_one("#add-candidates", OptionList)
            inp = scr.query_one("#add-input", Input)
            before = pilot.app.cfg["agents"]["sisyphus"].get("model")

            # Open = no list, nothing staged (F1: a reflexive Enter can't commit).
            assert cands.option_count == 0 and not cands.display, "open shows no list"
            assert scr._staged is None, "nothing pre-staged on open"
            await pilot.press("enter")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 2, "bare Enter on open must not close the modal"
            assert pilot.app.cfg["agents"]["sisyphus"].get("model") == before, "no assignment"

            # Type to surface deepseek/deepseek-v4-pro; the top row is auto-staged (dedicated-first).
            inp.value = "deepseek"
            await pilot.pause()
            assert cands.display and cands.highlighted == 0
            row0 = scr._candidate_rows[0]
            assert (row0["provider"], row0["model"]) == ("deepseek", "deepseek-v4-pro"), row0
            assert scr._staged == row0, "the top match is auto-staged"

            # Enter selects it; deepseek has variants → variant phase, then pick one to commit.
            await pilot.press("enter")
            await pilot.pause()
            variants = scr.query_one("#add-variants", OptionList)
            assert variants.display and pilot.app.focused is variants, "Enter selects the row"
            await pilot.press("down")   # highlight the first variant (low)
            await pilot.press("enter")  # commit
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 1
            node = pilot.app.cfg["agents"]["sisyphus"]
            assert node["model"] == "deepseek/deepseek-v4-pro", node
            assert node.get("variant") == "low", node

    asyncio.run(_run())


def test_pilot_addmodal_variantless_typed_then_enter_commits(pilot_config):
    """Type-to-search F1 (sharp form): even when the lone pair is a VARIANT-LESS family — where
    there is no variant phase to act as a stop — a reflexive Enter right after opening cannot commit
    it, because nothing is rendered/staged until you type. Typing surfaces + auto-stages the only
    pair (the variant-less alibaba/qwen-3-max); a single Enter then commits it with no variant key."""
    cfg_path, _ = pilot_config

    async def _run():
        catalog = Catalog(available={"alibaba": ["qwen-3-max"]}, connected=["alibaba"])
        app = _build_app_with(cfg_path, catalog)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            scr = pilot.app.screen
            before = pilot.app.cfg["agents"]["sisyphus"].get("model")
            assert scr._staged is None, "open must not pre-stage the lone variant-less pair"

            await pilot.press("enter")  # reflexive Enter on open → no-op
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 2, "bare Enter on open must not commit"
            assert pilot.app.cfg["agents"]["sisyphus"].get("model") == before, "no assignment"

            inp.value = "qwen"          # surface + auto-stage the lone pair
            await pilot.pause()
            assert scr._staged is not None
            await pilot.press("enter")  # variant-less → immediate commit
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 1, "Enter commits the variant-less row in one step"
            node = pilot.app.cfg["agents"]["sisyphus"]
            assert node["model"] == "alibaba/qwen-3-max", node
            assert "variant" not in node, node

    asyncio.run(_run())


def test_pilot_addmodal_bare_known_vs_unknown(pilot_config):
    """A bare (no-slash) KNOWN id is surfaced by fuzzy and staged dedicated-first (zhipuai/glm-5),
    so Enter works; a bare UNKNOWN id yields no row and Enter is a no-op (still blocked) — there is
    no synthetic row for a bare id (synth rows require a '/')."""
    cfg_path, _ = pilot_config

    async def _run():
        # Bare known id.
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "glm-5"
            await pilot.pause()
            scr = pilot.app.screen
            assert scr._staged is not None
            assert (scr._staged["provider"], scr._staged["model"]) == ("zhipuai", "glm-5"), (
                "bare known id resolves dedicated-first via the fuzzy list"
            )

        # Bare unknown id.
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "zzznope"
            await pilot.pause()
            scr = pilot.app.screen
            assert scr.query_one("#add-candidates", OptionList).option_count == 0
            assert scr._staged is None
            await pilot.press("enter")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 2, "bare unknown id: Enter is a no-op"
            assert pilot.app.cfg["agents"]["sisyphus"]["model"] == "opencode/claude-opus-4-7"

    asyncio.run(_run())


def test_pilot_addmodal_mixedcase_typed_duplicate(pilot_config):
    """F2: a mixed-case typed full id that matches an available pair collapses onto the single
    canonical lowercase row — no second uppercase 'use as typed' row, and no spurious ⚠ unavailable.
    The synth row is suppressed because ANY fuzzy match suppresses it, and the matcher is
    case-insensitive (so 'ZHIPUAI/GLM-5' fuzzy-matches 'zhipuai/glm-5'). The staged row is the
    canonical zhipuai/glm-5 (warn-free)."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "ZHIPUAI/GLM-5"
            await pilot.pause()
            labels = _add_candidate_labels(pilot)
            assert len(labels) == 1, (
                f"mixed-case typed id must collapse onto the canonical pair: {labels}"
            )
            assert labels[0].startswith("zhipuai/glm-5"), labels
            assert "unavailable" not in labels[0], labels
            scr = pilot.app.screen
            assert (scr._staged["provider"], scr._staged["model"]) == ("zhipuai", "glm-5")
            assert scr._staged["warn"] == [], scr._staged

    asyncio.run(_run())


def test_pilot_addmodal_trailing_slash_uses_fuzzy(pilot_config):
    """A trailing-slash typed text ('zhipuai/') is 'incomplete' on the typed path, but the fuzzy
    list still matches the provider's models, so a real pair (zhipuai/glm-5) is staged and Enter
    proceeds — the fuzzy list, not the bare typed text, drives selection."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "zhipuai/"
            await pilot.pause()
            scr = pilot.app.screen
            assert scr._staged is not None
            assert (scr._staged["provider"], scr._staged["model"]) == ("zhipuai", "glm-5"), (
                "trailing slash: the fuzzy hit is staged, not the incomplete typed text"
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test: _ensure_node coerces a hand-edited non-dict value back to {}
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw_cfg",
    [
        '{ "agents": null }',
        '{ "agents": { "sisyphus": null } }',
    ],
    ids=["agents-map-null", "agent-object-null"],
)
def test_pilot_ensure_node_coerces_non_dict_value(tmp_path, raw_cfg):
    """A hand-edited config with a non-dict value at the `agents` map itself, OR at an individual
    agent object, must not crash when setting a model: _ensure_node coerces the non-dict value
    back to `{}` (mirroring _node_for's defensive isinstance reads) instead of AttributeError'ing
    on `setdefault` (agents == null: setdefault sees the key present and returns None as-is) or
    handing back None for the caller's `node['model'] = ...` (sisyphus == null: same reason, one
    level down)."""
    cfg_path = str(tmp_path / "oh-my-openagent.jsonc")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(raw_cfg)

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            found_id = await _select_candidate(pilot, "zhipuai/glm-5")
            assert found_id is not None, "zhipuai/glm-5 must appear as a sisyphus candidate"
            await _save_and_confirm(pilot)

    asyncio.run(_run())  # must not raise (AttributeError / TypeError pre-fix)

    import json5

    with open(cfg_path, encoding="utf-8") as f:
        saved = json5.load(f)
    assert saved["agents"]["sisyphus"]["model"] == "zhipuai/glm-5", saved


# ---------------------------------------------------------------------------
# Unit test: _to_thread_daemon (quit-hang fix — daemon thread, not to_thread's executor)
# ---------------------------------------------------------------------------

def test_to_thread_daemon_returns_result():
    """The awaited result is the callable's return value."""
    async def _run():
        return await _to_thread_daemon(lambda: 42)

    assert asyncio.run(_run()) == 42


def test_to_thread_daemon_propagates_exception():
    """An exception raised in the callable propagates to the awaiter, like asyncio.to_thread."""
    def _boom():
        raise ValueError("boom")

    async def _run():
        await _to_thread_daemon(_boom)

    with pytest.raises(ValueError, match="boom"):
        asyncio.run(_run())


def test_to_thread_daemon_runs_on_daemon_thread():
    """The callable runs off the main thread, on a thread with daemon=True — so it can never
    block process exit (unlike asyncio.to_thread's non-daemon executor threads, which are
    joined at interpreter shutdown)."""
    captured = {}

    def _record():
        captured["daemon"] = threading.current_thread().daemon
        captured["is_main"] = threading.current_thread() is threading.main_thread()
        return "ok"

    async def _run():
        return await _to_thread_daemon(_record)

    assert asyncio.run(_run()) == "ok"
    assert captured["daemon"] is True, "the callable must run on a daemon thread"
    assert captured["is_main"] is False, "the callable must run off the main thread"


@pytest.mark.parametrize("boom", [False, True], ids=["returns", "raises"])
def test_to_thread_daemon_is_quiet_when_the_loop_is_already_gone(boom):
    """Outliving the loop is the WHOLE POINT of the daemon thread — `q` exits at once and the
    orphaned opencode call finishes into a process that has moved on. So the delivery hop is
    routinely made into a closed loop, where `call_soon_threadsafe` raises RuntimeError ON THE
    WORKER THREAD: no `await` is left to receive it and no caller can catch it, so Python prints
    `Exception in thread Thread-N` to stderr — corrupting the terminal the TUI just released,
    once per abandoned fetch. Both delivery paths swallow it; this covers both.

    Deterministic by construction, not by timing: the worker parks on an Event, the loop is
    cancelled and CLOSED while it is parked, and only then is it released — so the RuntimeError
    is guaranteed, never raced for. Asserted via `threading.excepthook`, since an exception on a
    non-main thread reaches the test no other way. Both cases fail with
    `RuntimeError('Event loop is closed')` if either `except RuntimeError` is dropped."""
    started, release = threading.Event(), threading.Event()
    worker = {}

    def _blocked():
        worker["thread"] = threading.current_thread()
        started.set()
        assert release.wait(timeout=5), "test bug: the worker was never released"
        if boom:
            raise ValueError("boom")
        return "too late"

    escaped = []
    prev_hook = threading.excepthook
    threading.excepthook = escaped.append
    try:
        loop = asyncio.new_event_loop()
        try:
            task = loop.create_task(_to_thread_daemon(_blocked))
            for _ in range(500):  # spin the loop until the coro has spawned its thread
                if started.is_set():
                    break
                loop.run_until_complete(asyncio.sleep(0.01))
            assert started.is_set(), "the worker thread never started"
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                loop.run_until_complete(task)  # drain, so close() has nothing pending
        finally:
            loop.close()

        assert loop.is_closed(), "precondition: the loop must be gone before delivery"
        release.set()  # NOW let the worker deliver into the closed loop
        worker["thread"].join(timeout=5)
        assert not worker["thread"].is_alive(), "the worker thread never finished"
    finally:
        threading.excepthook = prev_hook

    assert escaped == [], f"RuntimeError escaped the worker thread: {escaped}"


# ---------------------------------------------------------------------------
# Pilot test: double-`r` is single-flight (no concurrent refresh calls)
# ---------------------------------------------------------------------------

def test_pilot_refresh_double_r_is_single_flight(pilot_config, monkeypatch):
    """Pressing `r` twice while a refresh is already in flight must NOT spawn a second
    `opencode models --refresh` call: @work(exclusive=True) only cancels the first refresh's
    ASYNCIO TASK, not the underlying subprocess/thread it's awaiting (which can't be killed), so
    without a single-flight guard two concurrent calls would race cache.clear()/cache.write().
    The second press is a no-op that just notifies."""
    cfg_path, _ = pilot_config
    from omodel import app as app_mod

    call_count = {"n": 0}
    entered = threading.Event()
    proceed = threading.Event()
    notifications = []

    def _slow_refresh(*_a, **_k):
        call_count["n"] += 1
        entered.set()
        proceed.wait(timeout=5)
        return Catalog(available={"opencode": ["claude-opus-4-7"]}, connected=["opencode"])

    monkeypatch.setattr(app_mod.catalog_mod, "refresh", _slow_refresh)

    async def _run():
        app = _build_app(cfg_path)
        app.notify = lambda message, **kwargs: notifications.append(message)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await pilot.pause()
            # Wait (off the event loop thread, so the loop keeps spinning and the already
            # scheduled refresh worker actually runs) for the first refresh to enter the stub —
            # pins _refresh_inflight True before the second `r` fires, hitting the race window
            # deterministically rather than depending on scheduling luck.
            await asyncio.to_thread(entered.wait, 5)
            assert call_count["n"] == 1
            await pilot.press("r")
            await pilot.pause()
            proceed.set()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(_run())

    assert call_count["n"] == 1, (
        f"a second `r` while one is in flight must not spawn a second refresh call: "
        f"{call_count['n']} calls made"
    )
    assert any("already running" in m for m in notifications), (
        f"the second `r` must notify that a refresh is already running: {notifications}"
    )


# ---------------------------------------------------------------------------
# Pilot test: degraded mode (CatalogUnavailable) still gets a working add-model modal
# ---------------------------------------------------------------------------

def test_create_app_degraded_mode_add_model_still_works(pilot_config, monkeypatch):
    """create_app(), when `opencode models` raises CatalogUnavailable, still builds a resolver
    (over the empty degraded Catalog) — so add-model, the ONLY route to a model while degraded,
    stays live: the providers banner shows the retry hint, the candidates pane has no chain rows
    but still offers '+ add model…', and `a` opens AddModelModal rather than bell-ing as a no-op.
    In that modal, a typed pair's availability warn is suppressed (catalog.connected is empty, so
    availability is UNKNOWN — an unqualified ⚠ would mislead)."""
    from omodel.app import AddModelModal

    cfg_path, _ = pilot_config
    from omodel import app as app_mod

    def _raise(*_a, **_k):
        raise app_mod.CatalogUnavailable("`opencode models` exited with code 1")

    monkeypatch.setattr(app_mod.catalog_mod, "load", _raise)

    app = app_mod.create_app(cfg_path)
    assert app.resolver is not None, "create_app must build a resolver even in degraded mode"

    async def _run():
        async with app.run_test() as pilot:
            providers = pilot.app.query_one("#providers", Static)
            assert "couldn't read models" in str(providers.content), str(providers.content)

            await _select_target(pilot, "agent:sisyphus")
            cands = pilot.app.query_one("#candidates", OptionList)
            ids = [cands.get_option_at_index(i).id for i in range(cands.option_count)]
            # No CHAIN ('omo'-sourced) rows in degraded mode; the pilot config's own preset
            # assignment (opencode/claude-opus-4-7) still surfaces as its own off-chain 'add' row
            # (see _build_rows), alongside the ever-present '+ add model…'.
            assert ids == ["cand:0", "cand:add"], ids
            rows = pilot.app._build_rows("agent:sisyphus")
            assert all(r["source"] != "omo" for r in rows), (
                f"degraded mode must show no chain (omo) rows: {rows}"
            )

            cands.focus()
            cands.highlighted = 0
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 2, (
                "`a` must open AddModelModal in degraded mode, not bell as a no-op"
            )
            scr = pilot.app.screen
            assert isinstance(scr, AddModelModal)

            row, _preview, ok = scr._build_row("openai/gpt-99")
            assert ok and row is not None, (row, ok)
            assert row["warn"] == [], (
                f"degraded mode (empty catalog.connected): the unavailable warn must be "
                f"suppressed, not misleadingly flagged: {row}"
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test: a transient catalog.detail() failure is not cached forever
# ---------------------------------------------------------------------------

def test_pilot_candidates_rerender_when_verbose_lands(tmp_path):
    """When a background detail fetch lands it rewrites `verbose-<prov>` — the very file
    `catalog.variants_for` reads to validate omo's suggested variant (`⚠ variant`) and to feed
    `v`. So the CANDIDATE rows go stale too, not just the detail line, and `_rows` would pin
    them until the next cfg mutation dropped it: the correction surfaced the moment you pressed
    enter, reading as "the list changed under me". The worker must re-render both panes.

    Chain is defined inline (not from bundled omo data) so the ⚠ is deterministic: the heuristic
    glm family lists low/medium/high, so omo's `max` reads as unsupported until opencode's
    verbose — which does offer it — arrives."""
    from omodel.suggestions import Suggestions
    from omodel.suggestions import load as _load_suggestions

    cfg_path = str(tmp_path / "oh-my-openagent.jsonc")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write('{ "agents": {}, "categories": {} }')

    real = _load_suggestions()
    suggestions = Suggestions(
        meta={"omoVersion": "inline-for-test", "omoCommit": "", "generatedAt": ""},
        agents={},
        categories={"probe-cat": {"fallbackChain": [
            {"providers": ["opencode"], "model": "glm-5", "variant": "max"},
        ]}},
        families=real.families,
        known_variants=real.known_variants,
    )
    catalog = Catalog(available={"opencode": ["glm-5"]}, connected=["opencode"])

    def _labels(pilot):
        c = pilot.app.query_one("#candidates", OptionList)
        return [str(c.get_option_at_index(i).prompt) for i in range(c.option_count)]

    async def _run():
        from omodel import config_io as _config_io

        cfg, resolved = _config_io.load_config(cfg_path)
        app = OModelApp(
            catalog=catalog, suggestions=suggestions,
            resolver=Resolver.build(catalog, suggestions), cfg=cfg, config_path=resolved,
        )

        def _detail_that_warms_cache(model_id, use_cache=True, provider=None):
            """Stands in for catalog.detail(): the real one WRITES verbose-<prov> as a side
            effect of its `--verbose` call, which is the whole point here."""
            _seed_verbose("opencode", {"glm-5": ["high", "max"]})
            return {"context": 128000, "cost": None, "reasoning": False, "image": False}

        app.catalog.detail = _detail_that_warms_cache

        async with app.run_test() as pilot:
            await _select_target(pilot, "cat:probe-cat")
            before = _labels(pilot)
            assert any("⚠ variant" in s for s in before), (
                f"cold verbose → heuristic fallback flags omo's own suggestion: {before}"
            )

            # The fetch lands. NOTHING is selected — the pane must correct itself.
            pilot.app._fetch_detail("cat:probe-cat", "opencode", "glm-5")
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

            after = _labels(pilot)
            assert not any("⚠ variant" in s for s in after), (
                f"rows must re-resolve against the verbose the fetch just wrote: {after}"
            )
            assert any("opencode/glm-5 (max)" in s for s in after), after
            assert pilot.app.cfg["categories"] == {}, (
                f"a re-render must not stage anything: {pilot.app.cfg}"
            )

    asyncio.run(_run())


def test_pilot_detail_fetch_failure_not_cached_forever(pilot_config):
    """A TRANSIENT catalog.detail() failure (raises) must NOT be cached — the next render
    retries — unlike a genuine `None` RETURN (no record / no providers), which stays cached as
    'known-empty'. Regression: _fetch_detail unconditionally cached info=None even on the
    except-Exception path, permanently blanking the detail line for the rest of the session."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        calls = {"n": 0}

        def _flaky_detail(model_id, use_cache=True, provider=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient failure")
            return {"context": 128000, "cost": None, "reasoning": False, "image": False}

        app.catalog.detail = _flaky_detail
        # Silence the app's OWN ~0.2s debounce timer, armed whenever a target renders, so the
        # only fetches that can run are the two this test makes explicitly.
        #
        # Without this the counts below are a race against machine load, and the fix under test
        # is what loads the dice: a transient failure caches nothing, so _fetch_detail's closing
        # re-render (app.py `_render_detail` → `_detail_info`) arms ANOTHER timer. Should it fire
        # while we await, `calls` gains an increment this test never asked for. It passes on an
        # idle box and reds on a busy CI runner — it did, on 3.10 only, with `{'n': 2}`.
        #
        # Stubbing is faithful to the intent: the debounce is explicitly not what this regression
        # is about, and it keeps its coverage from every other pilot test that renders a target.
        app._schedule_detail_fetch = lambda *a, **k: None

        async with app.run_test() as pilot:
            # pilot_config's sisyphus is assigned opencode/claude-opus-4-7 → the detail cache
            # keys the (provider, model) pair as 'opencode/claude-opus-4-7' (_detail_key).
            # Drive the background worker directly, exactly as the debounce timer's callback
            # would, for a deterministic and fast test.
            key = "opencode/claude-opus-4-7"
            pilot.app._current_target = "agent:sisyphus"
            pilot.app._fetch_detail("agent:sisyphus", "opencode", "claude-opus-4-7")
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

            assert calls["n"] == 1, f"the first (failing) fetch must have run: {calls}"
            assert key not in pilot.app._detail_cache, (
                "a transient failure must NOT be cached, so the next fetch retries"
            )

            # Retry: still uncached, so a fresh call is not gated by _detail_fetching/cache
            # checks — this time it succeeds.
            pilot.app._fetch_detail("agent:sisyphus", "opencode", "claude-opus-4-7")
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

            assert calls["n"] == 2, f"the retry must have run: {calls}"
            assert pilot.app._detail_cache.get(key) is not None, (
                "the successful retry's result must now be cached"
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test: add-modal accept recomputes warn against the LIVE catalog
# ---------------------------------------------------------------------------

def test_pilot_addmodal_accept_recomputes_warn_against_live_catalog(pilot_config):
    """A background `r` refresh completing while the add-model modal is open replaces
    self.catalog/self.resolver — the modal's staged row.warn reflects the STALE (pre-refresh)
    catalog it was built against. _accept must recompute warn against the LIVE catalog before
    staging (not just re-add the modal's stale warn), so an id that became available (or
    unavailable) during the refresh is reported correctly."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "openrouter/zzz-custom"
            await pilot.pause()
            scr = pilot.app.screen
            assert scr._staged is not None
            assert scr._staged["warn"] == ["unavailable"], (
                "openrouter serves nothing in the initial catalog: must warn unavailable"
            )

            # Simulate a background `r` refresh completing while the modal is still open:
            # openrouter now serves zzz-custom. The modal keeps its OWN (stale) resolver/catalog
            # reference — only app.catalog (what _accept must consult) is swapped, exactly as
            # _refresh_catalog does.
            fresh_catalog = Catalog(available={"openrouter": ["zzz-custom"]}, connected=["openrouter"])
            pilot.app.catalog = fresh_catalog

            await pilot.press("enter")  # family-less id → immediate accept, no variant phase
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 1, "modal must close on accept"

            node = pilot.app.cfg["agents"]["sisyphus"]
            assert node["model"] == "openrouter/zzz-custom", node

            staged = pilot.app._custom_rows["agent:sisyphus"][-1]
            assert staged["warn"] == [], (
                f"warn must be recomputed against the LIVE (post-refresh) catalog: {staged}"
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot test: adding a sub-target renders synchronously (no stale queued render)
# ---------------------------------------------------------------------------

def test_pilot_add_sub_renders_synchronously_no_stale_target(pilot_config):
    """Adding a sub-target (`a` on a single-sub-kind agent — every non-Sisyphus agent adds
    `compaction` directly, no chooser) must update _current_target and render the right pane for
    the NEW sub-target SYNCHRONOUSLY, mirroring _restore_state — not rely on the queued
    OptionHighlighted event the highlight move posts (which _target_highlighted would otherwise
    handle later). Calling _add_sub() directly (a plain method — no pilot.press, no intervening
    await) and checking state IMMEDIATELY afterward proves the render already happened without
    that queued event ever being processed."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:oracle")
            assert pilot.app._current_target == "agent:oracle"

            # _add_sub() is a plain (non-async) method: this call runs to completion with NO
            # intervening await, so the highlight move's OptionHighlighted event is only POSTED
            # here — not yet processed (that needs the event loop to run, which we deliberately
            # don't give it before asserting below).
            pilot.app._add_sub()

            assert pilot.app._current_target == "agent:oracle.compaction", (
                "the right pane's notion of the current target must update synchronously, "
                "before the queued OptionHighlighted event is even processed"
            )
            detail = pilot.app.query_one("#detail", Static)
            assert "oracle.compaction" in str(detail.content), str(detail.content)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot: the 3 named presets (DESIGN §presets.py, decision #17)
#
# Pilot half of §Verification check #9 — the unit half is tests/test_presets.py.
# Everything here serves ONE invariant: the config on disk always equals the ACTIVE preset,
# never a fourth orphan state. What that implies, and what these tests pin:
#   * launching with no presets seeds one from your config (in memory — nothing written);
#   * your edits go into the preset you're on, and switching banks them first;
#   * `s` writes BOTH files, and nothing else writes anything;
#   * `x` refuses on the active preset (deleting it would strand the config);
#   * quitting offers save / discard / cancel, and discarding leaves BOTH files untouched.
# ---------------------------------------------------------------------------

def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _preset_labels(app) -> list:
    lst = app.query_one("#presets", OptionList)
    return [str(lst.get_option_at_index(i).prompt) for i in range(lst.option_count)]


def _active_row(app):
    """Index of the row drawn with the ● marker, or None."""
    for i, label in enumerate(_preset_labels(app)):
        if label.startswith("● "):
            return i
    return None


async def _focus_preset(pilot, index: int) -> None:
    """`tab` into the presets card, then highlight row `index`.

    `tab` is the only route in (Screen traversal targets → presets → candidates), so start from
    `#targets` to make the hop deterministic — and skip it when the card already has focus, which
    keeps this helper safe to call twice in a row."""
    card = pilot.app.query_one("#presets", OptionList)
    if pilot.app.focused is not card:
        pilot.app.query_one("#targets", OptionList).focus()
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
    pilot.app.query_one("#presets", OptionList).highlighted = index
    await pilot.pause()


def _capture_notifications(pilot) -> list:
    """Record `(severity, message)` for every toast raised from here on, and keep raising them.
    The toast rack isn't mounted headless, so this is how a test asserts on user-facing warnings."""
    seen = []
    original = pilot.app.notify

    def _spy(message, **kwargs):
        seen.append((kwargs.get("severity", "information"), message))
        return original(message, **kwargs)

    pilot.app.notify = _spy
    return seen


async def _new_preset(pilot, name: str, row: int | None = None) -> None:
    """`a` → name it → enter (append + switch to it). `a` is row-blind, so `row` only picks where
    the cursor sits when it's pressed; default is the trailing `+ add preset…` row."""
    lst = pilot.app.query_one("#presets", OptionList)
    await _focus_preset(pilot, lst.option_count - 1 if row is None else row)
    await pilot.press("a")
    await pilot.pause()
    # Modal widgets live on the pushed screen, not the base one (pilot.app.query_one would
    # search Screen#_default) — same access pattern the add-model tests use.
    pilot.app.screen.query_one("#preset-name-input", Input).value = name
    await pilot.press("enter")
    await pilot.pause()


async def _switch_preset(pilot, index: int) -> None:
    await _focus_preset(pilot, index)
    await pilot.press("enter")
    await pilot.pause()


def test_pilot_first_launch_seeds_a_default_preset(pilot_config):
    """Open a config that has never seen oModel: you get one preset, named `default`, holding
    the models already in your config and marked active — so the invariant holds from the first
    frame. It is IN MEMORY only (one write rule), and an untouched session is not dirty, so `q`
    exits without a prompt."""
    cfg_path, tmp_dir = pilot_config
    before = _read_text(cfg_path)

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            labels = _preset_labels(pilot.app)
            assert labels[0] == f"● 1 {presets_mod.DEFAULT_NAME}", labels
            # ONE preset plus the way to make more — no empty slots to fill in.
            assert labels[1:] == ["+ add preset…"], labels
            assert _active_row(pilot.app) == 0

            seeded = pilot.app._store.current()
            assert seeded.agents["sisyphus"]["model"] == "opencode/claude-opus-4-7"
            assert not pilot.app._is_dirty(), "seeding alone must not make the app dirty"

            await pilot.press("q")  # no prompt: nothing to save
            await pilot.pause()

    asyncio.run(_run())
    # One write rule: opening and closing wrote nothing at all.
    assert not os.path.exists(os.path.join(tmp_dir, ".omodel-presets.json"))
    assert _read_text(cfg_path) == before


def test_pilot_edits_flow_into_the_active_preset_and_s_writes_both(pilot_config):
    """Change a model and the ACTIVE preset changes with it — that is what stops the config from
    becoming a state matching no preset. `s` then publishes both files, and afterwards the
    config equals the active preset exactly."""
    cfg_path, tmp_dir = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            await _select_candidate(pilot, "zhipuai/glm-5")
            assert pilot.app._is_dirty()
            # Not on disk yet — neither file.
            assert not os.path.exists(os.path.join(tmp_dir, ".omodel-presets.json"))
            await _save_and_confirm(pilot)
            assert not pilot.app._is_dirty(), "a save re-baselines BOTH halves of dirtiness"

    asyncio.run(_run())

    store = presets_mod.load(cfg_path)
    active = store.current()
    assert active.name == presets_mod.DEFAULT_NAME
    assert active.agents["sisyphus"]["model"] == "zhipuai/glm-5", "the edit went into the preset"

    import json5

    with open(cfg_path, encoding="utf-8") as f:
        saved = json5.load(f)
    assert saved["agents"]["sisyphus"]["model"] == "zhipuai/glm-5"
    # The invariant, checked directly: the config on disk matches a preset — the active one.
    assert presets_mod.matching_index(store, saved) == store.active


def test_pilot_add_creates_a_preset_and_switches_to_it(pilot_config):
    """`a` copies the models you're looking at into a NEW row, names it, and moves you there —
    the only way presets 2 and 3 come into being."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            await _select_candidate(pilot, "zhipuai/glm-5")
            await _new_preset(pilot, "experiment")

            assert _active_row(pilot.app) == 1
            labels = _preset_labels(pilot.app)
            assert labels[1] == "● 2 experiment", labels
            assert labels[0].startswith("  1 "), labels
            store = pilot.app._projected_store()
            # Both presets hold the models as of the add; they diverge from here.
            assert store.presets[1].agents["sisyphus"]["model"] == "zhipuai/glm-5"
            assert store.presets[0].agents["sisyphus"]["model"] == "zhipuai/glm-5"

    asyncio.run(_run())


def test_pilot_a_on_an_existing_preset_appends_instead_of_overwriting(pilot_config):
    """`a` is ROW-BLIND: pressed on preset 1 it adds preset 2, it does not replace preset 1.

    It used to overwrite the highlighted row (with the name modal as the only confirm), which put
    the one destructive, non-undoable action in the app under the key that means *add* in every
    other pane. Pin the new behaviour: the row under the cursor keeps its models and its name."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            # Preset 1 is seeded from the config; give preset 2 a different model so an overwrite
            # of row 0 would be visible in its models as well as in the row count.
            await _new_preset(pilot, "second")
            await _select_target(pilot, "agent:sisyphus")
            await _select_candidate(pilot, "zhipuai/glm-5")

            before = copy.deepcopy(pilot.app._store.presets[0])
            await _new_preset(pilot, "third", row=0)  # cursor parked ON preset 1

            names = [p.name for p in pilot.app._store.presets]
            assert names == ["default", "second", "third"], names
            assert _active_row(pilot.app) == 2, "you land on the preset you just added"
            kept = pilot.app._store.presets[0]
            assert kept.name == before.name
            assert kept.agents == before.agents, "the row under the cursor was not touched"
            assert (
                pilot.app._store.presets[2].agents["sisyphus"]["model"] == "zhipuai/glm-5"
            ), "the new preset holds the models you were looking at"

    asyncio.run(_run())


def test_pilot_switching_banks_the_edits_you_made(pilot_config):
    """Switching preserves work: the models you changed while on preset 1 are folded INTO
    preset 1 before preset 2 loads, so going back and forth never loses anything."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            # Preset 1 (seeded) gets glm-5; fork it to preset 2, then give 2 a different model.
            await _select_target(pilot, "agent:sisyphus")
            await _select_candidate(pilot, "zhipuai/glm-5")
            await _new_preset(pilot, "experiment")
            await _select_target(pilot, "agent:sisyphus")
            await _select_candidate(pilot, "moonshotai-cn/kimi-k2.5")
            assert pilot.app.cfg["agents"]["sisyphus"]["model"] == "moonshotai-cn/kimi-k2.5"

            # Back to 1: its own models return.
            await _switch_preset(pilot, 0)
            assert _active_row(pilot.app) == 0
            assert pilot.app.cfg["agents"]["sisyphus"]["model"] == "zhipuai/glm-5"

            # Forward to 2: the edit made while on 2 was banked, not lost.
            await _switch_preset(pilot, 1)
            assert pilot.app.cfg["agents"]["sisyphus"]["model"] == "moonshotai-cn/kimi-k2.5"

    asyncio.run(_run())


def test_pilot_undo_moves_the_marker_back_with_the_models(pilot_config):
    """`u` after a switch must restore the ● too. If it moved only the models, they would be
    folded into the preset you switched TO — silently rewriting it."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            await _select_candidate(pilot, "zhipuai/glm-5")
            await _new_preset(pilot, "experiment")
            await _select_target(pilot, "agent:sisyphus")
            await _select_candidate(pilot, "moonshotai-cn/kimi-k2.5")
            await _switch_preset(pilot, 0)
            assert _active_row(pilot.app) == 0

            await pilot.press("u")
            await pilot.pause()
            assert pilot.app.cfg["agents"]["sisyphus"]["model"] == "moonshotai-cn/kimi-k2.5"
            assert _active_row(pilot.app) == 1, (
                "undoing a switch must put the marker back, not just the models"
            )

    asyncio.run(_run())


def test_pilot_delete_refuses_on_the_active_preset(pilot_config):
    """`x` on the preset you're editing is refused: the config mirrors it, so deleting it would
    strand the config as a state matching nothing. A non-active one deletes behind a confirm and
    is staged until `s` like every other change."""
    cfg_path, tmp_dir = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _new_preset(pilot, "experiment")  # active is now row 2
            assert _active_row(pilot.app) == 1

            await _focus_preset(pilot, 1)
            await pilot.press("x")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 1, "no confirm — the delete is refused outright"
            assert [p.name for p in pilot.app._store.presets] == ["default", "experiment"]

            # The other one deletes — and the list CLOSES UP behind it (dense, no holes).
            await _focus_preset(pilot, 0)
            await pilot.press("x")
            await pilot.pause()
            assert len(pilot.app.screen_stack) > 1
            await pilot.press("y")
            await pilot.pause()
            assert [p.name for p in pilot.app._store.presets] == ["experiment"]
            assert _active_row(pilot.app) == 0, "the active preset moved down with the list"
            assert pilot.app._store.current().name == "experiment", "…and is still the same one"

    asyncio.run(_run())
    # Staged only: nothing was written, because only `s` writes.
    assert not os.path.exists(os.path.join(tmp_dir, ".omodel-presets.json"))


def test_pilot_quit_discard_leaves_both_files_alone(pilot_config):
    """`q` with unsaved work offers three ways out. Discarding writes nothing — the config still
    equals the preset it did when you started."""
    cfg_path, tmp_dir = pilot_config
    before = _read_text(cfg_path)

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            await _select_candidate(pilot, "zhipuai/glm-5")
            await pilot.press("q")
            await pilot.pause()
            assert isinstance(pilot.app.screen, QuitModal)
            await pilot.press("d")  # discard
            await pilot.pause()

    asyncio.run(_run())
    assert _read_text(cfg_path) == before
    assert not os.path.exists(os.path.join(tmp_dir, ".omodel-presets.json"))


def test_pilot_quit_can_save_on_the_way_out(pilot_config):
    """\"Save & quit\" runs the normal diff+confirm and only then exits — so the exit that keeps
    your work is on the same screen as the one that drops it."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            await _select_candidate(pilot, "zhipuai/glm-5")
            await pilot.press("q")
            await pilot.pause()
            await pilot.press("s")  # save & quit → the save confirm
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()

    asyncio.run(_run())
    import json5

    with open(cfg_path, encoding="utf-8") as f:
        saved = json5.load(f)
    assert saved["agents"]["sisyphus"]["model"] == "zhipuai/glm-5"
    store = presets_mod.load(cfg_path)
    assert presets_mod.matching_index(store, saved) == store.active


def test_pilot_launch_prompts_when_the_config_matches_no_preset(pilot_config):
    """The one case the invariant can't cover itself: something outside oModel rewrote the
    config. On launch you're asked which way to sync, and NEITHER answer writes anything — both
    end at `s`."""
    cfg_path, tmp_dir = pilot_config
    other = presets_mod.capture("mine", {"agents": {"sisyphus": {"model": "zhipuai/glm-5"}}})
    store = presets_mod.Store(presets=[other], active=0)
    presets_mod.write(cfg_path, store)
    before_presets = _read_text(os.path.join(tmp_dir, ".omodel-presets.json"))
    before_cfg = _read_text(cfg_path)

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            assert isinstance(pilot.app.screen, ConfirmModal), "out-of-sync must be surfaced"
            # "Restore the preset" is the decline button.
            await pilot.press("n")
            await pilot.pause()
            assert pilot.app.cfg["agents"]["sisyphus"]["model"] == "zhipuai/glm-5"
            assert pilot.app._is_dirty(), "the fix is staged, to be reviewed via s"

    asyncio.run(_run())
    # Neither file was touched by the prompt itself.
    assert _read_text(cfg_path) == before_cfg
    assert _read_text(os.path.join(tmp_dir, ".omodel-presets.json")) == before_presets


def test_pilot_launch_activates_a_matching_preset_silently(pilot_config):
    """No prompt when the config matches a DIFFERENT preset — there is no conflict to resolve,
    so it just becomes the active one (and re-deriving that each launch reads clean)."""
    cfg_path, _ = pilot_config
    mine = presets_mod.capture("mine", {"agents": {"sisyphus": {"model": "zhipuai/glm-5"}}})
    from omodel import config_io as _config_io

    cfg, _resolved = _config_io.load_config(cfg_path)
    matching = presets_mod.capture("matching", cfg)
    presets_mod.write(cfg_path, presets_mod.Store(presets=[mine, matching], active=0))

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            assert len(pilot.app.screen_stack) == 1, "a match is not a conflict — no prompt"
            assert _active_row(pilot.app) == 1
            assert not pilot.app._is_dirty(), "re-pointing active alone must read clean"

    asyncio.run(_run())


def test_pilot_preset_keys_dispatch_on_focus(pilot_config):
    """`a`/`x`/`v` were focus-blind before this pane existed. With #presets focused: `v` is
    inert (no modal, no cfg change — unguarded it would retarget the hidden candidate pane),
    and `enter` on an EMPTY preset does nothing."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            await _select_candidate(pilot, "zhipuai/glm-5")
            snapshot = copy.deepcopy(pilot.app.cfg)

            await _focus_preset(pilot, 1)  # empty
            await pilot.press("v")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 1, "`v` must not open a modal on a preset row"
            assert pilot.app.cfg == snapshot

            await pilot.press("enter")  # empty preset → bell, no switch
            await pilot.pause()
            assert pilot.app.cfg == snapshot
            assert _active_row(pilot.app) == 0

    asyncio.run(_run())


def test_pilot_preset_survives_a_mangled_sidecar(pilot_config):
    """A hand-mangled presets file must not stop you editing models: it reads as empty, so the
    seed kicks in and you still get a working default preset."""
    cfg_path, tmp_dir = pilot_config
    with open(os.path.join(tmp_dir, ".omodel-presets.json"), "w", encoding="utf-8") as f:
        f.write("{{{ not json")

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            assert pilot.app._store.current().name == presets_mod.DEFAULT_NAME
            assert _active_row(pilot.app) == 0
            await _select_target(pilot, "agent:sisyphus")
            await _select_candidate(pilot, "zhipuai/glm-5")
            await _save_and_confirm(pilot)

    asyncio.run(_run())
    store = presets_mod.load(cfg_path)
    assert store.current().agents["sisyphus"]["model"] == "zhipuai/glm-5"


@pytest.mark.parametrize("count", [3, 12])
def test_pilot_preset_row_never_wraps(pilot_config, count):
    """A name at the CHARACTER cap made of WIDE (2-cell) characters must still render on one
    line: a wrapped row costs a whole extra line of a card capped at half the column, and the
    character cap alone doesn't prevent it (24 CJK chars = 48 cells).

    `count=12` is the case that matters and the one the fixed-3 card could never reach: past the
    height cap the card SCROLLS, and a scrollbar narrows the width rows are wrapped against.
    `size.width` (the content region) does not subtract it — measuring that overflowed every row
    by 2 cells (15 rendered lines for 13 rows). The fix is `scrollbar-gutter: stable` plus
    measuring `scrollable_content_region`; reading the width before `clear_options()` is NOT
    enough, because at the moment the list first outgrows the card the scrollbar isn't there yet.
    12 also crosses into two-digit row numbers, which take a cell off the name budget."""
    cfg_path, _ = pilot_config
    wide = presets_mod.capture("設" * presets_mod.MAX_NAME, {"agents": {}})
    presets_mod.write(
        cfg_path, presets_mod.Store(presets=[wide for _ in range(count)], active=0)
    )

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test(size=(80, 24)) as pilot:
            if len(pilot.app.screen_stack) > 1:
                await pilot.press("y")  # the sync prompt: adopt, we only care about layout
                await pilot.pause()
            lst = pilot.app.query_one("#presets", OptionList)
            rows = lst.option_count  # the presets + the `+ add preset…` row
            assert rows == count + 1
            # The first render happens pre-layout (width 0) — the _PRESET_NAME_CELLS fallback.
            assert lst.virtual_size.height == rows, (
                f"a preset row wrapped pre-layout: {lst.virtual_size.height} lines for {rows} rows"
            )
            # …and the fit is real truncation, not a silent overflow.
            assert str(lst.get_option_at_index(0).prompt).endswith("…")
            # Re-render now that the widget is measured, so the measured branch — the one a CSS
            # change would move — is covered too.
            assert lst.size.width, "the card should be laid out by now"
            pilot.app._populate_presets()
            await pilot.pause()
            assert lst.virtual_size.height == rows, (
                f"a preset row wrapped when measured: {lst.virtual_size.height} lines for {rows} rows"
            )
            if count == 12:
                assert lst.show_vertical_scrollbar, "12 presets must actually overflow the card"

    asyncio.run(_run())


def test_pilot_growing_past_the_card_does_not_wrap(pilot_config):
    """The transition the pre-clear width read gets wrong: the scrollbar appears DURING the
    rebuild that first overflows the card, so any width sampled beforehand is the pre-scrollbar
    one. Grow the store in-session and re-render, the way adding presets actually does."""
    cfg_path, _ = pilot_config
    wide = presets_mod.capture("設" * presets_mod.MAX_NAME, {"agents": {}})
    presets_mod.write(cfg_path, presets_mod.Store(presets=[wide], active=0))

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test(size=(80, 24)) as pilot:
            if len(pilot.app.screen_stack) > 1:
                await pilot.press("y")
                await pilot.pause()
            lst = pilot.app.query_one("#presets", OptionList)
            assert not lst.show_vertical_scrollbar, "one preset must not overflow"
            for _ in range(15):
                pilot.app._store.presets.append(copy.deepcopy(wide))
            pilot.app._populate_presets()
            await pilot.pause()
            assert lst.show_vertical_scrollbar
            assert lst.virtual_size.height == lst.option_count, (
                f"rows wrapped when the scrollbar appeared: {lst.virtual_size.height} lines "
                f"for {lst.option_count} rows"
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot: unlimited presets, `+ add preset…`, and `r` rename
# ---------------------------------------------------------------------------

def test_pilot_presets_are_unlimited(pilot_config):
    """There is no cap: `a` keeps appending, the card scrolls rather than truncating,
    and every one of them survives a save/reload round trip."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            for i in range(11):  # past 9, where the row number takes a second digit
                await _new_preset(pilot, f"p{i}")
            names = [p.name for p in pilot.app._store.presets]
            assert names == ["default"] + [f"p{i}" for i in range(11)]
            labels = _preset_labels(pilot.app)
            assert labels[-1] == "+ add preset…", "the add-preset row stays last"
            # default is row 1, so p9 is row 11 — two-digit numbering still fits the card.
            assert labels[10] == "  11 p9", labels[10]
            assert labels[11].startswith("● 12 p10"), labels[11]
            await _save_and_confirm(pilot)

    asyncio.run(_run())
    assert [p.name for p in presets_mod.load(cfg_path).presets] == ["default"] + [
        f"p{i}" for i in range(11)
    ]


def test_pilot_enter_on_the_new_row_also_creates(pilot_config):
    """`enter` activates every other row in the app; on `+ add preset…` it must mean the same
    thing as `a` rather than being inert."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            lst = pilot.app.query_one("#presets", OptionList)
            await _focus_preset(pilot, lst.option_count - 1)
            await pilot.press("enter")
            await pilot.pause()
            pilot.app.screen.query_one("#preset-name-input", Input).value = "via-enter"
            await pilot.press("enter")
            await pilot.pause()
            assert [p.name for p in pilot.app._store.presets] == ["default", "via-enter"]
            assert _active_row(pilot.app) == 1, "a new preset is the one you're editing"

    asyncio.run(_run())


def test_pilot_r_renames_a_preset(pilot_config):
    """`r` is the fourth key to dispatch on focus (with `a`/`x`/`v`): on a preset row it renames,
    everywhere else it still refreshes the model list. A rename changes the name and NOTHING
    else — the models and the `saved_at` stamp are untouched."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            before = pilot.app._store.presets[0]
            stamp, models = before.saved_at, copy.deepcopy(before.agents)

            await _focus_preset(pilot, 0)
            await pilot.press("r")
            await pilot.pause()
            pilot.app.screen.query_one("#preset-name-input", Input).value = "renamed"
            await pilot.press("enter")
            await pilot.pause()

            after = pilot.app._store.presets[0]
            assert after.name == "renamed"
            assert after.saved_at == stamp, "a rename banks nothing — the stamp stays"
            assert after.agents == models
            assert _preset_labels(pilot.app)[0] == "● 1 renamed"
            assert pilot.app._is_dirty(), "a rename is a change `s` has to write"
            await _save_and_confirm(pilot)

    asyncio.run(_run())
    assert presets_mod.load(cfg_path).presets[0].name == "renamed"


def test_pilot_r_outside_the_card_still_refreshes(pilot_config, monkeypatch):
    """The focus dispatch must not cost anyone the refresh key."""
    cfg_path, _ = pilot_config
    called = []

    async def _run():
        app = _build_app(cfg_path)
        monkeypatch.setattr(app, "_refresh_catalog", lambda: called.append(True))
        async with app.run_test() as pilot:
            pilot.app.query_one("#targets", OptionList).focus()
            await pilot.press("r")
            await pilot.pause()
            assert called, "r outside the presets card is still refresh"

    asyncio.run(_run())


def test_pilot_r_on_the_new_row_does_nothing(pilot_config):
    """`+ add preset…` names no preset, so there is nothing to rename — and it must not fall
    through to a catalog refresh either."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            lst = pilot.app.query_one("#presets", OptionList)
            await _focus_preset(pilot, lst.option_count - 1)
            await pilot.press("r")
            await pilot.pause()
            assert len(pilot.app.screen_stack) == 1, "no modal opened"
            assert not pilot.app._refresh_inflight, "and no refresh started"

    asyncio.run(_run())


def test_pilot_deleting_a_preset_renumbers_the_undo_history(pilot_config):
    """The hazard the dense list introduces: deleting a preset RENUMBERS every later one, while
    the undo history stores which preset each snapshot was made under. Without the remap, `u`
    after a delete would restore models into whichever preset slid into that number."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _new_preset(pilot, "middle")   # index 1
            await _new_preset(pilot, "last")     # index 2, active
            # An edit recorded while `last` (index 2) is active.
            await _select_target(pilot, "agent:oracle")
            await _select_candidate(pilot, "zhipuai/glm-5")
            assert _active_row(pilot.app) == 2

            await _switch_preset(pilot, 0)       # leave `last` so it can be deleted
            await _focus_preset(pilot, 1)        # delete `middle` — everything after shifts down
            await pilot.press("x")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            assert [p.name for p in pilot.app._store.presets] == ["default", "last"]

            # Undo back through the switch: the entry that said "preset 3" must now find `last`
            # at its new index 1, not point past the end and not land on `default`.
            for _ in range(3):
                await pilot.press("u")
                await pilot.pause()
            assert pilot.app._store.current().name == "last", (
                f"undo landed on {pilot.app._store.current().name!r}"
            )

    asyncio.run(_run())


def _actives(app):
    """The active-preset index each history entry was recorded under (-1 == the preset it named
    has since been deleted)."""
    return [e.aux.get("active") for e in app._history._entries]


def test_pilot_arrow_keys_move_between_modal_buttons(pilot_config):
    """Both button modals are a horizontal ROW, but Textual only gives them `tab`. `←`/`→` (and
    vim `h`/`l`) walk the row the way it's laid out, wrapping exactly as tab does — the App's own
    ←/→ are gated to the base screen, so nothing else claims them inside a modal."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:oracle")
            await _select_candidate(pilot, "zhipuai/glm-5")

            # Three-way quit modal.
            await pilot.press("q")
            await pilot.pause()
            assert pilot.app.focused.id == "quit-save"
            for key, want in (
                ("right", "quit-discard"),
                ("right", "quit-cancel"),
                ("right", "quit-save"),     # wraps, like tab
                ("left", "quit-cancel"),    # and backwards
                ("l", "quit-save"),
                ("h", "quit-cancel"),
            ):
                await pilot.press(key)
                await pilot.pause()
                assert pilot.app.focused.id == want, f"{key} -> {pilot.app.focused.id}"
            await pilot.press("escape")
            await pilot.pause()

            # Two-way save-diff confirm.
            await pilot.press("s")
            await pilot.pause()
            assert pilot.app.focused.id == "confirm-yes"
            for key, want in (
                ("right", "confirm-no"),
                ("right", "confirm-yes"),
                ("l", "confirm-no"),
                ("h", "confirm-yes"),
            ):
                await pilot.press(key)
                await pilot.pause()
                assert pilot.app.focused.id == want, f"{key} -> {pilot.app.focused.id}"
            # …and the vertical keys still belong to the scrolling diff body, not the buttons.
            await pilot.press("j")
            await pilot.pause()
            assert pilot.app.focused.id == "confirm-yes", "j must scroll, not move focus"

    asyncio.run(_run())


def test_pilot_modal_emphasis_follows_focus(pilot_config):
    """No button may carry a STATIC `variant="primary"`.

    It paints one button permanently, which competes with Textual's focus styling: two buttons
    look emphasized at once, the eye reads the always-coloured one as the selection, and moving
    along the row appears to do nothing at all. Emphasis has to be the cursor, so it comes from
    `:focus` — exactly one button is lit, and it is the focused one."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:oracle")
            await _select_candidate(pilot, "zhipuai/glm-5")

            async def _check(modal: str):
                buttons = list(pilot.app.screen.query(Button))
                assert buttons, f"{modal} has no buttons"
                assert [b for b in buttons if b.has_focus], f"{modal}: nothing focused"
                for b in buttons:
                    assert b.variant == "default", (
                        f"{modal}: {b.id} carries variant={b.variant!r} — a static emphasis that "
                        f"does not move with focus"
                    )
                # The focused one is the only one whose background differs from the rest.
                lit = {b.id for b in buttons if b.styles.background != buttons[-1].styles.background
                       or b.has_focus}
                assert lit == {b.id for b in buttons if b.has_focus}, lit

            await pilot.press("q")
            await pilot.pause()
            await _check("QuitModal")
            await pilot.press("escape")
            await pilot.pause()

            await pilot.press("s")
            await pilot.pause()
            await _check("ConfirmModal")

    asyncio.run(_run())


def test_pilot_a_new_preset_keeps_the_deleted_markers(pilot_config):
    """A fork must move only the entries recorded on the preset you were SITTING ON.

    It used to stamp one index onto every entry, which erased the markers a prior delete left —
    so undoing into a deleted preset's models landed them in the preset you had just created,
    with no warning at all. That silently voided the guarantee `_delete_preset` exists to make."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            notes = _capture_notifications(pilot)
            await _new_preset(pilot, "b")
            await _select_target(pilot, "agent:oracle")
            await _select_candidate(pilot, "zhipuai/glm-5")
            await _switch_preset(pilot, 0)
            await _focus_preset(pilot, 1)
            await pilot.press("x")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            assert -1 in _actives(pilot.app), _actives(pilot.app)

            await _new_preset(pilot, "fresh")
            assert -1 in _actives(pilot.app), (
                f"the fork erased the deleted markers: {_actives(pilot.app)}"
            )

            notes.clear()
            await pilot.press("u")
            await pilot.pause()
            assert any("was deleted" in m for sev, m in notes if sev == "warning"), notes

    asyncio.run(_run())


def test_pilot_no_deleted_warning_when_the_models_were_already_there(pilot_config):
    """The warning is about models arriving somewhere you didn't choose. Undoing back to a state
    the active preset ALREADY holds moves nothing, so announcing a move is noise — and it named
    as destination the very preset the entry originally pointed at."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            notes = _capture_notifications(pilot)
            await _new_preset(pilot, "b")
            await _select_target(pilot, "agent:oracle")
            await _select_candidate(pilot, "zhipuai/glm-5")
            await _switch_preset(pilot, 0)
            await _focus_preset(pilot, 1)
            await pilot.press("x")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()

            notes.clear()
            for _ in range(4):  # all the way back to the initial state
                await pilot.press("u")
                await pilot.pause()
            warnings = [m for sev, m in notes if sev == "warning" and "was deleted" in m]
            # Exactly one: the step whose models really did land in a preset that isn't theirs.
            assert len(warnings) == 1, warnings
            assert not (pilot.app.cfg.get("agents", {}).get("oracle") or {}).get("model"), (
                "the undo itself must still have gone all the way back"
            )

    asyncio.run(_run())


def test_pilot_a_legacy_three_slot_file_still_opens(pilot_config):
    """A presets file written before presets went unlimited holds exactly three entries with
    `null` for an empty slot, and its `active` indexes THAT list. Opening it must land the user
    on the same preset, not lose one to a refactor."""
    cfg_path, _ = pilot_config
    mine = presets_mod.capture("mine", {"agents": {"sisyphus": {"model": "zhipuai/glm-5"}}})
    import json5

    with open(cfg_path, encoding="utf-8") as f:
        third = presets_mod.capture("third", json5.load(f))
    legacy = {
        "version": presets_mod.FILE_VERSION,
        "active": 2,  # the third slot, with the second empty
        "presets": [
            {"name": p.name, "saved_at": p.saved_at, "agents": p.agents,
             "categories": p.categories}
            if p
            else None
            for p in (mine, None, third)
        ],
    }
    with open(presets_mod.presets_path(cfg_path), "w", encoding="utf-8") as f:
        json.dump(legacy, f)

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            assert len(pilot.app.screen_stack) == 1, "the config matches `third` — no sync prompt"
            assert [p.name for p in pilot.app._store.presets] == ["mine", "third"]
            assert pilot.app._store.current().name == "third", "the hole must not move you"
            assert _active_row(pilot.app) == 1

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pilot: regressions in WHO MOVES THE ● (found in review; each one reproduced first)
#
# `_store.active` is written from four places and only `_record` tells the undo history. Every
# bug below was some other writer getting out of step — and because `_projected_store()` folds
# cfg into whatever `active` points at, a misplaced ● means the next `s` rewrites the WRONG
# preset. These are cheap tests for an expensive class of mistake.
# ---------------------------------------------------------------------------

def test_pilot_forked_preset_survives_a_relaunch(pilot_config):
    """A fork makes a byte-identical duplicate, so at launch the config matches BOTH presets.
    `matching_index` returns the first, so scanning naively would move you back to preset 1 on
    every restart after the commonest flow (fork → save → quit) — with nothing dirty to correct
    it. The recorded `active` wins when it still matches."""
    cfg_path, _ = pilot_config

    async def _run_first():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _new_preset(pilot, "max-power")
            await _save_and_confirm(pilot)

    asyncio.run(_run_first())
    assert presets_mod.load(cfg_path).active == 1

    async def _run_again():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            assert pilot.app._store.active == 1, "the preset you were on must survive a restart"
            assert _active_row(pilot.app) == 1
            assert not pilot.app._is_dirty()

    asyncio.run(_run_again())


def test_pilot_undo_after_a_fork_does_not_move_the_marker(pilot_config):
    """A fork changes `active` without changing cfg, so it pushes no history entry. Unstamped,
    the next `u` applies the PREVIOUS entry's index and quietly moves you off the preset you
    just made — folding the restored models into it."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "cat:deep")
            await _select_candidate(pilot, "openai/gpt-5.5")
            await _new_preset(pilot, "forked")
            assert _active_row(pilot.app) == 1

            await pilot.press("u")
            await pilot.pause()
            assert _active_row(pilot.app) == 1, "an undo must not undo the fork's activation"

    asyncio.run(_run())


def test_pilot_undo_into_a_deleted_preset_says_so(pilot_config):
    """Undo can restore models recorded while a since-deleted preset was active. They have to
    land somewhere, so they land in the active preset — but the user is TOLD, rather than having
    a preset they never touched silently rewritten."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            notes = []
            original = pilot.app.notify
            pilot.app.notify = lambda msg, **kw: (
                notes.append((str(msg), kw.get("severity"))),
                original(msg, **kw),
            )[1]

            await _new_preset(pilot, "cheap")
            await _select_target(pilot, "cat:deep")
            await _select_candidate(pilot, "openai/gpt-5.5")
            await _switch_preset(pilot, 0)
            await _focus_preset(pilot, 1)
            await pilot.press("x")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()

            notes.clear()
            await pilot.press("u")
            await pilot.pause()
            warnings = [m for m, sev in notes if sev == "warning"]
            assert any("was deleted" in m for m in warnings), notes

    asyncio.run(_run())


def test_pilot_switch_to_an_identical_preset_drops_the_redo_tail(pilot_config):
    """Switching between presets holding identical models pushes nothing (cfg is unchanged), so
    the redo tail survived — and `ctrl+r` would resurrect an undone edit AND jump the ● to the
    preset just left. A switch is an action; it invalidates the tail like any other."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _new_preset(pilot, "twin")  # identical to preset 1 by construction
            await _select_target(pilot, "cat:deep")
            await _select_candidate(pilot, "openai/gpt-5.5")
            await pilot.press("u")  # creates a redo tail
            await pilot.pause()
            await _switch_preset(pilot, 0)
            assert not pilot.app._history.can_redo, "a switch must invalidate the redo tail"

            await pilot.press("ctrl+r")
            await pilot.pause()
            assert _active_row(pilot.app) == 0
            assert "deep" not in pilot.app.cfg.get("categories", {})

    asyncio.run(_run())


@pytest.mark.parametrize(
    ("case", "cfg", "keys", "typed"),
    [
        # The one you could hit by accident: one keystroke in the add-model box.
        (
            "typed into add-model",
            {"agents": {"sisyphus": {"model": "zhipuai/glm-5"}}, "categories": {}},
            ["right", "a"],
            "acme/[/b]",
        ),
        # The worse ones: persisted, so they fired on EVERY launch before anything was drawn.
        (
            "model id in the config",
            {"agents": {"sisyphus": {"model": "acme/[/b]"}}, "categories": {}},
            [],
            None,
        ),
        (
            "variant in the config",
            {"agents": {"sisyphus": {"model": "zhipuai/glm-5", "variant": "[/i]"}},
             "categories": {}},
            [],
            None,
        ),
        (
            "category model in the config",
            {"agents": {}, "categories": {"deep": {"model": "acme/[/u]"}}},
            [],
            None,
        ),
    ],
)
def test_pilot_markup_shaped_data_does_not_crash(tmp_path, case, cfg, keys, typed):
    """Textual parses content markup in every plain string it renders — an `Option` prompt, a
    `Static`, a toast. Model ids, variants and typed input are all data we don't control, so a
    `[` in one was an opening tag and an unmatched close (`acme/[/b]`) raised MarkupError from
    inside the render pass, where no call site can catch it. Widgets carrying data are now built
    `markup=False`, option prompts are literal `Content`, and `#detail` (the one widget that
    renders markup on purpose) escapes what it splices in."""
    cfg_path = str(tmp_path / "oh-my-openagent.jsonc")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            for key in keys:
                await pilot.press(key)
                await pilot.pause()
            if typed is not None:
                pilot.app.screen.query_one("#add-input", Input).value = typed
                await pilot.pause()

    asyncio.run(_run())  # the assertion IS "no MarkupError escaped the render pass"


def test_pilot_markup_shaped_data_renders_literally(tmp_path):
    """Not crashing isn't enough: a WELL-FORMED tag (`[red]…[/red]`) parses fine and would
    silently vanish into styling, so an id you can't run would read as one you can. Every route
    that shows a model id must show its brackets."""
    cfg_path = str(tmp_path / "oh-my-openagent.jsonc")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(
            {"agents": {"sisyphus": {"model": "acme/[red]glm[/red]", "variant": "[b]hi"}},
             "categories": {}},
            f,
        )

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            # `.content` is the PRE-parse string, so run Textual's own markup parse over
            # it to get what the pane actually shows (#detail is the one markup widget).
            raw = str(pilot.app.query_one("#detail", Static).content)
            detail = Content.from_markup(raw).plain
            assert "acme/[red]glm[/red]" in detail, detail
            assert "[b]hi" in detail, detail
            cands = pilot.app.query_one("#candidates", OptionList)
            labels = [
                str(cands.get_option_at_index(i).prompt) for i in range(cands.option_count)
            ]
            assert any("acme/[red]glm[/red]" in label for label in labels), labels

    asyncio.run(_run())


def test_pilot_notify_renders_markup_literally(pilot_config):
    """Toasts quote model ids, preset names, undo labels and `str(exc)` (file paths) — ~20 call
    sites, any of which can carry a `[`. `OModelApp.notify` defaults `markup=False` so none of
    them has to remember: a toast must never take the app down while reporting something."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            pilot.app.notify("Save failed: /tmp/[/b].jsonc")  # would raise MarkupError
            await pilot.pause()
            # The toast rack isn't mounted headless, so assert on the queued Notification —
            # `markup=False` there IS what stops the parse, and the message stays verbatim.
            note = list(pilot.app._notifications)[-1]
            assert note.markup is False, note
            assert note.message == "Save failed: /tmp/[/b].jsonc", note

            # …and a caller that means it can still opt back in.
            pilot.app.notify("plain", markup=True)
            await pilot.pause()
            assert list(pilot.app._notifications)[-1].markup is True

    asyncio.run(_run())


def test_pilot_ctrl_c_hint_is_literal_and_names_q(pilot_config):
    """`ctrl+c` is Textual's, not ours: its `action_help_quit` toasts
    `f"Press [b]{key}[/b] to quit the app"`. Two things go wrong unhandled — `notify` above
    defaults `markup=False`, so the tags render as literal `[b]`/`[/b]`; and `key` resolves to
    Textual's default `ctrl+q` binding, which exits via `App.action_quit` WITHOUT the
    unsaved-changes prompt. The override says `q` in plain text."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+c")
            await pilot.pause()
            note = list(pilot.app._notifications)[-1]
            assert "[b]" not in note.message and "[/b]" not in note.message, note
            assert note.message == "Press q to quit the app", note
            # …and it points at the quit that can save, not Textual's bare ctrl+q.
            assert "ctrl+q" not in note.message, note
            assert pilot.app.is_running, "ctrl+c must hint, never exit"

    asyncio.run(_run())


def test_pilot_a_markup_shaped_preset_name_does_not_crash(pilot_config):
    """Textual parses plain strings as content markup, so a preset named `[/b]` raised
    MarkupError from the compositor — and being persisted, it took the app down on EVERY launch
    afterwards. Names are stripped of brackets on the way in and on the way back off disk."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _new_preset(pilot, "[/b]")
            assert "[" not in "".join(_preset_labels(pilot.app))
            await _save_and_confirm(pilot)

    asyncio.run(_run())

    # …and a hand-edited file carrying markup is survivable too: it must load and render.
    store = presets_mod.load(cfg_path)
    store.presets[0].name = "[b]hand edited"
    with open(presets_mod.presets_path(cfg_path), "w", encoding="utf-8") as f:
        f.write(presets_mod._payload(store))

    async def _run_again():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            assert "[" not in "".join(_preset_labels(pilot.app))

    asyncio.run(_run_again())


def test_pilot_tab_into_the_presets_card_lands_on_a_row(pilot_config):
    """`tab` is documented as an equal way into the card, but Textual's OptionList does not
    auto-highlight on focus — an unseeded card swallowed `enter`/`a`/`x` entirely. The highlight
    is seeded when the rows are built, so both routes work."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            pilot.app.query_one("#targets", OptionList).focus()
            await pilot.press("tab")
            await pilot.pause()
            lst = pilot.app.query_one("#presets", OptionList)
            assert pilot.app.focused is lst
            assert lst.highlighted is not None, "tab must land on a live row"
            # …and a key that acts on the highlighted row actually does something.
            await pilot.press("a")
            await pilot.pause()
            assert pilot.app.screen.query_one("#preset-name-input", Input)

    asyncio.run(_run())


def test_pilot_tab_cycles_all_three_panes_both_ways(pilot_config):
    """`tab` / `shift+tab` are the ONLY route into `#presets` — there is deliberately no `p` (or
    any other) dedicated key, and `←`/`→` reach targets/candidates only. So the wrap-around
    traversal is load-bearing, not a Textual detail: pin the order and both directions."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            pilot.app.query_one("#targets", OptionList).focus()
            await pilot.pause()

            seen = []
            for _ in range(4):  # one full cycle plus the wrap back into #presets
                await pilot.press("tab")
                await pilot.pause()
                seen.append(pilot.app.focused.id)
            assert seen == ["presets", "candidates", "targets", "presets"], seen

            await pilot.press("shift+tab")
            await pilot.pause()
            assert pilot.app.focused.id == "targets", "shift+tab must walk back out"

            # …and the pane keys still skip the card, which is why tab has to reach it.
            for key, want in (("left", "targets"), ("right", "candidates")):
                await pilot.press(key)
                await pilot.pause()
                assert pilot.app.focused.id == want

    asyncio.run(_run())


def test_pilot_no_p_binding(pilot_config):
    """`p` shipped briefly as a presets shortcut and was removed as duplication of `tab`. It must
    stay unbound (and inert), so it's free for a future key."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            assert not [b for b in app.BINDINGS if b.key == "p"], app.BINDINGS
            targets = pilot.app.query_one("#targets", OptionList)
            targets.focus()
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause()
            assert pilot.app.focused is targets, "p must not move focus"

    asyncio.run(_run())


def test_pilot_escape_on_the_sync_prompt_changes_nothing(pilot_config):
    """`esc` used to dismiss False, which the sync prompt read as "restore the preset" —
    silently rewriting the config the user had just changed outside oModel, under a hint line
    that said "esc cancel"."""
    cfg_path, _ = pilot_config
    other = presets_mod.capture("mine", {"agents": {"sisyphus": {"model": "zhipuai/glm-5"}}})
    presets_mod.write(cfg_path, presets_mod.Store(presets=[other], active=0))

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            assert isinstance(pilot.app.screen, ConfirmModal)
            before = copy.deepcopy(pilot.app.cfg)
            await pilot.press("escape")
            await pilot.pause()
            assert pilot.app.cfg == before, "esc must not rewrite the config"
            assert len(pilot.app.screen_stack) == 1

    asyncio.run(_run())


def test_pilot_refresh_actually_re_resolves_the_chain(pilot_config, monkeypatch):
    """`r` must rebuild the PICK LIST against the refreshed catalog, not just the header.

    Regression guard for the session extraction: `catalog` / `resolver` / `catalog_error` are
    reassigned by `_refresh_catalog`, and `_build_rows` delegates to `session.rows()`. When those
    three were plain attributes copied in `__init__` instead of properties onto the session, a
    refresh updated the app and left the session holding the pre-refresh resolver — so
    `#providers` and the add-model modal went fresh while the chain silently kept resolving
    against the OLD catalog, which is the one thing `r` exists to do.

    Deliberately hands the worker a catalog that is BIGGER than the original (the newly-connected
    -provider case). `test_pilot_candidate_highlight_survives_refresh` above passes an equivalent
    catalog, so a stale resolver produces identical rows there and the bug survives it.
    """
    cfg_path, _ = pilot_config

    async def _run():
        # Start with ONLY opencode connected — as if zhipuai/openai weren't logged in yet.
        narrow = Catalog(
            available={"opencode": ["claude-opus-4-7"]},
            connected=["opencode"],
        )
        app = _build_app_with(cfg_path, narrow)

        from omodel import app as app_mod

        # ...then the user runs `opencode auth login` elsewhere and presses `r`.
        widened = Catalog(
            available={
                "opencode": ["claude-opus-4-7", "glm-5", "gpt-5.5"],
                "zhipuai": ["glm-5"],
                "openai": ["gpt-5.5"],
            },
            connected=["opencode", "zhipuai", "openai"],
        )
        monkeypatch.setattr(app_mod.catalog_mod, "refresh", lambda *a, **k: widened)

        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            before = _candidate_prompts(pilot)
            assert not any("zhipuai/glm-5" in p for p in before), (
                "precondition: zhipuai isn't connected yet"
            )

            await pilot.press("r")
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

            after = _candidate_prompts(pilot)
            assert any("zhipuai/glm-5" in p for p in after), (
                "after `r` the pick list must offer models from the newly-connected provider; "
                f"still showing {after!r}"
            )
            assert any("openai/gpt-5.5" in p for p in after)
            # The app and the session must be looking at the SAME catalog.
            assert pilot.app.catalog.connected == pilot.app.session.catalog.connected
            assert pilot.app.resolver is pilot.app.session.resolver

    asyncio.run(_run())


def _candidate_prompts(pilot) -> list:
    cands = pilot.app.query_one("#candidates", OptionList)
    return [str(cands.get_option_at_index(i).prompt) for i in range(cands.option_count)]


def test_pilot_survives_a_non_dict_agents_map(tmp_path, monkeypatch):
    """A truthy non-dict `agents` must not kill the app during the first render.

    `(cfg.get("agents") or {})` rescues `null` but NOT `"oops"`, so `_agent_subtargets` called
    `.get` on a str and the app died on launch — while `omodel check` reported the same config
    healthy. The CLI hardening made that inconsistency worse, not better: an agent would call it
    fine and the user still couldn't open the TUI.
    """
    cfg_path = tmp_path / "oh-my-openagent.jsonc"
    cfg_path.write_text('{"agents": "oops", "categories": {}}', encoding="utf-8")

    async def _run():
        app = _build_app(str(cfg_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            targets = pilot.app.query_one("#targets", OptionList)
            assert targets.option_count > 0, "the target list still renders from bundled omo data"
            # And an edit repairs the map rather than merely surviving it.
            await _select_target(pilot, "agent:sisyphus")
            assert pilot.app._agent_subtargets("sisyphus") == []

    asyncio.run(_run())
