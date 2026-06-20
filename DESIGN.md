# oModel — a TUI to quickly set OMO models

> Brand **oModel** · command `omodel` · Python package `omodel` · repo `~/proj/oModel`
> Self-contained: a published Python package that needs **only Python + the `opencode` CLI** at runtime.

## Core idea (in one breath)

> **what omo suggests  +  what you already have  →  pick one  →  save a clean config.**

Per agent/category you see **omo's fallback chain, filtered to what you can actually run** —
each recommended model you have (exactly, or via a same-line substitute like glm-5 → glm-5.1),
resolved to a provider you're connected to. You make **one small decision** (pick a model), and oModel fills in
the fiddly parts for you: the correct `provider/` prefix and a valid `variant` (both overridable, and
it never blocks you — just ⚠-warns). See your options, choose, done. Everything below is just the
detail that makes those three steps reliable.

## Problem

`~/.config/opencode/oh-my-openagent.jsonc` sets a `model` (and optional `variant`) per **agent**
(sisyphus, hephaestus, oracle, …) and per **category** (deep, quick, writing, …), plus nested
sub-models like `sisyphus.ultrawork`. Today the file carries a big hand-curated palette of
**commented-out alternatives**; switching means hand-editing JSONC and remembering the right
`provider/` prefix and the right `variant`. That manual edit is the pain.

**Goal:** a TUI that, per agent/category, shows the current model and a candidate list built from
**what omo suggests** + **what you actually have** + **free text**, applies the correct provider
prefix and a valid variant, and saves a clean config.

## Runtime requirements

- **Python ≥ 3.9** (`importlib.resources.files`). Pin Textual to a release whose own
  `requires-python` ≤ our floor (verify at lock time, else bump floor to 3.10).
- **`opencode` CLI** on `PATH` — the source of "what you have". Degrades gracefully if missing or failing.
- **No** dependency on a local omo checkout or omo cache at runtime.
- **`bun`** (NOT node) is required **only** for the optional `omodel --refresh-omo` — see §Refresh.
  Verified: `node --experimental-strip-types` cannot run omo's modules (extensionless relative
  imports → `ERR_MODULE_NOT_FOUND`); bun resolves them.

## Decisions (locked)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Stack | Python ≥3.9 + **Textual**. Self-contained; no runtime coupling to omo source or cache. |
| 2 | Save format | **Clean active-only** `.jsonc`; **timestamped backup each save** (`.backup/<ts>.jsonc`); non-model sections preserved. |
| 3 | Picker | **One pick list = the fallbackChain, filtered to models you have** (exact, else newest same-line `detect_family` substitute; unavailable entries hidden), **expanded to one row per serving provider — dedicated (single-vendor) before aggregator/gateway.** `enter` to pick (the row's prefix is what saves); a `+ add model…` row (`e`) types anything off-chain. Suggested variant. |
| 4 | Layout | **Two-pane master-detail**. |
| 5 | Availability flagging | **Invalid variant: warn but allow** (saves with ⚠). **Unavailable fallbackChain entries: hidden** from the pick list (decision #3) — a model you can't run isn't offered; a user-typed `+ add model…` that's unavailable still ⚠-warns and saves. |
| 6 | Agent coverage | **omo-specific only** (11 with requirements). |
| 7 | Categories | **omo's known set only** (8 with requirements). |
| 8 | Prefix rule | **Dedicated-first.** A provider is a *gateway* if its `opencode models` set spans ≥2 vendors; single-vendor providers are *dedicated*. The pick list shows **every** serving provider, **dedicated before gateway** (first-seen within each tier — `_ordered_providers`), so you choose the prefix by picking the row. (`resolve_prefix` still auto-prefixes a bare id typed in the add-model modal: `dedicated[0]`, else a gateway via `providers` order then first-seen.) |
| 9 | Suggestion data | **Bundled in the wheel** (`importlib.resources`); user-override dir supported. |
| 10 | Availability source | **Live `opencode models` CLI** — not omo's cache, **not `auth list`** (see §Data sources). |
| 11 | Refresh | `omodel --refresh-omo` regenerates the suggestion JSON via **bun** + an omo checkout. |
| 12 | Distribution | **GitHub-only** (no PyPI): PyInstaller binary + `install.sh` primary; `pipx`/`uvx` from git secondary. |
| 13 | First save | **Deletes the commented-out palette** (clean active-only); the original is pinned verbatim as **`.backup/original.jsonc`** (never pruned). |
| 14 | Variant validity | **Bundled family registry only** — never `opencode --verbose` (its `variants` is opencode's runtime namespace: different shape, empty for some providers). |
| 15 | Availability cache | opencode CLI output cached **24h** at `~/.cache/omodel/` (flat: `models.json`, `verbose-<prov>.json`); read-through in `catalog`. `r` / `--refresh-models` bust + rebuild it. Detail fetch is off the UI thread and **capped to one concurrent** (each opencode call is ~3s / ~320 MB). See §cache.py. |

## Data sources

- **What you have (runtime):** parse `opencode models` → lines `provider/model` (split on the
  **first** `/`). Group → `available = {provider: [model_ids]}` (first-seen order); `connected =
  list(prefixes)` (first-seen order, never a set). Verified prefixes: `opencode deepseek
  moonshotai-cn openai zhipuai` (79 models today — count varies; tests must **not** hard-assert it).
  **Error rule (one definition, used by `catalog.load` too):** `opencode` not on `PATH` → banner +
  suggestions/add-model only; else exit code ≠ 0 **or** zero `provider/model` lines parsed → raise
  `CatalogUnavailable` → banner "couldn't read models", offer retry (`r`), degrade. (There is no other
  "partial" state.) `opencode models --refresh` is exposed as `omodel --refresh-models`, which also
  rebuilds the local cache (§cache.py).
- **Why not `opencode auth list`:** it prints provider **display names** ("Moonshot AI (China)", not
  `moonshotai-cn`) wrapped in box-drawing/ANSI with **no `--json`/plain flag** (verified) — fragile,
  and would need a name→ID map. `opencode models` already yields the usable provider set as clean IDs
  in one call (a provider appears only if it can serve models = exactly "usable"). oModel **never
  calls `auth list`**; `connected` (above) *is* the logged-in/usable set, shown as a `Providers:`
  header line. (`auth list`'s only extra info — api/oauth method, and providers logged-in-but-serving-
  zero-models — isn't needed for resolution or flags.)
- **Per-model detail (on demand):** `opencode models <provider> --verbose` emits, **per model**, a
  bare `provider/model` header line **followed by a multi-line pretty-printed (2-space) JSON block**
  (~80 lines, incl. a nested `variants` map). Parser: a header line matches
  `^(?P<prov>[a-z0-9_-]+)/(?P<model>\S+)$` at **column 0**; brace-count each following block and
  `json.loads` it. (Verified: bare `provider/model` strings never appear at column 0 *inside* a block
  — they're always quoted values — so brace-counting from each header is unambiguous.) Use
  `limit.context`, `cost.input/output` (may also carry `cost.cache.{read,write}`; free models show
  `$0`), `capabilities.reasoning`, `capabilities.input.image` for the **detail pane display only**.
  ⚠ `--verbose.family`/`--verbose.variants` are **opencode's** runtime namespace, keyed/shaped unlike
  omo's family variants — and empty for some providers (zhipuai, moonshotai-cn) while populated for
  others (openai, opencode). For parity with how omo validates/applies variants, variant validity
  comes from the **bundled family registry only** (decision #14); **never read `--verbose.variants`**.
- **What omo suggests (bundled, build-time):** `omo-suggestions.json`, generated from
  `~/source/oh-my-openagent/packages/model-core/src/` (verified importable & serializable under bun:
  11 agents, 8 categories, 14 families, 9 variants). Schema the app **consumes**:
  ```json
  { "meta": {"omoVersion":"","omoCommit":"","generatedAt":""},
    "agents":   {"<name>": {"fallbackChain":[{"providers":[],"model":"","variant":""}], "variant":"",
                            "requiresProvider":[], "requiresModel":"", "requiresAnyModel":false}},
    "categories":{"<name>": {"fallbackChain":[], "variant":""}},
    "families": [{"family":"","pattern":"<RegExp.source|null>","includes":[],"variants":[],
                  "reasoningEfforts":[],"reasoningEffortAliases":{},"supportsThinking":false}],
    "knownVariants": ["low","medium","high","xhigh","max","minimal","none","auto","thinking"] }
  ```
  `requiresProvider`/`requiresModel`/`requiresAnyModel` are **carried but IGNORED** (they gate omo's
  auto-activation; oModel is a manual picker). `pattern` is stored as a string and `re.compile`d at
  load (negative-lookaheads like `k2(?![-.]?p\d)` verified to compile under Python `re`).
- **Your config (runtime):** `$XDG_CONFIG_HOME/opencode/oh-my-openagent.jsonc` (fallback
  `~/.config/...`), `--config` override; scaffold a bundled starter if missing.

## CLI

```
omodel                          # launch the TUI
omodel --config PATH            # use a specific config file
omodel --restore                # list recent backups (newest 10) and restore one
omodel --refresh-omo [--omo-src P]  # regenerate bundled suggestion data from an omo checkout (bun required)
omodel --print                  # print current resolved agent/category models, no UI
omodel --check                  # dry-run: resolve candidate lists for every target, exit 0 (CI-safe; degrades to suggestions-only if `opencode` absent)
omodel --refresh-models         # force `opencode models --refresh` + rebuild the ~/.cache/omodel cache
omodel --version
```

## Layout (approved)

```
┌ oModel ────────────────────────────────────────────────────┐
│ AGENTS              │ sisyphus                             │
│ > sisyphus     kimi │ model: moonshotai-cn/kimi-k2.7-code  │
│     ↳ ultrawork opus│ variant: —     ctx 256k · $0.6/$2.5  │
│   hephaestus   gpt  │                                      │
│   oracle       gpt  │ ── candidates ──────────────────     │
│   momus        gpt  │  opencode/claude-opus-4-7 (max)      │
│   ...               │  openai/gpt-5.5 (medium)             │
│ CATEGORIES          │  opencode/gpt-5.5 (medium)           │
│   deep         gpt  │● zhipuai/glm-5.1  (≈ omo glm-5)      │
│   quick        mini │ + add model…                         │
└ ↑↓ move · ←→ panes · enter set · v variant · e add · x clear · a sub · s save · r · q ────┘
```

The bottom hint bar (`Static#hints`) is **pane-aware** — it shows only the keys that act on the
focused pane + highlighted row, so it stays one line (left pane drops `enter set`/`v`/`x`; the
`+ add model…` row shows `enter add`; a category row drops `a sub`). See §Textual contract.

## Repo layout (src-layout, PyPI-ready)

```
oModel/
  pyproject.toml                 # hatchling; [project.scripts] omodel = "omodel.cli:main"
  README.md  LICENSE  NOTICE  CHANGELOG.md
  install.sh                     # curl|sh: detect os/arch → download release binary → ~/.local/bin
  src/omodel/
    __init__.py
    cli.py            # argparse: default → TUI; --config/--restore/--print/--check/--refresh-omo/--refresh-models
    app.py            # Textual two-pane App (see §Textual contract)
    catalog.py        # availability via `opencode models`; verbose-record parser; providers_for(); refresh()
    cache.py          # 24h on-disk cache of opencode stdout (~/.cache/omodel); read-through by catalog
    suggestions.py    # load bundled/override omo-suggestions.json; detect_family(); variants
    resolve.py        # prefix (prefer-dedicated), variant defaulting/validation, candidate assembly
    config_io.py      # read jsonc (json5) → dict; serialize(); diff+confirm save; .bak; scaffold
    refresh.py        # locate omo src + bun; run extractor; write repo or user-data override
    data/
      omo-suggestions.json        # BUNDLED, committed (regenerated by --refresh-omo)
      default-config.jsonc        # BUNDLED starter — oModel's OWN minimal template (not vendored)
    tools/
      snapshot_omo.ts             # BUNDLED extractor (oModel's own code; imports omo at maintainer time)
  tests/
    test_catalog_parse.py         # mocked `opencode models` + multi-block `--verbose` records
    test_resolve.py               # prefer-dedicated order, variant validity, ⚠ flags
    test_detect_family.py         # parity vs omo (kimi vs k2p#, opus vs non-opus, gpt-5 vs o-series)
    test_config_io.py             # clean rewrite preserves non-model sections; .bak; comment loss
    test_app_pilot.py             # Textual App.run_test() set + save via queryable IDs
  .github/workflows/
    ci.yml                        # lint + tests (opencode + bun mocked; no omo source needed)
    release.yml                   # on tag → PyInstaller one-file binary → attach to GitHub Release
    refresh-suggestions.yml       # checkout omo @ pinned tag → bun extractor → PR on change
```

## Components

### Data contracts (shared shapes — fix once so `resolve.py` and `app.py` agree)
- `target` id (string): `"agent:<name>"`, `"agent:<name>.ultrawork"`, `"agent:<name>.compaction"`, or
  `"cat:<name>"` — identical to the §Textual `OptionList#targets` option IDs.
- `source` (string enum): `"omo"` (a `fallbackChain` entry — exact or same-line substitute) ·
  `"add"` (typed in the add-model modal). (`"mine"` retired — no connected-model dump.)
- **candidate row** — dict yielded by `candidates()` and rendered by `app.py`:
  ```python
  {
    "source":   "omo" | "add",
    "model":    "glm-5.1",                   # RESOLVED bare id actually used (the substitute,
                                             #   for a same-line stand-in), no prefix
    "provider": "zhipuai",                   # one serving provider; candidates() emits one
                                             #   ROW PER provider, dedicated-first (non-empty str)
    "variant":  "max" | None,                # per precedence; None = unset
    "entry":    {...} | None,                # the omo fallbackChain entry; None for 'add'
    "substitute_for": None | "glm-5",        # None = exact; else the omo id this same-line row fills
    "warn":     [] | ["variant"],            # 'omo' rows: variant only; 'add' rows may add "unavailable"
  }
  ```
  The value written to config is `f"{provider}/{model}"` plus `variant` (omitted when `None`).

### `catalog.py` — availability from `opencode`
- `load()`: `opencode models` → `available` (dict) + `connected` (**list**, first-seen order — never
  a set). Per the §Data sources error rule: exit code ≠ 0 **or** zero `provider/model` lines parsed →
  raise `CatalogUnavailable` (UI shows banner + retry); `opencode` not on `PATH` → empty + banner.
- `providers_for(model_id)` → connected providers that have it, in first-seen order.
- `detail(model_id)`: query `<provider>` = the model's **resolved** provider (first of
  `providers_for(model_id)`); run `opencode models <provider> --verbose`; split records on header
  lines `^(?P<prov>[a-z0-9_-]+)/(?P<model>\S+)$` (col 0), brace-count each block, `json.loads`, and
  pick the record whose header == `<provider>/<model_id>` → `{context, cost, reasoning, image}` for
  the detail pane (display only). This is a ~3s subprocess, so `app.py` calls it from a background
  worker (cached per model, debounced) — never on the UI thread (see §Textual two-pane contract).

### `cache.py` — on-disk opencode cache
- Both opencode subprocesses (`opencode models` ~3s, and `opencode models <prov> --verbose` ~3s /
  ~320 MB RSS) are cached **24h** under `~/.cache/omodel/` (`$OMODEL_CACHE_DIR` → `$XDG_CACHE_HOME/omodel`
  → `~/.cache/omodel`), **flat**: `models.json` + one `verbose-<provider>.json` per provider. Each file
  wraps stdout as `{version, fetched_at, args, stdout}` — explicit `fetched_at` (not mtime; survives
  copies) and a `version` that auto-invalidates on format change. Reads tolerate missing/corrupt/expired
  (→ miss); writes are atomic (`os.replace`) and swallow errors, so a non-writable cache never breaks the
  app. `clear()` removes only `*.json` (+ orphaned `*.tmp-*`), never foreign files.
- `catalog.load()`/`detail()` read through it (`use_cache=True`). opencode presence is checked **first**,
  so "not on `PATH` → empty" (above) is unchanged — the cache is a perf layer, not an availability
  fallback. A live, successful run rewrites the cache; every opencode call carries a `timeout=`.
- `catalog.refresh()` — the `r` key / `omodel --refresh-models` — runs `opencode models --refresh`
  (network re-fetch), clears the cache, and rewrites `models.json` from the result. The TUI runs it in a
  worker (off the UI thread); the `Providers:` header shows cache age (`cached 3h ago · r to refresh`).
- **Memory safety (load-bearing):** `asyncio.to_thread` threads can't be killed, so the detail fetch is
  **capped to one concurrent** (a `_detail_fetching` gate; on completion the worker re-renders the
  *current* target, which schedules the next — "chase the cursor"). Uncapped/un-stubbed, stacked
  ~320 MB `--verbose` processes OOM'd a machine; a refresh bumps a generation counter so an in-flight
  fetch discards its now-stale result. Tests stub `subprocess.run` and isolate the cache dir
  (`tests/conftest.py` → `$OMODEL_CACHE_DIR`).

### `suggestions.py` — bundled omo data
- Load order: `$OMODEL_SUGGESTIONS` → `$XDG_DATA_HOME/omodel/omo-suggestions.json` (from `--refresh-omo`)
  → bundled `importlib.resources.files("omodel.data")/"omo-suggestions.json"`.
- `detect_family(model_id)` — faithful port of `detectHeuristicModelFamily`: **ordered** iteration of
  `families`, `pattern` tested before `includes` within each entry, first match wins; run
  `normalize_model_id` first (`re.sub(r"\.(\d+)", r"-\1", s).lower()` → `kimi-k2.7`→`kimi-k2-7`).
  Patterns pre-`re.compile`d. (Parity matters: `openai-reasoning` before `gpt-5`, `kimi-thinking`
  before `kimi`, `claude-opus` before `claude-non-opus`.)
- **Entry shape retained:** each `fallbackChain` item keeps `{providers[], model, variant?, …}` — the
  `providers` array (omo's per-model preference order) is **kept** for the gateway tie-break in
  `resolve_prefix`.
- **`FAMILY_VENDOR` — hardcoded dict in `suggestions.py` (NOT from omo; omo has no such table).** The
  complete 14-family → vendor map used by `vendors_served`. The authoritative table is the
  `FAMILY_VENDOR` dict in `src/omodel/suggestions.py` — read it there; not duplicated here (it drifts).
  `vendor(family) = FAMILY_VENDOR.get(family)` → `None` for unknown/None. Models whose `detect_family`
  is `None` (opencode's `big-pickle`, `qwen3.x-plus`, `*-free`, `nemotron-*` — no omo family)
  contribute **no** vendor and are skipped in `vendors_served`; **do not invent a family for them**.

### `resolve.py` — core logic
- **Gateway detection (`vendors_served`):** for each connected provider `p`,
  `vendors_served(p) = len({ vendor(detect_family(m)) for m in available[p] } - {None})` using the
  complete `FAMILY_VENDOR` map (§suggestions.py). `p` is a **gateway** iff `vendors_served(p) ≥ 2`,
  else **dedicated**; `gateways = {p for p in connected if vendors_served(p) >= 2}` is computed once at
  load. Data-driven, no hardcoded provider list — `opencode`/`openrouter`/`vercel`/`github-copilot`
  (and any future) self-classify; `openai`'s three families all map to vendor `openai` so it counts as
  **one** = dedicated. Verified live: `opencode`→8 vendors→gateway;
  `openai`/`zhipuai`/`moonshotai-cn`/`deepseek`→1→dedicated.
- **`resolve_prefix(model_id, source, entry=None)` (dedicated-first):** *mine* → its provider; else
  `cands = providers_for(model_id)`; `dedicated = [p for p in cands if p not in gateways]` → pick
  `dedicated[0]` (first-seen) if any; else only gateways serve it → walk `entry.providers` and pick
  the first **that is in `cands`**, else `cands[0]`. NB: `entry.providers` are omo-world IDs
  (`anthropic`, `github-copilot`, `vercel`, `zai-coding-plan`, …) that rarely intersect the user's
  `connected` set, so the `cands[0]` first-seen fallback is the common path; **both branches range over
  `providers_for` (availability IDs), never raw omo IDs**. `candidates()` no longer calls this — it
  lists *every* serving provider (`_ordered_providers`); `resolve_prefix` now only auto-prefixes a bare
  id typed in the add-model modal. Verified: `gpt-5.5`→`openai/…`,
  `claude-opus-4-7`→`opencode/…` (only gateway has it), `kimi-k2.5`→`moonshotai-cn/…`,
  `glm-5`→`zhipuai/…`. (`kimi-k2.5/2.6` and `glm-5/5.1` exist under both opencode and a dedicated
  provider — dedicated heads the list; add a second gateway like `openrouter` and it appears as just
  another row after the dedicated one.)
- **`_ordered_providers(model_id)` → list:** every connected provider serving the model, **dedicated
  (single-vendor) before aggregator/gateway**, first-seen within each tier (`[]` if none).
  `candidates()` emits one row per provider in this order — `glm-5` → `zhipuai/glm-5` then
  `opencode/glm-5`; `gpt-5.5` → `openai/gpt-5.5` then `opencode/gpt-5.5` — so the prefix is chosen by
  picking the row (no `p`-cycling).
- **`candidates(target)`:** one pick list — a single filtered pass over the `fallbackChain`, in
  chain (priority) order. For each entry: **(1) exact** — a connected provider serves the entry's
  model verbatim → that model (`substitute_for=None`); **(2) same-line** —
  else the **newest connected model of the same `detect_family`** (version-agnostic: `glm-5` →
  `glm-5.1`; "newest" = highest digit-tuple, ties → first-seen)
  (`substitute_for=<omo id>`); if that newest same-line model is itself an exactly-available chain
  entry, this entry is **skipped** (deferred to that model's own exact row) — never demoted to an
  *older* same-line model (so an unavailable `minimax-m3` resolves to the newest `minimax-m2.7` you
  have, not an older `minimax-m2.5`); **(3) else hidden** (neither exact nor same-line
  connected — a model you can't run isn't offered). Each entry id first passes through a hardcoded
  **omo-id alias** (`_OMO_MODEL_ALIASES`, oModel-only — omo has no such table): `k2p5` (a provider's
  dot-free spelling of kimi-k2.5) is treated as **exactly `kimi-k2.5`**, overriding omo's heuristic
  that would file the `p<digit>` suffix under the kimi-*thinking* family and pull in a kimi-k2-thinking
  model. The alias acts only here in `candidates()`; `detect_family`/`normalize_model_id` stay a
  faithful port. Each resolved model **expands to one row per serving provider** (dedicated-first,
  `_ordered_providers`); rows are then **deduped by resolved `provider/model`** (higher-priority
  entry/provider wins). **Variant precedence:** entry `variant` → requirement top-level
  `variant` → **none** (the family registry only *validates* variants — designates no default — so an
  unspecified variant stays unset; set one via `v`). (Top-level requirement `variant` is presently
  **always empty** in omo, so exercise that tier with a *synthetic* fixture, not a real ID.) Last row
  is `+ add model…` (`cand:add`) for off-chain picks; `enter` on any non-`add` row stages it. Flag:
  `⚠ variant` (variant ∉ family `variants` from the **bundled registry only**). (Unavailable entries
  are hidden, not flagged — decision #5.) **Current pick (`●`):** the row whose resolved
  `provider/model` equals what `oh-my-openagent.jsonc` has on disk for this target — snapshotted at
  launch (the file that becomes `.backup/original.jsonc`), so it stays put as you stage edits — is
  prefixed `● `; all other rows get a 2-space prefix. If the on-disk model isn't in the (chain-only)
  list (an off-chain hand-pick), nothing is marked.
- **GPT-only agents (Hephaestus):** omo's `no-hephaestus-non-gpt` hook makes Hephaestus
  GPT-exclusive (`isGptModel` = model name after the last `/`, lowercased, contains "gpt"; a non-GPT
  model reassigns the session to Sisyphus). oModel mirrors this for `agent:hephaestus[.sub]`: the
  `+ add model…` row stays, but the add modal is **gated** — a non-GPT model is **blocked** (enter
  disabled, `⚠ Hephaestus is GPT-only`), so you can pick any GPT model you have but can't footgun a
  non-GPT one; the detail pane shows a `⚑ GPT-only` tip. Encoded as `_GPT_ONLY_AGENTS` +
  `_is_gpt_model` in `app.py` (matching omo's hard-coded agent key, not a data field — `requires*`
  are activation flags, not user-choice restrictions).

### `config_io.py` — clean rewrite
- Read `json5.load` → ordered dict; `agents`/`categories` editable, all other top-level keys
  (`claude_code`, `experimental`, `team_mode`, `$schema`, future) passed through by value. (Comments
  **inside** preserved sections — e.g. a `//"skills": false` line within `claude_code` — are also
  dropped on rewrite; only `.backup/original.jsonc` retains them. Expected, not a bug.)
- **`serialize(cfg) -> str` (exact):** (1) build an ordered dict preserving on-disk key order, but
  **force `$schema` to position 0** if present; (2) within `agents`/`categories`, a freshly-added
  sub-key (`ultrawork`/`compaction`) is **appended** to the end of its parent object, a cleared field
  is **deleted**; (3) `body = json.dumps(cfg, indent=2, ensure_ascii=False)` — note `json.dumps`
  **cannot** emit comments, do not try; (4) return `"// Generated by oModel — edit via \`omodel\`\n"`
  `+ body + "\n"` (single trailing newline). Editable units: each agent's `model`/`variant`, its
  `ultrawork`/`compaction` `{model,variant}`; each category's `model`/`variant`. Example output head:
  ```jsonc
  // Generated by oModel — edit via `omodel`
  {
    "$schema": "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/assets/oh-my-opencode.schema.json",
    "agents": {
      "sisyphus": {
        "model": "moonshotai-cn/kimi-k2.5"
      }
    },
    "categories": {}
  }
  ```
- **Save flow:** diff `serialize(cfg)` vs on-disk file → confirm modal showing the diff → on accept,
  snapshot the current on-disk file to `<config_dir>/.backup/<ts>.jsonc` (**verbatim byte copy** —
  preserves comments), then atomic temp+rename of the new content. No diff → "nothing to save".
- **Backups & rollback:** `<config_dir>/.backup/` (next to the config; `<config_dir>` = dir of the
  active config, default `~/.config/opencode/`). **Exact save order (this sequence):** (1) if
  `.backup/original.jsonc` does **not** exist, copy the current on-disk config to it (verbatim);
  (2) write the verbatim timestamped snapshot `YYYYMMDD-HHMMSS[.mmm].jsonc` (UTC, sorts
  lexicographically; `.mmm` avoids same-second collisions); (3) prune **only** timestamped snapshots —
  `glob("[0-9]*.jsonc")`, which **excludes `original.jsonc`** — to the newest 20. So `original.jsonc`
  is written once, never overwritten, never pruned, and **never counts toward the 20** (your pristine
  pre-oModel palette). `omodel --restore` (and a TUI key) lists the **pinned `original.jsonc` + the
  newest 10** timestamped (each with timestamp + size / short diff); items 11–20 are an unlisted
  on-disk buffer. Restoring first snapshots the *current* file (so restore is itself undoable), then
  copies the chosen backup to the config path.
- ⚠ **First save is lossy by design:** the live config is comment-dense (3–6 commented alternatives
  per agent); `json5.load` drops comments, so the first clean save **deletes the whole palette**.
  Intended (decision #13); the palette is preserved verbatim as the pinned **`.backup/original.jsonc`**
  (never pruned, always restorable) — surface this in the first confirm modal.
- Missing config → scaffold oModel's own minimal `default-config.jsonc`, then open it. Template (the
  `$schema` is a **literal hardcoded string** committed in `default-config.jsonc`; nothing in the
  refresh path writes it):
  `{ "$schema": "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/assets/oh-my-opencode.schema.json", "agents": {}, "categories": {} }`
  — valid and minimal; the left pane is populated from the bundled snapshot, so empty maps still show
  all 11 agents / 8 categories as unset, and only what you set gets written.

### `refresh.py` — `omodel --refresh-omo`
- Locate omo src: `--omo-src` | `$OMO_SRC` | `~/source/oh-my-openagent` (needs
  `packages/model-core/src`). Runner: **bun only** (no node fallback — verified broken).
- Run bundled `tools/snapshot_omo.ts` → JSON (RegExp→`.source`, Set→array, + `meta`).
- Write target: writable repo checkout (`src/omodel/data/`) → write there (maintainer commits);
  else `$XDG_DATA_HOME/omodel/omo-suggestions.json` (user override).
- Missing omo src or bun → **non-fatal**: print current bundled `meta`, keep bundled data.

### `tools/snapshot_omo.ts` — the extractor (bun, maintainer-time)
Real source: `src/omodel/tools/snapshot_omo.ts` — read it there; not inlined here (it drifts). Design
contract: at maintainer time it dynamically `import`s omo's `packages/model-core/src` modules
(`model-capability-heuristics`, `agent-model-requirements`, `category-model-requirements`,
`known-variants`) — **bun** resolves omo's extensionless `.ts`, node can't (see §Runtime requirements) —
and prints JSON matching the §Data sources "what omo suggests" schema: each RegExp `pattern` →
`.source` string (e.g. `claude(?:-\d+(?:-\d+)*)?-opus`), `Set` → array, plus a `meta` block
(`omoVersion` from omo's `package.json`, `omoCommit` from `git rev-parse`, `generatedAt`). `refresh.py`
runs `bun run <this file> <omo-src>` and writes stdout to the data file.

### Textual two-pane contract (`app.py`)
- **Header** `Static#providers`: one line `Providers: <id · id · …>` from `catalog.connected` in its
  **first-seen order** (per §Data sources; e.g. `opencode · deepseek · moonshotai-cn · openai ·
  zhipuai`) — so you see what's available at a glance; doubles as the
  ⚠-unavailable explainer ("no listed provider serves this"). When the list came from the 24h cache it
  also shows its age (`cached 3h ago · r to refresh`; see §cache.py). On `CatalogUnavailable` it shows
  the banner + `r` retry instead.
- **Left** `OptionList#targets`: AGENTS then CATEGORIES; option IDs `agent:<name>`,
  `agent:<name>.ultrawork` / `.compaction` (indented sub-rows, shown when present in config or added
  via `a`), `cat:<name>`. Sub-target set per agent = `{model}` ∪ present `{ultrawork, compaction}`;
  `a` adds an `ultrawork`/`compaction` sub-target (verified: omo schema permits both on all 11 agents).
- **Right**: `Static#detail` (current model/variant + `catalog.detail` line) and
  `OptionList#candidates` (IDs `cand:<i>`, last = `cand:add` — the `+ add model…` row). The `cand:<i>`
  row matching the launch-time on-disk assignment is prefixed `● ` (others `  `). The `catalog.detail`
  line is a ~3s / ~320 MB subprocess, so it is fetched in a background worker (cached per model,
  debounced ~0.2s, and **capped to one fetch at a time** — §cache.py) and appears when ready; the rest
  of the pane renders instantly so highlighting is never blocked.
- **Hint bar** `Static#hints` (bottom row): **pane-aware** key hints — only the keys that do
  something for the focused pane + highlighted row, so it stays one line. Left/targets:
  `↑↓ move · → candidates · [a sub ·] s save · r refresh · q quit` (`a sub` only on an agent row).
  Right/candidates: `↑↓ move · ← targets · enter set · v variant · e add · x clear · s save · r · q`,
  or `… · enter add · …` on the `+ add model…` row. Re-rendered on focus (`on_descendant_focus`)
  and highlight changes. Modals carry their own one-line hint (`Static.modal-hints`) instead.
- **Events:** highlight on `#targets` → repopulate detail+candidates for that target;
  `enter` on `#candidates` **dispatches by row**: on `cand:add` → open the add-model modal (below);
  on any other `cand:<i>` → set that model (+ default variant) on the in-memory target;
  `v` → push `OptionList` of the family's valid variants + `(none)`; `e` (or `enter` on `cand:add`) →
  the add-model modal (below); `x` → clear; `a` → add sub-target; `s` → diff+confirm save; `r` → refresh
  (off-thread `opencode models --refresh` + rebuild cache; also retries after `CatalogUnavailable`);
  `q` → quit (confirm if dirty); `←`/`→` → focus the targets / candidates pane (gated to the base
  screen via `check_action`, so it never grabs focus from under a modal; the add-model `Input` keeps
  its cursor arrows). Pilot tests drive these via the stable IDs.
- **Add-model modal (`e` / `cand:add`):** empty one-line `Input` for `provider/model` + a live preview
  of what saves. A full `provider/model` → used **verbatim** (split on the *first* `/`, so
  `openrouter/anthropic/…` works); a bare id → auto-prefixed via `resolve_prefix` **if available**,
  else `⚠ unknown — add a provider/` and `enter` is **blocked** until qualified. Accept → inserts a
  selected `+ custom` row (default variant via `detect_family`); `⚠ unavailable` is allowed
  (warn-but-allow, decision #5). Not a separate mode — the result is just another pickable row.

## Packaging & distribution (GitHub-only, no PyPI)

- `pyproject.toml` (hatchling, src-layout); force-include `data/*.json`, `data/*.jsonc`, `tools/*.ts`.
  `requires-python = ">=3.9"`; deps `textual` (pinned), `json5`. Entry point
  `[project.scripts] omodel = "omodel.cli:main"`.
- **Primary — standalone binary + installer (GitHub Releases):** PyInstaller **one-file** build,
  `pyinstaller --onefile --name omodel --collect-data omodel src/omodel/__main__.py` (bundles
  `data/` + `tools/`; `importlib.resources` reads them from the frozen package). CI `release.yml`
  builds on tag push (matrix: **linux-x64** `ubuntu-latest`, **macos-arm64** `macos-latest`,
  **macos-x64** `macos-13`) and attaches `omodel-<os>-<arch>` (+ `.tar.gz`) to the Release.
  `install.sh` detects OS/arch (`linux-x64`, `darwin-arm64`, `darwin-x64`), downloads the matching
  asset, installs `omodel` to `~/.local/bin`:
  `curl -fsSL https://raw.githubusercontent.com/<you>/oModel/main/install.sh | sh`.
- **Secondary — pip/pipx/uvx straight from GitHub (no PyPI):**
  `pipx install git+https://github.com/<you>/oModel` ·
  `uvx --from git+https://github.com/<you>/oModel omodel` ·
  `uv tool install git+https://github.com/<you>/oModel`.
- **Maintainer:** `git clone … && uv pip install -e .`; refresh data with
  `OMO_SRC=~/source/oh-my-openagent omodel --refresh-omo`, commit `src/omodel/data/omo-suggestions.json`;
  `git tag vX.Y.Z && git push --tags` → `release.yml` builds and publishes the binary.
- ⚠ **Licensing:** the bundled `omo-suggestions.json` is **data derived from omo source**, redistributed
  in both the repo and the binary. Confirm omo's `LICENSE.md`/`CLA.md`/`THIRD-PARTY-NOTICES.md` permit
  it and add attribution in `NOTICE`. `default-config.jsonc` is oModel's own (not copied) to avoid this.

## Verification (fixtures use REAL omo suggestion IDs)

1. **Build/install:** `pipx install .` (and `pipx install git+https://…` once pushed); `omodel
   --version`; `omodel --check` runs with no omo source. Then a PyInstaller one-file build → run the
   **binary's** `omodel --version`/`--check` to confirm bundled `data/` loads via `importlib.resources`;
   `install.sh` places it on PATH.
2. **Availability + prefix (unit, mocked `opencode models`):** `vendors_served` classifies
   `opencode`/`openrouter`→gateway and `openai`/`zhipuai`/`moonshotai-cn`/`deepseek`→dedicated.
   `providers_for("gpt-5.5") == ["opencode","openai"]` → list shows `openai/gpt-5.5` **then** `opencode/gpt-5.5` (dedicated-first);
   `claude-opus-4-7` → `["opencode"]` → `opencode/claude-opus-4-7`; `kimi-k2.5` →
   `moonshotai-cn/kimi-k2.5`; `glm-5` → `zhipuai/glm-5`. A chain entry with no connected provider and
   no same-line relative is **omitted** from `candidates()`; with only `glm-5.1` connected, the `glm-5`
   entry resolves to a `zhipuai/glm-5.1` substitute row (`substitute_for="glm-5"`). `glm + max` renders
   ⚠ variant but accepts. With `openrouter` also connected, a both-gateways-only model lists *both*
   gateway rows in first-seen order; `resolve_prefix` (add-modal single pick) still tie-breaks via
   `entry.providers`-then-first-seen.
3. **Verbose parsing (unit):** feed a captured multi-record `--verbose` blob → N records with
   `limit.context`/`cost`/`capabilities` extracted; confirm variant logic does NOT read it.
4. **detect_family parity:** `kimi-k2.5`→`kimi` (no `max`), `k2p5`→`kimi-thinking`, `claude-opus-4-7`
   →`claude-opus` (has `max`), `gpt-5.5`→`gpt-5` (`xhigh`), `glm-5`→`glm` (no `max`),
   `deepseek-v4-pro`→`deepseek` (has `max`).
5. **Bundled suggestions:** `importlib.resources` loads with no omo checkout; 11 agents, 8 categories.
6. **Refresh:** checkout + `OMO_SRC` + bun → rewrites data file (meta bumped); no omo/bun → non-fatal.
7. **Headless UI (Pilot):** select `agent:sisyphus`, set `cand:*` → `deepseek/deepseek-v4-pro`, `s`,
   confirm → re-`json5.load`: model updated, `team_mode`/`experimental`/`claude_code` unchanged by
   value, palette comments gone, a `.backup/<ts>.jsonc` snapshot exists (verbatim original); a second
   save adds a second snapshot and `--restore` lists them newest-first.
8. **Live:** machine with `opencode`, no omo source → `omodel` launches, lists from `opencode models`,
   edits + saves a clean file OMO reloads.

## Execution playbook (team fan-out)

When ready to build, fan out as **6 specialists + a lead**, contract-first (model tier = "Safety").
**Launch trigger:** the user says "go" (say "lean" to drop the QA agent → 5; "cost-lean" to re-tier
Config/TUI to Haiku).

### Roster
| Role | Owns | Model |
|---|---|---|
| **Lead / Integrator** | §Data contracts + module signatures; repo scaffold; generate real bundled data (`snapshot_omo.ts`→`omo-suggestions.json`) + `default-config.jsonc`; wire `app.py`↔modules; final integration | **Opus** |
| **Core logic** | `catalog.py` · `suggestions.py` (detect_family, FAMILY_VENDOR) · `resolve.py` · `tools/snapshot_omo.ts` | **Sonnet** |
| **Config I/O** | `config_io.py` (serialize, backups/restore, scaffold) | **Sonnet** |
| **TUI** | `app.py` two-pane + variant/add-model/diff modals + keybindings | **Opus** |
| **CLI + packaging** | `cli.py` · `refresh.py` · `pyproject.toml` · `install.sh` · `.github/workflows/*` · README/LICENSE/NOTICE | **Sonnet** |
| **QA / verification** | all 5 `tests/test_*` authored **from this spec, independent of the implementations** + runs the 8 §Verification checks as the **merge gate** | **Sonnet** |

### Sequencing (contract-first)
0. **Lead (blocking):** freeze §Data contracts (`target` id, `source` enum, candidate-row dict) + each
   module's public signatures; scaffold the repo (`pyproject`, package dirs, stub modules); generate the
   real `omo-suggestions.json` (bun + omo checkout) and hand-write `default-config.jsonc`. Unblocks all.
1. **Fan out in parallel (isolated git worktrees):** Core, Config, TUI, CLI+packaging, QA each in their
   own worktree against the frozen interfaces. QA writes tests from the spec + stable widget IDs in
   parallel (not blocked on implementations).
2. **Integrate (lead):** merge tracks, wire `app.py` to catalog/suggestions/resolve/config_io, reconcile
   any interface drift against the §Data contracts.
3. **Gate (QA + lead):** QA's `test_*` green **and** all 8 §Verification checks pass (incl. a live
   `opencode` run + a Pilot save round-trip). Nothing ships until green.

### Notes
- **Integration risk concentrates at `app.py`** (it consumes all four modules); the §Data-contracts
  block is what lets it be built in parallel against frozen shapes. Lead owns final wiring.
- **Dependencies:** `resolve.py` → `suggestions.py` + `catalog.py`; `refresh.py` → `snapshot_omo.ts`.
  `config_io.py` and CLI+packaging are near-independent; everything else parallelizes once contracts
  are frozen.
