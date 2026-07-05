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

- **target** — one editable slot. Four id shapes: `agent:<name>`, `agent:<name>.ultrawork`,
  `agent:<name>.compaction`, `cat:<name>` (== the `#targets` option IDs). → CONTRACTS.md "Shared shapes"
- **agent / category** — a named omo agent (sisyphus, hephaestus…; 11) / task category (deep,
  quick…; 8). → DESIGN §Problem
- **sub-target** — `ultrawork` (model swapped in on an `ulw` message; **Sisyphus-only** — omo honors
  it on no other agent) or `compaction` (auto-summary model, any agent), nested under an agent.
  *Agents only; categories have none.* → DESIGN §Textual contract, app.py `_ULTRAWORK_AGENTS`

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
  in a chain id (`mini`, `fast`, `flash`, `nano`, …) — so `gpt-5.4-mini-fast` ≠ `gpt-5.4-mini`. NOT
  the same as a *variant* (reasoning mode). → resolve.py `_matches_omo_id` / `real_tokens`

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
  clear row, so the pickers never offer it and saving drops the `variant` key (`_is_no_variant`).
  → DESIGN decision #14
- **warn-but-allow (⚠)** — oModel flags but never blocks you (bad variant, unavailable add). One hard
  exception: **Hephaestus is GPT-only** (non-GPT blocked). → DESIGN decision #5, app.py `_GPT_ONLY_AGENTS`

## Save / history / cache

- **edit-in-place / text-preserving save** — the write splices only the top-level `agents`/`categories`
  spans clean (*no comments inside them*) and keeps **everything else byte-for-byte** — other keys,
  formatting, and any comments / commented-out config *outside* those two. → config_io.py `render`
- **active-only / clean config** — the *canonical clean form* (`serialize`): JSON, *no comments*. Used
  for dirtiness + as the from-scratch/fallback writer; the first save drops omo's commented **palette**
  *inside* agents/categories (preserved verbatim in `original.jsonc`). → config_io.py `serialize`
- **backup vs history** — *backup* = verbatim `.backup/<ts>.jsonc` copy at each save (on disk,
  cross-session, `--restore`); *history* = the **in-session** undo/redo stack (`u` / `ctrl+r`).
  → config_io.py / history.py
- **the two refreshes** — `--refresh-omo` rebuilds *"what omo suggests"* (bun + omo checkout);
  `--refresh-models` / `r` rebuilds *"what you have"* (re-runs opencode, busts the cache).
  → refresh.py vs catalog.refresh()
- **cache** — the 24h on-disk cache of `opencode` CLI output (`~/.cache/omodel`). A perf layer, *not*
  an availability source. → cache.py

## Docs

- **DESIGN.md** = the spec / design-of-record · **CONTRACTS.md** = frozen shapes + signatures ·
  **this file** = the vocabulary index that points at both.
