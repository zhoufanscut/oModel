# oModel — Verification Checklist (Lead's Merge Gate)

Maps each of the 8 §Verification checks in DESIGN.md to the concrete command(s) to run.
All checks must pass before any release.

---

## Check 1 — Build / install

**Goal:** wheel installs cleanly; bundled data loads via `importlib.resources` in both
editable and installed forms; the PyInstaller binary also works.

```sh
# Editable install (already in the shared venv)
python -m pip install -e . --quiet

# Version + CI-safe dry-run (no opencode required)
python -m omodel --version
python -m omodel --check

# PyInstaller one-file build (run from repo root, bun not required for this check):
python -m pip install pyinstaller --quiet
pyinstaller --onefile --name omodel \
    --collect-data omodel \
    src/omodel/__main__.py
./dist/omodel --version
./dist/omodel --check
```

**Real-config safety:** `--check` degrades to suggestions-only when `opencode` is absent;
it never writes the live config. Use `--config /tmp/omodel-test.jsonc` if `--check`
requires a config path.

**Pass criteria:** exit 0 for both `--version` and `--check`; no ImportError or
`importlib.resources` error for the bundled `omo-suggestions.json`.

---

## Check 2 — Availability + prefix (unit, mocked `opencode models`)

**Goal:** `vendors_served` classifies gateways vs dedicated correctly; `resolve_prefix`
applies dedicated-first; live model count is NOT hard-asserted.

```sh
python -m pytest tests/test_catalog_parse.py tests/test_resolve.py -v
```

**Key assertions:**
- `opencode`/`openrouter` → gateway (`vendors_served ≥ 2`)
- `openai`/`zhipuai`/`moonshotai-cn`/`deepseek` → dedicated
- `providers_for("gpt-5.5") == ["opencode","openai"]` → `resolve_prefix` picks `openai`
- `claude-opus-4-7` → `opencode` (only gateway serves it)
- `kimi-k2.5` → `moonshotai-cn` (dedicated wins)
- `glm-5` → `zhipuai`; `deepseek-v4-pro` → `deepseek`
- `glm+max` and absent model → `warn` includes `"variant"` / `"unavailable"` but row accepted
- With openrouter also connected, a both-gateways-only model resolves via first-seen,
  and `p` cycling reaches `openrouter/…`

**Real-config safety:** tests monkeypatch `subprocess.run`; no real `opencode` called.

---

## Check 3 — Verbose parsing (unit)

**Goal:** multi-record `--verbose` blob → N records with `limit.context`/`cost`/
`capabilities` extracted; `--verbose.variants` is never read.

```sh
python -m pytest tests/test_catalog_parse.py::TestVerboseParsing -v
```

**Key assertions:**
- Detail result has exactly the keys `context, cost, reasoning, image` (no `variants`)
- Each of 3 records parsed independently; `detail("glm-5")` picks the right block
- Cache cost nested inside `cost` dict passes through correctly

**Real-config safety:** no subprocess call to real `opencode`; blob is mocked.

---

## Check 4 — detect_family parity

**Goal:** Python heuristic matches omo's `detectHeuristicModelFamily` for all 6 specified
IDs, plus ordering guards (openai-reasoning before gpt-5, kimi-thinking before kimi,
claude-opus before claude-non-opus).

```sh
python -m pytest tests/test_detect_family.py -v
```

**Key cases (REAL omo IDs from bundled data):**
- `kimi-k2.5` → `kimi` (no `max`)
- `k2p5` → `kimi-thinking`
- `claude-opus-4-7` → `claude-opus` (has `max`)
- `gpt-5.5` → `gpt-5` (has `xhigh`)
- `glm-5` → `glm` (no `max`)
- `deepseek-v4-pro` → `deepseek` (has `max`)
- `normalize_model_id("kimi-k2.7")` → `"kimi-k2-7"`

**Real-config safety:** n/a — pure unit test, no subprocess or file I/O.

---

## Check 5 — Bundled suggestions load

**Goal:** `importlib.resources` loads `omo-suggestions.json` with no omo checkout present;
counts match the committed data (11 agents, 8 categories, 15 families, 9 knownVariants).

```sh
python -m pytest tests/test_detect_family.py::TestBundledSuggestionsLoad -v
```

**Pass criteria:** all assertions green; in particular the counts and that `patterns` are
compiled `re.Pattern` objects, not raw strings.

**Real-config safety:** no file writes; reads only the bundled wheel data.

---

## Check 6 — Refresh (`omodel --refresh-omo`)

**Goal:** with omo src + bun present, `--refresh-omo` regenerates `omo-suggestions.json` with
bumped `meta`; without them, non-fatal (prints current bundled meta, exits 0).

```sh
# Non-fatal path (no omo src):
python -m omodel --refresh-omo
# Expected: prints current bundled meta, exits 0, data file unchanged.

# Live path (requires omo checkout at ~/source/oh-my-openagent and bun):
OMO_SRC=~/source/oh-my-openagent python -m omodel --refresh-omo
# Expected: src/omodel/data/omo-suggestions.json overwritten; meta.generatedAt bumped.
# After refresh: re-run check #5 to confirm counts still valid.
```

**Note:** `--refresh-omo` (bundled omo suggestions) is distinct from `--refresh-models`
(opencode availability: runs `opencode models --refresh` + rebuilds `~/.cache/omodel/`).

**Real-config safety:** writes to `src/omodel/data/` (maintainer) or
`$XDG_DATA_HOME/omodel/` (user override); never touches `~/.config/opencode/`.

---

## Check 7 — Headless UI pilot

**Goal:** Textual `App.run_test()` drives a full set+save cycle; re-loading the config
confirms the model updated and non-model sections are untouched.

```sh
python -m pytest tests/test_app_pilot.py -v
```

**Key assertions (all use a temp config dir — never `~/.config`):**
- `agent:sisyphus` selectable via `OptionList#targets`
- A `cand:*` row for `deepseek/deepseek-v4-pro` is pickable
- After `s` + confirm: `agents.sisyphus.model == "deepseek/deepseek-v4-pro"`
- `team_mode` / `experimental` / `claude_code` unchanged by value
- Palette comments gone from the saved file
- `.backup/<ts>.jsonc` snapshot exists; `original.jsonc` verbatim
- A second save adds a second snapshot; `--restore` / `list_backups` lists newest-first

**Real-config safety:** HARD — pilot fixture uses `tmp_path` only; `OModelApp` must
accept `config_path=` kwarg (stable API). No interaction with `~/.config/opencode/`.

**Real-cache safety:** HARD — `tests/conftest.py` redirects `$OMODEL_CACHE_DIR` to a per-test
tmp dir, and `test_app_pilot.py`'s autouse `_no_real_opencode` fixture stubs `subprocess.run`,
so the pilot never spawns the real `opencode` CLI (~320 MB/call; un-stubbed it can OOM the box).
The full suite must show zero `opencode`/`bun` processes spawned.

**Note:** This test is currently skipped (`OModelApp not yet implemented`). It must be
green after TUI integration before this check is cleared.

---

## Check 8 — Live `opencode` run

**Goal:** on a machine with `opencode` logged in (no omo source needed), `omodel` launches,
lists models from `opencode models`, the user edits and saves a clean config that OMO
re-loads correctly.

```sh
# Prerequisite: opencode on PATH and at least one provider logged in.
opencode models | head -5    # confirm models visible

# Launch TUI against a TEMP config (never the live config during testing):
python -m omodel --config /tmp/omodel-live-test.jsonc

# Manual steps in the TUI:
#   1. Verify Providers: header shows connected provider(s) + cache age (e.g. "cached 0m ago · r to refresh")
#   2. Select agent:sisyphus — detail line (ctx/$/caps) appears within a moment (off-thread), UI never freezes
#   3. Pick a model from the candidate list
#   4. Press 'r' — header shows "Refreshing…", then updates; ~/.cache/omodel/ is rebuilt
#   5. Press 's', confirm
#   6. Quit

# Confirm the cache landed (and is the only place opencode output is cached by omodel):
ls ~/.cache/omodel/    # models.json + verbose-<provider>.json

# Verify output:
cat /tmp/omodel-live-test.jsonc    # must be clean JSON (no comments)
ls /tmp/.backup/                   # or wherever the backup dir lands for /tmp/ configs

# Confirm OMO can reload the file (requires omo running):
# opencode ... (launch opencode with --config /tmp/omodel-live-test.jsonc and verify it loads)
```

**Real-config safety:** use `--config /tmp/omodel-live-test.jsonc`, NOT the default
`~/.config/opencode/oh-my-openagent.jsonc`. The live config is safe only after all
automated checks pass and the user explicitly chooses to run against it.

---

## Running all automated checks at once

```sh
python -m pytest tests/ -x -q
```

Expected outcome before full integration:
- `test_detect_family.py` — **PASS** (bundled data + heuristics fully implemented)
- `test_catalog_parse.py` — **PASS** (catalog.py implemented)
- `test_resolve.py` — **PASS** (resolve.py implemented)
- `test_config_io.py` — **PASS** (config_io.py implemented)
- `test_app_pilot.py` — **SKIP** (OModelApp stub; unblocked after TUI lands)

The Lead's gate is: all 5 test files pass (or are explicitly waived with documented reason),
plus the 8 checks above run clean on the integration branch.
