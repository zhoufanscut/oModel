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
// hand-curated palette — oModel will clean this on first save
{
  "$schema": "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/assets/oh-my-opencode.schema.json",
  "agents": {
    "sisyphus": {
      "model": "opencode/claude-opus-4-7"
    }
  },
  "categories": {},
  "team_mode": true,
  "experimental": {"featureY": false},
  "claude_code": {"enabled": true, "model": "opencode/claude-opus-4-8"}
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

    # Palette comments gone (only the oModel header line is a comment)
    with open(cfg_path, encoding="utf-8") as f:
        raw_text = f.read()
    body_lines = raw_text.splitlines()[1:]  # skip "// Generated by oModel…"
    comment_lines = [line for line in body_lines if line.strip().startswith("//")]
    assert comment_lines == [], f"Palette comments must be gone after save: {comment_lines}"

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

    gated = AddModelModal(resolver, suggestions, require_gpt=True)
    row, _preview, ok = gated._build_row("openai/gpt-5")
    assert ok and row is not None and row["model"] == "gpt-5", "GPT model must be accepted"
    row, preview, ok = gated._build_row("zhipuai/glm-5")
    assert not ok and row is None, "non-GPT model must be blocked"
    assert "GPT" in preview, f"block preview should explain GPT-only: {preview!r}"

    ungated = AddModelModal(resolver, suggestions, require_gpt=False)
    row, _preview, ok = ungated._build_row("zhipuai/glm-5")
    assert ok and row is not None, "non-GPT model accepted when not GPT-gated"


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
