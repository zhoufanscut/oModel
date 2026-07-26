# oModel — Verification Checklist (Lead's Merge Gate)

Maps the §Verification checks in DESIGN.md to the concrete command(s) to run, plus Check 10 for
the agent surface (decision #18). All checks must pass before any release.

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
- `opencode` → gateway (`vendors_served ≥ 2`), and so is `openrouter`, whose ids CARRY the
  vendor (`openrouter/anthropic/claude-opus-4-7` → model `anthropic/claude-opus-4-7`). That is
  the only test feeding a slash-bearing id through `vendors_served`, and the only one where
  `gateways` holds more than one provider — assert it UNCONDITIONALLY. Guarding it behind
  `if vendors_served(...) >= 2:` makes it pass silently if the count ever drops to 0, which is
  exactly the regression it exists to catch.
- `openai`/`zhipuai`/`moonshotai-cn`/`deepseek` → dedicated
- `providers_for("gpt-5.5") == ["opencode","openai"]` → `resolve_prefix` picks `openai`
- `claude-opus-4-7` → `opencode` (only gateway serves it), with or without openrouter also
  connected — a both-gateways-only model resolves via first-seen
- `kimi-k2.5` → `moonshotai-cn` (dedicated wins)
- `glm-5` → `zhipuai`; `deepseek-v4-pro` → `deepseek`
- `glm+max` → `warn == ["variant"]`, row still accepted
- An entry no connected provider serves, with no same-line relative, is **hidden** — not warned
  (decision #5 reversed for the pick list). `candidates()` never emits an `"unavailable"` warn;
  that one comes from `app.py`'s synthesized off-chain assignment row
- Each serving provider gets its own pickable row, dedicated before gateway (pick the row you
  want — there is no cycling key)

**Data coupling:** assertions naming a specific model run against the FROZEN chain in
`tests/_helpers.py` (`FROZEN_AGENTS`, target `agent:probe`), never the live bundled data —
omo swaps models every few releases, and pinning them there reds CI on a healthy product.
`TestRealDataIntegration` keeps the real data flowing through `resolve` with structural
assertions only (contract shape, no duplicate rows, dedicated-before-gateway, substitutes
stay in-family). Do not "update" `FROZEN_AGENTS` to match a new omo release — churn immunity
is the point.

**Real-config safety:** tests monkeypatch `subprocess.run`; no real `opencode` called.

---

## Check 3 — Verbose parsing (unit)

**Goal:** multi-record `--verbose` blob → N records with `limit.context`/`cost`/
`capabilities` extracted; **`detail()` reads neither `--verbose.variants` nor `.family`** (variants
are read separately by `Catalog.variants_for` — see the `variants_for` check below).

```sh
python -m pytest tests/test_catalog_parse.py::TestVerboseParsing tests/test_catalog_parse.py::TestVariantsFor -v
```

**Key assertions:**
- Detail result has exactly the keys `context, cost, reasoning, image` (no `variants`)
- Each of 3 records parsed independently; `detail("glm-5")` picks the right block
- Cache cost nested inside `cost` dict passes through correctly
- `variants_for` (decision #14): reads the cached `--verbose` `variants` keys; prefers the first
  NON-EMPTY set across the picked provider then others (`{}` → keep looking); `[]` on empty-
  everywhere (kimi) or total cache miss; **never shells out** (guarded by `_NO_SHELL`)

**Real-config safety:** no subprocess call to real `opencode`; blob is mocked / cache seeded in tmp.

---

## Check 4 — detect_family parity

**Goal:** Python heuristic matches omo's `detectHeuristicModelFamily` for every specified
ID, plus ordering guards (openai-reasoning before gpt-5, kimi-thinking before kimi,
claude-opus before claude-non-opus).

```sh
python -m pytest tests/test_detect_family.py -v
```

**Key cases (REAL omo IDs from bundled data) — the `test_family_and_variants` table:**
- `kimi-k2.5` → `kimi` (no `max`); `kimi-k2.6` → `kimi` likewise
- `k2p5` → `kimi-thinking` (ordered before `kimi`, so it must not fall through)
- `claude-opus-4-7` → `claude-opus` (has `max`)
- `claude-sonnet-4-6` → `claude-non-opus` (no `max`) — the more-specific `claude-opus` first
- `gpt-5.5` → `gpt-5` (has `xhigh`)
- `glm-5` → `glm` (no `max`)
- `deepseek-v4-pro` → `deepseek` (has `max`)
- `big-pickle` → `None` (unrecognised ids invent no family)
- `normalize_model_id("kimi-k2.7")` → `"kimi-k2-7"`

**Real-config safety:** n/a — pure unit test, no subprocess or file I/O.

---

## Check 5 — Bundled suggestions load

**Goal:** `importlib.resources` loads `omo-suggestions.json` with no omo checkout present;
counts match the committed data (11 agents, 8 categories, 15 families, 9 knownVariants),
and the agent/category *name* sets are pinned so a rename can't hide behind an unchanged
count.

```sh
python -m pytest tests/test_detect_family.py::TestBundledSuggestionsLoad -v
```

**Pass criteria:** all assertions green; in particular the counts and that `patterns` are
compiled `re.Pattern` objects, not raw strings.

Chain *contents* are checked structurally, never by length — every agent/category
`fallbackChain` must be non-empty with `providers` + `model` on each entry, and every
`variant` must be one of `knownVariants`. Chain lengths are upstream churn (a weekly
`--refresh-omo` routinely moves them), so pinning one would fail on healthy data.

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
- A `cand:*` row for `zhipuai/glm-5` is pickable (the dedicated-first row for a sisyphus chain
  entry — the fragment is the full `provider/model`, so the pick is unambiguous)
- After `s` + confirm: `agents.sisyphus.model == "zhipuai/glm-5"`
- `team_mode` / `experimental` / `claude_code` unchanged by value
- Palette comments *inside* agents/categories gone; comments *outside* them (top banner, a
  comment inside `claude_code`) preserved verbatim; no `// Generated by oModel` header injected
- `.backup/<ts>.jsonc` snapshot exists; `original.jsonc` verbatim
- A second save adds a second snapshot; `--restore` / `list_backups` lists newest-first

**Real-config safety:** HARD — pilot fixture uses `tmp_path` only; `OModelApp` must
accept `config_path=` kwarg (stable API). No interaction with `~/.config/opencode/`.

**Real-cache safety:** HARD — `tests/conftest.py` redirects `$OMODEL_CACHE_DIR` to a per-test
tmp dir, and `test_app_pilot.py`'s autouse `_no_real_opencode` fixture stubs `subprocess.run`,
so the pilot never spawns the real `opencode` CLI (~320 MB/call; un-stubbed it can OOM the box).
The full suite must show zero `opencode`/`bun` processes spawned.

**Note:** `OModelApp` is fully implemented and the full pilot suite runs green — this check is
cleared. No test count is pinned here on purpose: it rotted once already, and the counts worth
pinning are the bundled-data ones in Check 5, which guard something real.

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
#   1. Verify oModel: header shows just the connected provider list (NO "cached … · r to refresh" suffix)
#   2. Verify the bottom hint bar shows "s save · q quit · ? help" (left) with the version "v<version>"
#      right-aligned at the far right; press '?' — the key
#      overlay opens (Navigate/Edit/Presets/Undo/Models/dialogs); '?'/esc/q closes it
#   3. Select agent:sisyphus — detail line (ctx/$/caps) appears within a moment (off-thread), UI never freezes
#   4. Pick a model from the candidate list
#   5. Press 'r' — header shows "Refreshing…", then updates; ~/.cache/omodel/ is rebuilt
#   6. Press 's', confirm
#   7. Quit

# Confirm the cache landed (and is the only place opencode output is cached by omodel):
ls ~/.cache/omodel/    # models.json + verbose-<provider>.json

# Verify output:
cat /tmp/omodel-live-test.jsonc    # agents/categories rewritten clean; any comments / commented-out
                                   # config OUTSIDE those two are preserved verbatim (edit-in-place)
ls /tmp/.backup/                   # or wherever the backup dir lands for /tmp/ configs

# Confirm OMO can reload the file (requires omo running):
# opencode ... (launch opencode with --config /tmp/omodel-live-test.jsonc and verify it loads)
```

**Real-config safety:** use `--config /tmp/omodel-live-test.jsonc`, NOT the default
`~/.config/opencode/oh-my-openagent.jsonc`. The live config is safe only after all
automated checks pass and the user explicitly chooses to run against it.

---

## Check 9 — Presets (the working state)

**Goal:** the invariant holds — the config on disk always equals the ACTIVE preset, never a
fourth orphan state — and only `s` ever writes. DESIGN §presets.py / decision #17.

Automated (both halves):

```sh
python -m pytest tests/test_presets.py -q                    # unit: store IO, active, fingerprints, migration
python -m pytest tests/test_app_pilot.py -q -k "preset or quit or launch or new_row or renames or refreshes or renumbers or legacy or switch"
```

Expected: all pass. The pilot half covers the first-launch seed, edits flowing into the active
preset, `s` writing both files (asserting `matching_index(store, config) == store.active`),
add + switch banking your edits, undo moving the `●` back with the models, `x` refused on the
active preset, the three-way quit, both launch-reconciliation paths, focus dispatch (`v` inert,
`r` renames here / refreshes elsewhere), a mangled sidecar, unlimited presets past two-digit
numbering, a legacy fixed-3 sidecar migrating, delete renumbering the undo history, and the
row-wrap guard.

Manual (run inside the Check 8 live session, before quitting):

```sh
# In the TUI, against the TEMP config:
#   1. First launch: the PRESETS card shows "● 1 default" seeded from your config, and
#        ls <config_dir>/.omodel-presets.json     # ABSENT — the seed is in memory (one write rule)
#      Press 'q' immediately: it exits with NO prompt (nothing to save).
#   2. Relaunch. Set a model, `tab` into the card, enter on "+ add preset…", name it → the new
#      row is now ● and holds those models. Change another model: it goes there, not into row 1.
#      Add several more — the card grows and then scrolls; it never pushes #targets below half.
#   3. Press enter on row 1 → its models come back. Enter on row 2 → your row-2 edits are still
#      there (switching banks them, it never drops them).
#   4. Press 'u' right after a switch → the models AND the ● both go back.
#   5. Press 'r' on a row → rename it (the models and the saved-at stamp are untouched).
#   6. Press 'x' on the ● row → refused ("switch to another one first"), no modal. On a
#      non-active row → confirm; the rows below it RENUMBER, and nothing is on disk yet.
#      Press 'u' a few times: the models come back into the right preset (or a warning says the
#      one they were in is gone) — never silently into a different preset.
#   7. Press 's', confirm → NOW both files land:
#        cat <config_dir>/.omodel-presets.json    # version/active/presets, active = the ● row
#        cat <config_dir>/oh-my-openagent.jsonc   # matches that preset's models exactly
#   8. Make one more change, press 'q' → three buttons. 'd' discards; relaunch and confirm BOTH
#      files are exactly as step 7 left them. Then repeat and use "Save & quit" instead.
#   9. Hand-edit the config's sisyphus model outside oModel, relaunch → the sync modal appears.
#      Neither answer writes anything; press 's' afterwards to land your choice.
#  10. With a very long / CJK preset name, every row still renders on ONE line (no wrap), at 9
#      presets and again at 10+ where the row number takes a second digit.
```

**Real-config safety:** presets live next to the ACTIVE config, so a `--config /tmp/...` run
writes `/tmp/.omodel-presets.json` and never touches `~/.config/opencode/`.

---

## Check 10 — Agent surface (decision #18)

**Goal:** an LLM agent can inspect and change models without the TUI, gets the same guards, and
can read the contract from the shipped binary.

```sh
# Automated
python -m pytest tests/test_session.py tests/test_cli.py -q

# Live, against a TEMP config (never the real one)
cp ~/.config/opencode/oh-my-openagent.jsonc /tmp/omodel-agent.jsonc
C="--config /tmp/omodel-agent.jsonc"

omodel show $C --json | jq '.degraded, .active_preset'
omodel candidates agent:sisyphus $C --json | jq -r '.candidates[].value'

# --dry-run must write NOTHING
before=$(md5sum /tmp/omodel-agent.jsonc)
omodel set agent:sisyphus <a value from that list> $C --dry-run --json | jq .diff
[ "$before" = "$(md5sum /tmp/omodel-agent.jsonc)" ] && echo "dry-run OK"

# A real set moves BOTH files together
omodel set agent:sisyphus <same value> $C --json | jq '.changed, .backup'
ls /tmp/.backup/ && cat /tmp/.omodel-presets.json | jq .active

# Guards: every one of these must print 3
for a in "set agent:nope opencode/gpt-5.5" \
         "set agent:sisyphus not-a-model" \
         "set agent:hephaestus zhipuai/glm-5" \
         "set agent:hephaestus zhipuai/glm-5 --force"; do
  omodel $a $C >/dev/null 2>&1; echo "$a -> $?"
done

# Degraded mode is LABELLED, not silently empty
# NB resolve omodel's path FIRST — it lives in ~/.local/bin, so a bare `omodel` under the
# stripped PATH would be "command not found" and this check would silently not run.
OMODEL=$(command -v omodel); PATH=/usr/bin:/bin "$OMODEL" show $C --json | jq .degraded   # true

# The guide is reachable the way an agent reaches it — including from the binary
omodel --help | grep agent-guide
omodel agent-guide | head -20
./dist/omodel agent-guide | wc -l                                 # needs Check 1's binary
```

**Pass criteria:** `--dry-run` leaves the file byte-identical; a real `set` writes the config,
the presets file and a `.backup/` snapshot; all four guard cases exit **3** (including
hephaestus **with** `--force` — that one is never overridable); `degraded` is `true` with
opencode off PATH; `agent-guide` prints from both the source tree and the PyInstaller binary.

**Then open the TUI on the same temp config** — it must show the model the CLI set, with **no**
sync-conflict modal. A prompt there means the CLI broke the config-equals-active-preset
invariant.

### Known-open: concurrent mutating commands lose updates

**Accepted, documented in `data/agent-usage.md` §7, NOT fixed.** Every mutating verb is an
unlocked read-modify-write, so parallel `set` calls overwrite each other: each process reports
`"ok": true` and only the last write survives. Files stay *consistent* (no corruption, no
conflict) — it is lost-update, not damage. It matters because firing independent tool calls in
parallel is how an LLM agent naturally operates.

Kept here as a manual check because it cannot be a CI test (it needs real concurrent processes
and is inherently racy). Re-run it if locking is ever added — it is the acceptance test.

```sh
# 6 concurrent sets on 6 DIFFERENT targets, against a temp config
C=/tmp/omodel-conc.jsonc
printf '{"agents":{},"categories":{}}' > $C
omodel set cat:quick opencode/glm-5 --config $C >/dev/null    # materialize the preset first
for t in agent:sisyphus agent:oracle agent:librarian cat:deep cat:quick cat:artistry; do
  omodel set "$t" opencode/glm-5 --config $C --json >/dev/null 2>&1 &
done; wait
# Count how many of the 6 actually landed:
python - "$C" <<'PY'
import sys
import json5
cfg = json5.load(open(sys.argv[1]))
maps = [cfg.get("agents") or {}, cfg.get("categories") or {}]
landed = sum(
    1
    for m in maps
    for v in m.values()
    if isinstance(v, dict) and v.get("model") == "opencode/glm-5"
)
print("landed:", landed, "of 6")
PY
```

**Expected TODAY:** all 6 processes exit 0, typically only 2–4 writes land. **If locking is
added:** either all 6 land, or the losers report a non-zero exit — silent success with a lost
write is the thing to prevent.

---

## Running all automated checks at once

```sh
python -m pytest tests/ -x -q
```

Expected outcome — every test file passes:
- `test_detect_family.py`, `test_catalog_parse.py`, `test_resolve.py`, `test_config_io.py`
- `test_app_pilot.py` (the full Textual pilot suite — much the slowest; it dominates wall clock)
- `test_cache.py`, `test_cli.py`, `test_history.py`, `test_presets.py`, `test_refresh.py`
- `test_session.py` (the headless core both surfaces edit through)

The Lead's gate is: every test file passes (or is explicitly waived with documented reason),
plus the 10 checks above run clean on the integration branch.
