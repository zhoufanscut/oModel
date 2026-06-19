"""Core resolution: prefix (prefer-dedicated), variant defaulting, candidate assembly.
DESIGN.md §resolve.py.  Depends on catalog.Catalog + suggestions.Suggestions.

FROZEN CONTRACT — owned by the Core-logic specialist. The candidate-row dict yielded by
`candidates()` is the central shared shape (see CONTRACTS.md) consumed verbatim by app.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .catalog import Catalog
from .suggestions import Suggestions, vendor


@dataclass
class Resolver:
    catalog: Catalog
    suggestions: Suggestions
    gateways: set = field(default_factory=set)  # {p in connected : vendors_served(p) >= 2}; set in build()

    @classmethod
    def build(cls, catalog: Catalog, suggestions: Suggestions) -> "Resolver":
        """Construct and compute `gateways` once:
        gateways = {p for p in catalog.connected if vendors_served(p) >= 2}."""
        resolver = cls(catalog=catalog, suggestions=suggestions)
        resolver.gateways = {
            p for p in catalog.connected if resolver.vendors_served(p) >= 2
        }
        return resolver

    def vendors_served(self, provider: str) -> int:
        """len({ vendor(suggestions.detect_family(m)) for m in catalog.available[provider] } - {None}).
        Data-driven gateway test: >= 2 distinct vendors ⇒ gateway, else dedicated."""
        models = self.catalog.available.get(provider, [])
        vendors = set()
        for m in models:
            v = vendor(self.suggestions.detect_family(m))
            if v is not None:
                vendors.add(v)
        return len(vendors)

    def resolve_prefix(self, model_id: str, source: str, entry: dict = None) -> "Optional[str]":
        """Dedicated-first → resolved provider id (str) or None if unavailable.
          * source == 'mine'  → providers_for(model_id)[0] (first-seen).
          * else: cands = providers_for(model_id);
              dedicated = [p for p in cands if p not in gateways] → dedicated[0] if any;
              elif cands → first of entry['providers'] that is IN cands, else cands[0];
              else (no connected provider serves it) → None.
        Both branches range over providers_for (availability IDs), NEVER raw omo IDs.
        The UI `p` key cycles the prefix across ALL of cands (override; saved = shown)."""
        if source == "mine":
            cands = self.catalog.providers_for(model_id)
            if cands:
                return cands[0]
            return None

        cands = self.catalog.providers_for(model_id)
        if not cands:
            return None

        dedicated = [p for p in cands if p not in self.gateways]
        if dedicated:
            return dedicated[0]

        # Only gateways serve it — use entry['providers'] tie-break then first-seen.
        if entry:
            entry_providers = entry.get("providers", [])
            cands_set = set(cands)
            for ep in entry_providers:
                if ep in cands_set:
                    return ep
        return cands[0]

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
        # Resolve the requirement dict for this target.
        requirement = self._requirement_for(target)
        if requirement is None:
            return []

        req_top_variant = requirement.get("variant")  # top-level variant (presently always empty)
        fallback_chain = requirement.get("fallbackChain", [])

        # Phase 1: ★ rows from the fallbackChain.
        star_rows: list = []
        # Track resolved 'provider/model' keys to detect ★✓ overlaps.
        star_keys: set = set()

        for entry in fallback_chain:
            model_id = entry.get("model", "")
            if not model_id:
                continue

            # Variant precedence: entry 'variant' → req top-level variant → None.
            entry_variant = entry.get("variant")
            if entry_variant:
                variant = entry_variant
            elif req_top_variant:
                variant = req_top_variant
            else:
                variant = None

            provider = self.resolve_prefix(model_id, "omo", entry=entry)

            # Build warn list.
            warn = []
            if not self.catalog.providers_for(model_id):
                warn.append("unavailable")
                # For unavailable omo rows, fall back to entry['providers'][0] if provider is None.
                if provider is None:
                    eps = entry.get("providers", [])
                    if eps:
                        provider = eps[0]

            if variant is not None:
                fam = self.suggestions.detect_family(model_id)
                if fam is not None and variant not in fam.variants:
                    warn.append("variant")

            key = f"{provider}/{model_id}" if provider else f"/{model_id}"
            star_keys.add(key)

            star_rows.append({
                "source": "omo",
                "model": model_id,
                "provider": provider,
                "variant": variant,
                "entry": entry,
                "tags": ["★"],
                "warn": warn,
            })

        # Phase 2: ✓ rows from connected providers, deduped against ★.
        mine_rows: list = []
        for prov in self.catalog.connected:
            for model_id in self.catalog.available.get(prov, []):
                # Resolve 'mine' prefix (always the serving provider).
                provider = prov
                key = f"{provider}/{model_id}"
                if key in star_keys:
                    # Upgrade the existing ★ row to ★✓.
                    for row in star_rows:
                        if (
                            row["provider"] == provider
                            and row["model"] == model_id
                        ):
                            if "✓" not in row["tags"]:
                                row["tags"] = ["★", "✓"]
                            break
                    continue

                # New ✓-only row.
                variant = None  # mine rows carry no variant by default
                warn = []
                mine_rows.append({
                    "source": "mine",
                    "model": model_id,
                    "provider": provider,
                    "variant": variant,
                    "entry": None,
                    "tags": ["✓"],
                    "warn": warn,
                })

        return star_rows + mine_rows

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _requirement_for(self, target: str) -> "Optional[dict]":
        """Look up the requirement dict for `target` in agents/categories."""
        if target.startswith("agent:"):
            rest = target[len("agent:"):]
            # Sub-target agent:<name>.ultrawork|.compaction inherits the PARENT agent's
            # requirement (omo defines no separate sub-requirement — verified 0 refs in
            # model-core source). Only the write destination differs; pick list is identical.
            agent_name = rest.split(".", 1)[0]
            return self.suggestions.agents.get(agent_name)
        elif target.startswith("cat:"):
            cat_name = target[len("cat:"):]
            return self.suggestions.categories.get(cat_name)
        return None
