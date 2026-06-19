"""omodel --refresh: regenerate suggestion JSON via bun + an omo checkout.  DESIGN.md §refresh.py.

FROZEN CONTRACT — owned by the CLI+packaging specialist (runs the Core-owned
tools/snapshot_omo.ts). Non-fatal when omo src or bun is missing.
"""
from __future__ import annotations


def refresh(omo_src: str = None) -> int:
    """Locate omo src: `omo_src` (= --omo-src) | $OMO_SRC | ~/source/oh-my-openagent
    (needs packages/model-core/src). Runner: bun ONLY (no node fallback — verified broken).
    Run bundled tools/snapshot_omo.ts → JSON; write to a writable repo checkout
    (src/omodel/data/) if present, else $XDG_DATA_HOME/omodel/omo-suggestions.json.
    Missing omo src OR bun → NON-FATAL: print the current bundled `meta`, keep bundled data.
    Returns the process exit code."""
    raise NotImplementedError
