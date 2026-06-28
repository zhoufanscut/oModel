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
| **TUI** | `src/omodel/app.py`, `src/omodel/history.py` |
| **CLI + packaging** | `src/omodel/cli.py`, `src/omodel/refresh.py`, `pyproject.toml`, `install.sh`, `.github/workflows/*`, `README.md`, `LICENSE`, `NOTICE`, `CHANGELOG.md` |
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
   explicit temp `path`/`--config`. The Lead's gate enforces this.
5. **Tests/imports run in a venv** with `textual json5 pytest` installed (PyPI reachable). Do
   not assume system-wide installs.
6. **REAL-CACHE SAFETY (hard rule).** The opencode-output cache lives at `~/.cache/omodel/`
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

## Public signatures (authoritative = the stub modules)

The stub files ARE the signatures; implement their bodies. Summary:

- `catalog.py`: `class CatalogUnavailable(Exception)`; `@dataclass Catalog(available: dict,
  connected: list)` with `.providers_for(model_id)->list`, `.detail(model_id, use_cache=True)->
  dict|None`, `.variants_for(provider, model)->list` (cached `--verbose` variant keys for the model
  pickers — first non-empty across the picked provider then others, else `[]`; never a subprocess);
  `load(opencode_bin="opencode", use_cache=True)->Catalog`;
  `refresh(opencode_bin="opencode")->Catalog` (force `opencode models --refresh` + rebuild cache).
  All three opencode calls read through the on-disk cache (`cache.py`) and carry a `timeout=`.
- `cache.py`: on-disk cache of opencode stdout (24h TTL, flat, under `~/.cache/omodel/`).
  `cache_dir()->str`; `read(key, ttl_seconds=None)->str|None`; `write(key, stdout, args=None)->None`;
  `age_seconds(key)->float|None`; `clear()->None`; `CACHE_VERSION`. Best-effort: missing/corrupt/
  expired → miss; write errors swallowed (a non-writable cache never breaks the caller).
- `suggestions.py`: `FAMILY_VENDOR` (frozen 14-map); `@dataclass Family`; `@dataclass
  Suggestions(meta, agents, categories, families, known_variants)` with `.detect_family(id)->
  Family|None`, `.vendor_for(id)->str|None`; `vendor(family)->str|None`;
  `normalize_model_id(s)->str`; `load(path=None)->Suggestions`.
- `resolve.py`: `@dataclass Resolver(catalog, suggestions, gateways, real_tokens)` (`gateways` +
  `real_tokens` are computed in `build()`) with classmethod
  `build(catalog, suggestions)`, `.vendors_served(p)->int`, `.resolve_prefix(model_id, source,
  entry=None)->str|None`, `.candidates(target)->list[dict]`.
- `config_io.py`: `config_path(cli_override=None)->str`; `load_config(path=None)->(cfg, path)`;
  `serialize(cfg)->str` (canonical clean form — dirtiness + from-scratch fallback; never required
  to equal the on-disk bytes); `render(cfg, base_text)->str` (**text-preserving write form**:
  `base_text` with only the top-level `agents`/`categories` value spans rewritten clean, everything
  else — incl. comments / commented-out config outside them — byte-for-byte; falls back to
  `serialize(cfg)` when `base_text` is empty or a key isn't a direct root member);
  `diff_text(cfg, path)->str` and `save(cfg, path)->SaveResult` both go through `render`;
  `@dataclass SaveResult(changed, backup, original_created)`; `@dataclass BackupInfo(name, path,
  is_original, size)`; `list_backups(path)->list`; `restore(path, backup_name)->None`.
- `app.py`: `class OModelApp(App)` (Textual) + `run_app(config_path=None)->None`. Stable widget
  IDs as documented in `app.py`'s docstring. Every cfg mutation routes through `_record`/`_stage_row`
  (which push onto `History`); `u` undo / `ctrl+r` redo; dirtiness is `_is_dirty()` (serialize vs
  `_saved_text`), not a flag.
- `history.py`: `@dataclass HistoryEntry(state, label, aux=None)`; `class History(initial,
  label="loaded", limit=200, aux=None)` with `.push(state, label, aux=None)->bool` (no-op when
  `state` unchanged; `aux` rides along), `.undo()`/`.redo()->(state, label)|None`,
  `.current_state()->dict`, `.current_aux()->dict` (the cursor entry's `aux`, `{}` if none),
  `.clear_aux()->None` (drop all entries' `aux`), `.matches_current(state)->bool`, and the
  `can_undo`/`can_redo`/`undo_label`/`redo_label` properties. `aux` is an out-of-cfg companion
  snapshot (app.py stores `_custom_rows`). Pure data; snapshots deep-copied in and out. Consumed
  only by `app.py`.
- `cli.py`: `main(argv=None)->int` (console-script entrypoint).
- `refresh.py`: `refresh(omo_src=None)->int` (the `--refresh-omo` flag — bundled omo suggestion
  data; distinct from `catalog.refresh()`, which is opencode availability via `--refresh-models`).

## Cross-module dependencies
- `resolve.py` → `suggestions.py` + `catalog.py`.  `refresh.py` → `tools/snapshot_omo.ts`.
- `app.py` → all four modules + `history.py` (Lead wires final).  `config_io.py` + CLI+packaging
  are near-independent.  `history.py` is a pure leaf (no omodel imports).

## Bundled data (already generated by Lead — do not regenerate)
- `data/omo-suggestions.json` — real omo v4.11.1 @ b949c34: 11 agents, 8 categories, 14
  families, 9 knownVariants. Consume via `suggestions.load()`.
- `data/default-config.jsonc` — oModel's own minimal starter.
