# Changelog

All notable changes to oModel are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- In-session **undo/redo** of every edit, for mis-press recovery: `u` undoes the last
  operation (set / clear / variant / add-model / add sub-target), `ctrl+r` redoes it — a
  snapshot stack (`history.py`) the app records on every config mutation. Each undo/redo
  notifies what changed; the hint bar shows `u undo` / `⌃r redo` only when available.

### Changed
- Dirtiness is now computed (`serialize(cfg)` vs the last-saved text) instead of a flag, so
  undoing back to the saved state quits without a prompt, and an empty ultrawork/compaction
  sub-object is undoable but never marks the file dirty.

## [0.1.0] — 2026-06-19

### Added
- Initial release.
- Textual two-pane TUI: agents + categories (left) / candidate list (right).
- Candidate list merges ★ omo suggestions and ✓ locally available models from `opencode models`.
- Dedicated-first prefix resolution (gateway ≥ 2 vendors; dedicated wins; `p` cycles prefix).
- Variant defaulting from the bundled family registry; `v` to override.
- Clean JSONC rewrite on save (comments dropped by design); timestamped `.backup/` each save;
  pinned `original.jsonc` (never pruned, never counts toward the 20-snapshot buffer).
- `omodel --restore` — list newest 10 backups + pinned original; restore interactively.
- `omodel --refresh [--omo-src P]` — regenerate `omo-suggestions.json` via bun + omo checkout;
  non-fatal if omo source or bun is absent.
- `omodel --print` — print current resolved agent/category models, no UI.
- `omodel --check` — dry-run CI-safe resolve for every target (exits 0; degrades if no opencode).
- `omodel --sync-models` — passthrough to `opencode models --refresh`.
- `omodel --version`.
- `install.sh` — POSIX-sh curl|sh installer (linux-x64, darwin-arm64, darwin-x64).
- GitHub Actions: `ci.yml` (matrix 3.9–3.13), `release.yml` (PyInstaller one-file binaries
  on tag push), `refresh-suggestions.yml` (weekly omo snapshot → PR on change).
- Bundled `omo-suggestions.json` from oh-my-openagent v4.11.1 @ b949c34:
  11 agents, 8 categories, 14 families, 9 known variants.
