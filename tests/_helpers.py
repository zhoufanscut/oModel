"""Shared test helpers (imported as `from _helpers import …` — pytest's default prepend
import mode puts this directory on sys.path, no package/__init__.py needed).

One canonical builder for the fake `opencode models <prov> --verbose` cache blob, so the
format lives in exactly one place — it was previously copy-pasted across three test files,
and a change to opencode's verbose shape needed three coordinated edits.
"""
from __future__ import annotations

import json

from omodel import cache


def seed_verbose(provider: str, records: dict) -> None:
    """Seed a cached `verbose-<provider>` entry so Catalog.variants_for()/detail() read it
    without a subprocess (the suite stubs opencode + isolates the cache dir via conftest).
    `records` maps model_id -> [variant, ...]; mirrors `opencode models <prov> --verbose`: a
    `provider/model` header line followed by a JSON block whose `variants` is an OBJECT keyed
    by variant name. An empty list → `variants: {}` — opencode's 'no variants' shape (kimi)."""
    parts = []
    for model, variants in records.items():
        parts.append(f"{provider}/{model}")
        parts.append(json.dumps({"id": model, "variants": {v: {} for v in variants}}))
    cache.write(
        f"verbose-{provider}",
        "\n".join(parts) + "\n",
        ["opencode", "models", provider, "--verbose"],
    )
