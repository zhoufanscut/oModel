# oModel — GLOSSARY (shared vocabulary)

A **disambiguation index, not a spec.** One line per term — what it means, what it's *not* — and a
`→` pointer to the canonical definition. When the word and the definition disagree, **the pointer
wins.** Deliberately lean: only the terms that actually cause miscommunication live here — add a
line when a new one does.

---

## The three external things (don't conflate)

- **omo / OMO (oh-my-openagent)** — the agent framework whose config oModel edits; the source of
  *"what omo suggests."* Bundled as a snapshot — never run or imported at runtime. → AGENTS.md "What this is"
- **oModel / `omodel`** — *this* tool. Brand "oModel", command + Python package `omodel`.
- **opencode** — ⚠ overloaded: usually the **CLI** that reports *"what you have"*, but also the name
  of a **provider** (a gateway). Say "the `opencode` CLI" vs "the `opencode` provider". → catalog.py

## Targets — *what you edit*

- **target** — one editable slot (**"slot" means a target and nothing else** — a *preset* is
  addressed by index, see below). Four id shapes: `agent:<name>`, `agent:<name>.ultrawork`,
  `agent:<name>.compaction`, `cat:<name>` (== the `#targets` option IDs). → CONTRACTS.md "Shared shapes"
- **agent / category** — a named omo agent (sisyphus, hephaestus…; 11) / task category (deep,
  quick…; 8). → DESIGN §Problem
- **sub-target** — `ultrawork` (model swapped in on an `ulw` message; **Sisyphus-only** — omo honors
  it on no other agent) or `compaction` (auto-summary model, any agent), nested under an agent.
  *Agents only; categories have none.* → DESIGN §Textual contract, session.py `ULTRAWORK_AGENTS`

## Recommendation → row

- **fallbackChain** — omo's *ordered* (priority) list of recommended models for a target. → suggestions.py
- **candidate / candidate-row** — the dict `resolve.candidates()` yields and `app.py` renders, **one
  row per serving provider.** *The integration seam.* → CONTRACTS.md (frozen), resolve.py
- **exact vs same-line substitute** — *exact* = a connected provider serves the model, allowing a
  trailing date stamp / sub-version tag (see *noise suffix*); *substitute* = no exact, so the
  **newest** connected model of the same family (`glm-5` → `glm-5.1`) — but never across a Claude
  *line* (haiku ≠ sonnet ≠ fable ≠ mythos). → resolve.py `candidates`
- **noise suffix vs real modifier** — a trailing id token an available id carries that the bare omo
  id lacks. *Noise* (stripped when matching) = a date/build stamp (`…-20251001`) or an unknown
  sub-version tag (`…-jibao`). *Real modifier* (kept; a distinct model) = a token omo itself uses
  in a chain id, plus a fixed floor of size/tier words that never become noise (`mini`, `fast`,
  `flash`, `nano`, …) — so `gpt-5.4-mini-fast` ≠ `gpt-5.4-mini`. NOT the same as a *variant*
  (reasoning mode). → resolve.py `_matches_omo_id` / `real_tokens` / `_TIER_TOKENS`

## provider vs vendor vs family (the most-confused trio)

- **provider** — the `provider/` prefix that *serves* a model (`openai`, `zhipuai`, `opencode`…); an
  availability/routing ID. The set you're connected to is **connected** (first-seen order, never a
  set). → catalog.py
- **vendor** — the *company* behind a family (via `FAMILY_VENDOR`). Used **only** to classify
  providers. → suggestions.py `FAMILY_VENDOR`
- **family** — a model line (15: gpt-5, claude-opus, glm, kimi…), via `detect_family` (a port of
  omo's heuristic). → suggestions.py `detect_family`
- **gateway vs dedicated** — *gateway* = serves **≥2 vendors** (aggregator, e.g. `opencode`);
  *dedicated* = single-vendor (e.g. `openai`). Dedicated sorts **first** in the pick list.
  → resolve.py `vendors_served` / `_ordered_providers`

## Flags & rules

- **variant** — a model's reasoning-effort/mode (`max`, `high`, `thinking`…). Offerings come from
  cached `opencode --verbose` (`Catalog.variants_for`) — the source of truth for the pickers; the
  bundled family registry is now only the fallback for the omo-suggestion `⚠` warn when opencode
  reports nothing for that model. Family *detection* itself stays heuristic-only — `--verbose.family`
  is never read. A `none` variant is treated as **no variant**: identical to the synthetic `(none)`
  clear row, so the pickers never offer it and saving drops the key (`_is_no_variant`).
  **`variant` is oModel's internal word for this** (the candidate-row field, the `v` picker); what
  gets WRITTEN to config is a different question — see *reasoning*.
  → DESIGN decision #14
- **reasoning** — omo's current *config key* for what oModel calls a **variant**.
  `2026-08-reasoning-unification` renamed `variant` → `reasoning` on agents and categories but
  deliberately left `ultrawork`/`compaction` alone, whose override reads `.variant` and nothing
  else. So oModel writes `reasoning` for agents/categories on a unified document, `variant` on a
  legacy one, and `variant` inside those two sub-objects in **both** scopes
  (`session.variant_key_for`). Reads accept all three spellings in omo's order —
  `reasoning` → `variant` → `reasoningEffort` (`session.read_variant`).
  → DESIGN §Config scope
- **config scope** — which node of the document holds the `agents`/`categories` oModel manages:
  `"opencode"` (nested under `"[opencode]"`, omo 4.19.3+) or `"root"` (the legacy top level).
  Detected from CONTENT, never the filename (`config_io.scope_of`), so `--config <anywhere>`
  still edits the right place. → DESIGN §Config scope
- **the `[opencode]` block** — the harness block in `~/.omo/omo.jsonc` carrying the whole OpenCode
  plugin config. omo folds base → `[opencode]`, **last wins**, so a top-level `agents` is legal but
  outranked — which is why oModel always writes the block on a unified document.
  → DESIGN §Config scope
- **warn-but-allow (⚠)** — oModel flags but never blocks you (bad variant, unavailable add). One hard
  exception: **Hephaestus is GPT-only** (non-GPT blocked). → DESIGN decision #5, session.py `GPT_ONLY_AGENTS`

## Save / history / cache

- **edit-in-place / text-preserving save** — the write splices only the top-level `agents`/`categories`
  spans clean (*no comments inside them*) and keeps **everything else byte-for-byte** — other keys,
  formatting, and any comments / commented-out config *outside* those two. → config_io.py `render`
- **active-only / clean config** — the *canonical clean form* (`serialize`): JSON, *no comments*. Used
  for dirtiness + as the from-scratch/fallback writer; the first save drops omo's commented **palette**
  *inside* agents/categories (preserved verbatim in `original.jsonc`). → config_io.py `serialize`
- **backup vs history vs preset** — the three save-ish things, don't conflate. *backup* = verbatim
  `.backup/<ts>.jsonc` copy taken automatically at each save (on disk, cross-session, `--restore`);
  *history* = the **in-session** undo/redo stack (`u` / `ctrl+r`); *preset* = one of any number of **named sets
  of assignments you switch between**, one of which is always active and mirrored by the config.
  → config_io.py / history.py / presets.py
- **`original.jsonc` vs `original-legacy.jsonc`** — two pins that mean different things. The first
  is the config as it was before oModel's first save *at the current location*, pinned
  automatically, never pruned, and offered by `--restore`. The second is the pre-4.19.3 config
  carried over when the config moved to `~/.omo/` (`adopt_original_backup`) — same-format-only
  restore means it can never be restored onto a unified document, so it is deliberately **not**
  listed, and its separate name is what leaves `original.jsonc` free for a pin that *is*
  restorable. → DESIGN §Backups across the move
- **preset / active preset** — one of **any number** of named sets of assignments in the `#presets`
  card (seeded with one `default`; `a` adds more); the **active** one (`●`) is what your
  edits go into and what `s` publishes to the config. `enter` switches (a staged, undoable replace),
  `a` adds one holding the current models (row-blind — it never overwrites; the trailing
  `+ add preset…` row does the same on `enter`), `r` renames, `x` deletes but never the active
  one. Addressed by **index**
  (0-based, shown 1-based) — ⚠ **never a "slot"**: that word means a *target*, and unlike a slot a
  preset index is **not stable**: the list is dense, so a delete renumbers everything after it.
  Stored beside the active config as `.omodel-presets.json`. → DESIGN decision #17, presets.py
- **the presets invariant** — *the config on disk always equals the active preset*, never a fourth
  orphan state. Source of the **one write rule**: only `s` writes, and it writes **both** files
  (so quitting discards both, and `x` refuses on the active preset). → DESIGN §presets.py
- **the two refreshes** — `--refresh-omo` rebuilds *"what omo suggests"* (bun + omo checkout);
  `--refresh-models` / `r` rebuilds *"what you have"* (re-runs opencode, busts the cache).
  → refresh.py vs catalog.refresh()
- **`--update`** — ⚠ neither of those: it updates **omodel itself** from its GitHub releases (the
  program, not the data, not the models), and asks before it does. A *flag*, not a subcommand —
  the subcommands are the agent surface. The two refreshes never leave your machine; this is the
  one thing that does. → DESIGN §update.py, decision #19
- **install kind** — how *this* omodel got here, and therefore who may update it: `binary` (the
  PyInstaller one-file from `install.sh` — the only self-updatable one) · `pipx` · `uv` · `pip` ·
  `source`. Everything but `binary` gets a printed command, never a reach into someone else's
  venv. → update.py `detect_install`
- **cache** — the 24h on-disk cache of `opencode` CLI output (`~/.cache/omodel`). A perf layer, *not*
  an availability source. → cache.py
- **stale-while-revalidate** — `variants_for` alone reads the cache at *any* age (`_STALE_OK`),
  because for variant sets a day-old answer beats the heuristic fallback; `detail()`'s TTL'd refetch
  and `r` are what revalidate it. Availability and cost keep the plain 24h TTL, and so does
  `stale_ok=False` — the opt-back-in for callers that *refuse* on the answer (the CLI's variant
  guard) rather than merely annotate with it. → DESIGN §cache.py
- **pending variant** — a `v` pick on a candidate row that is *not* the current assignment. Only
  Enter assigns, so it is not yet cfg; it lives in `app._pending_variants` (never in `_rows`, which
  is a cache) until Enter stages it, undo/redo moves, or `r` drops it. → DESIGN §Textual contract

## The two surfaces

- **session** — the *headless core* (`session.py`): the editable state (cfg + catalog + resolver
  + presets store) and every mutation, with no UI. Both surfaces below edit through it, which is
  what stops them answering "what may I set here?" two different ways. NOT an "opencode session"
  and not a shell session. → session.py
- **agent surface** — the JSON subcommands (`show`, `candidates`, `set`, …) an LLM agent calls.
  Contrast the **flat flags** (`--print`, `--check`, …), which are the *human surface* and are
  unchanged. → DESIGN §CLI
- **agent guide** — `data/agent-usage.md`, the contract written *for* an agent, printed by
  `omodel agent-guide`. Distinct from **AGENTS.md**, which is for agents editing oModel's own
  source. One is about USING omodel, the other about BUILDING it.
- **degraded** — `opencode` is absent/unreadable, so availability is **unknown**. Not the same as
  "no candidates": an empty pick list while degraded means *we can't tell*, never *nothing works*.

## Docs

- **DESIGN.md** = the spec / design-of-record · **CONTRACTS.md** = frozen shapes + signatures ·
  **data/agent-usage.md** = the contract for agents *using* omodel · **AGENTS.md** = guidance for
  agents *editing* omodel · **this file** = the vocabulary index that points at them all.
