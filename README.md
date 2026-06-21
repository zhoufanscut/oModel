# oModel

> A TUI to quickly set **OMO** (`oh-my-openagent`) models — command `omodel`.

**what omo suggests + what you already have → pick one → save a clean config.**

Per agent/category, oModel shows omo's fallback chain filtered to the models you can actually
run (via `opencode models`) — each as one row per serving provider, dedicated providers before
gateways. You pick one and it fills in the correct `provider/` prefix and a valid variant, then
saves a clean `oh-my-openagent.jsonc` (timestamped backups each save).

```
 Providers: opencode · deepseek · moonshotai-cn · openai · zhipuai    (cached 3h ago · r)
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
 ↑↓ move · ←→ panes · enter set · v variant · x clear · a edit/sub · u undo · s save · q quit
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
| `←` `→` (`h` `l`) | Switch panes (targets ↔ candidates) |
| `Enter` | Set the highlighted candidate (or open `+ add model…`) |
| `v` | Pick a variant for the current candidate |
| `a` | Add a custom model (candidates / category row), or an `ultrawork` / `compaction` sub-target (agent row) |
| `x` | Clear the current agent/category model |
| `u` / `Ctrl+r` | Undo / redo the last edit (in session) |
| `s` | Save (diff + confirm modal) |
| `r` | Refresh the model list (force a live `opencode models --refresh`) |
| `q` | Quit (confirm if unsaved changes) |

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

See [DESIGN.md](DESIGN.md) for the full design — data sources, resolution rules, caching, and
packaging.

## License

oModel's own code is MIT-licensed — see [LICENSE](LICENSE).

The bundled `omo-suggestions.json` is derived from
[oh-my-openagent](https://github.com/code-yeongyu/oh-my-openagent) (Sustainable Use
License v1.0) — see [NOTICE](NOTICE) for full attribution and redistribution constraints.
