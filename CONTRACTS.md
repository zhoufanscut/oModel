# oModel — FROZEN CONTRACTS (read with DESIGN.md before coding)

This is the contract-first boundary. **Read `DESIGN.md` in full first** — it is the spec.
This file pins the shared shapes + public signatures everything else builds against. Do not change
one without updating this file: both surfaces (`app.py`, `cli.py`) and the agent JSON depend on them.

## Hard rules (permanent)

1. **Python floor is 3.9.** Put `from __future__ import annotations` at the top of every
   module. No runtime PEP-604 unions (`isinstance(x, A | B)`) and no
   runtime PEP-585 generics; annotations-as-strings make `dict | None` in signatures fine.
2. **REAL-CONFIG SAFETY.** The live `~/.omo/omo.jsonc` (and the legacy
   `~/.config/opencode/oh-my-openagent.jsonc`) is the user's real file. Never read-then-write it
   in tests or examples. The unified path carries a side effect a temp one does not — the first
   run there adopts a stranded legacy presets store and DELETES the original — which is why
   `tests/conftest.py` redirects `$HOME`/`$USERPROFILE` as well as `$XDG_CONFIG_HOME`. Every
   test passes an explicit temp `path`/`--config`. **Nothing may write anywhere
   under the real config dir** — that covers `.backup/` *and* `.omodel-presets.json`
   (§presets.py), not just the config file itself.
3. **NEVER RENDER DATA AS A PLAIN `str` (`app.py`).** Textual parses content markup in
   any plain string it renders, so a `[` in a model id, provider, variant, agent/category name,
   preset name or `str(exc)` is a tag — and an unmatched close raises `MarkupError` inside the
   render pass, which kills the app. Build data-carrying `Static`/`Label` with `markup=False`, wrap
   `Option` prompts in `_lit()`, `_esc()` anything spliced into `#detail` (the one markup widget),
   and leave `OModelApp.notify`'s `markup=False` default alone. → DESIGN §Textual contract
4. **REAL-CACHE SAFETY.** The opencode-output cache lives at `~/.cache/omodel/`
   (`$OMODEL_CACHE_DIR` → `$XDG_CACHE_HOME/omodel` → `~/.cache/omodel`). Tests must never touch
   the real cache: the autouse `conftest.py` fixture points `$OMODEL_CACHE_DIR` at a tmp dir, and
   any test exercising the TUI/catalog must stub `subprocess.run` (no real `opencode` — each call
   is ~3s / ~320 MB RSS, and stacking them OOM'd a machine).

## Shared shapes (the integration seam)

**`target` id** (string): `"agent:<name>"` · `"agent:<name>.ultrawork"` ·
`"agent:<name>.compaction"` · `"cat:<name>"` — identical to the `OptionList#targets` option IDs.

**`source` enum** (string): `"omo"` (a fallbackChain entry — exact or same-line substitute) ·
`"add"` (an off-chain pick — typed in the add-model modal, or the target's current off-chain
assignment surfaced by `app.py` from cfg as a `cand:<i>` row). (`"mine"` retired: `candidates()`
no longer dumps every connected model — off-chain picks go through the add-model modal.)

**candidate-row dict** — yielded by `Resolver.candidates()`, rendered by `app.py`:
```python
{
  "source":   "omo" | "add",
  "model":    "glm-5.1",              # RESOLVED bare model id actually used (the substitute,
                                      #   when this is a same-line stand-in), no prefix
  "provider": "zhipuai",              # one serving provider; candidates() emits one row PER
                                      #   serving provider, dedicated-first (a non-empty str —
                                      #   rows with no connected provider are dropped, never shown)
  "variant":  "max" | None,           # per precedence; None = unset
  "entry":    {...} | None,           # the omo fallbackChain entry; None for an 'add' row
  "substitute_for": None | "glm-5",   # None = exact id; else the omo id this same-line row fills
  "warn":     [] | ["variant"],       # 'omo' rows: variant only ('unavailable' is skipped, not
                                      #   shown). 'add' rows may also carry ["unavailable"].
}
```
Value written to config = `f"{provider}/{model}"` plus `variant` (omitted when `None`) — i.e.
the resolved substitute, not the omo id. `substitute_for` is display-only.

The shape is **unchanged** by the two-phase add-model modal (`#add-input` fuzzy `provider/model`
list `#add-candidates`, then the variant list `#add-variants`): `variant` was always a field — an
`"add"` row now carries the variant picked in the modal's variant phase (still `None` when opencode
reports no variants for the chosen `(provider, model)` via `Catalog.variants_for`), instead of being
forced to `None`.

## Agent JSON (the second frozen shape — `cli.py --json`, decision #18)

Consumed by LLM agents, so it is a **public API**: additive fields are fine, renames and removals
are not. Every payload carries `"schema": 1`; bump only on a breaking change.

- **Exit codes** — `0` ok · `1` omodel failed (unwritable path, malformed config) · `2` usage ·
  `3` **refused by a guard**. The 1-vs-3 split is the contract an agent branches on; do not
  collapse it.
- **Refusal shape** — `{"schema", "ok": false, "error": <slug>, "message", …context}`. `error`
  slugs are stable: `unknown_target`, `bad_value`, `unavailable`, `bad_variant`, `gpt_only`,
  `unknown_preset`, `active_preset`, `bad_input`, `write_failed`, `bad_config` (the config
  could not be read or parsed — exit 1; the one failure that used to print no payload at all).
- **`degraded_reason`** (beside `degraded`) — `null` when not degraded; else
  `"opencode is not on PATH"` or the `CatalogUnavailable` message (non-zero exit, zero lines,
  timeout). Additive — no schema bump.
- **`degraded`** (on `show`/`candidates`/`check`) — `not catalog.connected`, i.e. availability is
  UNKNOWN. Never omit it: a consumer reading `candidates: []` without it concludes "no models
  exist" rather than "opencode is unreachable". Correspondingly `available` is `null` (not
  `false`) when unknown.
- **A candidate row** is the internal candidate-row dict MINUS `entry` PLUS `index`, `value`
  (`f"{provider}/{model}"`, pre-assembled so a consumer never builds it), `current` (bool),
  `settable` (bool) and `variants` (from `catalog.variants_for` — `[]` means "no information",
  not "no variants"). `entry` (the raw omo `fallbackChain` dict) is **deliberately withheld**:
  publishing it would freeze omo's internal schema into omodel's public output. `settable: false`
  marks a row `set` would refuse — the list surfaces the target's CURRENT assignment even when it
  is unpickable (a GPT-only agent holding a non-GPT model; a model whose provider you have since
  disconnected), and the guide tells consumers to use `value` verbatim, so the two had to be
  reconciled; marking beats hiding what is configured. **`settable` MUST be computed by calling
  `_validate` itself** (with `variant=None` — a bare `set` passes no variant), never by
  re-deriving the conditions: a hand-rolled version covered `gpt_only` and missed `unavailable`,
  so the synthesized off-chain row advertised `settable: true` and then exited 3.
- **`sync_conflict`** is on EVERY payload (reads and writes). True = the config matches no preset
  because something outside omodel wrote it, and **the next write adopts it into the active
  preset**, including targets the command never named. The TUI escalates the same decision via
  `_ask_sync`; the CLI cannot prompt, so it must report. The prose surfaces say it too — a
  JSON-only signal let `omodel check` print "OK" over a pending conflict.
- **`check`'s `problem` slugs**: `unknown_target`, `unavailable`, `bad_variant`, `gpt_only`,
  `malformed_map` (the last carries `target: null` — it belongs to the file, not a target).
  Anything `set` refuses, `check` must report — otherwise a config `set` would never have
  produced (a preset re-installing a non-GPT hephaestus) reads as healthy. Variant validity is
  **not** gated on `degraded`: it comes from the cached `--verbose`, not from opencode being on
  PATH, and gating it made `check` and `set` contradict each other on one file.

The full contract, written for the agent rather than the maintainer, is
`src/omodel/data/agent-usage.md` — shipped in the package and printed by `omodel agent-guide`.
Keep the two in step: the doc is what agents actually read.

## Public signatures (authoritative = the modules themselves)

The modules are the signatures; this is the summary you read before changing one. Where the two
disagree the code wins — and the divergence is a bug in this file, so fix it here in the same
commit.

- `catalog.py`: `class CatalogUnavailable(Exception)`; `@dataclass Catalog(available: dict,
  connected: list)` with `.providers_for(model_id)->list`, `.detail(model_id, use_cache=True,
  provider=None)->dict|None` (`provider`, when it serves the model, selects WHOSE record — the
  detail pane passes the assignment's provider; else first-of-providers_for as before),
  `.variants_for(provider, model, stale_ok=True)->list` (cached `--verbose` variant keys for the
  model pickers — first non-empty across the picked provider then others, else `[]`; never a
  subprocess; default reads at **any age** — `_STALE_OK`, the only TTL-exempt read;
  `stale_ok=False` restores the 24h TTL and is for callers that REFUSE on the answer rather than
  annotate with it — see DESIGN §cache.py);
  `load(opencode_bin="opencode", use_cache=True)->Catalog`;
  `refresh(opencode_bin="opencode")->Catalog` (force `opencode models --refresh` + rebuild cache).
  All three opencode calls read through the on-disk cache (`cache.py`) and carry a `timeout=`.
- `cache.py`: on-disk cache of opencode stdout (24h TTL, flat, under `~/.cache/omodel/`).
  `cache_dir()->str`; `read(key, ttl_seconds=None)->str|None`; `write(key, stdout, args=None)->None`;
  `age_seconds(key)->float|None`; `clear()->None`; `CACHE_VERSION`. Best-effort: missing/corrupt/
  expired → miss; write errors swallowed (a non-writable cache never breaks the caller).
- `suggestions.py`: `FAMILY_VENDOR` (frozen 15-map); `@dataclass Family`; `@dataclass
  Suggestions(meta, agents, categories, families, known_variants)` with `.detect_family(id)->
  Family|None`, `.vendor_for(id)->str|None`; `vendor(family)->str|None`;
  `normalize_model_id(s)->str`; `load(path=None)->Suggestions` (no explicit path/env override →
  the NEWER of the `$XDG_DATA_HOME` snapshot and the bundled data, by `meta.generatedAt`).
- `resolve.py`: `@dataclass Resolver(catalog, suggestions, gateways, real_tokens)` (`gateways` +
  `real_tokens` are computed in `build()`) with classmethod
  `build(catalog, suggestions)`, `.vendors_served(p)->int`, `.resolve_prefix(model_id, source,
  entry=None)->str|None`, `.candidates(target)->list[dict]`.
- `config_io.py`: `config_path(cli_override=None)->str` (first EXISTING of
  `~/.omo/omo.jsonc` → `~/.omo/omo.json` → legacy; else the unified path, so a scaffold never
  recreates the legacy file); `unified_config_path()->str`; `legacy_config_path()->str`
  (`$XDG_CONFIG_HOME` applies to the LEGACY path ONLY — `~/.omo` is `$HOME`/`$USERPROFILE` on
  every platform, matching omo);
  `OPENCODE_BLOCK = "[opencode]"`; `scope_of(cfg)->"opencode"|"root"` (CONTENT-based, never
  filename-based); `managed_root(cfg)->dict` (read, never creates) and
  `managed_root_for_write(cfg)->dict` (creates/coerces the block) — the node holding
  `agents`/`categories`; `load_config(path=None)->(cfg, path)`
  (raises `ConfigParseError(ValueError)` — message carries the path — on malformed JSONC, or its subclass `ConfigReadError` when the path cannot be opened or scaffolded at all; cli.py
  catches it for a friendly exit-1 message on the TUI/`--print` paths; scaffolds the UNIFIED
  shape except at an explicit legacy path);
  `serialize(cfg)->str` (canonical clean form — dirtiness + from-scratch fallback; never required
  to equal the on-disk bytes; scope-aware, and `cfg` is the WHOLE document so it never relocates
  agents/categories out of their scope); `render(cfg, base_text)->str` (**text-preserving write
  form**: `base_text` with only the managed `agents`/`categories` value spans rewritten clean —
  nested under `"[opencode]"` on a unified document — everything else, incl. comments /
  commented-out config outside them, byte-for-byte; falls back to
  `serialize(cfg)` when `base_text` is empty or a key is missing from the managed node);
  `diff_text(cfg, path)->str` and `save(cfg, path)->SaveResult` both go through `render`;
  `@dataclass SaveResult(changed, backup, original_created)`; `@dataclass BackupInfo(name, path,
  is_original, size)`; `list_backups(path)->list` (the pinned `original.jsonc` + the newest 10 of
  the `[0-9]*.jsonc` ring — `original-legacy.jsonc` matches neither and is never offered);
  `restore(path, backup_name)->None`, which **raises `BackupScopeMismatch(ValueError)`** when the
  snapshot's `scope_of` differs from the live config's (checked BEFORE the safety snapshot, so a
  refusal writes nothing; cli.py maps it to exit 3 — there is deliberately no `--force`);
  `adopt_original_backup(src_config_path, dst_config_path)->bool` (copies the pre-omodel pin to
  `<dst>/.backup/original-legacy.jsonc`, leaving the source in place and `original.jsonc` free for
  the first save's pin of the unified config).
- `app.py`: `class OModelApp(App)` (Textual) + `create_app(config_path=None)->OModelApp` (the
  testable construction half — builds catalog/suggestions/resolver/cfg; the resolver is built even
  in CatalogUnavailable degraded mode, over the empty catalog) + `run_app(config_path=None)->None`
  (== `create_app(...).run()`). Stable widget
  IDs as documented in `app.py`'s docstring. Every cfg mutation routes through `_record`/`_stage_row`
  (which push onto `History`); `u` undo / `ctrl+r` redo; dirtiness is `_is_dirty()` (serialize vs
  `_saved_text`), not a flag.
- `history.py`: `@dataclass HistoryEntry(state, label, aux=None)`; `class History(initial,
  label="loaded", limit=200, aux=None)` with `.push(state, label, aux=None)->bool` (no-op when
  `state` unchanged; `aux` rides along), `.undo()`/`.redo()->(state, label)|None`,
  `.current_state()->dict`, `.current_aux()->dict` (the cursor entry's `aux`, `{}` if none),
  `.clear_aux(keep=())->None` (drop all entries' `aux`, preserving the named keys for dict-shaped
  aux — app.py keeps `active` across a catalog refresh), `.drop_redo()->None` (discard the redo
  tail without pushing, for an action that changes state without changing cfg — switching to a
  preset holding identical models),
  `.matches_current(state)->bool`, `.map_aux_key(key, fn)->None` (rewrite a key PER ENTRY, for
  companion state that is deliberately not undoable — app.py remaps the active-preset index when
  a delete renumbers the list, and when an add or a no-op switch moves the preset some entries
  point at. Per-entry, never a blanket stamp: entries legitimately hold different values, and a
  stamp erased both older switches and the delete sentinels), and the
  `can_undo`/`can_redo`/`undo_label`/`redo_label` properties. `aux` is an out-of-cfg companion
  snapshot (app.py stores `_custom_rows` + the active preset index). Pure data; snapshots
  deep-copied in and out. Consumed only by `app.py`.
- `presets.py`: `@dataclass Preset(name, saved_at, agents, categories)`; `@dataclass
  Store(presets, active)` with `.current()->Preset|None` and `.is_empty()->bool`;
  `load(config_path)->Store` (a DENSE, unbounded list; missing/corrupt/wrong-version → empty
  store, never raises; `active` normalized into range; MIGRATES the original fixed-3 shape —
  `null` holes dropped, `active` following the preset it named);
  `write(config_path, store)->Store` (**the ONLY disk write in this module** — atomic, RAISES on
  failure so app.py notifies, returns the store as read back; an existing file that does not parse
  is moved to `<path>.corrupt` first, since `load` degrades it to an empty store the app would
  otherwise clobber); `adopt(src_config_path, dst_config_path)->int|None` (one-time hand-over of
  a pre-4.19.3 store to the unified location — writes, reads back, compares names, and only THEN
  deletes the original; any failure leaves it in place. `Session` calls it for the DEFAULT
  unified path only). Pure helpers: `capture(name, managed)` /
  `assignments(preset)` (deep-copy IN / OUT — the live cfg and a stored Preset never alias),
  `seeded(managed, name=DEFAULT_NAME)`, `matching_index(store, managed)`, `normalize_active(store)`,
  `fingerprint(agents, categories)` (does the config still reflect a preset? — comparison ONLY,
  never an input to what gets written, and deliberately **spelling-insensitive**: `variant` /
  `reasoning` / `reasoningEffort` fold to one key at both depths via `_canon`, because omodel
  respells a preset in memory while the config keeps what is on disk),
  `store_fingerprint(store)` (has `s` anything to persist? — excludes `saved_at`),
  `model_count(preset)`, `sanitize_name(text, index)` (also strips `[`/`]` — Textual parses plain
  strings as markup, and a persisted name crashed every launch), `timestamp()`,
  `presets_path(config_path)`;
  constants `FILE_VERSION`, `MAX_NAME = 24`, `DEFAULT_NAME`. The `managed` argument is the node
  that HOLDS `agents`/`categories` (`Session.managed`), not necessarily the whole config — that
  is what keeps this module a leaf with no knowledge of the `"[opencode]"` scope. Stored at
  `<config_dir>/.omodel-presets.json` — next to the ACTIVE config, so a temp `--config` gets its own
  set, and so the store follows the config to `~/.omo/`. Non-dict `agents`/`categories` coerce to `{}` on read and write. Pure data + file IO, no
  Textual; consumed only by `app.py`. **Invariant app.py upholds:** the config on disk equals the
  ACTIVE preset. In the TUI only `s` writes, and it writes both files. `cli.py`'s mutating verbs
  (decision #18) write both together too — config first — so the invariant holds on both surfaces;
  nothing else anywhere writes either file.
- `session.py`: the headless core BOTH `app.py` and `cli.py` edit through (decision #18).
  Module-level: `SUBKINDS`, `GPT_ONLY_AGENTS`, `ULTRAWORK_AGENTS`; `is_gpt_model(id)->bool`;
  `subkinds_for(name)->tuple`; `is_no_variant(v)->bool`; `coerce_dict(parent, key)->dict`;
  `gpt_only(target)->bool`; `target_label(target)->str`; `split_target(target)->tuple|None`
  (shape only — existence is `Session.is_known`); `managed_root(cfg)->dict` (re-export of
  `config_io.managed_root`, so app.py/cli.py reach the scope through one place);
  `REASONING_KEYS = ("reasoning", "variant", "reasoningEffort")`;
  `read_variant(node)->str|None` (omo's read precedence);
  `variant_key_for(cfg, subkind)->str` (which spelling to WRITE — `reasoning` for
  agents/categories on a unified document, `variant` for legacy AND for
  `ultrawork`/`compaction` in both scopes). `@dataclass Session(catalog, suggestions,
  resolver, cfg, config_path, catalog_error=None)` with `__post_init__`-filled `store`,
  `sync_conflict`, `saved_text`, `saved_store_fp`, `adopted_presets`;
  classmethod `build(config_path=None)`;
  `.managed` (property, the agents/categories-holding node) and `.scope` (property,
  `"opencode"`/`"root"`, surfaced as `config_scope` by `show --json`);
  `.degraded` (property, `not catalog.connected`); `.known_targets()->list`;
  `.is_known(target)->bool`; `.node_for()`/`.ensure_node()`/`.assignment()`;
  `.rows(target, custom_rows=())->list` (candidate-row dicts);
  `.variants_for(p, m, stale_ok=True)->list`;
  `.set_model(target, provider, model, variant=None)`/`.set_row(target, row)`;
  `.clear(target)->bool`; `.delete_subtarget(name, kind)`; `.projected_store()->Store`;
  `.preset_index(ref)->int|None`; `.switch_preset(index)->Preset`; `.is_dirty()`/
  `.store_is_dirty()`; `.diff()->str`; `.save_config()->SaveResult`; `.write_store(store=None)
  ->Store` (RAISES); `.save()->SaveResult` (both files, config first — `app.py` calls the two
  halves instead, since its save is interactive). **MUST NOT import textual or app.**
- `cli.py`: `main(argv=None)->int` (console-script entrypoint). Constants `SCHEMA = 1`,
  `EXIT_OK/ERROR/USAGE/REJECTED = 0/1/2/3` — and those FOUR are the whole range: `main` flushes
  stdout itself and maps a closed one (`… | head`) to `EXIT_OK`, so the interpreter's shutdown
  flush can't add a fifth code (120). See DESIGN §Exit codes. The main parser owns `--yes`,
  `--force` and `--json` for `--update`; because `set`/`apply` (`--force`) and every subcommand
  (`--json`) declare their own, **those subparser copies MUST keep `default=SUPPRESS`** — without
  it the subparser's False silently overwrites a flag the caller passed before the verb, and
  `omodel --force set …` would refuse the write for the reason it was told to override. The other
  half of that trade is `_flag_misuse`: argparse accepts every top-level flag on every run, so a
  combination those three flags make meaningless (`--update` with a command, `--json` with a flat
  flag) **must exit 2 rather than be ignored** — it used to, before they were global.
- `refresh.py`: `refresh(omo_src=None)->int` (the `--refresh-omo` flag — bundled omo suggestion
  data; distinct from `catalog.refresh()`, which is opencode availability via `--refresh-models`).
- `update.py`: `omodel --update` — the ONLY runtime network access in the codebase, and only when
  that verb runs (no launch-time version check). `class UpdateError(Exception)` with `.kind` (the
  `--json` `error`; `cli._UPDATE_REFUSALS` maps three of them to exit 3, everything else to 1);
  `parse_version(text)->tuple` / `is_newer(candidate, current)->bool` (an unparseable tag is never
  newer); `platform_asset()->str|None` (`omodel-linux-x64` · `omodel-darwin-arm64` · None —
  **must track `release.yml`'s matrix and `install.sh`'s detection**); `@dataclass Install(kind,
  path=None)` with `.self_updatable` and `.command_for(tag)` (kind ∈ `binary`/`pipx`/`uv`/`pip`/
  `source`; commands are tag-pinned); `detect_install()->Install` (never raises — unknown layout
  falls back to `pip`); `@dataclass Release(tag, version, url, published_at, assets)`;
  `latest_release(timeout=META_TIMEOUT)->Release`; `preflight(install, release=None)->str` (every
  reason an update is impossible, raised BEFORE `cli.py` prompts; returns the tarball asset name);
  `@dataclass UpdateResult(path, version, verified)`; `apply_update(release, install,
  timeout=DOWNLOAD_TIMEOUT, on_step=None)->UpdateResult` (calls `preflight` itself; **on ANY
  failure the installed binary is byte-for-byte untouched**; `on_step` is prose-only progress —
  `--json` passes None so stdout stays one object). `_open(url, timeout)` is the single network
  seam — **https-only**, since `build_opener` keeps urllib's File/FTP/Data handlers and asset URLs
  come from the release JSON; tests monkeypatch it (three patch `_opener` one level lower, to
  exercise `_open` itself). Reads catch `(OSError, http.client.HTTPException)`: `IncompleteRead`
  is **not** an OSError, and letting it escape produced a traceback with empty `--json` stdout.
  `detect_install` requires `sys.frozen` **and** `sys._MEIPASS` for the `binary` verdict — the
  verdict authorizes an `os.replace` over `sys.executable`. The **confirm** lives in
  `cli._confirm_update`, not here: `--yes` is the only yes, and `--json` / no-TTY are a no (which
  is why there is no `--update-check`). **MUST NOT import textual, app, session or config_io** —
  `cli.py` imports it lazily, inside the flag's branch.

## Cross-module dependencies
- `resolve.py` → `suggestions.py` + `catalog.py`.  `refresh.py` → `tools/snapshot_omo.ts`.
- `update.py` is a leaf: stdlib + `omodel.__version__`, nothing else. It has no config, no
  catalog and no session — it updates the program, not the models — and `cli.py` reaches it only
  from inside the `update` verb.
- `app.py` → all four modules + `history.py` + `presets.py` (Lead wires final).  `config_io.py` +
  CLI+packaging are near-independent.  `history.py` and `presets.py` are pure leaves (no omodel
  imports) — which is why `presets.fingerprint` re-implements `config_io._clean_agents`' empty-sub-
  object rule rather than importing it (drift there can only mis-draw the `●`; see DESIGN §presets.py).

## Bundled data (generated — do not hand-edit; regenerate with `--refresh-omo`)
- `data/omo-suggestions.json` — 11 agents, 8 categories. Consume via `suggestions.load()`.
  **The omo version/commit is not pinned here** — a weekly CI job refreshes this file from omo's
  newest stable tag, so any number written down goes stale within days; read `meta.omoVersion` /
  `meta.omoCommit` out of the file itself. Only those TWO counts are pinned (asserted by
  `test_detect_family.py::TestBundledSuggestionsLoad`), because they back target coverage and a
  change in *those* is a real event. Two things are deliberately NOT counted:
  - **families** — the *set* is what matters, and `TestFamilyVendorSync` pins it against
    `FAMILY_VENDOR` while naming the drifted key. A `== 15` alongside it only added a second red
    with no instruction on every upstream family add. The one thing a count uniquely caught — a
    duplicate name, which that set comparison cannot see and which would silently reorder
    `detect_family`'s first-match-wins precedence — is asserted directly by
    `test_families_are_unique_and_nonempty`.
  - **knownVariants** — no consumer in `src/` (variant validity is opencode's, decision #14), so
    its size is pure upstream churn: omo 4.19.4 renamed `none` → `off` and dropped `thinking`,
    reddening a `== 9` pin on a rename that changed no behaviour. Checked structurally instead
    (`test_known_variants_cover_what_chains_use`).
- `data/default-config.jsonc` — oModel's own minimal starter.

## Appendix — module ownership (from the original fan-out)

oModel was built contract-first by six parallel specialists in isolated worktrees. The table is
kept because it is still an accurate map of the module boundaries — which files move together, and
which one change can break both surfaces. The *process* rules that came with it (don't run git,
touch only your lane, leave `# CONTRACT-QUESTION:` for the Lead) belonged to that fan-out and no
longer bind. One thing they carried does: **tests and imports run from the project venv**
(`pip install -e ".[dev]"`, per AGENTS.md §Commands) — there is no system-wide install to fall back
on. `__init__.py`, `__main__.py` and `data/*` sat outside the table, under the Lead.

| Track | Files |
|---|---|
| **Core logic** | `src/omodel/catalog.py`, `src/omodel/cache.py`, `src/omodel/suggestions.py`, `src/omodel/resolve.py`, `src/omodel/tools/snapshot_omo.ts` |
| **Config I/O** | `src/omodel/config_io.py` |
| **TUI** | `src/omodel/app.py`, `src/omodel/history.py`, `src/omodel/presets.py` |
| **CLI + packaging** | `src/omodel/cli.py`, `src/omodel/refresh.py`, `src/omodel/data/agent-usage.md`, `pyproject.toml`, `install.sh`, `.github/workflows/*`, `README.md`, `LICENSE`, `NOTICE`, `CHANGELOG.md` |
| **Session (shared core)** | `src/omodel/session.py` — the one both TUI and CLI depend on; a change here can break either. Still the file to touch most carefully. |
| **QA / verification** | everything under `tests/` (incl. `conftest.py`) |
