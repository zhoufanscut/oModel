# oModel

> A TUI to quickly set **OMO** (`oh-my-openagent`) models — command `omodel`.

**what omo suggests + what you already have → pick one → save a clean config.**

Per agent/category, oModel shows one merged list (★ models omo suggests, ✓ models you have
via `opencode models`); you pick one and it fills in the correct `provider/` prefix and a valid
variant, then saves a clean `oh-my-openagent.jsonc` (timestamped backups each save).

```
┌ oModel ────────────────────────────────────────────────────┐
│ AGENTS              │ sisyphus                             │
│ > sisyphus     kimi │ model: moonshotai-cn/kimi-k2.7-code  │
│     ↳ ultrawork opus│ variant: —     ctx 256k · $0.6/$2.5  │
│   hephaestus   gpt  │                                      │
│   oracle       gpt  │ ── candidates ──────────────────     │
│   momus        gpt  │ ★ omo  opencode/claude-opus-4-7 (max)│
│   ...               │ ★ omo  moonshotai-cn/kimi-k2.5       │
│ CATEGORIES          │ ✓ mine opencode/claude-opus-4-8      │
│   deep         gpt  │ ✓ mine deepseek/deepseek-v4-pro      │
│   quick        mini │ + add model…                         │
└ ↑↓ move · enter set · v variant · p prefix · e add · x clear · a sub · s save · q quit ┘
```

## Requirements

- Python ≥ 3.9
- `opencode` CLI on `PATH` (degrades gracefully if absent)
- `bun` — only for `omodel --refresh`

## Installation

### Standalone binary (recommended)

```sh
curl -fsSL https://raw.githubusercontent.com/zhoufanscut/oModel/main/install.sh | sh
```

Installs `omodel` to `~/.local/bin`. Supported platforms: `linux-x64`,
`darwin-arm64`, `darwin-x64`.

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

## Usage

```
omodel                          # launch the TUI
omodel --config PATH            # use a specific config file
omodel --restore                # list recent backups and restore one
omodel --refresh [--omo-src P]  # regenerate suggestion data from an omo checkout
omodel --print                  # print current resolved models, no UI
omodel --check                  # dry-run CI check (exit 0; degrades if opencode absent)
omodel --sync-models            # passthrough to `opencode models --refresh`
omodel --version
```

### Key bindings (TUI)

| Key | Action |
|-----|--------|
| `↑` `↓` | Navigate agents/categories or candidates |
| `Enter` | Set the highlighted candidate as the current model |
| `v` | Pick a variant for the current candidate |
| `p` | Cycle the provider prefix across available providers |
| `e` | Add a custom model (modal) |
| `x` | Clear the current agent/category model |
| `a` | Add an `ultrawork` / `compaction` sub-target |
| `s` | Save (diff + confirm modal) |
| `q` | Quit (confirm if unsaved changes) |

## How it works

1. **Suggestion data** — oModel bundles a snapshot of the omo model requirements
   (11 agents, 8 categories, 14 families) generated from oh-my-openagent at build time.
   Update it with `omodel --refresh`.

2. **Available models** — fetched live from `opencode models`; groups into connected
   providers. The TUI degrades to suggestions-only if `opencode` is absent.

3. **Prefix resolution** — dedicated providers (serving one vendor's models) win over
   gateways (e.g. `opencode` which serves many vendors). Press `p` to cycle the prefix.

4. **Save** — shows a unified diff before writing. Each save creates a verbatim
   timestamped backup; the very first save also pins `original.jsonc` (never pruned).
   `omodel --restore` lists the newest 10 backups + the pinned original.

## Refresh suggestion data

```sh
# If omo is checked out at ~/source/oh-my-openagent (default):
omodel --refresh

# Or point to another checkout:
omodel --refresh --omo-src /path/to/oh-my-openagent

# Or via environment variable:
OMO_SRC=/path/to/oh-my-openagent omodel --refresh
```

Requires `bun`. Non-fatal if absent: prints current bundled meta and exits 0.

## License

oModel's own code is MIT-licensed — see [LICENSE](LICENSE).

The bundled `omo-suggestions.json` is derived from
[oh-my-openagent](https://github.com/code-yeongyu/oh-my-openagent) (Sustainable Use
License v1.0) — see [NOTICE](NOTICE) for full attribution and redistribution constraints.
