# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`omodel` is a Textual TUI that sets models in `oh-my-openagent.jsonc` (OMO's per-agent / per-category
config). Core flow: **what omo suggests + what you already have → pick one → save a clean config.**
It bundles a snapshot of omo's model requirements and reads live availability from the `opencode` CLI;
neither an omo checkout nor a network call is needed at runtime.

## Commands

```sh
# Dev install (gets pytest + ruff)
pip install -e ".[dev]"          # or: uv pip install -e .

# Lint (no ruff config → defaults; CI runs exactly this)
ruff check src/ tests/

# Tests
pytest tests/ -v --tb=short      # full suite
pytest tests/ -x -q              # fast, stop on first failure
pytest tests/test_resolve.py -v                              # one file
pytest tests/test_catalog_parse.py::TestVerboseParsing -v    # one class
pytest tests/test_detect_family.py::TestBundledSuggestionsLoad::test_14_families -v   # one test

# Run the app / CLI (also `python -m omodel ...`)
omodel                           # launch TUI
omodel --check                   # CI-safe dry-run resolve (exit 0; degrades w/o opencode)
omodel --print                   # resolved models, no UI
omodel --config /tmp/x.jsonc     # ALWAYS use a temp path when testing saves

# Refresh opencode availability: force `opencode models --refresh` + rebuild ~/.cache/omodel
omodel --refresh-models          # in-TUI equivalent: the `r` key (off-thread)

# Regenerate bundled suggestion data (needs bun + an omo checkout; non-fatal if absent)
OMO_SRC=~/source/oh-my-openagent omodel --refresh-omo
```

opencode CLI output is cached for 24h under `~/.cache/omodel/` (`cache.py`) so warm launches/detail
are instant; `--refresh-models` / `r` bust it. Tests isolate the cache via `tests/conftest.py`
(`$OMODEL_CACHE_DIR` → tmp) and must stub `subprocess.run` (each opencode call is ~3s / ~320 MB).

`tests/verification.md` maps the 8 DESIGN.md verification checks to concrete commands — use it as the
pre-release gate (it covers the live `opencode` and PyInstaller-binary checks that CI can't run).

## Architecture

A four-stage pipeline; `app.py` is the integration point that consumes all of it.

```
opencode models (live) ─► cache.py (24h) ─► catalog.py    ─┐
                                                           ├─► resolve.py ──► candidate-row dicts ──► app.py (TUI)
data/omo-suggestions.json ──────────────► suggestions.py ─┘                                              │
(bundled omo snapshot)                                                                                   ▼
                                                                                                  config_io.py (save)
```

- **`catalog.py`** — "what you have." Parses `opencode models` into `available={provider:[ids]}` +
  `connected=[providers]` (first-seen order, never a set). `detail()` parses `--verbose` JSON blocks for
  the detail pane (display only). Degradation is load-bearing: `opencode` missing → empty + banner;
  exit≠0 or zero lines parsed → `CatalogUnavailable` → banner + retry. `load()`/`detail()` read through
  `cache.py` and all opencode calls carry a `timeout=`; `refresh()` forces `opencode models --refresh`
  and rebuilds the cache (the `r` key / `--refresh-models`).
- **`cache.py`** — on-disk cache (24h TTL) of the two opencode subprocess outputs under
  `~/.cache/omodel/` (flat: `models.json`, `verbose-<provider>.json`). opencode calls are ~3s / ~320 MB,
  so the detail fetch runs in an `app.py` worker (never the UI thread) and is **capped to one at a time**
  (`asyncio.to_thread` can't kill a spawned process — stacking them OOM'd a machine). Best-effort:
  corrupt/expired → miss; write errors swallowed.
- **`suggestions.py`** — "what omo suggests." Loads the bundled JSON; `detect_family()` is a faithful
  port of omo's `detectHeuristicModelFamily` (ordered, pattern-before-includes, first match wins — order
  matters for parity). `FAMILY_VENDOR` is a hardcoded 14-family→vendor map (NOT from omo) used for
  gateway classification.
- **`resolve.py`** — the core logic. `candidates(target)` is the heart: a single filtered pass over
  omo's `fallbackChain` keeping only models you can run — **exact** match, else newest **same-line
  substitute** of the same family (`glm-5`→`glm-5.1`), else **hidden**. No connected-model dump; the
  list is chain-only plus a `+ add model…` row. `resolve_prefix()` is **dedicated-first**: a provider is
  a *gateway* if it serves ≥2 vendors (`vendors_served`), and a dedicated provider beats a gateway for
  the same model. This is data-driven — no hardcoded provider list.
- **`config_io.py`** — clean rewrite + backups. Save is **active-only** (`json.dumps`, no comments — the
  first save intentionally drops omo's commented palette). Each save snapshots the prior file verbatim to
  `<config_dir>/.backup/<ts>.jsonc`; the very first save pins `original.jsonc` (never pruned, never
  counts toward the 20-snapshot cap). Only `agents`/`categories` are edited; all other top-level keys
  pass through by value.
- **`app.py`** — Textual two-pane App. Stable widget IDs (`#targets`, `#candidates`, `#detail`,
  `#providers`) and option IDs (`agent:<name>[.ultrawork|.compaction]`, `cat:<name>`, `cand:<i>`,
  `cand:add`) are a contract that pilot tests depend on — see the module docstring; don't rename.
- **`cli.py`** — argparse dispatch. Imports are deliberately lazy so `--version`/`--check`/`--refresh-omo`/
  `--refresh-models` never import Textual. Two refresh flags, one per data source: `--refresh-omo`
  (bundled omo suggestions, via `refresh.py`) and `--refresh-models` (opencode availability, via
  `catalog.refresh()`).
- **`refresh.py` + `tools/snapshot_omo.ts`** — maintainer-time regeneration of the bundled data. The
  extractor runs under **bun** (node can't resolve omo's extensionless `.ts` imports).

### The integration seam: the candidate-row dict

`resolve.candidates()` yields these and `app.py` renders them — the one shape both sides agree on. Its
fields (`source`/`model`/`provider`/`variant`/`entry`/`substitute_for`/`warn`) are frozen in
**CONTRACTS.md**; the value written to config is `f"{provider}/{model}"` + `variant`. Read CONTRACTS.md
before changing any public signature or shared shape.

## Conventions specific to this repo

- **DESIGN.md is the design-of-record (the spec), CONTRACTS.md pins the frozen shapes + module
  signatures.** Update DESIGN.md in the *same commit* as the code it describes. Read both before
  non-trivial changes.
- **Python floor is 3.9** (CI matrix 3.9–3.13). Every module starts with
  `from __future__ import annotations`. No runtime PEP-604 unions (`isinstance(x, A | B)`) or PEP-585
  generics — annotations-as-strings make `dict | None` in signatures fine, but runtime use is not.
- **Real-config safety (hard rule):** never read-then-write the live
  `~/.config/opencode/oh-my-openagent.jsonc` in tests or examples. Pass an explicit temp `path` /
  `--config` everywhere. Tests monkeypatch `subprocess.run`; no test calls real `opencode`.
- **Real-cache safety (hard rule):** never let tests touch the real `~/.cache/omodel/`. The autouse
  `tests/conftest.py` fixture redirects `$OMODEL_CACHE_DIR` to a per-test tmp dir, and `test_app_pilot.py`
  stubs `subprocess.run` so the TUI never spawns real opencode (~320 MB/call — un-stubbed it OOM'd a box).
- **Variant validity comes only from the bundled family registry** — never from
  `opencode --verbose.variants` (that's opencode's runtime namespace, a different shape, empty for some
  providers). Invalid variants warn-but-allow (`⚠`), they don't block.
- **GPT-only agents:** Hephaestus mirrors omo's `no-hephaestus-non-gpt` hook via `_GPT_ONLY_AGENTS` /
  `_is_gpt_model` in `app.py` — a hardcoded agent key, not a data field.

## Bundled data & packaging

- `src/omodel/data/omo-suggestions.json` is generated (do not hand-edit); regenerate via `--refresh-omo`,
  which CI also runs weekly (`refresh-suggestions.yml`) to open a PR on change. It is derived from omo
  (Sustainable Use License) — keep `NOTICE` attribution intact when redistributing.
- Distribution is **GitHub-only, no PyPI**: `release.yml` builds PyInstaller one-file binaries on `v*`
  tags (linux-x64, darwin-arm64, darwin-x64); `install.sh` is the curl|sh installer. Non-Python payload
  (`data/`, `tools/`) ships because it lives under the package tree and is read via `importlib.resources`
  — do **not** add a hatch force-include for it (duplicates the path and fails the wheel build).
