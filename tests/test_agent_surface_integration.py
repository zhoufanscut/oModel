"""test_agent_surface_integration.py — the seam between the two surfaces.

`cli.py` (the agent surface) and `app.py` (the TUI) now edit through the same `session.Session`,
which is exactly why they can disagree in a way neither file's own tests would catch: test_cli.py
never imports Textual, and test_app_pilot.py never calls the CLI. These tests write with one
surface and open with the other.

Two properties are pinned here, both of which only exist ACROSS the seam:

  * **The preset invariant survives a CLI write.** After any mutating verb, opening the TUI must
    show no sync conflict and nothing dirty — otherwise the user's next launch asks "something
    else wrote your config", pointing at omodel itself.
  * **A CLI-written value renders.** Textual parses content markup in every plain string it
    draws, so a `[` in a model id or variant is an opening tag and an unmatched close raises
    MarkupError from INSIDE the render pass, where no call site can catch it (DESIGN §Textual
    contract). `--force` makes the CLI a writer of arbitrary ids, so it is a new route to that
    crash; test_app_pilot.py covers hand-written configs, not CLI-written ones.

Conventions as elsewhere: explicit temp `--config`, `subprocess.run` stubbed (a real `opencode`
is on PATH and costs ~3s / ~320 MB a call), cache/config dirs isolated by conftest.py.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import types
from unittest.mock import MagicMock, patch

import pytest

from omodel import cli

MOCK_MODELS_OUTPUT = "opencode/glm-5\nzhipuai/glm-5\nopencode/gpt-5.5\n"

SEED_JSONC = """\
// keep me — text outside agents/categories is byte-preserved
{
  "agents": {
    "sisyphus": {"model": "opencode/glm-5"}
  },
  "categories": {
    "quick": {"model": "zhipuai/glm-5"}
  },
  "somethingElse": {"keep": true}
}
"""


@pytest.fixture(autouse=True)
def _no_real_opencode(monkeypatch):
    """Hard rule: no test calls the real opencode CLI. Both surfaces reach catalog.load(), and
    the TUI's detail worker spawns `--verbose` (~320 MB) from a thread that outlives the test."""
    def _stub(*args, **kwargs):
        return types.SimpleNamespace(returncode=0, stdout=MOCK_MODELS_OUTPUT, stderr="")

    monkeypatch.setattr(subprocess, "run", _stub)


def _cfg(tmp_path, text: str = SEED_JSONC) -> str:
    path = tmp_path / "oh-my-openagent.jsonc"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return str(path)


def _run_cli(argv) -> int:
    """cli.main with `opencode` present and its output stubbed."""
    m = MagicMock()
    m.returncode, m.stdout, m.stderr = 0, MOCK_MODELS_OUTPUT, ""
    with patch("shutil.which", return_value="/usr/bin/opencode"), \
         patch("subprocess.run", return_value=m):
        return cli.main(argv)


def _open_tui(cfg_path: str, walk: bool = True) -> tuple:
    """Open the TUI on `cfg_path` through the production entry point and actually RENDER it.

    Returns `(sync_conflict, dirty)`. Constructing the app is not enough for the markup half:
    MarkupError is raised during the render pass, so every pane has to be drawn — hence walking
    the target list, which redraws #candidates and #detail for each row."""
    from textual.widgets import OptionList

    from omodel.app import create_app

    result = {}

    async def _run():
        app = create_app(cfg_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            if walk:
                targets = app.query_one("#targets", OptionList)
                for i in range(targets.option_count):
                    targets.highlighted = i
                    await pilot.pause()
                presets = app.query_one("#presets", OptionList)
                for i in range(presets.option_count):
                    presets.highlighted = i
                    await pilot.pause()
            result["state"] = (app._sync_conflict, app._is_dirty())

    asyncio.run(_run())
    return result["state"]


# ---------------------------------------------------------------------------
# The preset invariant, observed from the other surface
# ---------------------------------------------------------------------------

class TestCliWriteOpensCleanInTheTui:
    """"The config on disk always equals the active preset" (decision #17) is only really
    proven by the surface that CHECKS it — the TUI reconciles at launch and prompts when the
    two disagree. A CLI verb that wrote one file and not the other would surface here as a
    sync-conflict prompt on the user's next launch."""

    def test_set_leaves_no_conflict(self, tmp_path):
        cfg = _cfg(tmp_path)
        assert _run_cli(["set", "cat:quick", "opencode/glm-5", "--config", cfg]) == 0
        assert _open_tui(cfg) == (False, False)

    def test_clear_leaves_no_conflict(self, tmp_path):
        cfg = _cfg(tmp_path)
        assert _run_cli(["clear", "cat:quick", "--config", cfg]) == 0
        assert _open_tui(cfg) == (False, False)

    def test_apply_leaves_no_conflict(self, tmp_path, monkeypatch):
        import io
        cfg = _cfg(tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({
            "agent:sisyphus": {"model": "zhipuai/glm-5"},
            "cat:quick": "opencode/glm-5",
        })))
        assert _run_cli(["apply", "--config", cfg]) == 0
        assert _open_tui(cfg) == (False, False)

    def test_preset_new_then_use_leaves_no_conflict(self, tmp_path):
        """`new` writes only the presets file and `use` rewrites the config — the two verbs
        that touch the files asymmetrically, so the invariant is easiest to break here."""
        cfg = _cfg(tmp_path)
        assert _run_cli(["preset", "new", "alt", "--config", cfg]) == 0
        assert _run_cli(["set", "cat:quick", "opencode/glm-5", "--config", cfg]) == 0
        assert _run_cli(["preset", "use", "1", "--config", cfg]) == 0
        assert _open_tui(cfg) == (False, False)

    def test_preset_rm_renumbers_without_stranding_the_config(self, tmp_path):
        """The dense list renumbers on delete; if `active` weren't decremented with it the
        config would end up equal to a preset that is no longer the active one."""
        cfg = _cfg(tmp_path)
        assert _run_cli(["preset", "new", "one", "--config", cfg]) == 0
        assert _run_cli(["preset", "new", "two", "--config", cfg]) == 0
        assert _run_cli(["preset", "rm", "one", "--config", cfg]) == 0
        assert _open_tui(cfg) == (False, False)

    def test_a_dry_run_does_not_strand_the_presets_file(self, tmp_path):
        """A dry run writes neither file — including the seeded presets file, whose absence is
        what keeps the next real save honest."""
        import os
        cfg = _cfg(tmp_path)
        assert _run_cli(["set", "cat:quick", "opencode/glm-5", "--dry-run", "--config", cfg]) == 0
        assert not os.path.exists(os.path.join(str(tmp_path), ".omodel-presets.json"))
        assert _open_tui(cfg) == (False, False)


# ---------------------------------------------------------------------------
# Markup-shaped data, written by the CLI and rendered by the TUI
# ---------------------------------------------------------------------------

class TestCliWrittenDataRendersInTheTui:
    """`--force` lets the CLI write an id no catalog vouches for, so the agent surface is a new
    producer of the data that used to kill the render pass. Each case asserts only "the TUI
    opened and drew every pane" — a MarkupError escaping `run_test()` fails the test."""

    @pytest.mark.parametrize(
        ("case", "argv_tail"),
        [
            ("unmatched close tag in the model id", ["agent:sisyphus", "acme/[/b]"]),
            ("well-formed tag pair in the model id", ["agent:sisyphus", "acme/[red]glm[/red]"]),
            ("bare open bracket in the model id", ["cat:quick", "acme/glm[5"]),
            ("markup in the provider", ["cat:quick", "[/i]/glm-5"]),
        ],
    )
    def test_forced_markup_shaped_value_still_opens(self, tmp_path, case, argv_tail):
        cfg = _cfg(tmp_path)
        assert _run_cli(["set", *argv_tail, "--force", "--config", cfg]) == 0
        assert _open_tui(cfg) == (False, False)

    def test_the_forced_id_really_reaches_the_rendered_pane(self, tmp_path):
        """Proves the case above is not vacuous — a harness that never drew the id would pass
        it no matter what. Also pins the second half of the markup rule: a WELL-FORMED tag
        parses cleanly and would silently vanish into styling, so an id you cannot run would
        read as one you can. The brackets must survive to the pane."""
        from textual.widgets import OptionList

        from omodel.app import create_app

        cfg = _cfg(tmp_path)
        assert _run_cli(
            ["set", "agent:sisyphus", "acme/[red]glm[/red]", "--force", "--config", cfg]
        ) == 0

        labels = {}

        async def _run():
            app = create_app(cfg)
            async with app.run_test() as pilot:
                await pilot.pause()
                cands = app.query_one("#candidates", OptionList)
                labels["rows"] = [
                    str(cands.get_option_at_index(i).prompt) for i in range(cands.option_count)
                ]

        asyncio.run(_run())
        assert any("acme/[red]glm[/red]" in row for row in labels["rows"]), labels["rows"]

    def test_forced_markup_shaped_variant_still_opens(self, tmp_path):
        cfg = _cfg(tmp_path)
        assert _run_cli(
            ["set", "agent:sisyphus", "opencode/glm-5", "--variant", "[/i]", "--force",
             "--config", cfg]
        ) == 0
        assert _open_tui(cfg) == (False, False)

    def test_markup_shaped_preset_name_is_sanitized_and_opens(self, tmp_path):
        """`preset new` is the CLI's only route to a user-supplied string that becomes an
        `Option` prompt in #presets — the pane that a `[/b]` name used to crash."""
        cfg = _cfg(tmp_path)
        assert _run_cli(["preset", "new", "[/b]", "--config", cfg]) == 0
        with open(str(tmp_path / ".omodel-presets.json"), encoding="utf-8") as f:
            names = [p["name"] for p in json.load(f)["presets"]]
        assert "[" not in "".join(names) and "]" not in "".join(names)
        assert _open_tui(cfg) == (False, False)

    def test_non_ascii_names_and_ids_survive_the_round_trip(self, tmp_path):
        """`_emit` passes `ensure_ascii=False` and both files are utf-8; a mojibake round trip
        would show up as a changed model id or a preset the TUI can't match."""
        cfg = _cfg(tmp_path)
        assert _run_cli(["set", "cat:quick", "ai/模型-テスト", "--force", "--config", cfg]) == 0
        assert _run_cli(["preset", "new", "日本語プリセット", "--config", cfg]) == 0
        with open(cfg, encoding="utf-8") as f:
            assert "ai/模型-テスト" in f.read()
        assert _open_tui(cfg) == (False, False)


# ---------------------------------------------------------------------------
# Containment — the agent surface's blast radius
# ---------------------------------------------------------------------------

class TestSideFilesStayNextToTheConfig:
    """`.omodel-presets.json` and `.backup/` are written NEXT TO the config so an experimental
    `--config /tmp/x.jsonc` can't scatter state into the user's real config dir. An agent is
    told to use a temp path for anything experimental, which only holds if that is true."""

    def test_relative_config_keeps_everything_in_its_own_directory(self, tmp_path, monkeypatch):
        """A bare relative `--config` is the case that resolves through `abspath` rather than
        `dirname` — the path shape that used to crash the scaffold."""
        import os
        work = tmp_path / "work"
        work.mkdir()
        _cfg(work)
        monkeypatch.chdir(work)

        assert _run_cli(["set", "cat:quick", "opencode/glm-5",
                         "--config", "oh-my-openagent.jsonc"]) == 0

        assert sorted(os.listdir(str(work))) == [
            ".backup", ".omodel-presets.json", "oh-my-openagent.jsonc",
        ]
        # tmp_path itself holds only `work` (+ conftest's isolated cache/config/data dirs).
        assert "oh-my-openagent.jsonc" not in os.listdir(str(tmp_path))
