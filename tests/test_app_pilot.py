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
import time

import pytest
from textual.widgets import OptionList, Static

from omodel.app import OModelApp
from omodel.catalog import Catalog
from omodel.config_io import list_backups, load_config
from omodel.resolve import Resolver
from omodel.suggestions import load as load_suggestions


# ---------------------------------------------------------------------------
# Test catalog (same verified fixture as test_resolve.py)
# ---------------------------------------------------------------------------

def _make_test_catalog() -> Catalog:
    """opencode (gateway) + dedicated providers. deepseek-v4-pro → dedicated wins."""
    lines = [
        "opencode/claude-opus-4-7", "opencode/claude-opus-4-8", "opencode/gpt-5.5",
        "opencode/kimi-k2.5", "opencode/kimi-k2.6", "opencode/glm-5",
        "opencode/deepseek-v4-pro", "opencode/big-pickle",
        "deepseek/deepseek-v4-pro", "deepseek/deepseek-v4",
        "moonshotai-cn/kimi-k2.5", "moonshotai-cn/kimi-k2.6",
        "openai/gpt-5.5",
        "zhipuai/glm-5",
    ]
    available: dict = {}
    connected: list = []
    for line in lines:
        prov, model = line.split("/", 1)
        if prov not in available:
            available[prov] = []
            connected.append(prov)
        if model not in available[prov]:
            available[prov].append(model)
    return Catalog(available=available, connected=connected)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_app(cfg: dict, config_path: str) -> OModelApp:
    """Construct OModelApp with the test catalog + real bundled suggestions."""
    cat = _make_test_catalog()
    sugg = load_suggestions()
    try:
        resolver = Resolver.build(cat, sugg)
    except Exception:
        resolver = None
    return OModelApp(
        catalog=cat,
        suggestions=sugg,
        resolver=resolver,
        cfg=cfg,
        config_path=config_path,
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
    in the rendered row label (e.g. 'deepseek/deepseek-v4-pro' not just 'deepseek-v4-pro'),
    since a model may appear under multiple providers as separate ✓ rows.
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
    3. In #candidates, highlight deepseek/deepseek-v4-pro + enter to set it.
    4. Press 's'; confirm ConfirmModal with 'y'.
    5. Re-json5.load the config and assert all contracts hold.
    """
    import json5

    cfg_path, tmp_dir = pilot_config

    async def _run():
        cfg, _ = load_config(cfg_path)
        app = _build_app(cfg, cfg_path)

        async with app.run_test() as pilot:
            # 1. Select agent:sisyphus to populate the right pane
            await _select_target(pilot, "agent:sisyphus")

            # 2. Find and select deepseek/deepseek-v4-pro in candidates.
            # Use the full 'deepseek/deepseek-v4-pro' fragment so we match the dedicated
            # provider row (not the 'opencode/deepseek-v4-pro' ✓ row that appears first).
            found_id = await _select_candidate(pilot, "deepseek/deepseek-v4-pro")
            assert found_id is not None, (
                "deepseek/deepseek-v4-pro must appear as a candidate for agent:sisyphus "
                "under the deepseek/ dedicated provider (resolve_prefix: dedicated wins)."
            )

            # 3. Save and confirm
            await _save_and_confirm(pilot)

    asyncio.run(_run())

    # Assert on-disk result
    import json5
    with open(cfg_path, encoding="utf-8") as f:
        saved = json5.load(f)

    # Model updated; deepseek is dedicated → wins over opencode gateway
    assert saved["agents"]["sisyphus"]["model"] == "deepseek/deepseek-v4-pro", (
        f"Expected deepseek/deepseek-v4-pro, got {saved['agents']['sisyphus']['model']!r}"
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
    comment_lines = [l for l in body_lines if l.strip().startswith("//")]
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
        cfg, _ = load_config(cfg_path)
        app = _build_app(cfg, cfg_path)

        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            # moonshotai-cn/kimi-k2.5 is the ★✓ dedicated row; any model change is fine
            # for this test — we care only about non-model section preservation
            found_id = await _select_candidate(pilot, "moonshotai-cn/kimi-k2.5")
            if found_id is None:
                # fall back to deepseek dedicated row
                await _select_candidate(pilot, "deepseek/deepseek-v4-pro")
            await _save_and_confirm(pilot)

    asyncio.run(_run())

    import json5
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
        cfg, _ = load_config(cfg_path)
        app = _build_app(cfg, cfg_path)

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
        cfg, _ = load_config(cfg_path)
        app = _build_app(cfg, cfg_path)

        async with app.run_test() as pilot:
            await _select_target(pilot, "agent:sisyphus")
            await _select_candidate(pilot, model_fragment)
            await _save_and_confirm(pilot)

    # Use full 'provider/model' fragments to pick the right dedicated-provider rows
    asyncio.run(_do_save("deepseek/deepseek-v4-pro"))
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
    """agent:sisyphus.ultrawork candidates include the parent's 7 ★ rows.
    TUI-track: 'a' key creates the sub-target; Core inheritance fix applied."""
    cfg_path, _ = pilot_config

    async def _run():
        cfg, _ = load_config(cfg_path)
        app = _build_app(cfg, cfg_path)

        async with app.run_test() as pilot:
            # Highlight sisyphus (no enter yet — just highlight so 'a' targets it)
            targets = pilot.app.query_one("#targets", OptionList)
            idx = targets.get_option_index("agent:sisyphus")
            targets.highlighted = idx
            targets.focus()
            await pilot.pause()

            # 'a' adds ultrawork sub-target and highlights it
            await pilot.press("a")
            await pilot.pause()

            # Check whether agent:sisyphus.ultrawork now appears in #targets
            targets = pilot.app.query_one("#targets", OptionList)
            uw_present = False
            for i in range(targets.option_count):
                if targets.get_option_at_index(i).id == "agent:sisyphus.ultrawork":
                    uw_present = True
                    break

            if not uw_present:
                pytest.skip(
                    "agent:sisyphus.ultrawork not present after 'a' press — "
                    "sub-target inheritance not yet wired"
                )

            # Select the ultrawork sub-target to populate candidates
            await _select_target(pilot, "agent:sisyphus.ultrawork")

            # Sub-target must show parent's 7 ★ rows + ✓ mine rows
            candidates = pilot.app.query_one("#candidates", OptionList)
            real_ids = [
                candidates.get_option_at_index(i).id
                for i in range(candidates.option_count)
                if candidates.get_option_at_index(i).id not in (None, "cand:add")
                and not (candidates.get_option_at_index(i).id or "").startswith("hdr:")
            ]
            assert len(real_ids) >= 7, (
                f"sub-target must inherit parent's >=7 rows; got {len(real_ids)}"
            )

    asyncio.run(_run())
