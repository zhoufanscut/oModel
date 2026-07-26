# oModel

> A small TUI to help set **OMO** (`oh-my-openagent`) models — command `omodel`.

**what omo suggests + what you already have → pick one → save a clean config.**

Per agent/category, oModel shows omo's fallback chain filtered to the models you can actually
run (via `opencode models`) — each as one row per serving provider, dedicated providers before
gateways. You pick one and it fills in the correct `provider/` prefix and a valid variant, then
writes it back to `oh-my-openagent.jsonc`: only the `agents`/`categories` blocks are rewritten
clean — everything else in the file, including comments and commented-out config, is kept
verbatim (timestamped backups each save).

```
 oModel: opencode · deepseek · moonshotai-cn · openai · zhipuai
┌────────────────────┐┌────────────────────────────────────────────┐
│ AGENTS             ││ sisyphus                                   │
│ > sisyphus    kimi ││ model: moonshotai-cn/kimi-k2.7-code        │
│   ↳ ultrawork opus ││ variant: —    ctx 256k · $0.6/$2.5         │
│   hephaestus  gpt  │└────────────────────────────────────────────┘
│   oracle      gpt  │┌────────────────────────────────────────────┐
│   momus       gpt  ││  opencode/claude-opus-4-7 (max)            │
│   ...              ││  openai/gpt-5.5 (medium)                   │
│ CATEGORIES         ││  opencode/gpt-5.5 (medium)                 │
│   deep        gpt  ││● zhipuai/glm-5.1  (≈ omo glm-5)            │
│   quick       mini ││ + add model…                               │
└────────────────────┘└────────────────────────────────────────────┘
 s save · q quit · ? help                                     v0.2.0
```

## Requirements

- Python ≥ 3.9
- `opencode` CLI on `PATH` (degrades gracefully if absent)
- `bun` — only for `omodel --refresh-omo` (regenerating bundled suggestion data)

## Installation

### Standalone binary (recommended)

```sh
curl -fsSL https://raw.githubusercontent.com/zhoufanscut/oModel/main/install.sh | sh
```

Installs `omodel` to `~/.local/bin`. Supported platforms: `linux-x64` and
`darwin-arm64` (Apple Silicon). Intel macs (`darwin-x64`) aren't pre-built —
install via `pipx install git+https://github.com/zhoufanscut/oModel`.

The prebuilt `linux-x64` binary needs a glibc at least as new as whatever the `ubuntu-latest`
GitHub Actions runner ships at build time. On older distros where the binary fails to start
with a glibc-version error, use the pipx/uvx install path below instead.

### pipx / uvx (from GitHub, no PyPI)

```sh
# pipx
pipx install git+https://github.com/zhoufanscut/oModel

# uvx (run without installing)
uvx --from git+https://github.com/zhoufanscut/oModel omodel

# uv tool install
uv tool install git+https://github.com/zhoufanscut/oModel
```

### Maintainer / development

```sh
git clone https://github.com/zhoufanscut/oModel
cd oModel
uv pip install -e .
```

Regenerate the bundled suggestion data with `omodel --refresh-omo` (needs `bun` and an omo
checkout; point it with `--omo-src PATH` or `$OMO_SRC`). See [DESIGN.md](DESIGN.md) for details.

## Usage

```
omodel                          # launch the TUI
omodel --config PATH            # use a specific config file
omodel --restore                # list recent backups and restore one
omodel --refresh-omo [--omo-src P]  # regenerate bundled suggestion data from an omo checkout
omodel --refresh-models         # force `opencode models --refresh` + rebuild the local cache
omodel --print                  # print current resolved models, no UI
omodel --check                  # dry-run CI check (exit 0; degrades if opencode absent)
omodel --version
```

opencode's model list and per-model details are cached for 24h under `~/.cache/omodel/`, so
warm launches are instant. Press `r` in the TUI (or run `omodel --refresh-models`) to force a
live re-fetch and rebuild the cache.

### Key bindings (TUI)

| Key | Action |
|-----|--------|
| `↑` `↓` (`j` `k`) | Navigate agents/categories or candidates |
| `←` `→` (`h` `l`) | Jump between the targets and candidates panes |
| `Tab` / `Shift+Tab` | Cycle all three panes (targets → presets → candidates) — the way to reach the presets card |
| `Enter` | Set the highlighted candidate (or open `+ add model…`); on a preset, switch to it; on `+ add preset…`, add one |
| `v` | Pick a variant for the current candidate |
| `a` | Add a custom model (candidates / category row), an `ultrawork` / `compaction` sub-target (agent row), or a new preset holding the current models (presets card) |
| `r` | Refresh the model list — or, on a preset, rename it |
| `x` | Clear the current agent/category model (on a preset: delete it, after a confirm — never the one you're using) |
| `u` / `Ctrl+r` | Undo / redo the last edit (in session) |
| `s` | Save (diff + confirm modal) |
| `q` | Quit (if unsaved: save & quit / discard / cancel) |
| `←` `→` (`h` `l`) *in a dialog* | Move between buttons (`Tab` works too) |

## How it works

1. **What omo suggests** — oModel bundles a snapshot of omo's model requirements, so it needs
   neither an omo checkout nor a network call at runtime.
2. **What you have** — read live from `opencode models`. The TUI degrades to suggestions-only
   if `opencode` is absent.
3. **Pick** — each suggested model you can run is shown as one row per serving provider
   (dedicated providers before gateways). Pick a row and oModel applies the `provider/` prefix
   and a valid variant for you.
4. **Save** — shows a diff before writing a clean `oh-my-openagent.jsonc`, snapshotting the
   prior file to a timestamped backup (`omodel --restore` to roll back).

**Presets** sit under the agent list, and they're what you actually edit. One is always active
(`●`); your model changes go into it, and `s` writes it to `oh-my-openagent.jsonc`. Press `Enter`
on another preset to switch (your edits stay in the one you leave), `r` to rename one, `x` to
delete one, and `a` (or `Enter` on the last row, `+ add preset…`) to add another from the models
you're looking at — keep as many as you like. First
run seeds a `default` preset from the config you already have, so your config always matches one
of your presets — never a state you can't get back to. Nothing is written until you press `s`, and
then both files are written together; presets live beside your config in `.omodel-presets.json`,
so a `--config` override keeps its own set.

## Using oModel from an agent

If you work with an LLM coding agent, it can drive oModel directly instead of hand-editing your
config — which means it gets the `provider/` prefix, the variant check, the backup and the preset
invariant, same as you do from the TUI.

```sh
omodel agent-guide                            # the full contract, written for an agent
omodel candidates agent:sisyphus --json       # what this agent can run
omodel set agent:sisyphus opencode/gpt-5.5 --dry-run   # preview; drop --dry-run to write
```

Also `targets`, `show`, `check`, `clear`, `apply` (batch, one save) and
`preset ls|use|new|rm` — all with `--json`. Exit codes carry the meaning: `0` success, `1` oModel
failed, `2` usage, `3` refused (unknown target, a model you can't run, a bad variant).

### Telling your agent oModel exists

An agent that reaches for `omodel --help` finds `agent-guide` and reads the whole contract from
there. The gap is earlier than that: asked to change a model, an agent's first instinct is often
to open `oh-my-openagent.jsonc` and edit it — and nothing in that file mentions oModel.

So say it once, before the agent forms a plan. Paste this into your `CLAUDE.md`, `AGENTS.md`, or
whatever instructions file your agent reads at startup:

```markdown
## Changing OMO models

Never hand-edit `oh-my-openagent.jsonc` — it skips the `provider/` prefix, the variant check,
the backup and the preset invariant. Use the `omodel` CLI instead:

    omodel agent-guide                        # read this first — the full contract
    omodel candidates <target> --json         # what a target can run; use a row's `value` verbatim
    omodel set <target> <value> [--variant V] # writes config + presets together

Targets are `agent:<name>`, `agent:<name>.ultrawork`, `agent:<name>.compaction`, `cat:<name>`
(`omodel targets --json` lists them). Exit 3 means the request was refused — read the message
and pick another candidate. Exit 1 means omodel failed; stop and report.
```

One line of it does the real work: *never hand-edit the file*. Everything else the agent can
discover from `omodel agent-guide`.

See [DESIGN.md](DESIGN.md) for the full design — data sources, resolution rules, caching, and
packaging.

## License

oModel's own code is MIT-licensed — see [LICENSE](LICENSE).

The bundled `omo-suggestions.json` is derived from
[oh-my-openagent](https://github.com/code-yeongyu/oh-my-openagent) (Sustainable Use
License v1.0) — see [NOTICE](NOTICE) for full attribution and redistribution constraints.
