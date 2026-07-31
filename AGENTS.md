# AGENTS.md

This file provides guidance to coding agents (including Claude Code, via a `CLAUDE.md` that imports
it) when working **on this repository's source**.

> **Just want to USE `omodel` to set a model?** That is a different document: run
> **`omodel agent-guide`** (or read `src/omodel/data/agent-usage.md`) for the verbs, the JSON
> shapes, and the exit codes. Short version: never hand-edit `oh-my-openagent.jsonc` — run
> `omodel candidates <target> --json`, then `omodel set <target> <value>`.

## What this is

`omodel` is a Textual TUI that sets models in `oh-my-openagent.jsonc` (OMO's per-agent / per-category
config). Core flow: **what omo suggests + what you already have → pick one → save a clean config.**
It bundles a snapshot of omo's model requirements and reads live availability from the `opencode` CLI;
neither an omo checkout nor a network call is needed at runtime.

## Commands

```sh
# Dev install (gets pytest + ruff)
pip install -e ".[dev]"          # or: uv pip install -e .

# Lint (ruff defaults MINUS the documented ignores in pyproject; CI runs exactly this)
ruff check src/ tests/

# Tests
pytest tests/ -v --tb=short      # full suite
pytest tests/ -x -q              # fast, stop on first failure
pytest tests/test_resolve.py -v                              # one file
pytest tests/test_catalog_parse.py::TestVerboseParsing -v    # one class
pytest tests/test_detect_family.py::TestBundledSuggestionsLoad::test_15_families -v   # one test

# Run the app / CLI (also `python -m omodel ...`)
omodel                           # launch TUI
omodel --check                   # CI-safe dry-run resolve (exit 0; degrades w/o opencode)
omodel --print                   # resolved models, no UI
omodel --config /tmp/x.jsonc     # ALWAYS use a temp path when testing saves

# Agent surface (JSON + exit codes; see `omodel agent-guide` for the full contract)
omodel targets --json
omodel candidates agent:sisyphus --json
omodel set agent:sisyphus opencode/claude-opus-4-8 --variant max --dry-run --json
omodel apply --json < assignments.json     # batch, ONE save (backup ring holds 20)
omodel preset use cheap

# Refresh opencode availability: force `opencode models --refresh` + rebuild ~/.cache/omodel
omodel --refresh-models          # in-TUI equivalent: the `r` key (off-thread)

# Regenerate bundled suggestion data (needs bun + an omo checkout; non-fatal if absent)
OMO_SRC=~/source/oh-my-openagent omodel --refresh-omo
```

opencode CLI output is cached for 24h under `~/.cache/omodel/` (`cache.py`) so warm launches/detail
are instant; `--refresh-models` / `r` bust it. Tests isolate the cache via `tests/conftest.py`
(`$OMODEL_CACHE_DIR` → tmp) and must stub `subprocess.run` (each opencode call is ~3s / ~320 MB).

`tests/verification.md` maps DESIGN.md's 9 verification checks (plus Check 10, the agent surface) to
concrete commands — use it as the
pre-release gate (it covers the live `opencode` and PyInstaller-binary checks that CI can't run).

## Architecture

A four-stage pipeline; `app.py` is the integration point that consumes all of it.

```
opencode models (live) ─► cache.py (24h) ─► catalog.py    ─┐                          ┌─► app.py (TUI)
                                                           ├─► resolve.py ─► session.py ┤
data/omo-suggestions.json ──────────────► suggestions.py ─┘   candidate-row  (headless)└─► cli.py (agent JSON)
(bundled omo snapshot)                                            dicts            │
                                                                                   ▼
                                                                        config_io.py + presets.py (save, together)
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
  (a spawned process can't be killed — stacking them OOM'd a machine). Those workers run on **daemon
  threads** (`_to_thread_daemon` in app.py, not `asyncio.to_thread`) so `q` never blocks on an
  in-flight call; `r` is single-flight. Best-effort:
  corrupt/expired → miss; write errors swallowed.
- **`suggestions.py`** — "what omo suggests." Loads the bundled JSON; `detect_family()` is a faithful
  port of omo's `detectHeuristicModelFamily` (ordered, pattern-before-includes, first match wins — order
  matters for parity). `FAMILY_VENDOR` is a hardcoded 15-family→vendor map (NOT from omo) used for
  gateway classification.
- **`resolve.py`** — the core logic. `candidates(target)` is the heart: a single filtered pass over
  omo's `fallbackChain` keeping only models you can run — **exact** match, else newest **same-line
  substitute** of the same family (`glm-5`→`glm-5.1`), else **hidden**. No connected-model dump; the
  list is chain-only plus a `+ add model…` row. Each resolved model is **expanded to one row per
  serving provider, dedicated-first** (`_ordered_providers`): a provider is a *gateway* if it serves ≥2
  vendors (`vendors_served`), and a single-vendor *dedicated* provider sorts before a gateway — so
  `gpt-5.5` shows as `openai/gpt-5.5` then `opencode/gpt-5.5` and you pick either. Data-driven — no
  hardcoded provider list. (`resolve_prefix()` keeps the single dedicated-first pick for the add-model
  modal's bare-id auto-prefix.)
- **`config_io.py`** — edit-in-place save + backups. The write is **text-preserving** (`render`): only
  the top-level `agents`/`categories` value spans are rewritten clean (`json.dumps`, no comments —
  dropping omo's commented palette *inside* them); **everything else — other keys, formatting, and any
  comments / commented-out config *outside* those two — is kept byte-for-byte** (a small JSONC-aware
  span scanner locates the two spans; non-omo / hand-broken files fall back to a full clean rewrite).
  `serialize()` is the canonical clean form (dirtiness `_is_dirty` + the from-scratch/fallback writer),
  never required to equal the on-disk bytes. Each save snapshots the prior file verbatim to
  `<config_dir>/.backup/<ts>.jsonc`; the very first save pins `original.jsonc` (never pruned, never
  counts toward the 20-snapshot cap).
- **`presets.py`** — named presets (unlimited, dense list seeded with one `default`), and they ARE
  the working state (decision #17): a leaf like
  `history.py` (pure data + file IO, no omodel imports) over `<config_dir>/.omodel-presets.json`, so
  presets follow the **active** config. Exactly one preset is active; **the config on disk always
  equals it** — that invariant drives the rest: edits flow into the active preset (`app.py`'s
  `_projected_store`), `enter` switches (banking your edits into the one you leave), `r` renames,
  `x` refuses on the active one, and **only `s` writes — both files, together**, so quitting
  discards both in lockstep. First launch with no presets seeds one from your config, in memory.
  The list is dense, so **a delete renumbers every later preset** — `app.py` remaps the undo
  history's stored indices in the same breath (`History.map_aux_key`, always PER-ENTRY: a blanket
  stamp erases older switches and the delete sentinels); that is the sharp edge. Reads are
  best-effort (missing/corrupt → empty store, `active` normalized); `write()` raises so the app can
  notify.
- **`session.py`** — the **headless core** (decision #18), and the reason the CLI can do anything
  the TUI can. Holds cfg + catalog + suggestions + resolver + the presets store, and owns every
  cfg mutation (`set_model`/`clear`/`switch_preset`) plus the both-files save. `app.py` and
  `cli.py` are both thin over it, so the rules (provider prefixing, the `none`-variant drop, the
  GPT-only lock, config-equals-active-preset) can't fork between the two surfaces. `Session.build()`
  is the shared production wiring. **Never import textual or app here** — `cli.py`'s lazy imports
  depend on it. The guards moved here from `app.py` (`GPT_ONLY_AGENTS`, `ULTRAWORK_AGENTS`,
  `is_gpt_model`, `subkinds_for`, `is_no_variant`, `read_map`, `coerce_dict`). `app.py` re-imports
  only the four it calls directly, under their old private names (`SUBKINDS`, `is_gpt_model`,
  `is_no_variant`, `subkinds_for`), and reaches the rest through the module
  (`session_mod.gpt_only` / `read_map` / `target_label`) — **the two frozensets are never imported
  at all**, which is the point: they exist in exactly one place and can't fork.
- **`app.py`** — Textual two-pane App. Wraps a `Session` and keeps only what needs a UI (the undo
  `History`, the per-target row cache, `_custom_rows`, rendering); `cfg`/`_store`/`_saved_text`/
  `_saved_store_fp` are properties onto the session. Stable widget IDs (`#targets`, `#presets`, `#candidates`,
  `#detail`, `#providers`) and option IDs (`agent:<name>[.ultrawork|.compaction]`, `cat:<name>`, `cand:<i>`,
  `cand:add`, `preset:<i>`, `preset:new`) are a contract that pilot tests depend on — see the module
  docstring; don't rename.
- **`cli.py`** — argparse dispatch, two audiences. The **flat flags** (`--print`/`--check`/
  `--restore`/`--refresh-*`/`--version`) are the human surface and are unchanged. The
  **subcommands** are the agent surface: `agent-guide`, `targets`, `show`, `candidates`, `check`,
  `set`, `clear`, `apply`, `preset` — all with `--json` and meaningful exit codes (0 ok / 1 omodel
  failed / 2 usage / **3 refused by a guard**; an agent branches on 1-vs-3). Imports are
  deliberately lazy so `--version`/`--check`/the JSON verbs never import Textual. `--config` is on
  both the main parser and a shared `parents=` parser with `default=SUPPRESS`, so
  `omodel --config X show` and `omodel show --config X` both work (without SUPPRESS the subparser
  silently clobbers the main parser's value). Two refresh flags, one per data source:
  `--refresh-omo` (bundled omo suggestions, via `refresh.py`) and `--refresh-models` (opencode
  availability, via `catalog.refresh()`). `omodel check` (exit 3 on a problem) is deliberately
  distinct from `--check` (always exit 0, CI-safe) — don't merge them.
- **`refresh.py` + `tools/snapshot_omo.ts`** — maintainer-time regeneration of the bundled data. The
  extractor runs under **bun** (node can't resolve omo's extensionless `.ts` imports).

### The integration seam: the candidate-row dict

`resolve.candidates()` yields these and `app.py` renders them — the one shape both sides agree on. Its
fields (`source`/`model`/`provider`/`variant`/`entry`/`substitute_for`/`warn`) are frozen in
**CONTRACTS.md**; the value written to config is `f"{provider}/{model}"` + `variant`. Read CONTRACTS.md
before changing any public signature or shared shape.

## Conventions specific to this repo

- **DESIGN.md is the design-of-record (the spec), CONTRACTS.md pins the frozen shapes + module
  signatures, GLOSSARY.md disambiguates the vocabulary.** Update DESIGN.md in the *same commit* as
  the code it describes; add/fix a line in GLOSSARY.md when you coin or rename a term. Read DESIGN.md
  + CONTRACTS.md before non-trivial changes; skim GLOSSARY.md when a term is ambiguous.
- **CHANGELOG: check `[Unreleased]` before every push.** This is the upkeep rule that actually gets
  skipped — twice now the tag has shipped and user-visible fixes have sat on `main` with an empty
  `[Unreleased]`, to be reconstructed from `git log` later under time pressure. Nothing enforces it;
  CI doesn't check it and the release workflow publishes an **empty** body, so the file is the only
  record. Before you push, run:

  ```sh
  git log --oneline "$(git describe --tags --abbrev=0)"..HEAD    # what's unreleased
  sed -n '/^## \[Unreleased\]/,/^## \[/p' CHANGELOG.md           # what's written down
  ```

  Every commit that changes what a user sees or what an agent gets back needs a line — behaviour,
  a flag, JSON output, an exit code, bundled-data ids. Refactors, tests, lint and CI don't (0.3.0's
  `session.py` extraction earned one line only because it's the reason the CLI exists). Write it in
  the same commit as the change, not at tag time: you will not remember why it mattered. Prefer
  describing the symptom the user hit over the mechanism you changed. Keep the tone plain — see the
  existing entries. **`## [Unreleased]` stays as a heading even when empty**; never fold a released
  section into it (a bad edit did exactly that, hiding a whole shipped release).
- **Python floor is 3.9** (CI matrix 3.9–3.13). Every module starts with
  `from __future__ import annotations`. No runtime PEP-604 unions (`isinstance(x, A | B)`) or PEP-585
  generics — annotations-as-strings make `dict | None` in signatures fine, but runtime use is not.
  Signatures now use `X | None` throughout (ruff `UP045`); that is annotation-only and safe at 3.9,
  and is NOT licence to use `|` where the expression is evaluated. The one rule the floor genuinely
  blocks is `SIM117` (combining `with` needs 3.10 parenthesized context managers) — hence its ignore.
- **Real-config safety (hard rule):** never read-then-write the live
  `~/.config/opencode/oh-my-openagent.jsonc` in tests or examples. Pass an explicit temp `path` /
  `--config` everywhere. Tests monkeypatch `subprocess.run`; no test calls real `opencode`.
- **Real-cache safety (hard rule):** never let tests touch the real `~/.cache/omodel/`. The autouse
  `tests/conftest.py` fixture redirects `$OMODEL_CACHE_DIR` to a per-test tmp dir, and `test_app_pilot.py`
  stubs `subprocess.run` so the TUI never spawns real opencode (~320 MB/call — un-stubbed it OOM'd a box).
- **Never render data as a plain `str` (hard rule, `app.py`):** Textual parses content markup in
  any plain string it renders, so a `[` in a model id / provider / variant / target name / preset
  name / `str(exc)` is a tag, and an unmatched close raises `MarkupError` *inside the render pass* —
  uncatchable, app dies. Data-carrying `Static`/`Label` take `markup=False`; `Option` prompts go
  through `_lit()`; only `#detail` renders markup, so what it splices in goes through `_esc()`;
  `OModelApp.notify` defaults `markup=False`. See DESIGN §Textual contract.
- **The model pickers (add-model + `v`) read variants from cached `opencode --verbose`**, via
  `Catalog.variants_for(provider, model)` — opencode's per-(provider, model) `variants` keys are the
  source of truth (decision #14). It prefers the first non-empty set across the picked provider then
  others (dedicated providers report `{}`; the gateway has the real set), and offers **nothing** when
  empty everywhere or uncached — no heuristic fallback (kimi/glm-5 → no variant step). `--verbose.family`
  is still never read (family stays heuristic), and the bundled family registry still backs
  `detect_family`/substitution and resolve's omo-suggestion `⚠` warn (which warn-but-allow, never block).
- **GPT-only agents:** Hephaestus mirrors omo's `no-hephaestus-non-gpt` hook via `GPT_ONLY_AGENTS` /
  `is_gpt_model` in **`session.py`** (not `app.py` — they moved there so the CLI enforces the same
  lock) — a hardcoded agent key, not a data field. Same for `ULTRAWORK_AGENTS`.

## Bundled data & packaging

- `src/omodel/data/omo-suggestions.json` is generated (do not hand-edit); regenerate via `--refresh-omo`,
  which CI also runs weekly (`refresh-suggestions.yml`) to open a PR on change. It is derived from omo
  (Sustainable Use License) — keep `NOTICE` attribution intact when redistributing.
- Distribution is **GitHub-only, no PyPI**: `release.yml` builds PyInstaller one-file binaries on `v*`
  tags (**linux-x64 + darwin-arm64 only** — Intel-mac `darwin-x64` was dropped in 0.2.0; those runners
  are being retired, and Intel macs install via pipx); `install.sh` is the curl|sh installer. Non-Python payload
  (`data/`, `tools/`) ships because it lives under the package tree and is read via `importlib.resources`
  — do **not** add a hatch force-include for it (duplicates the path and fails the wheel build).
