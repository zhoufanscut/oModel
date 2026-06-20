# Changelog

All notable changes to oModel are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] — 2026-06-20

### Added
- Initial release.
- Textual two-pane TUI: agents + categories (left) / candidate list (right).
- Candidate list merges omo's fallback-chain suggestions with the models you actually have
  (from `opencode models`), filtered to what you can run.
- Dedicated-first prefix resolution: every serving provider is shown as its own row (a gateway
  serves ≥ 2 vendors; a single-vendor dedicated provider sorts first) — pick the row to choose
  the prefix.
- Variant defaulting from the bundled family registry; `v` to override.
- In-session **undo/redo** for mis-press recovery: `u` undoes the last edit
  (set / clear / variant / add-model / add sub-target), `ctrl+r` redoes it — a snapshot stack
  (`history.py`) recorded on every config mutation. Each notifies what changed; the hint bar
  shows `u undo` / `⌃r redo` only when available.
- Vim navigation: `h`/`j`/`k`/`l` alongside the arrow keys.
- Clean JSONC rewrite on save (comments dropped by design); timestamped `.backup/` each save;
  pinned `original.jsonc` (never pruned, never counts toward the 20-snapshot buffer).
- `omodel --restore` — list newest 10 backups + pinned original; restore interactively.
- `omodel --refresh-omo [--omo-src P]` — regenerate `omo-suggestions.json` via bun + an omo
  checkout; non-fatal if omo source or bun is absent.
- `omodel --refresh-models` — force `opencode models --refresh` + rebuild the local cache (the
  in-TUI `r` key does the same).
- `omodel --print` — print current resolved agent/category models, no UI.
- `omodel --check` — dry-run CI-safe resolve for every target (exits 0; degrades if no opencode).
- `omodel --version`.
- `install.sh` — POSIX-sh curl|sh installer (linux-x64, darwin-arm64; Intel macs via pipx).
- GitHub Actions: `ci.yml` (matrix 3.9–3.13), `release.yml` (PyInstaller one-file binaries
  on tag push), `refresh-suggestions.yml` (weekly omo snapshot → PR on change).
- Bundled `omo-suggestions.json` from oh-my-openagent v4.11.1 @ b949c34:
  11 agents, 8 categories, 14 families, 9 known variants.
