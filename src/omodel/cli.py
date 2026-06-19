"""argparse entrypoint.  DESIGN.md §CLI.

FROZEN CONTRACT — owned by the CLI+packaging specialist. `main` is the console-script
entrypoint ([project.scripts] omodel = "omodel.cli:main") and returns a process exit code.
"""
from __future__ import annotations

import argparse
import sys


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
    parser = argparse.ArgumentParser(
        prog="omodel",
        description="TUI to quickly set OMO (oh-my-openagent) models.",
        add_help=True,
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Use a specific config file instead of the default.",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="List recent backups (newest 10 + pinned original) and restore one interactively.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Regenerate suggestion data from an omo checkout (requires bun).",
    )
    parser.add_argument(
        "--omo-src",
        metavar="PATH",
        dest="omo_src",
        help="Path to the oh-my-openagent checkout (used with --refresh).",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_models",
        help="Print current resolved agent/category models, no UI.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Dry-run: resolve candidate lists for every target, exit 0 (CI-safe).",
    )
    parser.add_argument(
        "--sync-models",
        action="store_true",
        dest="sync_models",
        help="Passthrough to `opencode models --refresh`.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the omodel version and exit.",
    )

    args = parser.parse_args(argv)

    # --version: no imports beyond __init__
    if args.version:
        import omodel
        print(omodel.__version__)
        return 0

    # --refresh [--omo-src PATH]: non-fatal if omo or bun are absent
    if args.refresh:
        from omodel.refresh import refresh
        return refresh(omo_src=args.omo_src)

    # --sync-models: passthrough to `opencode models --refresh`
    if args.sync_models:
        import subprocess
        try:
            result = subprocess.run(["opencode", "models", "--refresh"])
            return result.returncode
        except FileNotFoundError:
            print("error: `opencode` not found on PATH", file=sys.stderr)
            return 1

    # --restore: list backups and prompt the user to pick one
    if args.restore:
        return _cmd_restore(args.config)

    # --print: resolve current models from config + suggestions/catalog, print, no UI
    if args.print_models:
        return _cmd_print(args.config)

    # --check: dry-run resolve for every target, CI-safe, exit 0
    if args.check:
        return _cmd_check(args.config)

    # Default: launch the TUI (import lazily so --version/--check/--refresh never import app)
    from omodel.app import run_app
    run_app(config_path=args.config)
    return 0


# ---------------------------------------------------------------------------
# Sub-command implementations
# ---------------------------------------------------------------------------

def _cmd_restore(config_override: "str | None") -> int:
    """List newest 10 backups + pinned original, prompt user, restore."""
    from omodel.config_io import config_path, list_backups, restore

    path = config_path(config_override)
    backups = list_backups(path)

    if not backups:
        print("No backups found.")
        return 0

    print(f"Backups for: {path}")
    print()
    for i, b in enumerate(backups):
        tag = " [original]" if b.is_original else ""
        print(f"  {i + 1:2d}.  {b.name}{tag}  ({b.size} bytes)")

    print()
    choice = input("Restore which backup? (number, or q to cancel): ").strip()
    if choice.lower() in ("q", ""):
        print("Cancelled.")
        return 0

    try:
        idx = int(choice) - 1
    except ValueError:
        print("Invalid choice.", file=sys.stderr)
        return 1

    if idx < 0 or idx >= len(backups):
        print("Choice out of range.", file=sys.stderr)
        return 1

    chosen = backups[idx]
    restore(path, chosen.name)
    print(f"Restored {chosen.name} to {path}")
    return 0


def _cmd_print(config_override: "str | None") -> int:
    """Resolve current agent/category models from config + suggestions/catalog, print."""
    from omodel.config_io import load_config
    from omodel.suggestions import load as load_suggestions
    from omodel.catalog import load as load_catalog, CatalogUnavailable

    cfg, path = load_config(config_override)
    suggestions = load_suggestions()

    try:
        catalog = load_catalog()
    except CatalogUnavailable as exc:
        print(f"[warn] Could not load catalog: {exc}", file=sys.stderr)
        from omodel.catalog import Catalog
        catalog = Catalog(available={}, connected=[])

    agents_cfg = cfg.get("agents", {})
    categories_cfg = cfg.get("categories", {})

    print(f"Config: {path}")
    if catalog.connected:
        print(f"Providers: {' · '.join(catalog.connected)}")
    else:
        print("Providers: (none — opencode unavailable)")
    print()

    print("AGENTS:")
    for name, data in agents_cfg.items():
        model = data.get("model", "(unset)") if isinstance(data, dict) else "(unset)"
        variant = data.get("variant") if isinstance(data, dict) else None
        suffix = f"  variant={variant}" if variant else ""
        print(f"  {name}: {model}{suffix}")
        # Sub-targets
        for sub in ("ultrawork", "compaction"):
            sub_data = data.get(sub) if isinstance(data, dict) else None
            if isinstance(sub_data, dict):
                sub_model = sub_data.get("model", "(unset)")
                sub_variant = sub_data.get("variant")
                sub_suffix = f"  variant={sub_variant}" if sub_variant else ""
                print(f"    .{sub}: {sub_model}{sub_suffix}")

    print()
    print("CATEGORIES:")
    for name, data in categories_cfg.items():
        model = data.get("model", "(unset)") if isinstance(data, dict) else "(unset)"
        variant = data.get("variant") if isinstance(data, dict) else None
        suffix = f"  variant={variant}" if variant else ""
        print(f"  {name}: {model}{suffix}")

    return 0


def _cmd_check(config_override: "str | None") -> int:
    """Dry-run: resolve candidate lists for every known target, CI-safe, always exit 0.
    Degrades gracefully if opencode is absent (suggestions-only)."""
    from omodel.config_io import config_path
    from omodel.suggestions import load as load_suggestions
    from omodel.catalog import load as load_catalog, CatalogUnavailable
    from omodel.resolve import Resolver

    suggestions = load_suggestions()

    degraded = False
    try:
        catalog = load_catalog()
        if not catalog.connected:
            # opencode absent → empty catalog, not CatalogUnavailable
            degraded = True
    except CatalogUnavailable as exc:
        print(f"[warn] Catalog unavailable ({exc}); running suggestions-only.", file=sys.stderr)
        from omodel.catalog import Catalog
        catalog = Catalog(available={}, connected=[])
        degraded = True

    if degraded:
        print("[check] Degraded mode: no opencode catalog; using bundled suggestions only.")

    resolver = Resolver.build(catalog, suggestions)

    # Build the list of all known targets from bundled suggestions
    targets = []
    for name in suggestions.agents:
        targets.append(f"agent:{name}")
        # Always include sub-targets that omo knows about; app adds them from config
        targets.append(f"agent:{name}.ultrawork")
        targets.append(f"agent:{name}.compaction")
    for name in suggestions.categories:
        targets.append(f"cat:{name}")

    errors = []
    total_candidates = 0
    for target in targets:
        try:
            cands = resolver.candidates(target)
            total_candidates += len(cands)
        except Exception as exc:
            errors.append(f"  {target}: {exc}")

    if errors:
        print("[check] Errors resolving some targets:", file=sys.stderr)
        for e in errors:
            print(e, file=sys.stderr)
        # Still exit 0 — CI-safe per DESIGN
        print(f"[check] Done ({total_candidates} candidates; {len(errors)} errors — see stderr).")
    else:
        mode = "degraded" if degraded else "full"
        print(
            f"[check] OK ({mode} mode): {len(targets)} targets, "
            f"{total_candidates} total candidates."
        )

    return 0
