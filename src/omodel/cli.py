"""argparse entrypoint.  DESIGN.md §CLI.

FROZEN CONTRACT — owned by the CLI+packaging specialist. `main` is the console-script
entrypoint ([project.scripts] omodel = "omodel.cli:main") and returns a process exit code.
"""
from __future__ import annotations


def main(argv: list = None) -> int:
    """Parse argv and dispatch (DESIGN §CLI):
      (default)        → run the TUI (omodel.app.run_app)
      --config PATH    → use a specific config file
      --restore        → list recent backups (newest 10 + pinned original) and restore one
      --refresh [--omo-src P] → regenerate suggestion data (omodel.refresh.refresh)
      --print          → print current resolved agent/category models, no UI
      --check          → dry-run: resolve candidate lists for every target, exit 0
                         (CI-safe; degrades to suggestions-only if `opencode` absent)
      --sync-models    → passthrough to `opencode models --refresh`
      --version
    Returns the process exit code."""
    raise NotImplementedError
