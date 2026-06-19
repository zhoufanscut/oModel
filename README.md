# oModel

> A TUI to quickly set **OMO** (`oh-my-openagent`) models — command `omodel`.

**what omo suggests + what you already have → pick one → save a clean config.**

Per agent/category, oModel shows one merged list (★ models omo suggests, ✓ models you have
via `opencode models`); you pick one and it fills in the correct `provider/` prefix and a valid
variant, then saves a clean `oh-my-openagent.jsonc` (timestamped backups each save).

> ⚠ Baseline README — the CLI+packaging specialist owns the final install/usage docs
> (install.sh, pipx/uvx-from-git, CLI reference). See `DESIGN.md` for the full spec.

## Requirements
- Python ≥ 3.9
- `opencode` CLI on `PATH`
- `bun` (only for `omodel --refresh`)
