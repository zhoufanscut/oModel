# oModel — FROZEN CONTRACTS (read with DESIGN.md before coding)

This is the contract-first boundary. **Read `DESIGN.md` in full first** — it is the spec.
This file pins the shared shapes + ownership so the five tracks build in parallel and
integrate cleanly. Do not change a public signature or shared shape without the Lead
updating this file (others depend on it).

## File ownership (DISJOINT — touch only your lane)

| Track | Owns (edit only these) |
|---|---|
| **Core logic** | `src/omodel/catalog.py`, `src/omodel/cache.py`, `src/omodel/suggestions.py`, `src/omodel/resolve.py`, `src/omodel/tools/snapshot_omo.ts` |
| **Config I/O** | `src/omodel/config_io.py` |
| **TUI** | `src/omodel/app.py`, `src/omodel/history.py`, `src/omodel/presets.py` |
| **CLI + packaging** | `src/omodel/cli.py`, `src/omodel/refresh.py`, `src/omodel/data/agent-usage.md`, `pyproject.toml`, `install.sh`, `.github/workflows/*`, `README.md`, `LICENSE`, `NOTICE`, `CHANGELOG.md` |
| **Session (shared core)** | `src/omodel/session.py` — **Lead-owned**, because both TUI and CLI depend on it; a change here can break either. Propose, don't edit unilaterally. |
| **QA / verification** | everything under `tests/` (incl. `conftest.py`) |

Lead owns: `__init__.py`, `__main__.py`, `data/*`, this file, and ALL git operations + final wiring.

## Rules for every track

1. **Do NOT run any git command** (no add/commit/branch/checkout). The Lead owns git and integration.
2. **Touch only your owned files.** Read others freely; never edit them. If you believe a
   frozen signature is wrong, leave a `# CONTRACT-QUESTION:` comment in YOUR file and proceed
   against the current signature — the Lead reconciles at integration.
3. **Python floor is 3.9.** Put `from __future__ import annotations` at the top of every
   module (already present in stubs). No runtime PEP-604 unions (`isinstance(x, A | B)`) and no
   runtime PEP-585 generics; annotations-as-strings make `dict | None` in signatures fine.
4. **REAL-CONFIG SAFETY (hard rule).** The live `~/.config/opencode/oh-my-openagent.jsonc`
   is the user's real file. Never read-then-write it in tests or examples. Every test passes an
   explicit temp `path`/`--config`. The Lead's gate enforces this. **Nothing may write anywhere
   under the real config dir** — that now covers `.backup/` *and* `.omodel-presets.json`
   (§presets.py), not just the config file itself.
5. **Tests/imports run in a venv** with `textual json5 pytest` installed (PyPI reachable). Do
   not assume system-wide installs.
6. **NEVER RENDER DATA AS A PLAIN `str` (hard rule, `app.py`).** Textual parses content markup in
   any plain string it renders, so a `[` in a model id, provider, variant, agent/category name,
   preset name or `str(exc)` is a tag — and an unmatched close raises `MarkupError` inside the
   render pass, which kills the app. Build data-carrying `Static`/`Label` with `markup=False`, wrap
   `Option` prompts in `_lit()`, `_esc()` anything spliced into `#detail` (the one markup widget),
   and leave `OModelApp.notify`'s `markup=False` default alone. → DESIGN §Textual contract
7. **REAL-CACHE SAFETY (hard rule).** The opencode-output cache lives at `~/.cache/omodel/`
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
  `unknown_preset`, `active_preset`, `bad_input`, `write_failed`.
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

## Public signatures (authoritative = the stub modules)

The stub files ARE the signatures; implement their bodies. Summary:

- `catalog.py`: `class CatalogUnavailable(Exception)`; `@dataclass Catalog(available: dict,
  connected: list)` with `.providers_for(model_id)->list`, `.detail(model_id, use_cache=True,
  provider=None)->dict|None` (`provider`, when it serves the model, selects WHOSE record — the
  detail pane passes the assignment's provider; else first-of-providers_for as before),
  `.variants_for(provider, model)->list` (cached `--verbose` variant keys for the model
  pickers — first non-empty across the picked provider then others, else `[]`; never a subprocess);
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
- `config_io.py`: `config_path(cli_override=None)->str`; `load_config(path=None)->(cfg, path)`
  (raises `ConfigParseError(ValueError)` — message carries the path — on malformed JSONC; cli.py
  catches it for a friendly exit-1 message on the TUI/`--print` paths);
  `serialize(cfg)->str` (canonical clean form — dirtiness + from-scratch fallback; never required
  to equal the on-disk bytes); `render(cfg, base_text)->str` (**text-preserving write form**:
  `base_text` with only the top-level `agents`/`categories` value spans rewritten clean, everything
  else — incl. comments / commented-out config outside them — byte-for-byte; falls back to
  `serialize(cfg)` when `base_text` is empty or a key isn't a direct root member);
  `diff_text(cfg, path)->str` and `save(cfg, path)->SaveResult` both go through `render`;
  `@dataclass SaveResult(changed, backup, original_created)`; `@dataclass BackupInfo(name, path,
  is_original, size)`; `list_backups(path)->list`; `restore(path, backup_name)->None`.
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
  otherwise clobber). Pure helpers: `capture(name, cfg)` /
  `assignments(preset)` (deep-copy IN / OUT — the live cfg and a stored Preset never alias),
  `seeded(cfg, name=DEFAULT_NAME)`, `matching_index(store, cfg)`, `normalize_active(store)`,
  `fingerprint(agents, categories)` (does the config still reflect a preset?),
  `store_fingerprint(store)` (has `s` anything to persist? — excludes `saved_at`),
  `model_count(preset)`, `sanitize_name(text, index)` (also strips `[`/`]` — Textual parses plain
  strings as markup, and a persisted name crashed every launch), `timestamp()`,
  `presets_path(config_path)`;
  constants `FILE_VERSION`, `MAX_NAME = 24`, `DEFAULT_NAME`. Stored at
  `<config_dir>/.omodel-presets.json` — next to the ACTIVE config, so a temp `--config` gets its own
  set. Non-dict `agents`/`categories` coerce to `{}` on read and write. Pure data + file IO, no
  Textual; consumed only by `app.py`. **Invariant app.py upholds:** the config on disk equals the
  ACTIVE preset. In the TUI only `s` writes, and it writes both files. `cli.py`'s mutating verbs
  (decision #18) write both together too — config first — so the invariant holds on both surfaces;
  nothing else anywhere writes either file.
- `session.py`: the headless core BOTH `app.py` and `cli.py` edit through (decision #18).
  Module-level: `SUBKINDS`, `GPT_ONLY_AGENTS`, `ULTRAWORK_AGENTS`; `is_gpt_model(id)->bool`;
  `subkinds_for(name)->tuple`; `is_no_variant(v)->bool`; `coerce_dict(parent, key)->dict`;
  `gpt_only(target)->bool`; `target_label(target)->str`; `split_target(target)->tuple|None`
  (shape only — existence is `Session.is_known`). `@dataclass Session(catalog, suggestions,
  resolver, cfg, config_path, catalog_error=None)` with `__post_init__`-filled `store`,
  `sync_conflict`, `saved_text`, `saved_store_fp`; classmethod `build(config_path=None)`;
  `.degraded` (property, `not catalog.connected`); `.known_targets()->list`;
  `.is_known(target)->bool`; `.node_for()`/`.ensure_node()`/`.assignment()`;
  `.rows(target, custom_rows=())->list` (candidate-row dicts); `.variants_for(p, m)->list`;
  `.set_model(target, provider, model, variant=None)`/`.set_row(target, row)`;
  `.clear(target)->bool`; `.delete_subtarget(name, kind)`; `.projected_store()->Store`;
  `.preset_index(ref)->int|None`; `.switch_preset(index)->Preset`; `.is_dirty()`/
  `.store_is_dirty()`; `.diff()->str`; `.save_config()->SaveResult`; `.write_store(store=None)
  ->Store` (RAISES); `.save()->SaveResult` (both files, config first — `app.py` calls the two
  halves instead, since its save is interactive). **MUST NOT import textual or app.**
- `cli.py`: `main(argv=None)->int` (console-script entrypoint). Constants `SCHEMA = 1`,
  `EXIT_OK/ERROR/USAGE/REJECTED = 0/1/2/3`.
- `refresh.py`: `refresh(omo_src=None)->int` (the `--refresh-omo` flag — bundled omo suggestion
  data; distinct from `catalog.refresh()`, which is opencode availability via `--refresh-models`).

## Cross-module dependencies
- `resolve.py` → `suggestions.py` + `catalog.py`.  `refresh.py` → `tools/snapshot_omo.ts`.
- `app.py` → all four modules + `history.py` + `presets.py` (Lead wires final).  `config_io.py` +
  CLI+packaging are near-independent.  `history.py` and `presets.py` are pure leaves (no omodel
  imports) — which is why `presets.fingerprint` re-implements `config_io._clean_agents`' empty-sub-
  object rule rather than importing it (drift there can only mis-draw the `●`; see DESIGN §presets.py).

## Bundled data (already generated by Lead — do not regenerate)
- `data/omo-suggestions.json` — omo v4.13.0 @ f31c735: 11 agents, 8 categories, 15
  families, 9 knownVariants. Consume via `suggestions.load()`.
- `data/default-config.jsonc` — oModel's own minimal starter.
