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
- **No network access at runtime** — with exactly one opt-in exception, `omodel --update`, which
  reaches api.github.com only when that flag is passed (decision #19, §update.py). Launching the
  TUI, resolving, and every JSON verb work offline.
- **`bun`** (NOT node) is required **only** for the optional `omodel --refresh-omo` — see §Refresh.
  Verified: `node --experimental-strip-types` cannot run omo's modules (extensionless relative
  imports → `ERR_MODULE_NOT_FOUND`); bun resolves them.

## Decisions (locked)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Stack | Python ≥3.9 + **Textual**. Self-contained; no runtime coupling to omo source or cache. |
| 2 | Save format | **Edit-in-place**: only `agents`/`categories` are rewritten clean; **everything else — other keys, formatting, comments, commented-out config — is preserved byte-for-byte** (`render()` splices just those two spans). **Timestamped backup each save** (`.backup/<ts>.jsonc`). |
| 3 | Picker | **One pick list = the fallbackChain, filtered to models you have** (exact, else newest same-line `detect_family` substitute; unavailable entries hidden), **expanded to one row per serving provider — dedicated (single-vendor) before aggregator/gateway.** `enter` to pick (the row's prefix is what saves); a `+ add model…` row (`a`) types anything off-chain. Suggested variant. |
| 4 | Layout | **Two-pane list-detail**. |
| 5 | Availability flagging | **Invalid variant: warn but allow** (saves with ⚠). **Unavailable fallbackChain entries: hidden** from the pick list (decision #3) — a model you can't run isn't offered; a user-typed `+ add model…` that's unavailable still ⚠-warns and saves. |
| 6 | Agent coverage | **omo-specific only** (11 with requirements). |
| 7 | Categories | **omo's known set only** (8 with requirements). |
| 8 | Prefix rule | **Dedicated-first.** A provider is a *gateway* if its `opencode models` set spans ≥2 vendors; single-vendor providers are *dedicated*. The pick list shows **every** serving provider, **dedicated before gateway** (first-seen within each tier — `_ordered_providers`), so you choose the prefix by picking the row. (`resolve_prefix` still auto-prefixes a bare id typed in the add-model modal: `dedicated[0]`, else a gateway via `providers` order then first-seen.) |
| 9 | Suggestion data | **Bundled in the wheel** (`importlib.resources`); user-override dir supported. |
| 10 | Availability source | **Live `opencode models` CLI** — not omo's cache, **not `auth list`** (see §Data sources). |
| 11 | Refresh | `omodel --refresh-omo` regenerates the suggestion JSON via **bun** + an omo checkout. |
| 12 | Distribution | **GitHub-only** (no PyPI): PyInstaller binary + `install.sh` primary; `pipx`/`uvx` from git secondary. |
| 13 | First save | **Deletes the commented-out palette *inside* agents/categories** (those spans are rewritten clean); comments / commented-out config **outside** them are kept verbatim. The whole original is pinned verbatim as **`.backup/original.jsonc`** (never pruned). |
| 14 | Variant validity (pickers) | **opencode `--verbose` (cached) is the source of truth** for the add-model + `v` pickers (`Catalog.variants_for`): per-(provider, model) `variants` keys; prefer the first NON-EMPTY set across the picked provider then the gateway (dedicated providers report `{}`); empty everywhere / uncached → **offer nothing, no heuristic fallback** (kimi, glm-5 → no variant step). A `none` in that set is dropped as a duplicate of the synthetic `(none)` clear row (`_is_no_variant`) — never offered, never written (`none` ≡ `(none)` ≡ no `variant` key). The bundled family registry stays the source for `detect_family`/substitution; the omo-suggestion ⚠ warn (`resolve._variant_warn`) **also** prefers `--verbose` now — the heuristic family `variants` is its fallback only when opencode is silent (dedicated `{}` / uncached) — but the registry is never the source for what the pickers offer. (Reverses the old "registry only, never `--verbose`" rule.) |
| 15 | Availability cache | opencode CLI output cached **24h** at `~/.cache/omodel/` (flat: `models.json`, `verbose-<prov>.json`); read-through in `catalog`. `r` / `--refresh-models` bust + rebuild it. Detail fetch is off the UI thread and **capped to one concurrent** (each opencode call is ~3s / ~320 MB). See §cache.py. |
| 16 | Undo | **In-session undo/redo of every edit** (`u` / `ctrl+r`) for mis-press recovery — a snapshot stack of cfg states (`history.py`), separate from the on-disk `.backup/` (decision #2). Each edit (set/clear/variant/add-model/add-sub/delete-sub) records a labelled snapshot; dirtiness is **computed** (`serialize(cfg)` vs last-saved text), so undo-to-saved reads clean. See §history.py. |
| 17 | Presets | **Named presets ARE the working state** (as many as you keep — seeded with one `default`), in their own pane under `#targets`, stored next to the config (`<config_dir>/.omodel-presets.json`). Exactly one is **active**, and the invariant is: **the config on disk always equals the active preset — never a fourth, orphan state.** Your edits go into the active preset; `enter` switches (a replace, banking your edits into the one you leave); `a` adds one holding the current models (as does `enter` on the trailing `+ add preset…` row) — it is row-blind and never overwrites; `r` renames; `x` refuses on the active one (so you can never reach zero). **One write rule: only `s` touches disk, and it writes BOTH files** — so quitting without saving discards both in lockstep and the invariant survives. See §presets.py. |
| 18 | Agent surface | **omodel is a tool an LLM agent can call**, not only a TUI a human drives: JSON subcommands (§CLI) over a headless `session.py` that `app.py` and `cli.py` BOTH edit through. The alternative — leaving the CLI read-only — was rejected because an agent asked to change a model would then hand-edit `oh-my-openagent.jsonc`, bypassing provider prefixing, variant validity, the GPT-only lock, the backup and the preset invariant: exactly the failure mode oModel exists to prevent. So the extraction is the point, and the verbs are the cheap part. `session.py` must never import Textual (cli.py's lazy-import discipline depends on it). |
| 19 | Self-update | **`omodel --update` updates the program, on demand, after asking.** A flat flag, not a subcommand: the subcommands are the agent surface (#18), and this one edits no config and is not a model change. No launch-time version check and no background poll — decision #1's "self-contained, no runtime coupling" covers the network too, and a TUI that phones home on every start to say "0.3.1 is out" has spent a socket timeout on the path where the user wanted a model picker. It **confirms before swapping** (like `--restore`), which is why there is no `--update-check`: declining is the check, `--json`/no-TTY decline by construction, `--yes` is the one way to mean it. Only the **standalone binary** is replaced in place (download → published sha256 → **run the new binary and require the release's version back** → `os.replace`); pipx/uv/pip/source installs get a tag-pinned command and exit 3, because writing into a tree another package manager owns is how you end up with a venv and no way back. The rejected alternative — "just re-run `install.sh`" — works, but is unfindable from inside the tool and re-downloads with no version comparison at all. See §update.py. |

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
  calls `auth list`**; `connected` (above) *is* the logged-in/usable set, shown as an `oModel:`
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
  `--verbose.variants` (a per-model object whose KEYS are the variant names) **is** the variant source
  of truth for the model pickers (`Catalog.variants_for`, decision #14) — read it; `--verbose.family`
  is still **never** read (family stays heuristic). Caveat that shapes `variants_for`: the object is
  empty (`{}`) for the dedicated providers (zhipuai, moonshotai-cn) while populated by the gateway
  (opencode) and openai — so `variants_for` prefers the first NON-EMPTY set across the picked provider
  then others, treating `{}` as "ask another endpoint", and offers nothing only when it is empty
  everywhere (kimi) or uncached.
- **What omo suggests (bundled, build-time):** `omo-suggestions.json`, generated from
  `~/source/oh-my-openagent/packages/model-core/src/` (verified importable & serializable under bun:
  11 agents, 8 categories, 15 families, 9 variants). Schema the app **consumes**:
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

Two audiences, one parser. The flat flags are the **human** surface and are unchanged; the
subcommands are the **agent** surface (decision #18).

```
omodel                          # launch the TUI
omodel --config PATH            # use a specific config file
omodel --restore                # list recent backups (newest 10) and restore one
omodel --refresh-omo [--omo-src P]  # regenerate bundled suggestion data from an omo checkout (bun required)
omodel --print                  # print current resolved agent/category models, no UI
omodel --check                  # dry-run: resolve candidate lists for every target, exit 0 (CI-safe; degrades to suggestions-only if `opencode` absent)
omodel --refresh-models         # force `opencode models --refresh` + rebuild the ~/.cache/omodel cache
omodel --update [--yes] [--force]   # update omodel ITSELF from its GitHub releases (asks first)
omodel --version
```

```
omodel agent-guide                       # print data/agent-usage.md — the agent contract
omodel targets [--json]                  # every valid target id
omodel show [--json]                     # assignments + providers + presets + degraded
omodel candidates <target> [--json]      # the pick list, with a ready-to-use `value` per row
omodel check [--json]                    # config problems; exit 3 if any
omodel set <target> <provider/model> [--variant V] [--dry-run] [--force] [--json]
omodel clear <target> [--dry-run] [--json]
omodel apply [--dry-run] [--force] [--json]   # batch assignments from stdin JSON, ONE save
omodel preset ls|use <name|index>|new <name>|rm <name> [--json]
```

**`--update` is a FLAG, not a subcommand** — the line between the two surfaces is what it is for.
The subcommands are the agent surface; updating omodel itself edits no config, is not a model
change, and is not something an agent should do mid-task (it would swap the binary it is running
on). Its cousins are `--refresh-omo` / `--refresh-models`: maintain the tool, not the models. It
is absent from `agent-guide` for the same reason. It keeps the 1-vs-3 split the rest of the CLI
uses: an environmental refusal the caller can act on (`not_self_updatable` → run the printed
command, `unsupported_platform`, `not_writable`) is a **3**; a real failure (network, checksum, a
binary that won't run) is a **1**. → §update.py

**It asks before swapping anything**, as `--restore` does before overwriting a config: the user
typed a verb, not a consent. There is therefore **no `--update-check`** — declining the prompt is
the check, and one flag with an obvious escape beats two that differ in what they do to your
disk. Three things stand in for "no": answering anything but `y`, **having no TTY** (piped,
redirected, CI — `input()` could only raise), and **`--json`** (a caller reading a payload cannot
answer, and a prompt would break the single-object contract). In the last two the release is
reported and nothing is installed, so `omodel --update --json` *is* the machine-readable check.
`--yes` is the one way to mean it; `--force` (reinstall at the same version) changes what counts
as an update, not whether you agreed to it, so it still asks. Every refusal — `not_self_updatable`
and all of `preflight`'s — comes **before** the prompt: there is nothing to confirm when the
answer is "not by me" or "not on this machine".

**The main parser now owns `--yes`/`--force`/`--json`, and argparse accepts every top-level flag
on every run** — so combinations that used to exit 2 on an unrecognized argument started parsing
cleanly and doing something else (`omodel --update show` ran `show` and dropped the update;
`omodel --json --print` printed prose to a caller awaiting an object). `cli._flag_misuse` puts
the exit 2 back, scoped strictly to the flags this added: `omodel --check show` was
accepted-and-ignored long before, and tightening *that* would be a behaviour change smuggled in
under a fix. The subcommands' own `--force`/`--json` keep `default=SUPPRESS` so both orders still
reach them (§CLI, `--config`'s rule).

**Exit codes** (the agent surface's real contract — `0` success, `1` omodel failed, `2` usage,
`3` **refused by a guard**). The 1-vs-3 split is load-bearing: an agent that can't distinguish
them either retries a broken tool or abandons a fixable pick.

There are **four codes and no fifth**, which takes a guard in `main`. `omodel show --json | head`
leaves ~1 KB of a 9 KB payload in Python's 8 KB stdio buffer, so the dead pipe surfaces not in
`print` but in the interpreter's *shutdown* flush — past any caller's reach, printing
`Exception ignored on flushing sys.stdout` and exiting **120**. So `main` flushes while it can
still catch, and treats it as success: the reader chose to stop, omodel did its work. `EXIT_OK`,
silent, with fd 1 pointed at `os.devnull` so the shutdown flush can't raise it a second time.

**Strictness of `set`/`apply`** — refuse, `--force` overrides, with one exception:

| Condition | Result | `--force`? |
|---|---|---|
| unknown target, unqualified `provider/model` | exit 3 | never |
| non-GPT model on a GPT-only agent (hephaestus) | exit 3 | **never** — omo's hook would reassign the session, so the config could not take effect; the CLI must not be a looser door than the TUI, which hides the escape hatch entirely |
| no connected provider serves the model | exit 3 | yes (writes with `warn: ["unavailable"]`) |
| variant not in opencode's set | exit 3 | yes (writes with `warn: ["variant"]`) |

The variant check fires **only when opencode reports a non-empty set** for that (provider,
model). `variants_for` is cache-only and dedicated providers report `{}`, so empty means "no
information", not "no variants" — refusing on silence would reject valid picks on a cold cache.

It reads that set with **`stale_ok=False`** (`cli._variant_guard_set`), the one place that opts
back into the 24h TTL: this is a *refusal*, and unlike `resolve._variant_warn`'s ⚠ — which it
otherwise mirrors (decision #14) — it must not rest on a file of unbounded age, on a surface that
never calls `detail()` and so never re-warms the cache behind an agent. Expired → `[]` → allowed.
The TTL gates only the verdict, never which provider answers, so `candidates --json`'s advisory
`variants` field (stale-ok, like the pickers) can never advertise a variant this guard rejects —
see §cache.py.

**Write rule.** Every mutating verb is a complete transaction: **validate → mutate cfg** →
`config_io.save` → `presets.write(projected_store())`, **config first** (mirroring `app.py`'s
`_save`). Validate-before-mutate is what makes `apply` all-or-nothing, and is safe because
`_validate` reads only suggestions + catalog, never cfg — so no entry's validity can depend on
another entry's effect. Nothing to change → write **nothing** (no file rewrite, no backup slot
burned), mirroring the TUI's "Nothing to save." There is no staging across processes — a staged state would be the orphan fourth state
decision #17 forbids. `apply` validates **all-or-nothing** so a half-applied config never lands,
and exists because each save snapshots a backup and the ring keeps only 20: eleven individual
`set` calls would evict eleven of the user's own snapshots.

**JSON.** Every payload carries `"schema": 1`. `degraded: true` (empty `catalog.connected`) means
availability is UNKNOWN, not that nothing works — without it a consumer reads `candidates: []` as
"no models exist". The raw omo `fallbackChain` `entry` is deliberately **not** exposed (it would
freeze omo's internal schema into omodel's public output); `substitute_for` carries what a
consumer needs. Shapes are pinned in CONTRACTS.md §agent JSON.

**The doc ships in the package** (`data/agent-usage.md`, read via `importlib.resources`) so
`omodel agent-guide` works from the PyInstaller binary, where most users meet omodel and where
there is no repo to read.

CLI error behavior: a malformed (unparseable) config makes the TUI launch and `--print` exit 1 with
a one-line friendly message + a "fix the file or `omodel --restore`" hint (`ConfigParseError` from
`config_io.load_config`) — never a raw json5 traceback. (`--check` never parses the config, so it
stays exit-0/CI-safe regardless.) `--restore`'s interactive prompt treats Ctrl-D/Ctrl-C as
"Cancelled." (exit 1) rather than crashing. `--config` accepts a bare relative filename (scaffold
resolves the parent via `abspath` — `dirname("x.jsonc") == ""` used to crash `makedirs`).

## Layout (approved)

Captured from a real 84×28 render (`OModelApp.run_test`), not drawn — the pane widths, the row
formats and the detail pane's line split are what the code actually emits. Providers/models are
omo's own first-choice ones for `sisyphus`, so the picture names no particular user's setup.

```
 oModel: opencode · anthropic · openai
┌──────────────────────────────┐┌──────────────────────────────────────────────────┐
│ AGENTS                       ││ agent: sisyphus                                  │
│   sisyphus                   ││ model: anthropic/claude-opus-5                   │
│     ↳ ultrawork              ││ variant: max                                     │
│   hephaestus                 ││ ctx 256k · $5/$25 · reasoning · image            │
│   oracle                     │└──────────────────────────────────────────────────┘
│   librarian                  │┌──────────────────────────────────────────────────┐
│   explore                    ││ ● anthropic/claude-opus-5 (max)                  │
│   multimodal-looker          ││   opencode/claude-opus-5 (max)                   │
│   prometheus                 ││   opencode/kimi-k3                               │
│   metis                      ││   openai/gpt-5.6-sol (medium)                    │
│   momus                      ││   opencode/gpt-5.6-sol (medium)                  │
│   atlas                      ││   opencode/glm-5.2  (≈ omo glm-5)                │
│   sisyphus-junior            ││ + add model…                                     │
│ CATEGORIES                   ││                                                  │
│   visual-engineering      ▂▂ ││                                                  │
│   ultrabrain                 ││                                                  │
│   deep                       ││                                                  │
│   artistry                   ││                                                  │
└──────────────────────────────┘│                                                  │
┌─ PRESETS ────────────────────┐│                                                  │
│ ● 1 daily                    ││                                                  │
│   2 max-power                ││                                                  │
│ + add preset…                ││                                                  │
│                              ││                                                  │
└───── saved 07-30 · 4 models ─┘└──────────────────────────────────────────────────┘
 s save · q quit · ? help                                                    v0.3.0
```

Note what the picture is *not*: `#targets` rows carry the target name and nothing else — no
model/family column beside it, and no `>` cursor glyph (the highlight is Textual styling, which
a text capture can't show). The `▂▂` is `#targets`' own scrollbar, shown because its 22 rows
(20 targets + the two disabled section headers) overflow the 18-row pane this capture gives it —
a live scrollbar, not a reserved gutter (`scrollbar-gutter: stable` is on `#presets` alone, for
the name-width reason in §presets.py).

Each region is a bordered card; the **focused** pane's border brightens to `$primary`, while blurred
panes use a muted `$surface-lighten-3` border — a theme token (not a literal), chosen over
`$border-blurred`, which the default textual-dark theme renders near-black on a dark terminal.
`Static#providers` and `Horizontal#hints-bar` are full-width bars (not cards) with a neutral
`$surface-lighten-1` fill (deliberately not the blue-gray `$panel`). The hint bar is a *container*:
its text lives in the `Static#hints` / `Static#hints-version` it holds, which is why the fill rule
sits on the Horizontal and the width rules on the two Statics. `Static#detail` is display-only — it
shows the frame but never the focus highlight (Statics never receive focus; only `#targets`,
`#presets` and `#candidates` do). The left column is a `Vertical#left` (width 32) stacking `#targets` (`height: 1fr`)
over the `#presets` card (decision #17), which is `height: auto` bounded by `max-height: 50%` — it
grows with however many presets you keep and scrolls internally past that, so `#targets` can never be
squeezed below half the column no matter how many you make. Seeded with one preset the card costs 4
lines; **at ~5 lines** an 80×24 terminal drops `#targets` from ~20 visible rows to ~15 against 21 rows
of content, so the list scrolls sooner (it already scrolled). `#presets` carries the same muted `$surface-lighten-3` border as the other
cards and joins the focus rule (`#targets:focus, #presets:focus, #candidates:focus`).

**Color depth:** the CLI pins `TEXTUAL_COLOR_SYSTEM=256` (in `cli._default_color_system`, set
before `app` imports Textual) so the palette is consistent across terminals — a terminal with no
`$COLORTERM` and a bare `TERM=xterm` is otherwise auto-detected as only 16 colors and the UI
collapses to its ANSI slots, looking nothing like a `xterm-256color` session. Overridable:
`TEXTUAL_COLOR_SYSTEM=truecolor` for 24-bit, `=auto` to restore Textual's own detection.

The bottom hint bar (`Horizontal#hints-bar`) is **minimal and static**: the keys `s save · q quit · ? help`
sit at the left (`Static#hints`, `width: 1fr`), the app version (`v<version>`, `Static#hints-version`) at the tail — the
three keys you won't discover by convention and that act regardless of focus. It never tracks
pane / row / undo state. **Every other base-screen key lives in the `?` help overlay**
(`HelpModal`), a short modal grouped by pane; dialogs state their own keys on their own hint line.
See §Textual contract.

## Repo layout (src-layout, PyPI-ready)

```
oModel/
  pyproject.toml                 # hatchling; [project.scripts] omodel = "omodel.cli:main"
  README.md  LICENSE  NOTICE  CHANGELOG.md
  install.sh                     # curl|sh: detect os/arch → download release binary → ~/.local/bin
  src/omodel/
    __init__.py
    cli.py            # argparse: default → TUI; the flat flags; the agent subcommands (§CLI)
    session.py        # HEADLESS CORE: cfg+catalog+resolver+store, every mutation, the save. No Textual.
    app.py            # Textual two-pane App (see §Textual contract) — a Session + rendering
    catalog.py        # availability via `opencode models`; verbose-record parser; providers_for(); refresh()
    cache.py          # 24h on-disk cache of opencode stdout (~/.cache/omodel); read-through by catalog
    suggestions.py    # load bundled/override omo-suggestions.json; detect_family(); variants
    resolve.py        # prefix (prefer-dedicated), variant defaulting/validation, candidate assembly
    config_io.py      # read jsonc (json5) → dict; serialize(); diff+confirm save; .bak; scaffold
    history.py        # in-session undo/redo: snapshot stack of cfg states (u / ctrl+r)
    presets.py        # named presets of agents/categories, stored next to the config
    refresh.py        # locate omo src + bun; run extractor; write repo or user-data override
    data/
      omo-suggestions.json        # BUNDLED, committed (regenerated by --refresh-omo)
      default-config.jsonc        # BUNDLED starter — oModel's OWN minimal template (not vendored)
      agent-usage.md              # BUNDLED agent contract — printed by `omodel agent-guide`
    tools/
      snapshot_omo.ts             # BUNDLED extractor (oModel's own code; imports omo at maintainer time)
  tests/
    test_catalog_parse.py         # mocked `opencode models` + multi-block `--verbose` records
    test_resolve.py               # prefer-dedicated order, variant validity, ⚠ flags
    test_detect_family.py         # parity vs omo (kimi vs k2p#, opus vs non-opus, gpt-5 vs o-series)
    test_config_io.py             # clean rewrite preserves non-model sections; .bak; comment loss
    test_history.py               # undo/redo stack: change detection, deep-copy isolation, cap
    test_presets.py               # preset file IO: 3 entries, name, missing/corrupt → 3 empties
    test_session.py               # the headless core: pick list, mutations, both-files save
    test_app_pilot.py             # Textual App.run_test() set + save + undo/redo via queryable IDs
  .github/workflows/
    ci.yml                        # lint + tests (opencode + bun mocked; no omo source needed)
    release.yml                   # on tag → PyInstaller one-file binary → attach to GitHub Release
    refresh-suggestions.yml       # checkout omo @ pinned tag → bun extractor → PR on change
```

## Components

### Data contracts (shared shapes — fix once so `resolve.py` and `app.py` agree)

**The authoritative field-by-field spec is CONTRACTS.md §Shared shapes — read it there.** It is
deliberately not restated here: this shape is the one seam both surfaces and the agent JSON are
pinned to, and two copies of it in two files is exactly the drift this section exists to prevent.
In summary:

- `target` id (string): `"agent:<name>"`, `"agent:<name>.ultrawork"`, `"agent:<name>.compaction"`, or
  `"cat:<name>"` — identical to the §Textual `OptionList#targets` option IDs.
- `source` (string enum): `"omo"` (a `fallbackChain` entry — exact or same-line substitute) ·
  `"add"` (typed in the add-model modal). (`"mine"` retired — no connected-model dump.)
- **candidate row** — the dict `candidates()` yields and `app.py` renders, one row per serving
  provider. Fields: `source` · `model` (the RESOLVED bare id, so the substitute when this is a
  same-line stand-in) · `provider` · `variant` · `entry` · `substitute_for` · `warn`. The value
  written to config is `f"{provider}/{model}"` plus `variant` (omitted when `None`).

### `catalog.py` — availability from `opencode`
- `load()`: `opencode models` → `available` (dict) + `connected` (**list**, first-seen order — never
  a set). Per the §Data sources error rule: exit code ≠ 0 **or** zero `provider/model` lines parsed →
  raise `CatalogUnavailable` (UI shows banner + retry); `opencode` not on `PATH` → empty + banner.
- `providers_for(model_id)` → connected providers that have it, in first-seen order.
- `detail(model_id, provider=None)`: query `<provider>` = the requested `provider` when it serves
  the model — the detail pane passes the current assignment's provider, so an `opencode/x`
  assignment shows the **gateway's** record (cost can differ per provider), never silently the
  dedicated provider's — else the model's **resolved** provider (first of `providers_for(model_id)`);
  run `opencode models <provider> --verbose`; split records on header
  lines `^(?P<prov>[a-z0-9_-]+)/(?P<model>\S+)$` (col 0), brace-count each block, `json.loads`, and
  pick the record whose header == `<provider>/<model_id>` → `{context, cost, reasoning, image}` for
  the detail pane (display only). This is a ~3s subprocess, so `app.py` calls it from a background
  worker (cached per `(provider, model)`, debounced) — never on the UI thread (see §Textual two-pane
  contract).

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
- **`variants_for` ignores the TTL (`catalog._STALE_OK`) — stale-while-revalidate.** It is the one
  read that accepts any age, and only it: availability (`load`) and cost/context (`detail`) keep the
  24h TTL. Rationale: an expired `verbose-<prov>.json` is still opencode's answer, and discarding it
  doesn't yield fresher data, it yields *none* — `variants_for` returns `[]`, so `resolve._variant_warn`
  falls back to the coarse heuristic `family.variants` (glm → low/medium/high, no `max`) and flags omo's
  own suggestion `⚠ variant`, while `v` offers nothing at all. Variant sets are near-static next to
  availability and pricing, so a day-old set beats a guessed one. The **revalidate** half is `detail()`'s
  existing background fetch (still TTL'd — its write re-warms exactly the file `variants_for` reads) plus
  `r` / `--refresh-models`, whose `cache.clear()` deletes the file outright and is therefore the only
  thing that makes a genuinely-removed variant disappear. Cheap by construction: **no extra subprocess**,
  since the read half is free and the write half was already happening.
  **The exemption stops at anything that REFUSES.** `variants_for(..., stale_ok=False)` restores the TTL
  for the CLI's hard variant guard (`cli._variant_offered` → `bad_variant` → exit 3) — stale data is
  right for a marker you can overrule and a picker you can ignore, wrong for a rejection. That surface
  has no revalidate half either (**`cli.py`/`session.py` never call `catalog.detail()`**), so a wrong
  verdict there would stick until a human ran `--refresh-models`, and an agent branching on exit 3 would
  back off from a valid variant indefinitely. Expired → `[]` → "no information" → allowed, as before.
  `_validate`'s `bad_variant` message quotes that same guard set (`_variant_guard_set`) — a message
  naming a set the guard didn't use would send an agent to retry with a doomed variant.
- `catalog.refresh()` — the `r` key / `omodel --refresh-models` — runs `opencode models --refresh`
  (network re-fetch), clears the cache, and rewrites `models.json` from the result. The TUI runs it in a
  worker (off the UI thread); `r` is documented in the `?` help overlay (the `oModel:` header shows
  only the connected list — no cache-age suffix).
- **The completing fetch re-renders BOTH panes**, not just `#detail`: its `--verbose` write is also the
  variant source above, so the candidate rows resolved before it landed are stale in the same breath
  (the `⚠ variant` marker). `_rows` is cleared and `#candidates` re-rendered — otherwise the rows stayed
  pinned until the next cfg mutation dropped that cache, i.e. the list visibly corrected itself the
  moment you pressed enter. Two guards on that re-render: it is **skipped under an open `VariantModal`**
  (`v`'s callback holds a row dict captured before the modal opened and returns early if
  `_rows[target][idx]` is no longer that same object — its way of yielding to an `r` refresh; rebuilding
  under it drops the pick before `_pending_variants` sees it). That guard is **VariantModal-specific on
  purpose**: gating on the whole screen stack meant a fetch landing under the `?` overlay skipped the
  rebuild and *nothing ever retried it* — the completed fetch is cached, so no further fetch is
  scheduled and a re-highlight hits the still-stale `_rows`. Second, the whole block catches `NoMatches`
  (the daemon thread outlives the widgets on `q`, and an exception out of a `@work` worker is a
  `WorkerFailed`, not a no-op).
- **`_rows` is a CACHE, so nothing unpersisted may live in it.** A `v` pick on a row that is *not* the
  current assignment reaches cfg only on Enter (only Enter assigns — §Events), so until then it is
  pending state and it lives in **`_pending_variants`** (`{target: {"provider/model": variant}}`),
  re-applied by `_build_rows` after `session.rows()`. It used to be an in-place mutation of the cached
  row dict, which made any rebuild silently revert it to omo's suggested variant — tolerable while only
  cfg mutations rebuilt rows, a data-loss bug once a landing background fetch did too (it fires on its
  own schedule, so the pick vanished with no user action and a later Enter wrote omo's variant). Cleared
  where the pick stops being pending: `_stage_row` (it reached cfg), `_restore_state` (not in the undo
  aux, so undo/redo has nothing to restore) and `_refresh_catalog` (chosen from pre-refresh sets — same
  reasoning as `_custom_rows`).
- **Memory safety (load-bearing):** a spawned opencode subprocess can't be killed, so the detail fetch
  is **capped to one concurrent** (a `_detail_fetching` gate; on completion the worker re-renders the
  *current* target, which schedules the next — "chase the cursor"). Uncapped/un-stubbed, stacked
  ~320 MB `--verbose` processes OOM'd a machine; a refresh bumps a generation counter so an in-flight
  fetch discards its now-stale result. Tests stub `subprocess.run` and isolate the cache dir
  (`tests/conftest.py` → `$OMODEL_CACHE_DIR`).
- **Quit never blocks on a subprocess:** both the detail fetch and the `r` refresh run their blocking
  call via `_to_thread_daemon` (app.py) — a **daemon**-thread analogue of `asyncio.to_thread`.
  `to_thread`'s non-daemon executor threads are joined at interpreter shutdown, which made `q` hang
  until the in-flight call finished (up to its 20s/90s timeout); a daemon thread lets the process
  exit immediately, and the orphaned opencode child just finishes on its own (nothing awaits it).
  `r` is additionally **single-flight** (`_refresh_inflight`): a second press notifies "already
  running" instead of spawning a second worker — `@work(exclusive=True)` only cancels the prior
  asyncio *task*, not the subprocess, so two live `--refresh` runs would race `cache.clear()`/
  `write()` (last finisher wins).

### `suggestions.py` — bundled omo data
- Load order: explicit `path` arg → `$OMODEL_SUGGESTIONS` (both unconditional) → the **newer** of
  `$XDG_DATA_HOME/omodel/omo-suggestions.json` (from a past `--refresh-omo`) and the bundled
  `importlib.resources.files("omodel.data")/"omo-suggestions.json"`, compared by `meta.generatedAt`
  (ISO-8601 string compare; missing/unparseable/unreadable → oldest; ties → bundled) — so a stale
  user-local snapshot can never shadow newer bundled data after an app upgrade.
- `detect_family(model_id)` — faithful port of `detectHeuristicModelFamily`: **ordered** iteration of
  `families`, `pattern` tested before `includes` within each entry, first match wins; run
  `normalize_model_id` first (`re.sub(r"\.(\d+)", r"-\1", s).lower()` → `kimi-k2.7`→`kimi-k2-7`).
  Patterns pre-`re.compile`d. (Parity matters: `openai-reasoning` before `gpt-5`, `kimi-thinking`
  before `kimi`, `claude-opus` before `claude-non-opus`.)
- **Entry shape retained:** each `fallbackChain` item keeps `{providers[], model, variant?, …}` — the
  `providers` array (omo's per-model preference order) is **kept** for the gateway tie-break in
  `resolve_prefix`.
- **`FAMILY_VENDOR` — hardcoded dict in `suggestions.py` (NOT from omo; omo has no such table).** The
  complete 15-family → vendor map used by `vendors_served`. The authoritative table is the
  `FAMILY_VENDOR` dict in `src/omodel/suggestions.py` — read it there; not duplicated here (it drifts).
  `vendor(family) = FAMILY_VENDOR.get(family)` → `None` for unknown/None. Models whose `detect_family`
  is `None` (opencode's `big-pickle`, `*-free`, `nemotron-*` — no omo family; note omo 4.13 added a
  `qwen` family, so `qwen3.x-plus` now detects `qwen`→`alibaba` and is no longer `None`)
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
  model, tolerating `.`/`-` spelling and a trailing **date stamp / sub-version tag** (a provider's
  `claude-haiku-4-5-20251001` or `claude-sonnet-4-8-jibao` fills the bare `claude-haiku-4-5` /
  `claude-sonnet-4-8`) → that **concrete available id** (`substitute_for=None`). A real modifier
  token omo itself uses (`mini`/`fast`/`nano`/`flash`/…, derived from the chain ids) is *not*
  stripped, and a short trailing digit stays a version (`glm-5.1` ≠ `glm-5`); **(2) same-line** —
  else the **newest connected model of the same `detect_family`** (version-agnostic: `glm-5` →
  `glm-5.1`; "newest" = highest digit-tuple, ties → first-seen) — except within the coarse
  `claude-non-opus` family (haiku, sonnet, fable, mythos, …) the substitute must also share the
  **product-line** token, so a haiku slot is never filled by a sonnet (nor a fable by a mythos) —
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
  `⚠ variant` (variant unsupported for the row's (provider, model): checked against opencode
  `--verbose` when it lists a non-empty set, else the bundled family `variants`). (Unavailable entries
  are hidden, not flagged — decision #5.) **Current pick (`●`):** the row whose resolved
  `provider/model` equals the target's current assignment in `self.cfg` — at launch that's what
  `oh-my-openagent.jsonc` has on disk, and it follows your selection as you stage edits — is
  prefixed `● `; all other rows get a 2-space prefix. If the current model isn't in the (chain-only)
  list (an off-chain hand-pick — a custom model set in a prior session / by hand, or one that has
  dropped off the chain), `app.py` **surfaces it as its own row just before `+ add model…`** (built
  from `self.cfg`; ⚠-flagged `unavailable` only when the catalog is readable and the *assigned*
  provider doesn't serve the model — suppressed in degraded mode, where availability is unknown) so
  the configured model is always shown and re-selectable, and that row carries the `●` (see `_build_rows`). The
  picker proper stays chain-only; this single extra row is the current assignment, never a
  connected-model dump.
- **The one row you can delete (`x` on a `#candidates` row you added).** Two different mechanisms
  put an off-chain model in this list, and only one of them needs a delete key. The row above is
  **derived from cfg**, so it appears and vanishes on its own as the assignment moves — clearing
  the assignment is what removes it. A model typed into the add-model modal is different: it is
  kept in `app.py`'s `_custom_rows` and **stays pickable after you try something else** (that's the
  point — you can go back to it without retyping), which made it the one row with no way out.
  Worse, `x` was **target-scoped**: `action_clear` read only `_current_target` and never the
  cursor, so pressing it while pointing at an added row cleared *whatever model was assigned* —
  one you weren't pointing at — and left the row sitting there. So `x` now reads the cursor
  (`_remove_custom_row`), and:
  - only `_custom_rows` entries are deletable — the chain is omo's data, not yours to remove, and
    chain rows keep the clear meaning, so `x` still unsets a target from anywhere in the pane;
  - it is **gated on `#candidates` having focus** (`_candidates_focused`), so `x` from `#targets`
    can't reach across and eat whatever row the other pane's cursor happens to sit on;
  - when the deleted row **is** the assignment, the assignment goes with it — clear == delete, the
    same rule as a sub-target row: a model left set on a target whose row just disappeared is the
    one state this pane must not show;
  - the cursor re-aims at the assigned (`●`) row instead of coming back un-highlighted where the
    deleted row used to be (`_cand_choice` is identity-keyed, so the stale identity is repointed).
  **Undo:** deleting an assigned row touches cfg, so it lands in the history like any edit and `u`
  restores model *and* row (`_custom_rows` rides in the entry's `aux`). Deleting a row that isn't
  assigned changes nothing that will ever be written — it is pane state, not an edit — so it
  records no entry, matching `History.push`'s cfg-only contract (§history.py); `a` re-adds it in
  one keystroke.
- **GPT-only agents (Hephaestus):** omo's `no-hephaestus-non-gpt` hook makes Hephaestus
  GPT-exclusive (`isGptModel` = model name after the last `/`, lowercased, contains "gpt"; a non-GPT
  model reassigns the session to Sisyphus). oModel mirrors this for `agent:hephaestus[.sub]`: the
  `+ add model…` row stays, but the add modal is **gated** — a non-GPT model is **blocked** (enter
  disabled, `⚠ Hephaestus is GPT-only`), so you can pick any GPT model you have but can't footgun a
  non-GPT one; the detail pane shows a `⚑ GPT-only` tip. Encoded as `GPT_ONLY_AGENTS` +
  `is_gpt_model` in **`session.py`** (so the CLI applies the identical lock — §session.py; `app.py`
  re-imports `is_gpt_model` as `_is_gpt_model` and asks `session.gpt_only(target)` for the rest).
  A hard-coded agent key matching omo's, not a data field — `requires*` are activation flags, not
  user-choice restrictions.

### `config_io.py` — edit-in-place save
- Read `json5.load` → ordered dict; `agents`/`categories` are editable, all other top-level keys
  (`claude_code`, `experimental`, `team_mode`, `$schema`, future) pass through. **The on-disk write
  is text-preserving (`render`, below): only the `agents`/`categories` value spans are rewritten;
  the rest of the file — other keys, formatting, and any comments / commented-out config *outside*
  those two (e.g. a `//"skills": false` line within `claude_code`, or a parked top-level block) — is
  kept byte-for-byte.** The commented palette *inside* agents/categories is still dropped (those
  spans are rewritten clean); only `.backup/original.jsonc` retains it.
- **`serialize(cfg) -> str` (exact):** the **canonical clean form** — used for dirtiness
  (`_is_dirty` = `serialize(cfg) != _saved_text`, both sides this function, never the on-disk bytes)
  and as the from-scratch / fallback writer; the actual on-disk write goes through `render`. (1) build an ordered dict preserving on-disk key order, but
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
- **`render(cfg, base_text) -> str` (the write form):** returns `base_text` with **only** the
  top-level `agents` and `categories` value spans replaced by their clean form (`json.dumps`,
  comment-free, `_clean_agents`-cleaned), re-indented under the key. Everything else splices through
  verbatim — comments, commented-out config, other keys, key order, formatting. A small JSONC-aware
  scanner (`_top_level_value_span`, honoring strings / `//` / `/* */` / nesting, so a `}` or
  `"agents"` inside a string never fools it) locates the two spans; the later span is replaced first
  so offsets stay valid. **Falls back to `serialize(cfg)`** when `base_text` is empty/blank or either
  key is not a direct root member (non-omo / hand-broken file — splice unsafe). `render` is
  **idempotent** (rendering its own output reproduces it byte-for-byte → an unchanged save is a
  no-op). It does **not** inject the `// Generated by oModel` header (that would touch outside
  agents/categories); the header is emitted only by the `serialize` from-scratch / fallback path.
- **Save flow:** diff `render(cfg, on-disk)` vs the on-disk file → confirm modal showing the diff
  (exactly what changes — agents/categories only, comments outside intact; the diff body is
  **scrollable** — ↑↓/`j``k`, PageUp/PageDown, Home/End — since a real config diff easily exceeds the
  modal's height, while the Yes button keeps focus so Enter still confirms) → on accept, snapshot the
  current on-disk file to `<config_dir>/.backup/<ts>.jsonc` (**verbatim byte copy** — preserves
  comments), then atomic temp+rename of `render(cfg, on-disk)`. No diff → "nothing to save".
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
- ⚠ **First save drops the palette *inside* agents/categories:** the live config is comment-dense
  (3–6 commented alternatives per agent), and those live inside the `agents`/`categories` objects,
  which `render` rewrites clean — so the first save deletes that palette (decision #13). Comments /
  commented-out config **outside** those two are preserved verbatim. The whole pre-oModel file is
  also pinned as **`.backup/original.jsonc`** (never pruned, always restorable) — surface this in the
  first confirm modal.
- Missing config → scaffold oModel's own minimal `default-config.jsonc`, then open it. Template (the
  `$schema` is a **literal hardcoded string** committed in `default-config.jsonc`; nothing in the
  refresh path writes it):
  `{ "$schema": "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/assets/oh-my-opencode.schema.json", "agents": {}, "categories": {} }`
  — valid and minimal; the left pane is populated from the bundled snapshot, so empty maps still show
  all 11 agents / 8 categories as unset, and only what you set gets written.

### `session.py` — the headless core (decision #18)
- **Purpose:** hold the editable state and perform every mutation, so `app.py` (TUI) and
  `cli.py` (agent surface) cannot drift into two answers for "what may I set here?" or "what
  does a save write?". Before it existed, those rules lived in `OModelApp` methods that queried
  widgets, so nothing outside a running TUI could apply them.
- **`Session`** = `catalog` + `suggestions` + `resolver` + `cfg` + `config_path` + `store`
  (+ `catalog_error`, `sync_conflict`). `__post_init__` does the presets load / seed /
  launch-reconcile and takes both dirtiness baselines (`saved_text`, `saved_store_fp`), so every
  entry point upholds the invariant identically. `Session.build(config_path)` is the production
  wiring both `create_app()` and every CLI verb call.
- **Reads:** `known_targets()` / `is_known()` (omo's targets, sub-kinds filtered per agent),
  `rows(target, custom_rows=())` (the pick list: chain + caller-held custom rows + the current
  off-chain assignment), `assignment()`, `variants_for()`, `degraded`.
- **Writes:** `set_model` / `set_row` / `clear` / `delete_subtarget` / `switch_preset`, then
  `save_config()` + `write_store()` (or `save()`, which does both, config first). `app.py`
  calls the two halves rather than `save()` because its save is interactive (diff → confirm) and
  it reports a config-landed-store-didn't failure differently.
- **What deliberately stays in `app.py`:** the undo `History`, the per-target row cache,
  `_custom_rows`, and all rendering — session-shaped only inside a UI (a CLI process edits once
  and exits, so it has no undo stack and no cursor to remember). `rows()` therefore takes the
  custom rows as an ARGUMENT rather than owning them.
- **Aliasing:** `cfg` is shared by identity with `app.py` (`OModelApp.cfg` is a property onto
  `session.cfg`); `store` is REASSIGNED by a switch and by a write, so it is reached through the
  property rather than aliased at construction — otherwise the two would fork.
- **No Textual, no `app` import** — `cli.py`'s lazy imports (`--version` / `--check` / the JSON
  verbs never pay for Textual) depend on it. The guards and the target-id vocabulary
  (`GPT_ONLY_AGENTS`, `ULTRAWORK_AGENTS`, `is_gpt_model`, `subkinds_for`, `is_no_variant`,
  `read_map`, `coerce_dict`, `gpt_only`, `split_target`, `target_label`) moved here. `app.py`
  re-imports only the four it calls directly, under their historical private names (`_SUBKINDS`,
  `_is_gpt_model`, `_is_no_variant`, `_subkinds_for`), and reaches the rest through
  the module (`session_mod.gpt_only` / `read_map` / `target_label`) — so **the frozensets themselves
  are never imported anywhere**, living in exactly one place where neither surface can hold a
  private copy that drifts.

### `history.py` — in-session undo/redo (decision #16)
- **Purpose:** recover from a mis-press *within a session*, before/independent of saving — a
  wrong pick, a fat-fingered `x` (clear), an accidental `a` sub-target. This is distinct from
  the on-disk `.backup/` rollback (decision #2 / §config_io.py): that is cross-session file
  history (`--restore`); this is the live edit stack.
- **Model:** `History` holds a linear list of cfg **snapshots** with labels; a cursor marks the
  current one. Entry 0 is the loaded cfg. `push(state, label)` appends a deep copy (and is a
  **no-op when `state == current`**, so a re-pick of the same model makes no junk entry),
  truncating any redo tail first (standard undo semantics). `undo()`/`redo()` move the cursor
  and return `(state, label)`. Each entry also carries an optional **`aux`** companion snapshot
  (`push(state, label, aux=)`, read back via `current_aux()`) for state that must move with the
  entry but isn't cfg — app.py stores `_custom_rows` there; `clear_aux()` wipes it across all
  entries on a refresh. Change detection stays cfg-only (`aux` never on its own makes an entry).
  A `limit` (200) caps memory for long sessions. Snapshots are deep-copied **in and out** so the
  app's live cfg and history never alias. Pure data, no Textual — unit-tested in isolation.
- **App integration (`app.py`):** every cfg mutation routes through one chokepoint — `_record`
  (and `_stage_row`, which calls it) — so **set / clear / variant / add-model / add-sub /
  delete-sub** are all undoable. `u` → `action_undo`, `ctrl+r` → `action_redo` (vim-style; distinct from `r`
  refresh), both **gated to the base screen** via `check_action` (a modal owns its own keys —
  e.g. AddSubModal binds `u`). `_restore_state` swaps in the snapshot and re-renders **both**
  panes (a sub-target row appears/vanishes on the left; the `●` current-pick follows cfg on the
  right; a vanished sub-target falls back to its parent agent, repopulated via `_populate_targets(
  select=)` so no stale intermediate highlight fires). The per-target row cache is dropped and
  rebuilt (like a refresh); `_cand_choice`/`_detail_cache` are kept. `_custom_rows` (off-chain
  typed models, merged into `_build_rows`) is **snapshotted into the history alongside cfg** (each
  entry's `aux`, via `_record`) and restored here, so it moves in lockstep with undo/redo —
  **undoing an add-model drops its typed row** and redoing brings it back, not just the bare cfg
  value. A refresh still clears it (and the stored `aux` snapshots, via `clear_aux()`), since the
  stored availability ⚠ is now stale.
- **Dirtiness is computed, not flagged:** `_is_dirty()` = `serialize(cfg) != _saved_text` (the
  text last written/loaded). So undo back to the saved state quits without a prompt, and an
  empty `ultrawork`/`compaction` sub-object — which `serialize()` drops — is **undoable but not
  dirty** (nothing to save). The undo history is **preserved across a save** (re-baselines
  `_saved_text` only), so a just-saved edit can still be undone (then re-saved).

### `presets.py` — named presets, the working state (decision #17)
- **The model in one line:** you don't save *into* presets, you **edit one**. Named sets of
  assignments — as many as you keep; exactly one is **active**; your edits go into it; `s`
  publishes it to `oh-my-openagent.jsonc`.
- **The invariant everything else follows from:** *the config on disk always equals the ACTIVE
  preset* — the file is never a fourth state matching none of them. Read the rest of this section
  as consequences of that one rule:
  - edits must flow into the active preset, or editing itself would create the orphan state;
  - the presets file and the config must therefore move **together** → **one write rule: only `s`
    writes anything**, and it writes both (`a`, `x` and the first-launch seed all stage in memory);
  - so quitting without saving discards *both*, in lockstep, leaving disk exactly as it was —
    which is why `q` offers **save & quit / discard / cancel** rather than a bare yes/no;
  - and `x` must refuse on the active preset — deleting the one the config mirrors would strand
    the config. (That also makes "at least one preset, always" free.)
- **First launch seeds one.** No presets file (or a mangled one) → **one** preset is captured from
  the config you already have, named `default`, active. In memory only: an untouched session stays
  clean and writes nothing, and re-seeding next launch is identical. Your first `s` materializes it.
- **Unlimited, and the list is DENSE.** There is no cap and no such thing as an empty preset: you
  start with one and `a` appends. That is a deliberate reversal of the original fixed
  3 — "3" was never load-bearing, and a fixed count forced an `(empty)` row that meant nothing and
  a "which slot?" decision the user shouldn't have to make. The cost is that **a delete renumbers
  every later preset**, which the undo history stores references to — see `map_aux_key` below.
- **Distinct from the other two save-ish things** (GLOSSARY): a **backup** is the verbatim on-disk
  file copy taken automatically at every save (§config_io.py, `--restore`); the **history** is the
  in-session undo stack (§history.py); a **preset** is a named set of assignments you switch
  between. "Slot" stays reserved for a *target* — a preset is addressed by **index** (0-based,
  shown 1-based), and that index is **not stable across a delete**.
- **What a preset holds:** the two subtrees oModel owns — `agents` + `categories`, sub-targets
  included — deep-copied **in and out** (`capture` / `assignments`), so the live cfg and a stored
  preset never alias. **Never** other top-level keys (`claude_code`, `experimental`, `$schema`,
  future) and **never** file text: switching presets must not be able to clobber the keys/comments
  `render()` preserves (decision #2).
- **Where:** `<config_dir>/.omodel-presets.json` — next to the config, sibling of `.backup/`. Presets
  therefore **follow the config file**: `--config /tmp/x.jsonc` gets its own set, which is what keeps
  the real-config safety rule satisfiable in tests with **no new env override**
  (`tests/conftest.py` already redirects `$XDG_CONFIG_HOME`). Plain JSON:
  ```json
  {"version": 2, "active": 0,
   "presets": [{"name": "daily-cheap", "saved_at": "2026-07-26T09:14:03Z",
                "agents": {"sisyphus": {"model": "zhipuai/glm-5.1"}}, "categories": {}}]}
  ```
  Any length, no `null`s. **`version` 2, and `load` still reads 1** (the fixed-3 shape), migrating
  it in place — holes dropped, `active` following the preset it named into the compacted list.
  The bump is **not** about reading: the payload shape never changed, so reading would work
  unversioned. It is about the **downgrade**. A build predating unlimited presets accepts a
  version-1 file, silently truncates it to 3, and its next save deletes the rest with no copy
  kept — verified against that build. Facing an unknown version it instead reads empty *and*
  `_preserve_unreadable` moves the file to `<path>.corrupt` first, so a rollback costs you the
  presets pane, not the presets. (Presets are unreleased, so this is a rollback-during-development
  hazard rather than a user-facing one — but the bump is three lines.)
- **Read = best-effort, write = loud.** `load()` never raises: missing / corrupt / wrong `version`
  → an empty store; a non-dict `agents`/`categories` → `{}`; an out-of-range `active` → the first
  preset (`normalize_active`), so `Store.current()` is None only for a genuinely empty store and
  app.py never handles "active points at nothing". `write()` is atomic (temp + `os.replace`) and **does raise** on failure (read-only
  dir, path taken by a directory); the app catches and notifies, exactly as `action_save` does.
  That splits the repo's two conventions deliberately: `cache.py` swallows write errors because a
  lost cache write costs only speed, while a silently-dropped preset write would be a lie about
  durable state. **Best-effort reading has a sharp edge, so `write` guards it:** an existing file
  that doesn't parse is moved to `<path>.corrupt` before the overwrite — otherwise a transiently
  unreadable sidecar reads as empty, gets seeded, and the first save destroys presets the app
  never saw.
- **API (pure data + file IO, no Textual — a leaf like `history.py`, no omodel imports):**
  `@dataclass Preset(name, saved_at, agents, categories)`; `@dataclass Store(presets, active)` with
  `.current()` / `.is_empty()`; `load(config_path) -> Store`; `write(config_path, store) -> Store`
  (**the only function here that touches disk**, returning the store as read back); plus the pure
  helpers `capture(name, cfg)` / `assignments(preset)` (the deep-copy in / out pair),
  `seeded(cfg, name=DEFAULT_NAME)`, `matching_index(store, cfg)`, `normalize_active(store)`,
  `fingerprint(agents, categories)`, `store_fingerprint(store)`, `model_count(preset)`,
  `sanitize_name(text, index)`, `timestamp()`, `presets_path(config_path)`, and the constants
  `FILE_VERSION` / `MAX_NAME` / `DEFAULT_NAME`.
- **The two fingerprints.** `fingerprint` answers *does the config still reflect a preset?* —
  `json.dumps` of the two subtrees, empty `ultrawork`/`compaction` sub-objects dropped
  (`config_io._clean_agents`' rule) and `sort_keys=True`, so neither key order nor an
  added-but-unfilled sub-target reads as a difference. **That cleaning is necessary because a naive
  `==` would lie:** `serialize` cleans those sub-objects for `agents` but not `categories`, so raw
  equality reports "differs" for two states that save byte-identically. `store_fingerprint` answers
  *does `s` have anything to persist?* — contents + names + active index, deliberately **excluding
  `saved_at`** so a re-stamp alone never marks the app dirty. Neither decides what gets WRITTEN, so
  a disagreement can mis-answer a question but can't corrupt a save. Both re-implement the
  empty-sub-object rule locally rather than importing `config_io._clean_agents`, keeping this module
  a pure leaf — the one deliberate duplication, and a contained one.
- **App integration (`app.py`):** `OptionList#presets` — a card under `#targets` inside
  `Vertical#left` sized `height: auto` / `max-height: 50%`, `border_title = "PRESETS"`, option IDs
  `preset:0` … `preset:<n-1>` plus the trailing `preset:new` (the id stays `new` — option ids are a
  contract; the row reads `+ add preset…`). Rows read `● 1 daily-cheap`; **`●` = the ACTIVE preset** (the one your edits go into, and the one `s`
  publishes). The row-number prefix widens past 9 presets, and `_populate_presets` derives the name
  budget from it, so the CJK no-wrap guarantee survives two-digit numbering.
  - **`self.cfg` IS the active preset's content** — never stored twice. `_projected_store()` is the
    single place that reconciles them (a copy of the store whose active entry carries the live cfg),
    and everything needing the whole store — dirtiness, saving, switching away — goes through it. So
    "your edits go into the preset you're on" is structural, not something each edit path remembers.
  - **`enter` = switch.** Banks the in-flight edits into the preset you're leaving
    (`_projected_store`), then REPLACES cfg with the target preset's assignments — a target it
    doesn't define is cleared, because a preset is a complete state, not an overlay. `_record` +
    `_restore_state` give it the same treatment as any edit: undoable, `_rows` cache dropped, left
    pane rebuilt, cursor falling back off a sub-target the new preset lacks. `enter` on the active
    preset just says so; on `+ add preset…` it means the same as `a` (creating one), so the
    row isn't inert under the key that activates every other row in the app.
  - **`a` = add, and it is ROW-BLIND** — it appends a preset holding the models you're looking at,
    names it and switches you there, whatever the cursor is on. The trailing `+ add preset…` row
    (`preset:new`, always last — the same idiom and now the same wording as `cand:add`) is where
    `enter` does it. `a` used to **overwrite the highlighted row** instead, with the name modal
    doubling as the confirm (`Overwrite preset 2 "max-power"?`) — that made `a` the one
    destructive, non-undoable key in an app where it means *add* in every other pane, reachable by
    habit plus a reflexive `enter`. Dropped: replacing a preset's models now means switching to it
    and editing, which `u` can walk back. `a` never destroys anything anywhere.
  - **`r` = rename**, name only — `saved_at` deliberately unchanged, because that stamp answers
    "when were these models banked" and a rename banks nothing. Second mode of the same modal
    (`PresetNameModal(index, existing)`; `existing is not None` ⟺ rename), retitled so the two
    can't be confused.
  - **Names**: trimmed, control characters
    and newlines **stripped**, `[`/`]` **stripped** (Textual 8 parses a plain `str` handed to a
    widget as content markup, so a preset named `[/b]` raised `MarkupError` from the compositor —
    and being *persisted*, it took the app down on every launch afterwards; names read off disk are
    stripped too, so a hand-edited file can't do it either), ≤`MAX_NAME` (24) chars, truncated with `…`
    measured in **display cells**. The cell budget is **measured, not assumed**: the card's content
    box is 26 cells — the 32-wide pane less its border (2), `OptionList`'s own `DEFAULT_CSS`
    `padding: 0 1` (2) and the scrollbar gutter (2) — leaving 22 for the name after a `● 1 `
    prefix, one less once the count reaches double digits (the prefix width is derived from the
    preset count, not hardcoded). Two separate ways this went wrong, both verified then fixed:
    deriving the budget from the pane width alone (30 → 26) wrapped every row of a 24-character
    CJK name; and measuring `size.width` — the *content* region, which does **not** subtract the
    scrollbar — overflowed every row by 2 cells the moment the card scrolled (15 rendered lines
    for 13 rows). So `_populate_presets` measures **`scrollable_content_region`**, with
    `_PRESET_NAME_CELLS` only as the pre-layout fallback. **`scrollbar-gutter: stable` is what
    makes that reliable:** reading the width before `clear_options()` is not enough, because at
    the moment the list first outgrows the card the scrollbar is not there yet, so any width
    sampled beforehand is the pre-scrollbar one. Reserving the gutter always makes the number
    the same whether the card scrolls or not. The fixed-3 card could never reach this — it was
    exactly 3 rows and never scrolled — so the regression test runs at 3 **and** 12.
  - **`x` = delete, behind a `ConfirmModal` — and REFUSED on the active preset** (bell + "switch to
    another one first"), per the invariant; with one preset left it IS the active one, so you can
    never reach zero. Staged like everything else. Because the list is dense, the delete **closes
    the gap**, renumbering every later preset — see the next bullet.
  - **Undo carries the active index — and every writer of `active` must say so.** `_record` puts
    `{custom_rows, active}` in each history entry's `aux`, and `_restore_state` applies both:
    undoing a switch has to move the `●` back with the models, or the restored models would be
    folded into the preset you switched TO. A catalog refresh wipes `aux` **except** `active`
    (`History.clear_aux(keep=("active",))`) — typed rows go stale with the catalog, the index
    doesn't. `active` is written from four places and only `_record` reaches the history, so the
    other three compensate — this is the feature's sharpest edge, and every one of these was a
    real bug found in review:
      * **add** changes `active` without changing cfg → no entry is pushed, so the entries
        recorded on the preset you were sitting on must follow you (`History.map_aux_key` with
        `_retarget_active`); otherwise the next `u` quietly moves you off the preset you just made.
      * **switch to an identical preset** pushes nothing either (cfg unchanged) → it must
        `History.drop_redo()`, or `ctrl+r` resurrects an undone edit *and* jumps the `●` back;
        plus the same `_retarget_active` remap, for the same reason as the add.
      * **all three remaps are per-entry, never a blanket stamp.** Stamping one index onto the
        whole timeline was the first attempt and was wrong three ways: it erased older switches
        (so they could no longer be undone), it erased the delete sentinels, and by erasing them
        it silently voided the warning below. Verified: add-after-delete then `u` landed the
        deleted preset's models in the newly created one, with no message at all.
      * **delete** RENUMBERS: it remaps every entry's stored index through `_shift_active` via
        `History.map_aux_key`, never a blanket stamp, because entries legitimately hold
        *different* values (that is what makes undoing a switch move the `●`). The deleted index
        becomes the `_DELETED` sentinel, so `_restore_state` can see the destination is gone and
        **say so** ("That preset was deleted — these models are now in 'default'") rather than
        landing the models in whichever preset slid into that number, in silence. The warning is
        gated on `_lands_elsewhere` — it is about models *arriving* somewhere you didn't choose,
        so when the restored state is what the active preset already holds nothing arrives and
        the message is noise (reachable because an add retargets the entries of the preset it
        copied). This hazard is new with the dense list: when presets were a fixed 3 with `null`
        holes, a delete left every other index alone.
      * **launch** prefers the *recorded* `active` when it still matches the config, falling back
        to `matching_index` only otherwise — an add makes a byte-identical duplicate, so scanning
        first would silently return you to preset 1 after every add → save → quit, with nothing
        dirty to correct it.
  - **Focus-dependence (`a`/`x`/`v`/`r`).** `action_clear` and `action_variant` are focus-*blind*
    otherwise, so `#presets` is the first pane that makes `x` and `a` dispatch on focus; `r` joins
    them (rename here, refresh everywhere else — refreshing the model list is a whole-app action
    you would never want while looking at this card, and the card is one `tab` from anywhere). **`v`
    bells on a preset row**: left unspecified it would silently retarget the hidden candidate pane's
    variant. `#presets` is the third focusable widget, and **`tab` / `shift+tab` are the only way
    in** — Textual's own `Screen` traversal, DOM order targets → presets → candidates, wrapping, so
    it already reaches every pane in both directions. **Deliberately no dedicated `p` key** (nor a
    global `1`/`2`/`3` quick-switch): it shipped briefly and was pure duplication of `tab` — one
    more key to learn, and one fewer left for a future binding. `←`/`→` stay targets-vs-candidates
    only; widening them would cost the direct jump they exist for. The highlight is therefore seeded
    in `_populate_presets`, never in a focus action: Textual's `OptionList` does not auto-highlight
    on focus, and an unseeded card swallows `enter`/`a`/`x` entirely — which `tab`, the sole route
    in, would hit every time.
  - **Summary line = the card's own `border_subtitle`, NOT `#detail`.** Highlighting `preset:<i>`
    sets `saved 07-24 · 12 models` (read through the projection, so the active row counts your
    in-flight edits). `#detail` is deliberately left alone: it has two **async** writers that
    re-render whatever `_current_target` is *at completion time* (the detail worker's tail and
    `_refresh_catalog`), so anything a preset highlight wrote there would be silently clobbered by
    an in-flight `opencode --verbose` — and restoring it on focus-return would need a Focus/Blur
    hook, since Textual doesn't re-post `OptionHighlighted` when a list merely regains focus. The
    subtitle has exactly one writer. A preset row also schedules **no** `catalog.detail` fetch,
    leaving the one-concurrent-fetch rule (§cache.py) untouched.
  - **`s` writes both** (`_save`): the config through the usual diff+confirm (backup, atomic
    rename), then the presets file. Config first — it is the artifact with the backup and the diff
    you just approved, so a failure there aborts before the store moves; if the presets write then
    fails, the config is ahead and the app says exactly that ("Config saved — the presets file did
    not. Press s again.", never a bare "Saved."), and a second `s` takes the **presets-only path**
    (no config diff → nothing to confirm → write straight out) and heals it. That same path serves
    an add, a delete, a rename, and adopting an out-of-band config edit. `_is_dirty` is now
    *config-dirty OR store-dirty*, so `q` prompts for either.
  - **Launch reconciliation** (`_ask_sync`), the one case the invariant can't cover itself — the
    config matches **no** preset because something outside oModel wrote it. If it matches a
    *different* preset, that one silently becomes active (no conflict to resolve; the dirtiness
    baseline is taken after, so a re-point alone reads clean). Otherwise a modal asks: **adopt the
    config** (the active preset takes the models now in the file — already true in memory, so it
    just needs saving) or **restore the preset** (cfg goes back to the preset's models, staged as an
    ordinary edit with a diff you can read). Neither writes anything; both end at `s`. `esc` is a real
    third answer here (`ConfirmModal(escape_cancels=True)`, with a matching `hints=` line): it
    decides later, changing nothing — the default dismissal would have meant *restore*, silently
    rewriting the config the user had just edited outside oModel.
  - **Stale models degrade, they don't break:** a preset naming a model you no longer have switches
    in fine; `_build_rows` surfaces it as the off-chain current-assignment row with `⚠ unavailable`
    (warn-but-allow, decision #5) when the catalog is readable, suppressed in degraded mode.
  - **Degraded mode + the two refreshes:** presets are cfg data, independent of availability, so
    they work **fully** with `opencode` missing or after `CatalogUnavailable`. `r` /
    `--refresh-models` does **not** touch the presets file.
  - **`--restore` is orthogonal:** restoring a backup rewrites the config and leaves the presets
    file alone — so the next launch will find the config matching a different preset (silent
    re-point) or none (the sync prompt). Both land back on the invariant.
- **No CLI surface in v1.** `omodel --preset <N|name>` (switch + save headless) is a plausible
  follow-up, deliberately deferred: the TUI is where you *see* what a switch would change.

### `refresh.py` — `omodel --refresh-omo`
- Locate omo src: `--omo-src` | `$OMO_SRC` | `~/source/oh-my-openagent` (needs
  `packages/model-core/src`). Runner: **bun only** (no node fallback — verified broken).
- Run bundled `tools/snapshot_omo.ts` → JSON (RegExp→`.source`, Set→array, + `meta`). The bun run
  carries a **300s timeout** (non-fatal on expiry — every subprocess call in the repo carries one);
  a temp-materialized copy of the `.ts` (frozen/zipimport case) is removed afterwards.
- Write target: **frozen (PyInstaller) build → always `$XDG_DATA_HOME/omodel/omo-suggestions.json`**
  (under `--onefile`, `__file__`'s sibling `data/` dir lives in the ephemeral `_MEIPASS` extraction
  tempdir — writable but deleted on exit, so writing "to the checkout" there silently loses the
  output; `sys.frozen` gates the branch); else writable repo checkout (`src/omodel/data/`) → write
  there (maintainer commits); else `$XDG_DATA_HOME/omodel/omo-suggestions.json` (user override —
  read back by `suggestions.load()`'s newest-wins rule, §suggestions.py).
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

### `update.py` — `omodel --update` (self-update from GitHub Releases)

The **only** module that touches the network at runtime, and only when the verb is run. There is
**no launch-time version ping**: "no network call needed at runtime" (§Runtime requirements) is a
property worth more than a nag line, and a check on every launch would spend a socket timeout on
the path where the user just wants the TUI. Stdlib only (`urllib` + `tarfile` + `hashlib`) — a
dependency added here would ship inside the very binary this replaces.

- **What "update" means depends on how omodel was installed** (`detect_install` → `Install`):
  `binary` (PyInstaller one-file — `sys.frozen`, `realpath(sys.executable)`, the only
  self-updatable kind) · `pipx` / `uv` (recognized from `sys.prefix`) · `source` (src-layout +
  `pyproject.toml` + `.git` → an editable install) · `pip` (anything else). For every kind but the
  first, `update` **prints the exact command and exits 3** rather than reaching into another
  package manager's tree. Those commands are **tag-pinned** (`pipx install --force
  "git+https://…@v0.4.0"`): `pipx upgrade` / `pip install -U` re-resolve a `git+` spec against the
  default branch, which is not necessarily the release we just named.
- **Everything that makes an update impossible is checked BEFORE the prompt** (`preflight`:
  install kind, platform, a writable install *directory* — `os.replace` needs the directory, not
  the file — and the asset's presence in the release). Asking "Update now? [y/N]", getting a yes,
  and only then saying "this platform has no binary" is a question that should never have been
  put. `apply_update` repeats it: it is a public entry point and has to be safe alone.
- **The swap** (`apply_update`), in this order, on the standalone binary:
  1. temp dir **inside the target's own directory** — `os.replace` is atomic only within one
     filesystem, and `$TMPDIR` is usually a different mount (`EXDEV`). Leftovers from a run that
     was *killed* (no `finally` ever ran) are swept first, but only ones over an hour old — a
     concurrent `--update` is the other reason one exists, and deleting its half-finished
     download would be a self-inflicted failure. "Old" is the newest mtime of the directory **or
     anything in it** (`_touched_at`), never the directory's own: a directory's mtime advances
     when an entry is created or removed and **not** when a file inside it is written, so the
     naive check measures time since `mkdtemp` and would sweep a live download that had been
     running for an hour — and `DOWNLOAD_TIMEOUT` is per socket operation, not per transfer, so
     a big asset on a bad link legitimately runs that long;
  2. checksum against the release's published `.sha256`, same rule as `install.sh` — mismatch is
     fatal, a *missing* checksum asset warns and continues (older releases have none) and reports
     `verified: false` rather than implying a check that never ran;
  3. extract **only** the `omodel` member, to a path we choose — never `extractall`, so a `..`
     member has nowhere to write (3.9 has no `filter="data"`; this is the portable equivalent).
     LICENSE/NOTICE ride in the tarball and are simply not written;
  4. **run the downloaded binary's `--version` and require the release's version back.** The step
     that earns its keep: a linux binary built against a newer glibc than this machine's (the
     documented failure of the prebuilt asset), a truncated download, an asset uploaded from the
     wrong build — each dies here with the *working* omodel still installed;
  5. `os.replace` over the running executable. POSIX renames the directory entry, so the running
     process keeps its inode and finishes normally — you cannot *write* a busy executable
     (`ETXTBSY`), but you can always replace one. Mode is inherited from the old file, so a
     deliberately-restricted install stays restricted. The staged file is **fsynced before** the
     rename (and the directory after): `os.replace` is atomic against a concurrent *reader*, not
     against power loss, and a rename that reaches disk ahead of the data it names is a
     zero-length `omodel` — the exact broken install this section promises cannot happen. Both
     fsyncs are **best-effort**: some FUSE/network mounts refuse them outright, and since there
     was no durability guarantee here at all before, a refused fsync must not turn a working
     update into a failed one.
  Every failure path leaves the existing binary **byte-for-byte untouched** and the temp dir gone.
- **Network errors are not all `OSError`.** `http.client.IncompleteRead` (a body cut mid-transfer)
  descends from `HTTPException`, so it walks straight through an `except OSError` — out of the
  module, out of the verb's `except UpdateError`, and onto the terminal as a traceback with an
  **empty `--json` stdout**. Every read catches `(OSError, HTTPException)`, and `cli._cmd_update`
  keeps a typed backstop so no future surprise can break the single-object contract either. That
  backstop prints the **traceback to stderr** before swallowing: stdout is all the JSON contract
  covers, and a one-line `RuntimeError: …` from a user who will never run it again is not a bug
  report.
- **https only, on every hop.** `build_opener` keeps urllib's default File/FTP/Data handlers, and
  the download URL is read straight out of the release JSON — so `_open` refuses any non-https
  scheme rather than let a `file:///…` asset URL be "downloaded", and `_StripAuthOnRedirect`
  refuses to follow a redirect to one (`_open` sees only the first URL).
- **Auth is optional and one-way.** `$GITHUB_TOKEN`/`$GH_TOKEN`, when set, is sent to
  api.github.com only — purely to dodge the 60-req/hour unauthenticated limit on a shared NAT.
  Asset downloads never carry it, and `_StripAuthOnRedirect` drops the header when a redirect
  crosses hosts **or downgrades to http** (GitHub sends asset URLs to
  `objects.githubusercontent.com`, and urllib otherwise re-sends the original headers there).
- **`detect_install` requires `sys.frozen` AND `sys._MEIPASS`** for the `binary` verdict. `frozen`
  alone is a convention several freezers and embedders set, and what we do with that verdict is
  `os.replace` over `sys.executable` — mistaking a real interpreter for our binary would
  overwrite *it*, and the smoke test cannot catch that (it validates the download, never the
  target). Anything else falls through to a printed command, which is the safe way to be wrong.
- Version comparison is a lenient tuple parse (`v0.3.1` → `(0,3,1)`; `0.4` == `0.4.0`); an
  **unparseable tag is never "newer"**, since the cost of that mistake is swapping the user's
  binary for nothing. `releases/latest` (not `releases[0]`) leaves drafts/pre-releases to GitHub.

### Textual two-pane contract (`app.py`)
- **Rendering user data is never a plain `str` (hard rule).** Textual parses *content markup* in
  every plain string it renders — a `Static`'s content, an `Option` prompt, a toast. Almost nothing
  we render is ours: model + provider ids come from `opencode` and from the config, variants from
  `--verbose`, preset names and the add-model box are typed. A `[` in any of them is an opening tag,
  and an unmatched close (`acme/[/b]`) raises `MarkupError` **from inside the render pass** — where
  no call site can catch it, so the app dies. Typing it in add-model was one keystroke; a config or
  presets file holding such an id was fatal on **every launch**. Three mechanisms, in order of
  preference:
  1. **`markup=False` at construction** for any `Static`/`Label` that shows data (`#providers`,
     `#add-preview`, `#add-title`, `#confirm-body-text`, `#quit-body`, `#preset-name-title`). It's a
     property of the *widget*, so it also covers every future `.update()` on it — a flag can't be
     forgotten, an escape call can.
  2. **`_lit(text) → Content`** for `Option` prompts (`OptionList` has no such flag): a `Content` is
     a Visual, rendered verbatim. Every option built from data uses it — `#targets`, `#candidates`,
     `#presets`, the add-model list, both variant lists.
  3. **`_esc(text)`** for the one widget that renders markup *on purpose*: `#detail` (`[b]` header,
     `[dim]` pending-fetch placeholder). Its target, model, variant and catalog line are all escaped.
  `OModelApp.notify` overrides Textual's to default **`markup=False`** — one choke point for ~20
  call sites that quote ids, preset names, undo labels and `str(exc)`. Note the failure mode isn't
  only a crash: a *well-formed* tag (`[red]…[/red]`) parses fine and silently vanishes into styling,
  so an id you can't run would read as one you can — hence the "renders literally" test beside the
  "doesn't crash" ones. Preset names additionally strip brackets at input (`presets._render_safe`),
  belt-and-braces since they're persisted.
  The default has one **inherited** caller: Textual's own `ctrl+c` handler (`action_help_quit`)
  notifies `Press [b]{key}[/b] to quit the app`, whose tags then showed verbatim. `app.py` overrides
  `action_help_quit` rather than letting markup back through the choke point — and takes the chance
  to name **`q`**, since the `key` Textual resolves is its default `ctrl+q`, which exits via
  `App.action_quit` with **no** unsaved-changes prompt (the `q` → QuitModal flow under §Events).
- **Header** `Static#providers`: one line `oModel: <id · id · …>` from `catalog.connected` in its
  **first-seen order** (per §Data sources; e.g. `opencode · deepseek · moonshotai-cn · openai ·
  zhipuai`) — so you see what's available at a glance; doubles as the
  ⚠-unavailable explainer ("no listed provider serves this"). Just the connected list — **no cache-age
  suffix** (dropped as clutter; the served list is bounded-fresh since an expired cache auto-refetches,
  and `r` refresh is documented in the `?` overlay). On `CatalogUnavailable` it shows the banner +
  `r` retry instead.
- **Left** `OptionList#targets`: AGENTS then CATEGORIES; option IDs `agent:<name>`,
  `agent:<name>.ultrawork` / `.compaction` (indented sub-rows, shown when present in config or added
  via `a`), `cat:<name>`. Sub-target set per agent = `{model}` ∪ present `{ultrawork, compaction}`.
  `compaction` is valid on every agent; `ultrawork` is **Sisyphus-only** (omo's `ultrawork`/`ulw`
  keyword swaps the model only on Sisyphus — on any other agent it's dead config omo never reads)
  (`ULTRAWORK_AGENTS` / `subkinds_for` in `session.py`, hard-coded like `GPT_ONLY_AGENTS`). So only
  Sisyphus has a choice of sub-kind: `a` there opens a **chooser modal** (below) — naming each kind
  + what it's for rather than blindly cycling. Every other agent has the single kind `compaction`,
  so `a` adds it **directly** (no modal — there's nothing to choose).
- **Left (bottom)** `OptionList#presets` (decision #17 / §presets.py): a card under `#targets`
  inside `Vertical#left`, `height: auto` capped at `max-height: 50%`, `border_title = "PRESETS"`,
  option IDs `preset:0` … `preset:<n-1>` plus the trailing `preset:new`, rows `● 1 <name>` and a
  final `+ add preset…` — **`●` = the ACTIVE preset**, the one your edits go into and the one `s`
  publishes to the config. Presets are **unlimited** and the list is dense (no empty rows). `enter`
  switches to a preset (a staged, undoable replace that first banks your edits into the one you
  leave) — or, on `+ add preset…`, adds one; `a` adds one from any row (row-blind, never
  overwrites); `r` renames; `x` deletes behind a confirm and is **refused on the active
  one**, so you can never reach zero. Third
  focusable widget, reached by `tab` / `shift+tab` only — no dedicated key (`←`/`→` remain
  targets-vs-candidates).
  Highlighting a row writes its one-line summary to **this card's `border_subtitle`,
  never `#detail`** (which has two async writers that would clobber it — §presets.py) and schedules
  no `catalog.detail` fetch. `v` bells here.
- **Right**: `Static#detail` (current model/variant + `catalog.detail` line) and
  `OptionList#candidates` (IDs `cand:<i>`, last = `cand:add` — the `+ add model…` row). The `cand:<i>`
  row matching the current assignment (at launch the on-disk model; follows your pick) is prefixed
  `● ` (others `  `); an off-chain assignment not otherwise in the list gets its own `cand:<i>` row
  just before `cand:add` so it's shown + re-selectable (see §`resolve.py` "Current pick"). The **highlighted (cursor) row is remembered per target** — keyed by the row's
  `provider/model` identity, not its index — and restored on every re-render, so the cursor returns
  to your last position when you revisit a target **and after `r` refresh** (a refresh re-resolves
  the chain against new availability and reorders rows; identity-keying survives that, an index
  wouldn't). It's the one per-session cache a refresh deliberately does **not** clear. The `catalog.detail`
  line is a ~3s / ~320 MB subprocess, so it is fetched in a background worker (cached per model,
  debounced ~0.2s, and **capped to one fetch at a time** — §cache.py) and appears when ready; the rest
  of the pane renders instantly so highlighting is never blocked.
- **Hint bar** `Horizontal#hints-bar` (bottom row): **minimal and static** — keys `s save · q quit · ? help` in
  `Static#hints` at the left, app version `v<version>` (`Static#hints-version`) right-aligned at the tail
  (the `_HINT_BAR` constant), set once in `on_mount` and never re-rendered. It advertises only the
  must-have keys: `s` (the app's whole point), `q`, and `?` (the overlay that documents the rest of
  the base screen) — save and quit, the pair you reach for, sit together, and `?` tails the line
  as the pointer to the rest. All the pane/row-contextual keys (`enter`, `v`, `x`, `a`) and `u`/`⌃r` undo/redo and `r`
  refresh **moved into the `?` help overlay** (`HelpModal`), so the bar never grows past one line
  and never has to track focus/row/undo state. This is deliberately *not* a full reference — the
  bar is a floor (save/quit/help); `?` is the ceiling. Modals carry their own one-line hint
  (`Static.modal-hints`) instead. (`r` is also still advertised in the `#providers` header.)
- **Help overlay** `HelpModal` (`?`): a read-only, scrollable `ModalScreen` (same body pattern as
  `ConfirmModal`) carrying the base-screen keys **grouped by pane** — Move / Models / Presets /
  Undo. Base-screen-only (gated in `check_action` alongside focus + undo/redo — `?` over a
  modal is pointless; `esc` closes the modal first). Closes with `?` (toggles), `esc`, or `q`.
  Grouping by pane is the point: `enter`/`a`/`r`/`x` each mean something *different* on a
  `#presets` row (switch / add / rename / delete) than on the model panes (set / add-edit /
  refresh / clear), and two short adjacent groups show that contrast better than any prose could.
  It is deliberately **not** an exhaustive reference — the earlier version was, at 37 lines, and
  scrolled on every terminal anyone actually uses. Three rules
  keep it short: (1) keys already on screen aren't repeated — `s`/`q`/`?` sit on the hint bar
  *behind* the modal, and every dialog states its own keys on its own hint line
  (`Static.modal-hints`), so the old **In dialogs** group was eight lines of duplication;
  (2) universal conventions go unsaid — enter confirms, esc cancels, `y`/`n` answer, arrows move;
  (3) each key earns **one** line, in the group that describes what it does. What survives is what
  you cannot guess: the vim aliases, `v`, the pane-contextual meanings, undo/redo, and the keys
  that are *not* app bindings — **`tab` is listed under Move**, since Textual provides it for free
  and it is otherwise the one undiscoverable way to reach `#presets` (hence the group's
  `(tab to reach)` tag rather than a second `tab` line). The `+ add preset…` row earned no line of
  its own once `a` became row-blind (it does exactly what `a` does, and the row says so on
  screen) — §presets.py. `_BODY` and `_HINT_BAR` stay in sync with the module KEYS docstring.
  Body lines stay **≤54 cells**, not the 56 of content the 62-cell panel actually offers: on a
  short terminal the body scrolls and the scrollbar takes 2 of them, so a 55-cell line wraps and
  gives back the height the trim just bought. `test_help_body_stays_light` pins both the width and
  the line count (the old body needed 47).
  **It still scrolls below ~30 rows, and that is accepted.** The body gets
  `floor(0.90 × terminal_height) − 7` rows (the `max-height: 90%` cap, then border 2 + padding 2 +
  title 1 + hint 1 + its `margin-top` 1), so 20 lines of `_BODY` need 30 rows: measured 14 at 80×24,
  18 at 84×28, 20 at 100×30. Fitting 24 would mean cutting to 14 lines — the three blank separators
  only buy 3, so the rest would come out of the group headers or the key lines, i.e. out of exactly
  the pane-grouping this overlay exists for. CSS can't close it either: dropping the 90% cap, the
  padding *and* the hint margin reaches 20 rows at 24 with zero slack. So the modal is scrollable by
  design (`↑↓`/`jk`/PageUp/PageDown/Home/End, advertised on its own hint line) rather than trimmed
  further. `test_help_body_stays_light`'s cap is a growth budget, **not** a no-scroll guarantee —
  its docstring says so; don't read it as one.
- **Events:** highlight on `#targets` → repopulate detail+candidates for that target;
  `enter` on `#candidates` **dispatches by row**: on `cand:add` → open the add-model modal (below);
  on any other `cand:<i>` → set that model (+ default variant) on the in-memory target;
  `v` → push `OptionList` of the family's valid variants + `(none)`; `a` → pane-contextual: opens the
  add/edit-model modal (below) from #candidates **and** from a #targets *category* row (`enter` on
  `cand:add` also opens it), adds a sub-target from a #targets *agent* row (chooser on Sisyphus,
  direct on every other agent — below), or, in `#presets`, adds a preset holding the current
  assignments and switches to it (name modal, §presets.py — row-blind, it never replaces the row
  under the cursor);
  `x` → clear
  the assignment (on an ultrawork/compaction sub-target row → **delete the whole row**, parent agent
  regains focus — clear == delete since an empty sub-object serializes away; on a `#presets` row →
  **delete that preset**, behind a confirm, and REFUSED on the active one — the config mirrors it;
  on a `#candidates` row **you added** → **delete that row**, see §The one row you can delete);
  `u` → undo / `ctrl+r` → redo the last edit (in-session snapshot stack, §history.py — gated to the
  base screen via `check_action`, so they don't reach through a modal that binds `u` itself);
  `s` → diff+confirm save; `r` → refresh
  (off-thread `opencode models --refresh` + rebuild cache; also retries after `CatalogUnavailable`);
  `q` → quit (when dirty: the three-way `QuitModal` — save & quit / discard / cancel, since a
  discard now drops preset work as well as config work); `?` → open the `HelpModal` full-key overlay (base-screen-only, same
  `check_action` gate as focus + undo/redo); highlight on `#presets` → one-line summary in that
  card's own `border_subtitle` (never `#detail`; no `catalog.detail` fetch); `enter` there →
  **switch** to that preset (a staged replace via `_restore_state`, `u`-undoable — and the undo
  moves the `●` back too); `v` there → bell;
  `tab` / `shift+tab` → cycle all three panes, the only route into `#presets` (Textual `Screen`
  traversal, **not** an app binding — so it needs no `check_action` gate and inside a modal it
  cycles that modal's widgets instead);
  **button emphasis follows focus** in both button modals — deliberately no static
  `variant="primary"`, which paints one button permanently and competes with Textual's focus
  styling: two buttons look emphasized at once, the eye reads the always-coloured one as the
  selection, and walking the row appears to do nothing. `Button:focus` carries it instead, so
  exactly one is lit and it is the cursor. (Textual focuses buttons with `text-style: b reverse`,
  which wins over anything the app sets, so the rule renders as `$primary` text on a light fill
  rather than the reverse — one unmistakable block either way.) In those modals
  `←`/`→` + vim `h`/`l` walk the button row
  instead, wrapping exactly like `tab` because they run the same `app.focus_next` /
  `app.focus_previous` actions — the buttons are laid out horizontally and `tab` alone was the
  only way along them; those keys are free there precisely because the App's own are gated off a
  modal. In `ConfirmModal` the vertical keys stay on the scrolling diff body (`j`/`k` scroll,
  `h`/`l` pick), the same split the base screen uses;
  `←`/`→` → focus the targets / candidates pane (gated to
  the base screen via `check_action`, so it never grabs focus from under a modal; the add-model
  `Input` keeps its cursor arrows). **Vim aliases:** `h`/`l` mirror `←`/`→` (the *same* gated focus
  actions); `j`/`k` mirror `↓`/`↑` within whatever list is focused — bound on the `VimOptionList`
  every list uses (so they also work in the variant / add-sub modals), while a focused `Input` still
  takes `h`/`j`/`k`/`l` as literal text (printable keys reach a widget before its bindings). The vim
  keys — like every non-must-have key — are **absent from the (static, one-line) hint bar**; they're
  documented in the `?` overlay instead. Pilot tests drive these via the stable IDs.
- **Add-model modal (`a` / `cand:add`):** a **two-phase** picker (IDs `#add-input`,
  `#add-candidates`, `#add-variants`, `#add-title`, `#add-preview`, `#add-hints`).
  **Model phase** — the `Input` (`#add-input`) fuzzy-filters `#add-candidates`, a list of the
  `provider/model` pairs you actually have (`catalog.available`), **dedicated-first** (single-vendor
  before gateway, then first-seen). The fuzzy engine is `textual.fuzzy.Matcher`, scored on the full
  `provider/model` string (so you can filter by either side). It is **type-to-search**: the modal
  opens with **no list** (the empty-query browse dump is intentionally not rendered — building/
  laying out every available pair, which a gateway can make hundreds, lagged the open), and the
  list appears only once you type. Results are capped (`_MAX_CANDIDATES`) so a broad one-letter
  query can't reintroduce that lag — type more to narrow. `Matcher("")` is never constructed (it
  raises). A typed query auto-highlights the top match for quick-select; with the list empty (right
  after opening, or a query that matches nothing) **nothing is staged**, so a reflexive `enter` is a
  no-op — you never commit a model you didn't choose. `↑`/`↓` (or emacs **`Ctrl-P`/`Ctrl-N`**) move
  the list while the `Input` keeps focus (driven from screen bindings; the list is `can_focus=False`)
  — and this phase's hint line (`_MODEL_HINTS`) is where `⌃p`/`⌃n` are **advertised**, since it is
  the one place `j`/`k` are literal text and the emacs aliases are the only home-row way to move.
  (They work in the variant phase too, but that list is focused and already offers `↑↓`/`jk`.)
  `Ctrl-P` is normally the App's *priority* command-palette binding, so `OModelApp.check_action`
  suppresses the palette while this modal is open (the only way to gate a priority binding — it is
  checked App-down before the key reaches the modal). **`Tab`** fills the highlighted
  pair into the `Input` (intercepted in `on_key`, before focus traversal); `enter` chooses the
  highlighted/staged pair, or — when the list is empty — the validated typed text; `esc` cancels. A full
  `provider/model` → used **verbatim** (split on the *first* `/`); a bare id → auto-prefixed via
  `resolve_prefix` **if available**, else `⚠ unknown — add a provider/` and `enter` is **blocked**; a
  typed full id that **fuzzy-matches nothing** appears as a synthetic **"use as typed"** row (so
  custom / `⚠ unavailable` ids still work — warn-but-allow, decision #5; the `unavailable` warn is
  **suppressed in degraded mode** — empty `connected`, availability unknown — mirroring the
  off-chain row's rule, and recomputed against the **live** catalog on accept in case an `r`
  refresh landed while the modal was open). A half-typed fragment that
  *still* fuzzy-matches (e.g. a Tab-filled id after a backspace — `zhipuai/glm-` ⊂ `zhipuai/glm-5`)
  falls back to those matches rather than leading with that ⚠-unavailable synth row. *(Trade-off:
  the synth row is offered **only** when nothing fuzzy-matches, so the rare custom id that is itself a
  subsequence of a longer available pair — e.g. `openrouter/claude` ⊂ `openrouter/anthropic-claude-…`
  — can't be committed as-typed; it shows the fuzzy matches instead. Accepted to kill the mid-edit
  footgun: a longer/distinct custom id is never a subsequence of a shorter available one, so the
  common "add a model I don't have yet" path is unaffected.)* A **GPT-only** target
  (Hephaestus) filters the list to GPT models and still blocks a typed non-GPT id.
  **Variant phase** — *iff* opencode reports variants for the chosen `(provider, model)`
  (`Catalog.variants_for` — the cached `--verbose` map, decision #14), `#add-variants` (a
  `VimOptionList`, IDs `var:<v>` / `var:__none__`) lets you pick one or `(none)` ⇒ `variant=None` (a
  *fresh add*, **not** `VariantModal`'s `''` clear sentinel); a model opencode lists with no variants
  (kimi, glm-5) — or whose verbose isn't cached anywhere — skips it and adds immediately. A `none`
  opencode may list is dropped as a duplicate of the synthetic `(none)` (`_is_no_variant`) — never
  offered, never written; picking `(none)` (or a `none`) removes the `variant` key. `esc`
  returns to the model phase. The post-hoc **`v` key** (`action_variant`/`VariantModal`) now reads the
  **same** `variants_for` source — the old `known_variants` "always offer *something*" fallback is
  **gone**; `v` on a model with no reported variants just **bells**. The result dismisses one
  candidate-row dict (`source` `"add"`); it's just another pickable row.
- **Add-sub (`a` on an agent):** an agent supports `compaction` always + `ultrawork` only on
  Sisyphus (`_subkinds_for`). Only Sisyphus has a *choice*, so only there does `a` open a **chooser
  modal**: an `OptionList` (`#sub-list`, IDs `sub:ultrawork` / `sub:compaction`) with one row per
  valid kind, each naming the kind + a one-line description of what omo uses it for (ultrawork =
  model swapped in on an `ultrawork`/`ulw` message; compaction = model for auto summaries). A kind
  already on the agent is **disabled** (`✓ added`); the `u`/`c` shortcut or `enter` picks one
  (→ empty sub-row, not dirty until a model is staged), `esc` cancels. **Every non-Sisyphus agent
  has the single kind `compaction`**, so `a` skips the modal and adds it **directly** — there's
  nothing to choose. Either way, every supported kind already present → `a` just bells (nothing to
  add). Replaces the old blind add-next cycle so the choice — and what each kind means — is explicit
  for newcomers, without making single-kind agents click through a one-option modal.

## Packaging & distribution (GitHub-only, no PyPI)

- `pyproject.toml` (hatchling, src-layout): `[tool.hatch.build.targets.wheel] packages =
  ["src/omodel"]`. The non-Python payload (`data/*.json`,`*.jsonc` + `tools/*.ts`) ships
  **automatically** because it lives under the package tree — do **NOT** add a `force-include`
  (it duplicates the path and fails the wheel build). `data/` and `tools/` each carry an
  `__init__.py` so they are **regular** packages: `importlib.resources.files("omodel.data" /
  "omodel.tools")` only resolves on a regular package under the **3.9** floor (namespace-package
  `files()` support landed in 3.10) — without it, every bundled-data read raises `TypeError:
  … not NoneType` on 3.9. `requires-python = ">=3.9"`; deps `textual` (pinned), `json5`. Entry
  point `[project.scripts] omodel = "omodel.cli:main"`.
- **Primary — standalone binary + installer (GitHub Releases):** PyInstaller **one-file** build,
  `pyinstaller --onefile --name omodel --collect-data omodel src/omodel/__main__.py` (bundles
  `data/` + `tools/`; `importlib.resources` reads them from the frozen package). CI `release.yml`
  builds on tag push (matrix: **linux-x64** `ubuntu-latest`, **darwin-arm64** `macos-latest`),
  gated by: a **tag↔version check** (tag must equal `__init__.__version__` and pyproject's
  `version`), the **full pytest suite**, and a smoke test running `--version` **and `--check`**
  (`--check` imports the data-loading path, so a broken `--collect-data` bundle fails the release
  — `--version` alone returns before touching bundled data and can't catch it). It attaches
  `omodel-<os>-<arch>` (bare binary), `omodel-<os>-<arch>.tar.gz` (**binary + LICENSE + NOTICE**
  — the tarball is the canonical asset; NOTICE must ship with the omo-derived bundled data), and
  `….tar.gz.sha256` to the Release. (Intel-mac `macos-13` was dropped — GitHub is retiring those
  runners and they queue for hours; Intel macs install via pipx.) `install.sh` detects OS/arch
  (`linux-x64`, `darwin-arm64`), downloads the **tarball**, verifies the published sha256 when
  present (hard-fails on mismatch; warns-and-continues when absent, e.g. an older release),
  extracts, and installs `omodel` to `~/.local/bin`:
  `curl -fsSL https://raw.githubusercontent.com/zhoufanscut/oModel/main/install.sh | sh`.
  The linux binary needs a glibc ≥ the `ubuntu-latest` builder's (documented in README; older
  distros → pipx/uvx path).
- **Staying current — `omodel --update`:** the standalone binary updates itself from the same
  Release assets `install.sh` reads (tarball + `.sha256`, `releases/latest`), which is why the
  installer's asset naming is a contract and not an implementation detail: the two must agree on
  `omodel-<os>-<arch>.tar.gz` forever, and a release that stops publishing the checksum silently
  downgrades both to unverified. Every other install kind gets a tag-pinned command instead. →
  §update.py
- **Secondary — pip/pipx/uvx straight from GitHub (no PyPI):**
  `pipx install git+https://github.com/<you>/oModel` ·
  `uvx --from git+https://github.com/<you>/oModel omodel` ·
  `uv tool install git+https://github.com/<you>/oModel`.
- **Maintainer:** `git clone … && uv pip install -e .`; refresh data with
  `OMO_SRC=~/source/oh-my-openagent omodel --refresh-omo`, commit `src/omodel/data/omo-suggestions.json`;
  `git tag vX.Y.Z && git push --tags` → `release.yml` builds and publishes the binary.
- ⚠ **Licensing:** the bundled `omo-suggestions.json` is **data derived from omo source** (Sustainable
  Use License — satisfied while oModel stays free/non-commercial), redistributed in the repo, the
  wheel, and the binary. Attribution lives in `NOTICE`; the wheel carries it via hatchling's
  `dist-info/licenses/`, and each release **tarball ships `LICENSE` + `NOTICE` next to the binary**
  (the bare-binary asset alone carries none — the tarball is the compliant artifact). Keep `NOTICE`
  intact when redistributing. `default-config.jsonc` is oModel's own (not copied) to avoid this.

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
   value, the palette *inside* agents/categories gone but comments *outside* them preserved verbatim,
   a `.backup/<ts>.jsonc` snapshot exists (verbatim original); a second
   save adds a second snapshot and `--restore` lists them newest-first.
8. **Live:** machine with `opencode`, no omo source → `omodel` launches, lists from `opencode models`,
   edits + saves a clean file OMO reloads.
9. **Presets — the working state (Pilot + unit):** against a temp `--config`, launch with no
   presets file → preset 1 is seeded from the config, named `default`, marked `●`, **nothing is
   written**, and `q` exits without a prompt. Set a model → the active preset carries it; `s` →
   confirm → **both** files land and `matching_index(store, config) == store.active` (the invariant,
   asserted directly). `a` on row 2 → name → it holds those models and becomes `●`; edit, switch back
   with `enter` (row 1's models return), switch forward (row 2's edits were banked, not lost); `u`
   after a switch moves the `●` back **with** the models. `x` on the active preset is refused
   outright (no modal); on another it confirms, and nothing reaches disk until `s`. `q` while dirty →
   the three-way modal: `d` leaves both files byte-identical, `s` runs the diff+confirm then exits.
   Launch with a presets file whose active preset differs from the config → the sync modal; both
   answers write nothing. Launch where the config matches a *different* preset → no modal, that one
   becomes active, app reads clean. Unit (`test_presets.py`): missing / corrupt / wrong-`version` /
   short file → empty store, no exception; a non-dict `agents`/`categories` → `{}`; an out-of-range
   or empty-pointing `active` → the first non-empty preset; `write` is atomic, raises on an
   unwritable path and leaves no temp file; `store_fingerprint` ignores `saved_at` but sees content,
   names and `active`; `fingerprint` is order-insensitive and treats an empty
   `ultrawork`/`compaction` as absent. **With a readable, non-empty catalog**, a preset naming an
   unavailable model shows the `⚠ unavailable` off-chain row — assert that precondition explicitly,
   or a stubbed-empty catalog makes the test pass for the wrong reason. Never touches the real
   `~/.config/opencode/` (temp `--config` only).

## Appendix — execution playbook (HISTORICAL: how v0.1.0 was built)

> **This section is a record, not an instruction.** oModel shipped; the fan-out below is how the
> initial build was organised and is kept because the dependency analysis in §Notes still explains
> *why* the module boundaries fall where they do. Nothing here describes current process, and its
> counts are frozen at v0.1.0: there are 12 test files today, and `tests/verification.md` runs 10
> checks — §Verification below still lists 9, because decision #18's Check 10 (the agent surface)
> was only ever written up there. Don't act on it.

The build fanned out as **6 specialists + a lead**, contract-first.

### Roster
| Role | Owns | Model |
|---|---|---|
| **Lead / Integrator** | §Data contracts + module signatures; repo scaffold; generate real bundled data (`snapshot_omo.ts`→`omo-suggestions.json`) + `default-config.jsonc`; wire `app.py`↔modules; final integration | **Opus** |
| **Core logic** | `catalog.py` · `suggestions.py` (detect_family, FAMILY_VENDOR) · `resolve.py` · `tools/snapshot_omo.ts` | **Sonnet** |
| **Config I/O** | `config_io.py` (serialize, backups/restore, scaffold) | **Sonnet** |
| **TUI** | `app.py` two-pane + variant/add-model/diff modals + keybindings | **Opus** |
| **CLI + packaging** | `cli.py` · `refresh.py` · `pyproject.toml` · `install.sh` · `.github/workflows/*` · README/LICENSE/NOTICE | **Sonnet** |
| **QA / verification** | the `tests/test_*` suite (7 files at the time) authored **from this spec, independent of the implementations** + runs the §Verification checks as the **merge gate** | **Sonnet** |

### Sequencing (contract-first)
0. **Lead (blocking):** freeze §Data contracts (`target` id, `source` enum, candidate-row dict) + each
   module's public signatures; scaffold the repo (`pyproject`, package dirs, stub modules); generate the
   real `omo-suggestions.json` (bun + omo checkout) and hand-write `default-config.jsonc`. Unblocks all.
1. **Fan out in parallel (isolated git worktrees):** Core, Config, TUI, CLI+packaging, QA each in their
   own worktree against the frozen interfaces. QA writes tests from the spec + stable widget IDs in
   parallel (not blocked on implementations).
2. **Integrate (lead):** merge tracks, wire `app.py` to catalog/suggestions/resolve/config_io, reconcile
   any interface drift against the §Data contracts.
3. **Gate (QA + lead):** QA's `test_*` green **and** every §Verification check passes (incl. a live
   `opencode` run + a Pilot save round-trip). Nothing ships until green.

### Notes
- **Integration risk concentrates at `app.py`** (it consumes all four modules); the §Data-contracts
  block is what lets it be built in parallel against frozen shapes. Lead owns final wiring.
- **Dependencies:** `resolve.py` → `suggestions.py` + `catalog.py`; `refresh.py` → `snapshot_omo.ts`.
  `config_io.py` and CLI+packaging are near-independent; everything else parallelizes once contracts
  are frozen.
