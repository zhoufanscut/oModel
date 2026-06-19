"""Core resolution: prefix (prefer-dedicated), variant defaulting, candidate assembly.
DESIGN.md §resolve.py.  Depends on catalog.Catalog + suggestions.Suggestions.

FROZEN CONTRACT — owned by the Core-logic specialist. The candidate-row dict yielded by
`candidates()` is the central shared shape (see CONTRACTS.md) consumed verbatim by app.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import Catalog
from .suggestions import Suggestions


@dataclass
class Resolver:
    catalog: Catalog
    suggestions: Suggestions
    gateways: set = field(default_factory=set)  # {p in connected : vendors_served(p) >= 2}; set in build()

    @classmethod
    def build(cls, catalog: Catalog, suggestions: Suggestions) -> "Resolver":
        """Construct and compute `gateways` once:
        gateways = {p for p in catalog.connected if vendors_served(p) >= 2}."""
        raise NotImplementedError

    def vendors_served(self, provider: str) -> int:
        """len({ vendor(suggestions.detect_family(m)) for m in catalog.available[provider] } - {None}).
        Data-driven gateway test: >= 2 distinct vendors ⇒ gateway, else dedicated."""
        raise NotImplementedError

    def resolve_prefix(self, model_id: str, source: str, entry: dict = None):
        """Dedicated-first → resolved provider id (str) or None if unavailable.
          * source == 'mine'  → providers_for(model_id)[0] (first-seen).
          * else: cands = providers_for(model_id);
              dedicated = [p for p in cands if p not in gateways] → dedicated[0] if any;
              elif cands → first of entry['providers'] that is IN cands, else cands[0];
              else (no connected provider serves it) → None.
        Both branches range over providers_for (availability IDs), NEVER raw omo IDs.
        The UI `p` key cycles the prefix across ALL of cands (override; saved = shown)."""
        raise NotImplementedError

    def candidates(self, target: str) -> list:
        """One pick list of candidate-row dicts (CONTRACTS.md / DESIGN §candidates):
          1. EVERY fallbackChain entry of `target` as a ★ row, in chain order, resolved
             provider/model + variant. Variant precedence: entry 'variant' → requirement
             top-level 'variant' → None (registry validates only, designates no default).
          2. Then ✓ all connected-provider models, DEDUPED against ★ by resolved 'provider/model'.
        Row tags: ['★'] omo · ['✓'] mine · ['★','✓'] both. warn ⊆ {'unavailable','variant'}.
        Does NOT append the `+ add model…` row (app.py adds cand:add).
        `target` is a §Data-contracts id: 'agent:<n>' | 'agent:<n>.ultrawork' |
        'agent:<n>.compaction' | 'cat:<n>'."""
        raise NotImplementedError
