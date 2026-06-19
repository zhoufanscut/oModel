"""Availability from the `opencode` CLI.  DESIGN.md §catalog.py / §Data sources.

FROZEN CONTRACT — owned by the Core-logic specialist. Implement the bodies; do not
change the public signatures or the `Catalog` shape without the Lead reconciling
CONTRACTS.md (app.py and resolve.py depend on these exact shapes).
"""
from __future__ import annotations

from dataclasses import dataclass


class CatalogUnavailable(Exception):
    """`opencode` IS on PATH but `opencode models` exited != 0 or produced zero
    `provider/model` lines. (opencode MISSING from PATH is NOT this — `load()` returns
    an empty Catalog so the UI degrades to suggestions/add-model only.) The UI shows a
    "couldn't read models" banner + `r` retry."""


@dataclass
class Catalog:
    available: dict  # {provider: [model_id, ...]} — FIRST-SEEN order
    connected: list  # [provider, ...] — FIRST-SEEN order, NEVER a set

    def providers_for(self, model_id: str) -> list:
        """Connected providers that serve `model_id`, in first-seen order."""
        raise NotImplementedError

    def detail(self, model_id: str):
        """On-demand `opencode models <prov> --verbose` for the RESOLVED provider
        (providers_for(model_id)[0]); brace-count + json.loads each record; pick the one
        whose header == `<prov>/<model_id>`. Returns a DISPLAY-ONLY dict
        {"context": int|None, "cost": {...}, "reasoning": bool, "image": bool} or None.
        NEVER read `--verbose.variants`/`.family` (decision #14)."""
        raise NotImplementedError


def load(opencode_bin: str = "opencode") -> Catalog:
    """`opencode models` → Catalog. Split each line on the FIRST `/`.
    Error rule (DESIGN §Data sources — the single definition, used by catalog.load too):
      * `opencode` not on PATH        → return Catalog(available={}, connected=[])
      * exit != 0 OR zero lines parsed → raise CatalogUnavailable
    Tests must NOT hard-assert the model count (varies)."""
    raise NotImplementedError
