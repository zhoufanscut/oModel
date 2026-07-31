"""argparse entrypoint.  DESIGN.md §CLI.

FROZEN CONTRACT — owned by the CLI+packaging specialist. `main` is the console-script
entrypoint ([project.scripts] omodel = "omodel.cli:main") and returns a process exit code.

Two audiences, one parser:

  * **A human** runs `omodel` (the TUI) or one of the original flat flags — `--print`,
    `--check`, `--restore`, `--refresh-omo`, `--refresh-models`, `--version`. Every one of
    those still behaves exactly as it did; they predate the subcommands and are not deprecated.
  * **An LLM agent** runs the SUBCOMMANDS: `agent-guide`, `targets`, `show`, `candidates`,
    `check`, `set`, `clear`, `apply`, `preset`. These emit machine-readable JSON (`--json`) and
    branch on exit codes. `omodel agent-guide` prints the whole contract; start there.

Both go through `session.Session`, so the CLI applies the same provider prefixing, variant
rules, GPT-only lock, backups and config-equals-active-preset invariant the TUI does. That is
the entire point: before this existed, an agent asked to change a model had to hand-edit
`oh-my-openagent.jsonc` and bypassed all of it.

Imports stay LAZY (inside the branches). `--version` and the JSON verbs must never import
Textual — it is a heavy import and none of them draws a UI.
"""
from __future__ import annotations

import argparse
import os
import sys

# JSON payload version. Bump ONLY on a breaking shape change; additive fields do not bump it.
# Consumers should check it and refuse a major they don't know (CONTRACTS.md §agent JSON).
SCHEMA = 1

# Exit codes. The 1-vs-3 split is the one an agent branches on: 3 means "your request was
# refused, pick something else"; 1 means "omodel failed, stop and report".
EXIT_OK = 0
EXIT_ERROR = 1      # operational failure — unwritable path, malformed config
EXIT_USAGE = 2      # argparse usage error (argparse's own convention)
EXIT_REJECTED = 3   # rejected by a guard — unknown target, unavailable model, bad variant


def main(argv: list | None = None) -> int:
    """Parse argv and dispatch. Returns the process exit code (see EXIT_* above).

    A thin wrapper over `_main` that keeps a CLOSED STDOUT inside the exit-code contract.
    `omodel show --json | head` leaves ~1 KB of a 9 KB payload in the stdio buffer; without
    this, the failure lands in the interpreter's shutdown flush — too late for any caller to
    catch — which prints `Exception ignored on flushing sys.stdout` and exits **120**. On the
    surface whose whole promise is that 0/1/2/3 mean four specific things, a fifth code that
    reads like a crash is exactly the ambiguity the contract exists to remove.

    Flushing HERE turns that into a catchable BrokenPipeError, and the reader stopping early is
    not a failure: omodel did its work, so this is EXIT_OK and silent."""
    try:
        code = _main(argv)
        sys.stdout.flush()
        return code
    except BrokenPipeError:
        _drop_stdout()
        return EXIT_OK


def _drop_stdout() -> None:
    """Point fd 1 at os.devnull so the shutdown flush can't hit the dead pipe a second time —
    that flush is what turns a caught BrokenPipeError back into stderr noise and exit 120.
    Best-effort: under pytest's capture (and anywhere else stdout is not a real fd)
    `fileno()` raises io.UnsupportedOperation, which is both an OSError and a ValueError.

    The devnull fd is closed once dup2 has copied it — `main` is importable, so a caller that
    invokes it in-process and keeps running must not leak one per call."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return
    try:
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError):
        pass
    finally:
        os.close(devnull)


def _main(argv: list | None = None) -> int:
    """Parse argv and dispatch. The real body; `main` wraps it (see there)."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --version: no imports beyond __init__
    if args.version:
        import omodel
        print(omodel.__version__)
        return EXIT_OK

    command = getattr(args, "command", None)
    if command is not None:
        return _dispatch_command(command, args)

    # ----- the original flat flags (unchanged behavior) -----

    # --refresh-omo [--omo-src PATH]: regenerate bundled omo data; non-fatal if omo/bun absent
    if args.refresh_omo:
        from omodel.refresh import refresh
        return refresh(omo_src=args.omo_src)

    # --refresh-models: force opencode upstream re-fetch + rebuild our cache
    if args.refresh_models:
        return _cmd_refresh_models()

    # --restore: list backups and prompt the user to pick one
    if args.restore:
        return _cmd_restore(args.config)

    # --print: resolve current models from config + suggestions/catalog, print, no UI
    if args.print_models:
        return _cmd_print(args.config)

    # --check: dry-run resolve for every target, CI-safe, exit 0
    if args.check:
        return _cmd_check(args.config)

    # Default: launch the TUI (import lazily so --version/--check/the JSON verbs never import
    # app). Pin the color depth BEFORE importing app — Textual reads $TEXTUAL_COLOR_SYSTEM at
    # import.
    _default_color_system()
    from omodel.app import run_app
    from omodel.config_io import ConfigParseError
    # load_config() runs once, at app construction (before the UI starts) and is never
    # re-called during the session — catching this narrow type around the whole call is safe.
    try:
        run_app(config_path=args.config)
    except ConfigParseError as exc:
        _print_config_parse_error(exc)
        return EXIT_ERROR
    return EXIT_OK


def _dispatch_command(command: str, args) -> int:
    """Route a subcommand. Split out of `main` so the flat-flag path above stays readable."""
    config = getattr(args, "config", None)
    as_json = getattr(args, "json", False)

    if command == "agent-guide":
        return _cmd_agent_guide()
    if command == "targets":
        return _cmd_targets(config, as_json)
    if command == "show":
        return _cmd_show(config, as_json)
    if command == "candidates":
        return _cmd_candidates(config, args.target, as_json)
    if command == "check":
        return _cmd_check_json(config, as_json)
    if command == "set":
        return _cmd_set(config, args.target, args.value, args.variant,
                        args.dry_run, args.force, as_json)
    if command == "clear":
        return _cmd_clear(config, args.target, args.dry_run, as_json)
    if command == "apply":
        return _cmd_apply(config, args.dry_run, args.force, as_json)
    if command == "preset":
        return _cmd_preset(config, args, as_json)
    return EXIT_USAGE


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_EPILOG = """\
exit codes:
  0  success
  1  omodel failed (unwritable path, malformed config) — stop and report
  2  usage error
  3  request refused (unknown target, unavailable model, bad variant) — pick something else

For LLM agents: run `omodel agent-guide` for the full contract, or add --json to any of
targets / show / candidates / check / set / clear / apply / preset.
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omodel",
        description="TUI to quickly set OMO (oh-my-openagent) models — and a JSON CLI for agents.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        "--refresh-omo",
        action="store_true",
        dest="refresh_omo",
        help="Regenerate bundled omo suggestion data from an omo checkout (requires bun).",
    )
    parser.add_argument(
        "--omo-src",
        metavar="PATH",
        dest="omo_src",
        help="Path to the oh-my-openagent checkout (used with --refresh-omo).",
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
        "--refresh-models",
        action="store_true",
        dest="refresh_models",
        help="Force `opencode models --refresh` and rebuild the local ~/.cache/omodel cache.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the omodel version and exit.",
    )

    # Shared options for every subcommand. `default=SUPPRESS` is load-bearing: without it the
    # subparser's own `--config=None` would overwrite a value the MAIN parser already parsed,
    # so `omodel --config X show` would silently fall back to the default config. With SUPPRESS
    # the attribute is set only when the flag is actually given, and both orders work —
    # `omodel --config X show` and `omodel show --config X`. Agents write the second.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config", metavar="PATH", default=argparse.SUPPRESS,
        help="Use a specific config file instead of the default.",
    )
    common.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help=f"Emit machine-readable JSON (schema {SCHEMA}).",
    )

    subs = parser.add_subparsers(dest="command", metavar="COMMAND")

    subs.add_parser(
        "agent-guide",
        help="Print the full contract for LLM agents (start here).",
        description="Print omodel's agent contract — verbs, JSON shapes, exit codes, safety "
                    "rules — to stdout. Written to be read by an LLM agent before it uses "
                    "omodel for the first time.",
    )

    subs.add_parser(
        "targets", parents=[common],
        help="List every target id you can set.",
        description="List every target id omo defines: agent:<name>, its valid "
                    "agent:<name>.ultrawork / .compaction sub-targets, and cat:<name>. "
                    "Example: omodel targets --json",
    )

    subs.add_parser(
        "show", parents=[common],
        help="Show current assignments, providers and presets.",
        description="Show what is currently assigned to every target, which providers are "
                    "connected, and which presets exist. Example: omodel show --json",
    )

    p_cand = subs.add_parser(
        "candidates", parents=[common],
        help="List the models you can set a target to.",
        description="List the models a target can be set to — omo's fallback chain filtered to "
                    "what you can actually run, one row per serving provider. Use a row's "
                    "`value` verbatim with `omodel set`. "
                    "Example: omodel candidates agent:sisyphus --json",
    )
    p_cand.add_argument("target", help="Target id, e.g. agent:sisyphus or cat:deep.")

    subs.add_parser(
        "check", parents=[common],
        help="Report config problems; exit 3 if any.",
        description="Report problems with the current config — targets set to a model no "
                    "connected provider serves, invalid variants, unknown agents/categories. "
                    "Exits 3 if any are found (the flat --check flag always exits 0 for CI). "
                    "Example: omodel check --json",
    )

    p_set = subs.add_parser(
        "set", parents=[common],
        help="Set a target's model.",
        description="Set a target's model, writing the config and the presets file together. "
                    "Refuses (exit 3) an unknown target, a model no connected provider serves, "
                    "or an invalid variant; --force overrides the last two. "
                    "Example: omodel set agent:sisyphus opencode/claude-opus-4-7 --variant max",
    )
    p_set.add_argument("target", help="Target id, e.g. agent:sisyphus.")
    p_set.add_argument("value", help="provider/model, exactly as `candidates` reports it.")
    p_set.add_argument("--variant", metavar="V", help="Variant to write (omit for none).")
    p_set.add_argument("--dry-run", action="store_true", dest="dry_run",
                       help="Preview the change and write nothing (--json carries the diff).")
    p_set.add_argument("--force", action="store_true",
                       help="Write despite an unavailable model or invalid variant.")

    p_clear = subs.add_parser(
        "clear", parents=[common],
        help="Clear a target's model.",
        description="Remove a target's model and variant. Example: omodel clear cat:quick",
    )
    p_clear.add_argument("target", help="Target id, e.g. cat:quick.")
    p_clear.add_argument("--dry-run", action="store_true", dest="dry_run",
                         help="Preview the change and write nothing (--json carries the diff).")

    p_apply = subs.add_parser(
        "apply", parents=[common],
        help="Set many targets at once from stdin JSON.",
        description="Read {\"<target>\": {\"model\": \"provider/model\", \"variant\": \"v\"}} "
                    "from stdin and apply every assignment in ONE save — preferred over "
                    "repeated `set`, which writes a backup each time (the ring holds 20). "
                    "Example: echo '{\"cat:quick\": {\"model\": \"openai/gpt-5.5-mini\"}}' "
                    "| omodel apply --json",
    )
    p_apply.add_argument("--dry-run", action="store_true", dest="dry_run",
                         help="Preview the change and write nothing (--json carries the diff).")
    p_apply.add_argument("--force", action="store_true",
                         help="Write despite unavailable models or invalid variants.")

    p_preset = subs.add_parser(
        "preset", parents=[common],
        help="List, switch, add or remove presets.",
        description="Presets are named sets of assignments; the config on disk always equals "
                    "the active one. `use` is the cheapest bulk change there is — one save, "
                    "one backup. Example: omodel preset use cheap",
    )
    p_preset.add_argument(
        "action", choices=("ls", "use", "new", "rm"),
        help="ls: list; use: switch to one; new: add one from the current models; rm: delete.",
    )
    p_preset.add_argument(
        "name", nargs="?",
        help="Preset name (or 1-based index) — required by use/new/rm.",
    )

    return parser


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# Said on every surface that can observe a sync conflict, so the prose path can't be quieter
# than the JSON one. A conflict means the config matches no preset — something outside omodel
# wrote it — and the next write ADOPTS it into the active preset (the TUI escalates the same
# decision via its sync modal; the CLI cannot prompt, so it must at least say so).
_SYNC_CONFLICT_NOTE = (
    "note: preset conflict — this config was changed outside omodel and matches no preset, so "
    "the next write will adopt it into the active preset (including targets you didn't name). "
    "`omodel preset new <name>` keeps it as its own preset without touching the others; "
    "`omodel preset use <name>` discards it."
)


def _print_config_parse_error(exc: Exception) -> None:
    """Friendly stderr message for a ConfigParseError, in place of a raw json5 traceback."""
    print(f"error: {exc}", file=sys.stderr)
    print("Fix the file or restore a backup with `omodel --restore`.", file=sys.stderr)


def _default_color_system() -> None:
    """Pin the TUI to a 256-color palette by default so it looks the same across terminals.

    Textual/Rich auto-detect color depth from $COLORTERM / $TERM: a terminal that doesn't set
    $COLORTERM and reports a bare `TERM=xterm` is detected as only **16 colors**, so omodel's
    colors collapse to that terminal's 8/16 ANSI slots — looking very different from a
    `TERM=xterm-256color` (256-color) session. Default to 256 everywhere for a consistent look;
    honour an explicit choice the user already made (e.g. `TEXTUAL_COLOR_SYSTEM=truecolor` for
    24-bit, or `=auto` to restore Textual's own detection)."""
    import os
    os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "256")


def _emit(payload: dict, as_json: bool, lines=()) -> None:
    """Print `payload` as JSON, or `lines` as prose. Every JSON verb goes through here so the
    schema stamp can't be forgotten on one of them."""
    if as_json:
        import json
        payload = dict(payload)
        payload.setdefault("schema", SCHEMA)
        # `ok` on EVERY payload, success or refusal. It is the field an agent branches on first,
        # and the read verbs used to omit it — so `payload["ok"]` KeyError'd on exactly the calls
        # that had succeeded. `check` sets its own (it reports ok=False for a problem config).
        payload.setdefault("ok", True)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for line in lines:
            print(line)


def _fail_write(exc: Exception, as_json: bool, **extra) -> int:
    """Report a failed publish. A `_StoreWriteFailed` that got past the config write says so
    explicitly: the config IS on disk and only the presets file is behind, so the agent should
    retry (which heals it) rather than assume nothing happened and redo its whole plan."""
    config_written = getattr(exc, "config_written", False)
    message = f"could not write: {exc}"
    if config_written:
        message = (
            f"the config was written but the presets file was not ({exc}) — "
            "re-run the same command to bring them back in step"
        )
    return _fail("write_failed", message, as_json, code=EXIT_ERROR,
                 config_written=config_written, **extra)


def _fail(error: str, message: str, as_json: bool, code: int = EXIT_REJECTED, **extra) -> int:
    """Report a refusal/failure the same way on both surfaces and return its exit code."""
    if as_json:
        payload = {"ok": False, "error": error, "message": message}
        payload.update(extra)
        _emit(payload, True)
    else:
        print(f"error: {message}", file=sys.stderr)
    return code


def _open_session(config_override):
    """`(session, None)` or `(None, exit_code)` — builds a Session, turning a malformed config
    into the same friendly exit-1 the TUI path gives."""
    from omodel.config_io import ConfigParseError
    from omodel.session import Session
    try:
        return Session.build(config_override), None
    except ConfigParseError as exc:
        _print_config_parse_error(exc)
        return None, EXIT_ERROR


def _split_value(value):
    """`provider/model` → (provider, model), or None if it isn't qualified. Split on the FIRST
    '/', matching what config_io writes and what candidates reports.

    The isinstance check is load-bearing, not defensive noise: `apply` reads its values from
    agent-supplied JSON, where `{"model": 123}` is entirely plausible. Without it `"/" not in 123`
    raises TypeError, which surfaces as a traceback and exit 1 ("omodel failed, stop") on what is
    really a caller error the agent could simply correct (exit 3)."""
    if not isinstance(value, str) or "/" not in value:
        return None
    provider, model = value.split("/", 1)
    if not provider or not model:
        return None
    return provider, model


def _assignment_row(session, target: str) -> dict:
    """One target's current state, for `show` / `check`."""
    from omodel import session as session_mod
    model, variant = session.assignment(target)
    provider, bare = (model.split("/", 1) + [""])[:2] if "/" in model else ("", model)
    available = None
    if model and not session.degraded:
        available = bool(provider) and provider in session.catalog.providers_for(bare)
    return {
        "target": target,
        "kind": "agent" if target.startswith("agent:") else "cat",
        "name": session_mod.target_label(target),
        "model": model or None,
        "provider": provider or None,
        "bare": bare or None,
        "variant": variant,
        "assigned": bool(model),
        "available": available,   # None = unknown (degraded, or nothing assigned)
    }


def _configured_targets(session) -> list:
    """Every target the CONFIG mentions, in config order — including agents/categories omo
    doesn't define (a hand-edited or stale entry), which `show` marks `known: false` rather
    than hiding. Hiding them would let an agent 'fix' a config while a broken entry it never
    saw stayed put."""
    from omodel import session as session_mod
    out = []
    for name, data in session_mod.read_map(session.cfg, "agents").items():
        out.append(f"agent:{name}")
        if isinstance(data, dict):
            for kind in session_mod.SUBKINDS:
                if isinstance(data.get(kind), dict):
                    out.append(f"agent:{name}.{kind}")
    for name in session_mod.read_map(session.cfg, "categories"):
        out.append(f"cat:{name}")
    return out


def _all_targets(session) -> list:
    """Known targets first (pane order), then anything extra the config mentions."""
    known = session.known_targets()
    seen = set(known)
    return known + [t for t in _configured_targets(session) if t not in seen]


def _candidate_payload(session, target: str, index: int, row: dict, current: str) -> dict:
    """One candidate-row dict rendered for JSON.

    `entry` (the raw omo fallbackChain dict) is deliberately NOT exposed: it is omo's internal
    shape and publishing it would freeze omo's schema into omodel's output. `substitute_for`
    carries the only part a consumer needs. `value` is pre-assembled so a caller never builds
    `provider/model` itself."""
    value = f"{row['provider']}/{row['model']}"
    # `settable` closes the gap between what this list OFFERS and what `set` ACCEPTS. The list
    # includes the target's current assignment even when it is off-chain, so a row can be
    # unpickable — a GPT-only agent holding a non-GPT model, or a model whose provider you have
    # since disconnected — while the guide tells agents to use `value` verbatim. Rather than
    # hide what is configured, mark it.
    #
    # Delegates to `_validate` itself rather than re-deriving the conditions: hand-rolling it
    # covered gpt_only but missed availability, so an `unavailable` row advertised
    # `settable: true` and then exited 3. Routing through the real guard means the two cannot
    # drift again, and a future guard is picked up for free. `variant=None` because a bare
    # `set <target> <value>` passes no variant — the row's `variant` is a suggestion the caller
    # opts into, and `warn` already flags it when opencode disagrees.
    settable = _validate(session, target, value, None, force=False) is None
    return {
        "index": index,
        "source": row["source"],
        "provider": row["provider"],
        "model": row["model"],
        "value": value,
        "variant": row.get("variant"),
        "substitute_for": row.get("substitute_for"),
        "warn": list(row.get("warn") or []),
        "current": value == current,
        "settable": settable,
        "variants": session.variants_for(row["provider"], row["model"]),
    }


# ---------------------------------------------------------------------------
# Guards (shared by set / apply)
# ---------------------------------------------------------------------------

def _validate(session, target: str, value: str, variant, force: bool):
    """`None` if the assignment may be written, else `(error, message)`.

    Strictness (DESIGN §CLI): an unknown target or an unqualified value is never writable; a
    non-GPT model on a GPT-only agent is never writable EVEN WITH --force (omo's
    no-hephaestus-non-gpt hook would reassign the session, so the config could not take
    effect); an unavailable model or an invalid variant refuses but yields to --force.

    The variant check fires ONLY when opencode reports a non-empty set for this (provider,
    model). `variants_for` is cache-only and dedicated providers report `{}`, so an empty set
    means "no information", not "no variants" — refusing on silence would reject valid picks
    on a cold cache. Mirrors resolve._variant_warn (decision #14)."""
    from omodel import session as session_mod

    if not session.is_known(target):
        return ("unknown_target",
                f"{target!r} is not a target omo defines — run `omodel targets` for the list.")

    parts = _split_value(value)
    if parts is None:
        return ("bad_value",
                f"{value!r} is not a provider/model — use a `value` from `omodel candidates`.")
    provider, model = parts

    if session_mod.gpt_only(target) and not session_mod.is_gpt_model(model):
        label = session_mod.target_label(target)
        return ("gpt_only", (
            f"{label} only runs GPT models (omo reassigns the session otherwise) — "
            f"{model!r} cannot be set, and --force does not override this."
        ))

    if not force and not session.degraded and provider not in session.catalog.providers_for(model):
        return ("unavailable", (
            f"no connected provider serves {value!r} — run `omodel candidates {target}` "
            "for what you can run, or pass --force."
        ))

    # A non-string variant would be written into the JSONC verbatim (a nested object, a number)
    # — agent-supplied JSON makes that reachable, and no guard downstream catches it.
    if variant is not None and not isinstance(variant, str):
        return ("bad_input", f"variant must be a string or null, got {type(variant).__name__}")

    if not force and not _variant_offered(session, provider, model, variant):
        offered = _variant_guard_set(session, provider, model)
        return ("bad_variant", (
            f"{variant!r} is not a variant of {value!r} "
            f"(opencode reports: {', '.join(offered)}) — or pass --force."
        ))
    return None


class _StoreWriteFailed(Exception):
    """The presets file could not be written. `config_written` says whether the config already
    landed — if it did, the two files are out of step and a retry heals it, which is exactly
    what the caller has to tell the agent (app.py tells a human the same thing)."""

    def __init__(self, message: str, config_written: bool) -> None:
        super().__init__(message)
        self.config_written = config_written


def _publish(session, dry_run: bool):
    """Write both files (or, for a dry run, neither) and return the result payload fragment.

    Nothing to change → write NOTHING, mirroring the TUI's "Nothing to save." Without this gate
    a semantically no-op call still rewrites the file (`render` normalizes the agents/categories
    spans) and burns one of the 20 backup-ring slots — so an agent verifying its own work by
    re-running `set` or `apply` would evict the user's snapshots a call at a time.

    Config first, presets second (decision #17's order). If the presets write fails after the
    config landed, that is NOT a plain failure: the config is ahead of the store and a retry
    heals it, so it is raised as `_StoreWriteFailed` and reported distinctly."""
    diff = session.diff()
    store_dirty = session.store_is_dirty()
    if dry_run:
        return {"changed": bool(diff.strip()), "dry_run": True, "backup": None, "diff": diff}
    if not diff.strip() and not store_dirty:
        return {"changed": False, "dry_run": False, "backup": None, "diff": ""}

    result = None
    if diff.strip():
        result = session.save_config()
    try:
        session.write_store()
    except Exception as exc:
        raise _StoreWriteFailed(str(exc), config_written=result is not None) from exc
    return {
        "changed": bool(result.changed) if result is not None else store_dirty,
        "dry_run": False,
        "backup": result.backup if result is not None else None,
        "diff": diff,
    }


# ---------------------------------------------------------------------------
# Agent-facing subcommands
# ---------------------------------------------------------------------------

def _cmd_agent_guide() -> int:
    """Print the bundled agent contract. Read via importlib.resources so it works from the
    wheel AND the PyInstaller one-file binary — an agent on a user's machine has the binary and
    no repo checkout, which is the whole reason this verb exists."""
    from importlib.resources import files
    try:
        text = (files("omodel.data") / "agent-usage.md").read_text(encoding="utf-8")
    except Exception as exc:
        print(f"error: could not read the agent guide: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(text.rstrip("\n"))
    return EXIT_OK


def _cmd_targets(config_override, as_json: bool) -> int:
    session, rc = _open_session(config_override)
    if session is None:
        return rc
    targets = session.known_targets()
    _emit(
        {"targets": targets, "config_path": session.config_path,
         "sync_conflict": session.sync_conflict},
        as_json,
        lines=targets,
    )
    return EXIT_OK


def _cmd_show(config_override, as_json: bool) -> int:
    session, rc = _open_session(config_override)
    if session is None:
        return rc

    import omodel
    from omodel import presets as presets_mod

    rows = []
    known = set(session.known_targets())
    for target in _all_targets(session):
        row = _assignment_row(session, target)
        row["known"] = target in known
        rows.append(row)

    current = session.store.current()
    payload = {
        "omodel_version": omodel.__version__,
        "config_path": session.config_path,
        "degraded": session.degraded,
        "providers": list(session.catalog.connected),
        "active_preset": (
            {"index": session.store.active, "name": current.name} if current else None
        ),
        "presets": [
            {
                "index": i,
                "name": p.name,
                "models": presets_mod.model_count(p),
                "active": i == session.store.active,
            }
            for i, p in enumerate(session.store.presets)
        ],
        "sync_conflict": session.sync_conflict,
        "targets": rows,
    }
    if as_json:
        _emit(payload, True)
        return EXIT_OK

    # Prose form — same content as --print, which stays a supported alias.
    rc = _cmd_print(config_override)
    if session.sync_conflict:
        print(_SYNC_CONFLICT_NOTE)
    return rc


def _cmd_candidates(config_override, target: str, as_json: bool) -> int:
    session, rc = _open_session(config_override)
    if session is None:
        return rc
    from omodel import session as session_mod

    if not session.is_known(target):
        return _fail(
            "unknown_target",
            f"{target!r} is not a target omo defines — run `omodel targets` for the list.",
            as_json, target=target,
        )

    current_model, _ = session.assignment(target)
    rows = session.rows(target)
    cands = [
        _candidate_payload(session, target, i, row, current_model)
        for i, row in enumerate(rows)
    ]
    payload = {
        "target": target,
        "degraded": session.degraded,
        "sync_conflict": session.sync_conflict,
        "gpt_only": session_mod.gpt_only(target),
        "current": current_model or None,
        "candidates": cands,
    }
    lines = []
    if session.degraded:
        lines.append("(degraded: opencode unavailable — availability unknown)")
    for c in cands:
        marker = "*" if c["current"] else " "
        variant = f" ({c['variant']})" if c["variant"] else ""
        sub = f"  (~ omo {c['substitute_for']})" if c["substitute_for"] else ""
        warn = ("  ! " + " ".join(c["warn"])) if c["warn"] else ""
        blocked = "" if c["settable"] else "  [not settable: GPT-only agent]"
        lines.append(f"{marker} {c['index']:2d}. {c['value']}{variant}{sub}{warn}{blocked}")
    if not cands:
        lines.append("(no candidates)")
    _emit(payload, as_json, lines=lines)
    return EXIT_OK


def _cmd_check_json(config_override, as_json: bool) -> int:
    """`omodel check` — report config problems and exit 3 if any.

    Distinct from the flat `--check` flag, which resolves every target and ALWAYS exits 0 so CI
    can run it unconditionally. This one is for an agent verifying its own work."""
    session, rc = _open_session(config_override)
    if session is None:
        return rc
    from omodel import session as session_mod

    known = set(session.known_targets())
    problems = []
    # A present-but-not-a-dict `agents`/`categories` silently discards everything under it —
    # `read_map` keeps every surface from crashing, but "we survived it" is not "it's fine", and
    # check calling such a config healthy is how it stays broken.
    for key in ("agents", "categories"):
        raw = session.cfg.get(key)
        if raw is not None and not isinstance(raw, dict):
            problems.append({
                "target": None, "problem": "malformed_map",
                "message": (
                    f"{key!r} is {type(raw).__name__}, not an object — everything under it is "
                    "ignored; the next write replaces it with an empty object"
                ),
            })
    for target in _all_targets(session):
        row = _assignment_row(session, target)
        if target not in known:
            problems.append({
                "target": target, "problem": "unknown_target",
                "message": f"{row['name']!r} is not an agent/category omo defines",
            })
            continue
        if not row["assigned"]:
            continue
        if row["available"] is False:
            problems.append({
                "target": target, "problem": "unavailable",
                "message": f"no connected provider serves {row['model']!r}",
            })
        # The GPT-only lock is the one guard --force cannot open, so `check` must be able to see
        # it: a preset captured from a foreign config can re-install a non-GPT hephaestus that
        # `set` would have refused, and check calling that healthy was the gap.
        if session_mod.gpt_only(target) and not session_mod.is_gpt_model(row["bare"] or ""):
            problems.append({
                "target": target, "problem": "gpt_only",
                "message": (
                    f"{row['name']} only runs GPT models — {row['model']!r} cannot take effect "
                    "(omo reassigns the session)"
                ),
            })
        # NOT gated on `degraded`. Availability needs opencode on PATH; variant validity does
        # not — it comes from the CACHED `--verbose`, which `_variant_offered` reads either way
        # (and which returns "no information" as permissive). Gating it here made `check` and
        # `set` give opposite verdicts on the same file with opencode merely off PATH.
        if not _variant_offered(session, row["provider"], row["bare"], row["variant"]):
            problems.append({
                "target": target, "problem": "bad_variant",
                "message": f"{row['variant']!r} is not a variant of {row['model']!r}",
            })

    payload = {
        "ok": not problems,
        "config_path": session.config_path,
        "degraded": session.degraded,
        "sync_conflict": session.sync_conflict,
        "problems": problems,
    }
    # `target` is None for a whole-map problem (malformed_map), which belongs to the file rather
    # than to any one target — don't render it as "None: ...".
    lines = [
        f"{p['target']}: {p['problem']} — {p['message']}" if p["target"]
        else f"{p['problem']} — {p['message']}"
        for p in problems
    ]
    if not problems:
        # Don't claim a clean bill of health while a conflict is pending — the models are fine,
        # but the next write does something the caller has not agreed to.
        lines = ["No problems with your models." if session.sync_conflict
                 else "OK — no problems found."]
    # The prose surface must say this too. It was JSON-only, so a human running `omodel check`
    # after something else wrote their config was told "OK" while the payload said otherwise.
    if session.sync_conflict:
        lines.append(_SYNC_CONFLICT_NOTE)
    if session.degraded:
        lines.append("(degraded: opencode unavailable — availability was not checked)")
    _emit(payload, as_json, lines=lines)
    return EXIT_REJECTED if problems else EXIT_OK


def _cmd_set(config_override, target, value, variant, dry_run, force, as_json) -> int:
    session, rc = _open_session(config_override)
    if session is None:
        return rc

    # A whitespace-only --variant is junk that would otherwise be written verbatim; treat it as
    # "no variant" here rather than loosening session.is_no_variant (which the TUI shares).
    variant = variant.strip() if isinstance(variant, str) else variant

    problem = _validate(session, target, value, variant, force)
    if problem is not None:
        return _fail(problem[0], problem[1], as_json, target=target, value=value)

    provider, model = _split_value(value)
    before, _ = session.assignment(target)
    session.set_model(target, provider, model, variant)
    try:
        published = _publish(session, dry_run)
    except Exception as exc:
        return _fail_write(exc, as_json, target=target)

    payload = {
        "ok": True, "target": target, "from": before or None, "to": value,
        "variant": variant if not _is_none_variant(variant) else None,
        "warn": _warn_for(session, provider, model, variant),
        # Surfaced on every mutating verb, not just show/check: when the config matched no
        # preset, this write also resolved that conflict by adopting the config into the active
        # preset. That is a defensible resolution, but doing it without saying so is not — the
        # agent has to be able to report it.
        "sync_conflict": session.sync_conflict,
    }
    payload.update(published)
    _emit(payload, as_json, lines=_set_lines(target, before, value, variant, published))
    return EXIT_OK


def _cmd_clear(config_override, target, dry_run, as_json) -> int:
    session, rc = _open_session(config_override)
    if session is None:
        return rc
    if not session.is_known(target):
        return _fail(
            "unknown_target",
            f"{target!r} is not a target omo defines — run `omodel targets` for the list.",
            as_json, target=target,
        )
    before, _ = session.assignment(target)
    session.clear(target)
    try:
        published = _publish(session, dry_run)
    except Exception as exc:
        return _fail_write(exc, as_json, target=target)
    payload = {"ok": True, "target": target, "from": before or None, "to": None,
               "sync_conflict": session.sync_conflict}
    payload.update(published)
    verb = "would clear" if dry_run else "cleared"
    _emit(payload, as_json, lines=[f"{verb} {target}" if before else f"{target} was already unset"])
    return EXIT_OK


def _cmd_apply(config_override, dry_run, force, as_json) -> int:
    """Batch assignments from stdin, in ONE save.

    Preferred over repeated `set`: every save snapshots a backup and the ring keeps only the
    newest 20, so eleven individual sets evict eleven of the user's own snapshots. Validation
    is all-or-nothing — a single bad entry writes nothing, so a partial config never lands."""
    import json

    session, rc = _open_session(config_override)
    if session is None:
        return rc

    try:
        raw = json.loads(sys.stdin.read())
    except Exception as exc:
        return _fail("bad_input", f"stdin is not valid JSON: {exc}", as_json, code=EXIT_USAGE)
    if not isinstance(raw, dict):
        return _fail("bad_input",
                     'expected an object mapping target -> {"model": ..., "variant": ...}',
                     as_json, code=EXIT_USAGE)

    planned = []
    for target, spec in raw.items():
        if isinstance(spec, str):
            spec = {"model": spec}
        if not isinstance(spec, dict):
            return _fail("bad_input", f"{target!r}: expected an object or a model string",
                         as_json, code=EXIT_USAGE, target=target)
        value = spec.get("model")
        variant = spec.get("variant")
        variant = variant.strip() if isinstance(variant, str) else variant
        problem = _validate(session, target, value or "", variant, force)
        if problem is not None:
            # All-or-nothing: refuse the whole batch so a half-applied config never lands.
            return _fail(problem[0], problem[1], as_json, target=target, value=value)
        planned.append((target, value, variant))

    applied = []
    for target, value, variant in planned:
        provider, model = _split_value(value)
        before, _ = session.assignment(target)
        session.set_model(target, provider, model, variant)
        applied.append({"target": target, "from": before or None, "to": value,
                        "variant": variant if not _is_none_variant(variant) else None})

    try:
        published = _publish(session, dry_run)
    except Exception as exc:
        return _fail_write(exc, as_json)

    payload = {"ok": True, "applied": applied, "sync_conflict": session.sync_conflict}
    payload.update(published)
    verb = "would set" if dry_run else "set"
    _emit(payload, as_json,
          lines=[f"{verb} {a['target']} -> {a['to']}" for a in applied] or ["(nothing to apply)"])
    return EXIT_OK


def _cmd_preset(config_override, args, as_json: bool) -> int:
    session, rc = _open_session(config_override)
    if session is None:
        return rc
    from omodel import presets as presets_mod

    action = args.action
    name = args.name

    if action == "ls":
        current = session.store.current()
        payload = {
            "active": session.store.active if current else None,
            "presets": [
                {"index": i, "name": p.name, "models": presets_mod.model_count(p),
                 "active": i == session.store.active}
                for i, p in enumerate(session.store.presets)
            ],
        }
        lines = [
            f"{'*' if p['active'] else ' '} {p['index'] + 1}. {p['name']}  ({p['models']} models)"
            for p in payload["presets"]
        ]
        _emit(payload, as_json, lines=lines or ["(no presets)"])
        return EXIT_OK

    if not name:
        return _fail("bad_input", f"`omodel preset {action}` needs a name", as_json,
                     code=EXIT_USAGE)

    if action == "new":
        clean = presets_mod.sanitize_name(name, len(session.store.presets))
        session.store.presets.append(presets_mod.capture(clean, session.cfg))
        session.store.active = len(session.store.presets) - 1
        try:
            session.write_store()
        except Exception as exc:
            return _fail("write_failed", f"could not write presets: {exc}", as_json,
                         code=EXIT_ERROR)
        _emit({"ok": True, "action": "new", "name": clean,
               "index": session.store.active, "active": True},
              as_json, lines=[f"added preset '{clean}' (now active)"])
        return EXIT_OK

    index = session.preset_index(name)
    if index is None:
        return _fail("unknown_preset",
                     f"no preset named {name!r} — run `omodel preset ls`", as_json)

    if action == "use":
        if index == session.store.active:
            _emit({"ok": True, "action": "use", "name": session.store.presets[index].name,
                   "index": index, "changed": False},
                  as_json, lines=[f"already using '{session.store.presets[index].name}'"])
            return EXIT_OK
        preset = session.switch_preset(index)
        try:
            session.save_config()
            session.write_store()
        except Exception as exc:
            return _fail("write_failed", f"could not write: {exc}", as_json, code=EXIT_ERROR)
        _emit({"ok": True, "action": "use", "name": preset.name, "index": index,
               "changed": True},
              as_json, lines=[f"now using '{preset.name}'"])
        return EXIT_OK

    # rm — refused on the active preset, mirroring the TUI's `x`: the config equals the active
    # preset, so deleting it would leave the config matching nothing.
    if index == session.store.active:
        return _fail("active_preset",
                     f"'{session.store.presets[index].name}' is the one you're using — "
                     "switch to another first", as_json)
    removed = session.store.presets.pop(index)
    if session.store.active > index:
        session.store.active -= 1  # dense list: a delete renumbers everything after it
    try:
        # Pass the store EXPLICITLY rather than letting write_store default to
        # projected_store(). Projecting folds the live cfg into the active preset, which is
        # right for a save (your edits belong to the preset you're on) but catastrophic here
        # when the config matches no preset (sync_conflict): deleting preset A would silently
        # overwrite the CONTENT of preset B with whatever is in the config, and there is no
        # backup ring for .omodel-presets.json to recover from. `rm` must delete and nothing
        # else, leaving any conflict intact for the user to resolve deliberately.
        session.write_store(session.store)
    except Exception as exc:
        return _fail("write_failed", f"could not write presets: {exc}", as_json, code=EXIT_ERROR)
    _emit({"ok": True, "action": "rm", "name": removed.name},
          as_json, lines=[f"deleted preset '{removed.name}'"])
    return EXIT_OK


def _is_none_variant(variant) -> bool:
    from omodel.session import is_no_variant
    return is_no_variant(variant)


def _variant_offered(session, provider: str, model: str, variant) -> bool:
    """Is `variant` one opencode reports for (provider, model)?

    True when there is nothing to contradict it — an empty `variants_for` means "no
    information" (cache-only; dedicated providers report `{}`), never "no variants". The
    `.strip().lower()` normalization is shared by `_validate`, `check` and `_warn_for` on
    purpose: when they differed, a hand-edited `variant: " max "` was flagged by `check` and
    accepted by `set`.

    **`stale_ok=False` — the one caller that opts back into the 24h TTL.** This is a HARD guard
    (→ `bad_variant`, exit 3), and a refusal must not rest on a file of unbounded age: `set`
    rejecting a variant opencode added last week, on the strength of a cached set from before it
    existed, is worse than not checking. This surface never calls `catalog.detail()` either, so
    nothing re-warms the cache behind an agent — the wrong verdict would stick until a human ran
    `--refresh-models`. Expired → `[]` → "no information" → allow. The `⚠` marker and the pickers
    keep reading stale (see `Catalog.variants_for`): they annotate, they don't refuse."""
    if _is_none_variant(variant):
        return True
    offered = _variant_guard_set(session, provider, model)
    if not offered:
        return True
    return str(variant).strip().lower() in [v.strip().lower() for v in offered]


def _variant_guard_set(session, provider: str, model: str) -> list:
    """The set `_variant_offered` refuses on — and therefore the ONLY set a `bad_variant` message
    may quote. Keep the two reading the same call: a message naming a set the guard didn't use
    would tell an agent to retry with a variant that is about to be rejected again."""
    return session.variants_for(provider, model, stale_ok=False)


def _warn_for(session, provider, model, variant) -> list:
    """Warnings that survived the guards — i.e. what --force let through."""
    warn = []
    if not session.degraded and provider not in session.catalog.providers_for(model):
        warn.append("unavailable")
    if not _variant_offered(session, provider, model, variant):
        warn.append("variant")
    return warn


def _set_lines(target, before, value, variant, published) -> list:
    verb = "would set" if published["dry_run"] else "set"
    suffix = f" ({variant})" if variant and not _is_none_variant(variant) else ""
    line = f"{verb} {target} -> {value}{suffix}"
    if before:
        line += f"   (was {before})"
    return [line]


# ---------------------------------------------------------------------------
# Original flat-flag implementations (unchanged behavior)
# ---------------------------------------------------------------------------

def _cmd_refresh_models() -> int:
    import shutil

    from omodel.catalog import CatalogUnavailable
    from omodel.catalog import refresh as refresh_catalog

    if shutil.which("opencode") is None:
        print("error: `opencode` not found on PATH", file=sys.stderr)
        return EXIT_ERROR
    try:
        catalog = refresh_catalog()
    except CatalogUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    n_models = sum(len(v) for v in catalog.available.values())
    print(
        f"Refreshed {n_models} models across {len(catalog.connected)} providers; "
        "cache updated."
    )
    return EXIT_OK


def _cmd_restore(config_override: str | None) -> int:
    """List newest 10 backups + pinned original, prompt user, restore."""
    from omodel.config_io import config_path, list_backups, restore

    path = config_path(config_override)
    backups = list_backups(path)

    if not backups:
        print("No backups found.")
        return EXIT_OK

    print(f"Backups for: {path}")
    print()
    for i, b in enumerate(backups):
        tag = " [original]" if b.is_original else ""
        print(f"  {i + 1:2d}.  {b.name}{tag}  ({b.size} bytes)")

    print()
    try:
        choice = input("Restore which backup? (number, or q to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("Cancelled.")
        return EXIT_ERROR
    if choice.lower() in ("q", ""):
        print("Cancelled.")
        return EXIT_OK

    try:
        idx = int(choice) - 1
    except ValueError:
        print("Invalid choice.", file=sys.stderr)
        return EXIT_ERROR

    if idx < 0 or idx >= len(backups):
        print("Choice out of range.", file=sys.stderr)
        return EXIT_ERROR

    chosen = backups[idx]
    restore(path, chosen.name)
    print(f"Restored {chosen.name} to {path}")
    return EXIT_OK


def _cmd_print(config_override: str | None) -> int:
    """Resolve current agent/category models from config + suggestions/catalog, print."""
    from omodel.catalog import CatalogUnavailable
    from omodel.catalog import load as load_catalog
    from omodel.config_io import ConfigParseError, load_config

    try:
        cfg, path = load_config(config_override)
    except ConfigParseError as exc:
        _print_config_parse_error(exc)
        return EXIT_ERROR

    try:
        catalog = load_catalog()
    except CatalogUnavailable as exc:
        print(f"[warn] Could not load catalog: {exc}", file=sys.stderr)
        from omodel.catalog import Catalog
        catalog = Catalog(available={}, connected=[])

    # read_map, not `cfg.get(k, {})` — the default only applies when the key is ABSENT, so a
    # present `"agents": null` (or a truthy non-dict) returned None and tracebacked on .items().
    from omodel.session import read_map

    agents_cfg = read_map(cfg, "agents")
    categories_cfg = read_map(cfg, "categories")

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

    return EXIT_OK


def _cmd_check(config_override: str | None) -> int:
    """Dry-run: resolve candidate lists for every known target, CI-safe, always exit 0.
    Degrades gracefully if opencode is absent (suggestions-only).

    ALWAYS exit 0 — that is the contract CI depends on. `omodel check` (the subcommand) is the
    one that exits 3 on a problem."""
    from omodel.catalog import CatalogUnavailable
    from omodel.catalog import load as load_catalog
    from omodel.resolve import Resolver
    from omodel.suggestions import load as load_suggestions

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

    try:
        resolver = Resolver.build(catalog, suggestions)
    except Exception as exc:  # ironclad: --check must exit 0 (CI-safe)
        print(f"[check] Could not build resolver ({exc}); suggestions-only.", file=sys.stderr)
        return EXIT_OK

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

    return EXIT_OK
