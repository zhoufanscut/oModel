"""omodel --refresh: regenerate suggestion JSON via bun + an omo checkout.  DESIGN.md §refresh.py.

FROZEN CONTRACT — owned by the CLI+packaging specialist (runs the Core-owned
tools/snapshot_omo.ts). Non-fatal when omo src or bun is missing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from importlib.resources import files

# Generous budget for the weekly CI refresh job: bun must never hang forever (AGENTS.md —
# every subprocess call carries a timeout).
_BUN_TIMEOUT = 300


def refresh(omo_src: str = None) -> int:
    """Locate omo src: `omo_src` (= --omo-src) | $OMO_SRC | ~/source/oh-my-openagent
    (needs packages/model-core/src). Runner: bun ONLY (no node fallback — verified broken).
    Run bundled tools/snapshot_omo.ts → JSON; write to a writable repo checkout
    (src/omodel/data/) if present, else $XDG_DATA_HOME/omodel/omo-suggestions.json.
    Missing omo src OR bun → NON-FATAL: print the current bundled `meta`, keep bundled data.
    Returns the process exit code."""

    # --- Locate omo source ---
    if omo_src is None:
        omo_src = os.environ.get("OMO_SRC")
    if omo_src is None:
        omo_src = os.path.expanduser("~/source/oh-my-openagent")

    model_core = os.path.join(omo_src, "packages", "model-core", "src")
    omo_ok = os.path.isdir(model_core)

    if not omo_ok:
        print(
            f"[refresh] omo source not found at {omo_src!r} "
            f"(needs packages/model-core/src). Non-fatal — keeping bundled data."
        )
        _print_bundled_meta()
        return 0

    # --- Locate bun ---
    bun_bin = shutil.which("bun")
    if bun_bin is None:
        print(
            "[refresh] `bun` not found on PATH. Non-fatal — keeping bundled data."
        )
        _print_bundled_meta()
        return 0

    # --- Locate bundled snapshot_omo.ts ---
    ts_resource = files("omodel.tools") / "snapshot_omo.ts"
    # importlib.resources may return a Path or a non-filesystem traversable; materialize
    # it to a real filesystem path so bun can open it. ts_temp_path tracks that materialized
    # copy (stays None when ts_resource was already a plain path) so it can be removed once
    # bun is done with it below — never the real bundled tools/snapshot_omo.ts.
    ts_temp_path = None
    try:
        ts_path = str(ts_resource)
        # Verify it's accessible as a file
        if not os.path.isfile(ts_path):
            raise FileNotFoundError(ts_path)
    except (TypeError, FileNotFoundError):
        # If the resource is not a plain-filesystem path (e.g. inside a zipimport or
        # a PyInstaller bundle), extract it to a temp file.
        import tempfile
        ts_text = ts_resource.read_text(encoding="utf-8")
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".ts",
            delete=False,
            encoding="utf-8",
        )
        tmp.write(ts_text)
        tmp.close()
        ts_path = tmp.name
        ts_temp_path = ts_path

    try:
        # --- Run bun snapshot_omo.ts <omo_src> ---
        try:
            result = subprocess.run(
                [bun_bin, "run", ts_path, omo_src],
                capture_output=True,
                text=True,
                timeout=_BUN_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            print(
                f"[refresh] `bun` timed out after {_BUN_TIMEOUT}s. "
                "Non-fatal — keeping bundled data."
            )
            _print_bundled_meta()
            return 0
        except Exception as exc:
            print(f"[refresh] Failed to run bun: {exc}. Non-fatal — keeping bundled data.")
            _print_bundled_meta()
            return 0
    finally:
        if ts_temp_path is not None:
            try:
                os.remove(ts_temp_path)
            except OSError:
                pass

    if result.returncode != 0:
        print(
            f"[refresh] bun exited with code {result.returncode}. "
            "Non-fatal — keeping bundled data."
        )
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        _print_bundled_meta()
        return 0

    # Validate the output is JSON
    stdout = result.stdout.strip()
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        print(f"[refresh] bun output is not valid JSON ({exc}). Non-fatal — keeping bundled data.")
        _print_bundled_meta()
        return 0

    # --- Determine write target ---
    out_path = _resolve_write_target()

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2, ensure_ascii=False)
        f.write("\n")

    meta = parsed.get("meta", {})
    print(
        f"[refresh] Written to {out_path!r}. "
        f"omo {meta.get('omoVersion', '?')} @ {meta.get('omoCommit', '?')[:7]} "
        f"({meta.get('generatedAt', '?')})"
    )
    return 0


def _resolve_write_target() -> str:
    """Return the path to write omo-suggestions.json.
    Prefer the repo checkout src/omodel/data/ if it is writable (maintainer mode);
    else fall back to $XDG_DATA_HOME/omodel/omo-suggestions.json."""
    # A PyInstaller --onefile build extracts to an ephemeral _MEIPASS tempdir; __file__ (and
    # its "writable" sibling data/ dir) resolves inside it and vanishes on process exit, so a
    # frozen build must never be mistaken for a maintainer checkout — go straight to the XDG
    # fallback below instead of even looking at the repo-checkout branch.
    if not getattr(sys, "frozen", False):
        # Walk up from this file to find a src/omodel/data/ that is writable
        this_file = os.path.abspath(__file__)
        # This file is at <repo>/src/omodel/refresh.py when installed editable;
        # try the sibling data/ directory.
        repo_data = os.path.join(os.path.dirname(this_file), "data", "omo-suggestions.json")
        # Check if the data directory is writable
        data_dir = os.path.dirname(repo_data)
        if os.path.isdir(data_dir) and os.access(data_dir, os.W_OK):
            return repo_data

    # Fall back to XDG_DATA_HOME/omodel/omo-suggestions.json (user override)
    xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return os.path.join(xdg_data, "omodel", "omo-suggestions.json")


def _print_bundled_meta() -> None:
    """Print the meta block from the currently bundled omo-suggestions.json."""
    try:
        from omodel.suggestions import load as load_suggestions
        suggestions = load_suggestions()
        meta = suggestions.meta
        print(
            f"[refresh] Bundled data: omo {meta.get('omoVersion', '?')} "
            f"@ {meta.get('omoCommit', '?')[:7]} "
            f"(generated {meta.get('generatedAt', '?')})"
        )
    except Exception as exc:
        print(f"[refresh] Could not read bundled meta: {exc}", file=sys.stderr)
