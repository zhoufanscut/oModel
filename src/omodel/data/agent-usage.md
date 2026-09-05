# Using omodel from an LLM agent

`omodel` sets which model each OMO (`oh-my-openagent`) agent and category runs, in omo's config —
`~/.omo/omo.jsonc` on omo 4.19.3+, falling back to the pre-4.19.3
`~/.config/opencode/oh-my-openagent.jsonc`. It applies the right `provider/` prefix, checks the
variant against what `opencode` reports, keeps a timestamped backup of every save, and keeps the
config in step with its preset.

This page is the contract for using it non-interactively. Print it any time with
`omodel agent-guide`.

## 1. Do not hand-edit the config. Use `omodel set`.

Editing that file directly skips every check omodel exists to apply:

- **which node the assignment belongs in.** On omo 4.19.3+ `agents`/`categories` live under
  `"[opencode]"`, and omo folds base → `[opencode]` with the block winning — so a top-level
  `agents` you write by hand is valid JSON, saves fine, and is then silently ignored.
- **which spelling the reasoning level takes.** It is `reasoning` on agents and categories, but
  still `variant` inside `ultrawork`/`compaction`; omo resolves every `reasoning` ahead of any
  `variant`, so the wrong one is accepted and dropped. `show --json` reports `config_scope` if
  you need to know which shape the config is in.
- the `provider/` prefix (a bare model id does not work, and which provider serves a model
  changes as you connect and disconnect them),
- whether any connected provider can actually serve the model you wrote,
- whether the variant is one that model supports,
- the GPT-only lock on `hephaestus` (omo reassigns the session otherwise),
- the timestamped backup that makes a mistake recoverable,
- the invariant that the config on disk equals the active preset — break it and the user gets a
  "something else wrote your config" prompt next time they open the TUI.

`omodel set` does all of that and writes both files together. Use it.

## 2. Target ids

Everything is addressed by a target id:

```
agent:<name>                 e.g. agent:sisyphus
agent:<name>.ultrawork       only on agents where omo honors it (sisyphus)
agent:<name>.compaction      valid on every agent
cat:<name>                   e.g. cat:quick
```

Do not guess names — enumerate them:

```sh
omodel targets --json
```

## 3. The loop

Ask what a target can run, then set it to one of the answers:

```sh
omodel candidates agent:sisyphus --json
omodel set agent:sisyphus opencode/claude-opus-5 --variant max
```

**Use a candidate's `value` verbatim.** It is already `provider/model`; do not assemble that
string yourself. Each candidate also carries `variants` — the variants `opencode` reports for
that exact `(provider, model)`, which is what `--variant` is checked against. An **empty**
`variants` means opencode has not reported a set for that pair — nothing is cached for it yet,
or the provider reports none — not that the model has no levels; `--variant` is then accepted
unchecked. That cache fills when the TUI shows a model's detail, so on a machine where only the
CLI has run it is usually empty.

⚠ **Every model id on this page is illustrative, and some are certainly out of date.** omo
revises its suggested models most weeks, and which providers serve them depends on what this
user has connected. Never copy an id from this page into a `set`; take it from `candidates`.

The candidate list is omo's fallback chain filtered to models you can actually run, one row per
serving provider, dedicated providers before gateways. A model omo suggests but nobody serves is
not listed at all.

Preview any change before making it:

```sh
omodel set agent:sisyphus opencode/claude-opus-5 --dry-run --json   # writes nothing; `diff` carries the change
```

## 4. Exit codes

```
0  success
1  omodel failed — unwritable path, malformed config. Stop and report.
2  usage error — bad arguments or bad stdin JSON.
3  refused — unknown target, unavailable model, bad variant, GPT-only violation.
   Your request was wrong. Read the message, pick a different candidate, retry.
```

The 1-vs-3 split is the important one. **3 means try something else; 1 means stop.** Do not retry
a 1 and do not give up on a 3.

Failures also come back as JSON when you pass `--json`:

```json
{ "schema": 1, "ok": false, "error": "unavailable",
  "message": "no connected provider serves 'ghost/nope' — run `omodel candidates agent:sisyphus` for what you can run, or pass --force.",
  "target": "agent:sisyphus", "value": "ghost/nope" }
```

`error` is a stable slug: `unknown_target`, `bad_value`, `unavailable`, `bad_variant`,
`gpt_only`, `unknown_preset`, `active_preset`, `bad_input`, `write_failed`, and `bad_config`
(the config file could not be read or parsed — an exit 1, so stop and report it).

## 5. Commands

Every command takes `--config PATH` (use a temp path for anything experimental) and `--json`.

| Command | What it does |
|---|---|
| `omodel agent-guide` | Print this page. |
| `omodel targets` | Every target id you can set. |
| `omodel show` | Current assignments, connected providers, presets. |
| `omodel candidates <target>` | What that target can be set to. |
| `omodel check` | Problems with the current config; **exit 3** if any. Reports anything `set` would refuse, plus a `agents`/`categories` key that isn't an object. |
| `omodel set <target> <provider/model>` | Set one model. `--variant`, `--dry-run`, `--force`. |
| `omodel clear <target>` | Remove a target's model. |
| `omodel apply` | Set many at once from stdin JSON, in one save. |
| `omodel preset ls\|use\|new\|rm` | List, switch, add, delete presets. |

Payload shapes (all stamped `"schema": 1` — refuse a major you don't recognise):

```json
// omodel show --json
{ "schema": 1, "ok": true, "omodel_version": "0.5.1",
  "config_path": "/home/you/.omo/omo.jsonc", "config_scope": "opencode",
  "degraded": false, "degraded_reason": null, "providers": ["opencode", "zhipuai"],
  "active_preset": {"index": 0, "name": "default"},
  "presets": [{"index": 0, "name": "default", "models": 12, "active": true}],
  "sync_conflict": false,
  "targets": [{"target": "agent:sisyphus", "kind": "agent", "name": "sisyphus",
               "model": "opencode/claude-opus-5", "provider": "opencode", "bare": "claude-opus-5",
               "variant": "max", "assigned": true, "available": true, "known": true}] }

// omodel candidates agent:sisyphus --json
{ "schema": 1, "ok": true, "target": "agent:sisyphus", "degraded": false, "degraded_reason": null,
  "gpt_only": false, "sync_conflict": false, "current": "opencode/claude-opus-4-8",
  "candidates": [{"index": 0, "source": "omo", "provider": "opencode", "model": "claude-opus-5",
                  "value": "opencode/claude-opus-5", "variant": "max", "substitute_for": null,
                  "warn": [], "current": false, "settable": true,
                  "variants": ["low", "medium", "high", "max"]}] }

// omodel set … --json
{ "schema": 1, "ok": true, "target": "agent:sisyphus", "from": "opencode/claude-opus-4-8",
  "to": "opencode/claude-opus-5", "variant": "max", "warn": [], "changed": true,
  "sync_conflict": false,
  "dry_run": false, "backup": "…/.backup/20260726-123728.728.jsonc", "diff": "…" }
```

These show every key each payload carries; `sync_conflict` in particular is on all of them (§9).

`substitute_for` means the row stands in for a model omo named that you don't have — e.g. omo
wants `glm-5`, you have `glm-5.2`, so the row is `glm-5.2` with `substitute_for: "glm-5"`. It is
informational; `value` is still what gets written.

`settable: false` marks a row `set` would refuse. The list always shows the target's *current*
assignment even when omodel would not let you choose it — a GPT-only agent already holding a
non-GPT model, or a model whose provider is no longer connected. **Pick only from rows where
`settable` is `true`**; it is computed by the same check `set` runs, so it will not mislead you.

## 6. `degraded: true` means unknown, not empty

If `opencode` is missing or unreadable, omodel cannot tell what you can run. Then:

- `providers` is `[]`, and `candidates` holds nothing from omo's chain (at most the target's
  current assignment, as a `source: "add"` row, so you can still see what is set),
- `available` on every target is `null`, not `false`,
- `check` reports no availability problems, because it cannot know of any,
- `set` skips the availability guard rather than refusing everything.

**Do not conclude a model is unusable from an empty candidate list.** Check `degraded` first. If
it is true and that matters, tell the user `opencode` isn't reachable rather than "reporting" that
nothing works. `degraded_reason` (on `show`, `candidates` and `check`) says which it is:
`"opencode is not on PATH"`, or what `opencode models` did instead of answering — exited
non-zero, printed nothing, timed out. The first is fixed by installing opencode; the rest by
`omodel --refresh-models`, or by the user looking at opencode itself.

## 7. Changing several models: use `apply` or `preset use`

Every save snapshots the config to `.backup/` and the ring keeps only the newest **20**. Eleven
separate `set` calls therefore evict eleven of the user's own snapshots. Prefer one call:

```sh
echo '{"agent:sisyphus": {"model": "opencode/claude-opus-5", "variant": "max"},
       "cat:quick": "openai/gpt-5.4-mini-fast"}' | omodel apply --json
```

A value may be an object (`{"model": …, "variant": …}`) or a bare model string. Validation is
all-or-nothing: if any entry is bad, nothing is written and you get exit 3 — so a half-applied
config never lands.

`omodel preset use <name>` switches a whole named set of assignments in one save, and is the
cheapest bulk change available.

**Never run two mutating commands at once.** Each one reads the config, changes it, and writes it
back with no locking, so concurrent `set` calls overwrite each other — every process reports
`"ok": true` and only the last write survives. If you are used to firing independent tool calls in
parallel, do not do it here: batch them into one `apply` instead. Read-only verbs (`show`,
`candidates`, `targets`, `check`) are safe to run in parallel.

## 8. Presets

A preset is a named set of assignments. Exactly one is active, and **the config on disk always
equals the active preset.** Your edits go into the active one. `omodel preset use` banks the
current edits into the preset you are leaving before switching, so nothing is lost. `rm` refuses
to delete the active preset — switch away first.

## 9. Safety

- `--dry-run` on `set` / `clear` / `apply` writes nothing; with `--json` the payload's `diff`
  field shows exactly what would change. Use it when you are unsure.
- A command that would change nothing writes nothing — no file rewrite, no backup. `changed:
  false` is success, not failure.
- `--config PATH` for anything experimental. The default path is the user's real, live config.
- Every save keeps a timestamped backup, and the very first save pins the original permanently.
  The user restores with `omodel --restore` (interactive — **do not call it**, it blocks on a
  prompt).
- `--force` overrides the unavailable-model and invalid-variant refusals. Don't reach for it to
  make an error go away; the guard is usually right. It does **not** override the GPT-only rule.
- If `sync_conflict` is `true`, something outside omodel wrote the config and it matches no
  preset. **Your next write adopts it**: because the config on disk must always equal the active
  preset, writing anything folds the whole foreign config into that preset — including targets
  your command never mentioned. That is a real decision and it is not yours to make silently.
  Tell the user, and offer the two clean resolutions: `omodel preset new <name>` keeps the
  foreign state as its own preset without touching the others, and `omodel preset use <name>`
  discards it and returns to a known one. Both are non-destructive; a bare `set` is not.
  `sync_conflict` appears on every payload, so check it before your first write, not after.

## 10. What omodel will not do

- No network calls, ever. Availability comes from the local `opencode` CLI (cached 24h).
- It does not install, authenticate or connect providers. If a model isn't available, the fix is
  `opencode auth login`, which is the user's to run — not omodel's.
- `set` and `apply` will not put a non-GPT model on `hephaestus`, even with `--force`: omo's
  `no-hephaestus-non-gpt` hook would reassign the session, so the config could not take effect.
  (`preset use` installs a stored preset wholesale without re-checking it, matching the TUI — so
  a preset saved with a non-GPT hephaestus model will apply one. `omodel check` reports it.)
- It does not choose models for you. `candidates` tells you what is possible; which one suits the
  user's cost, speed and quality tradeoff is a judgement call — make it explicitly, or ask.
