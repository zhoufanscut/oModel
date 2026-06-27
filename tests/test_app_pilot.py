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
import glob
import os
import subprocess
import time
import types

import pytest
from textual.widgets import Input, OptionList, Static

from omodel.app import OModelApp
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
# Pilot test 2: non-model sections unchanged by value
# ---------------------------------------------------------------------------

def test_pilot_non_model_sections_unchanged(pilot_config):
    """After a save, team_mode / experimental / claude_code are unchanged by value."""
    import json5

    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)

        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            # moonshotai-cn/kimi-k2.5 is the dedicated-resolved chain row; any model change
            # is fine for this test — we care only about non-model section preservation
            found_id = await _select_candidate(pilot, "moonshotai-cn/kimi-k2.5")
            if found_id is None:
                # fall back to deepseek dedicated row
                await _select_candidate(pilot, "deepseek/deepseek-v4-pro")
            await _save_and_confirm(pilot)

    asyncio.run(_run())

    with open(cfg_path, encoding="utf-8") as f:
        saved = json5.load(f)

    # Non-model sections unchanged regardless of which model was set
    assert saved["team_mode"] is True
    assert saved["experimental"] == {"featureY": False}
    assert saved["claude_code"]["enabled"] is True
    assert saved["claude_code"]["model"] == "opencode/claude-opus-4-8"


# ---------------------------------------------------------------------------
# Pilot test 3: Providers header shows connected providers in first-seen order
# ---------------------------------------------------------------------------

def test_pilot_providers_header_visible(pilot_config):
    """Static#providers renders 'Providers: <id · id · …>' from catalog.connected."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)

        async with app.run_test() as pilot:
            providers_widget = pilot.app.query_one("#providers", Static)
            # Static.content is the canonical way to read the current display value
            text = str(providers_widget.content)
            assert "Providers:" in text, f"Missing 'Providers:' in header: {text!r}"
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
            if not uw_present:
                pytest.skip(
                    "agent:sisyphus.ultrawork not present after 'a' press — "
                    "sub-target inheritance not yet wired"
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
    from omodel.app import AddModelModal
    from omodel import suggestions as suggestions_mod

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
# Pilot test 9: pane-aware hint bar + ←/→ pane crossing
# ---------------------------------------------------------------------------

def test_pilot_hint_bar_pane_aware(pilot_config):
    """Static#hints shows only the keys valid for the focused pane + highlighted row, and
    ←/→ move focus between the targets and candidates panes."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            # Left pane, an AGENT highlighted → 'a sub' + '→ candidates', no candidate-only keys.
            await _select_target(pilot, "agent:sisyphus")
            txt = str(pilot.app.query_one("#hints", Static).content)
            assert "→ candidates" in txt, f"left-pane hints must point right: {txt!r}"
            assert "a sub" in txt, f"agent row must advertise 'a sub': {txt!r}"
            assert "enter set" not in txt, f"candidate-only key shown on left pane: {txt!r}"

            # → crosses to the candidates pane.
            await pilot.press("right")
            await pilot.pause()
            cands = pilot.app.query_one("#candidates", OptionList)
            assert pilot.app.focused is cands, "→ must move focus to the candidates pane"

            # ↓ highlights a real candidate row → real-row hints. Asserting *after* a real row
            # is highlighted (not via the highlighted-is-None fallback) proves the real branch.
            await pilot.press("down")
            await pilot.pause()
            assert cands.highlighted is not None
            assert cands.get_option_at_index(cands.highlighted).id != "cand:add"
            txt = str(pilot.app.query_one("#hints", Static).content)
            assert "← targets" in txt, f"right-pane hints must point left: {txt!r}"
            assert "enter set" in txt and "v variant" in txt, f"real-row keys missing: {txt!r}"

            # The '+ add model…' row repurposes enter and drops the model-only keys — this
            # exercises _candidate_highlighted + the cand:add branch of _render_hints.
            cands.highlighted = cands.option_count - 1
            await pilot.pause()
            assert cands.get_option_at_index(cands.highlighted).id == "cand:add"
            txt = str(pilot.app.query_one("#hints", Static).content)
            assert "enter add" in txt, f"add-model row must show 'enter add': {txt!r}"
            assert "v variant" not in txt and "x clear" not in txt, (
                f"add-model row must drop candidate-only keys: {txt!r}"
            )

            # ← crosses back to targets.
            await pilot.press("left")
            await pilot.pause()
            assert pilot.app.focused is pilot.app.query_one("#targets", OptionList), (
                "← must move focus back to the targets pane"
            )

            # A CATEGORY row swaps 'a sub' for 'a edit' — no sub-targets, so `a` opens the model modal.
            cat_name = next(iter(pilot.app.suggestions.categories.keys()))
            await _select_target(pilot, f"cat:{cat_name}")
            txt = str(pilot.app.query_one("#hints", Static).content)
            assert "→ candidates" in txt, f"category hints must still point right: {txt!r}"
            assert "a sub" not in txt, f"category row must not advertise 'a sub': {txt!r}"
            assert "a edit" in txt, f"category row must advertise 'a edit': {txt!r}"

    asyncio.run(_run())


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


def test_pilot_undo_hint_appears_after_edit(pilot_config):
    """`u undo` is absent from the hint bar until there's something to undo; `⌃r redo` shows
    only after an undo opens a redo. (Keeps the one-line bar minimal — DESIGN §Layout.)"""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            txt = str(pilot.app.query_one("#hints", Static).content)
            assert "u undo" not in txt, f"no edits yet → no undo hint: {txt!r}"

            await _select_candidate(pilot, "zhipuai/glm-5")
            txt = str(pilot.app.query_one("#hints", Static).content)
            assert "u undo" in txt, f"after an edit the undo hint must show: {txt!r}"

            await pilot.press("u")
            await pilot.pause()
            txt = str(pilot.app.query_one("#hints", Static).content)
            assert "⌃r redo" in txt, f"after an undo the redo hint must show: {txt!r}"

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


def test_pilot_redo_keeps_custom_added_row(pilot_config):
    """A model typed in the add-model modal is an off-chain row stored in _custom_rows, which
    survives undo/redo: after add → undo → redo, the typed model is the assignment AND comes
    back as a `●`-marked candidate row (not just a bare cfg value)."""
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

            await pilot.press("u")  # undo the add → assignment reverts
            await pilot.pause()
            assert pilot.app.cfg["agents"]["sisyphus"]["model"] == "opencode/claude-opus-4-7"

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

            await pilot.press("a")  # chooser
            await pilot.pause()
            await pilot.press("u")  # → add ultrawork
            await pilot.pause()
            assert f"{target}.ultrawork" in _ids(targets)

            await pilot.press("u")  # app-level undo → remove the sub-target
            await pilot.pause()
            assert f"{target}.ultrawork" not in _ids(targets)
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
    binding so the key drives the list instead)."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
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


def test_pilot_addmodal_select_enters_variant_phase(pilot_config):
    """Choosing a model whose family declares variants enters the variant phase: #add-variants is
    visible + focused listing the family's variants + (none); picking one sets the assignment's
    variant alongside the resolved provider/model."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "glm"
            await pilot.pause()
            await pilot.press("enter")  # choose highlighted zhipuai/glm-5 → variant phase
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
            assert node["model"] == "zhipuai/glm-5", node
            assert node.get("variant") == "high", node

    asyncio.run(_run())


def test_pilot_addmodal_variant_skipped_for_familyless(pilot_config):
    """A model whose family declares no variants (or has no family) skips the variant phase: a
    single Enter adds it with no variant key. Covers a custom (family-less) id AND a qwen id
    (a real family whose `variants` list is empty)."""
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
    """Esc in the variant phase returns to the model phase (Input visible + focused); a second Esc
    cancels the modal, leaving the assignment untouched."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            before = pilot.app.cfg["agents"]["sisyphus"].get("model")
            inp.value = "glm"
            await pilot.pause()
            await pilot.press("enter")  # → variant phase
            await pilot.pause()
            variants = pilot.app.screen.query_one("#add-variants", OptionList)
            assert variants.display and pilot.app.focused is variants

            await pilot.press("escape")  # back to the model phase
            await pilot.pause()
            inp = pilot.app.screen.query_one("#add-input", Input)
            assert inp.display, "Esc from the variant phase must restore the Input"
            assert pilot.app.focused is inp, "the model phase must re-focus the Input"
            assert not pilot.app.screen.query_one("#add-variants", OptionList).display

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
    from omodel.app import AddModelModal
    from omodel import suggestions as suggestions_mod

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
    full provider/model still stages a synthetic row warned 'unavailable', and Enter adds it."""
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

            # A typed full id for a model nobody serves still stages a (warned) row.
            inp.value = "openrouter/zzz-custom"
            await pilot.pause()
            assert cands.option_count == 1, _add_candidate_labels(pilot)
            assert "unavailable" in _add_candidate_labels(pilot)[0]
            assert scr._staged is not None and scr._staged["warn"] == ["unavailable"]

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


def test_pilot_addmodal_esc_back_preserves_input_value(pilot_config):
    """Esc from the variant phase returns to the model phase with the Input's typed value intact,
    the candidate list visible again, and the variant list hidden (extends the esc-back test with
    value + candidate-visibility assertions)."""
    cfg_path, _ = pilot_config

    async def _run():
        app = _build_app(cfg_path)
        async with app.run_test() as pilot:
            inp = await _open_add_modal(pilot)
            inp.value = "glm"
            await pilot.pause()
            await pilot.press("enter")  # → variant phase
            await pilot.pause()
            scr = pilot.app.screen
            assert scr.query_one("#add-variants", OptionList).display

            await pilot.press("escape")  # back to model phase
            await pilot.pause()
            inp = scr.query_one("#add-input", Input)
            assert inp.display and pilot.app.focused is inp
            assert inp.value == "glm", f"the typed value must survive esc-back: {inp.value!r}"
            assert scr.query_one("#add-candidates", OptionList).display, "candidate list back"
            assert not scr.query_one("#add-variants", OptionList).display, "variant list hidden"

    asyncio.run(_run())


def test_pilot_addmodal_open_then_type_selects(pilot_config):
    """Type-to-search F1: opening renders no list and stages nothing, so a reflexive Enter right
    after opening is a no-op (modal stays, no assignment). Typing surfaces the fuzzy list and
    auto-stages the top (dedicated-first) row; Enter selects it — deepseek/deepseek-v4-pro has
    variants, so it enters the variant phase, and picking one commits."""
    cfg_path, _ = pilot_config

    async def _run():
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
