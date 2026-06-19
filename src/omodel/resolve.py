"""Core resolution: prefix (prefer-dedicated), variant defaulting, candidate assembly.
DESIGN.md §resolve.py.  Depends on catalog.Catalog + suggestions.Suggestions.

FROZEN CONTRACT — owned by the Core-logic specialist. The candidate-row dict yielded by
`candidates()` is the central shared shape (see CONTRACTS.md) consumed verbatim by app.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .catalog import Catalog
from .suggestions import Suggestions, normalize_model_id, vendor


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
        """One pick list of candidate-row dicts — a single filtered pass over `target`'s
        fallbackChain (CONTRACTS.md / DESIGN §candidates). For each entry, in chain order:
          1. EXACT — a connected provider serves the entry's model verbatim → that model,
             provider via resolve_prefix (dedicated-first); substitute_for=None.
          2. SAME-LINE — else the newest connected model of the SAME detect_family
             (version-agnostic: glm-5 → glm-5.1), provider dedicated-first;
             substitute_for=<the omo model id>. A same-line model that is itself an
             exactly-available chain entry is NOT used here (its own entry shows it exact).
          3. else SKIP — neither exact nor same-line connected (truly unavailable: hidden).
        Rows are deduped by resolved 'provider/model' (higher-priority entry wins).
        Every row is source 'omo'; warn ⊆ {'variant'}. Does NOT append `+ add model…`.
        `target` is a §Data-contracts id: 'agent:<n>' | 'agent:<n>.ultrawork' |
        'agent:<n>.compaction' | 'cat:<n>'."""
        requirement = self._requirement_for(target)
        if requirement is None:
            return []

        req_top_variant = requirement.get("variant")  # top-level variant (presently always empty)
        fallback_chain = requirement.get("fallbackChain", [])

        # Models that appear EXACTLY in this chain AND are connected: never used as a
        # same-line substitute — each is represented by its own (exact) entry instead.
        exact_chain_models = {
            e["model"] for e in fallback_chain
            if e.get("model") and self.catalog.providers_for(e["model"])
        }

        rows: list = []
        seen_keys: set = set()  # resolved 'provider/model' — dedup within the chain

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

            # 1. Exact → 2. same-line substitute → 3. skip.
            if self.catalog.providers_for(model_id):
                resolved_model = model_id
                provider = self.resolve_prefix(model_id, "omo", entry=entry)
                substitute_for = None
            else:
                resolved_model = self._same_line_match(model_id, exclude=exact_chain_models)
                if resolved_model is None:
                    continue
                provider = self.resolve_prefix(resolved_model, "omo", entry=None)
                substitute_for = model_id

            key = f"{provider}/{resolved_model}"
            if key in seen_keys:
                continue
            seen_keys.add(key)

            # warn: variant only (unavailable entries are skipped, never shown).
            warn: list = []
            if variant is not None:
                fam = self.suggestions.detect_family(resolved_model)
                if fam is not None and variant not in fam.variants:
                    warn.append("variant")

            rows.append({
                "source": "omo",
                "model": resolved_model,
                "provider": provider,
                "variant": variant,
                "entry": entry,
                "substitute_for": substitute_for,
                "warn": warn,
            })

        return rows

    def _same_line_match(self, model_id: str, exclude: "frozenset" = frozenset()) -> "Optional[str]":
        """Newest connected model sharing `model_id`'s detect_family, else None.
        'Newest' = highest version tuple (digit groups in the normalized id); ties resolve
        to first-seen (catalog order). Returns None when the omo model has no family (never
        substitute blindly across unknown ids) or no connected model shares its family.
        `exclude` drops connected ids that are themselves exact chain entries."""
        target_fam = self.suggestions.detect_family(model_id)
        if target_fam is None:
            return None
        same: list = []
        seen: set = set()
        for prov in self.catalog.connected:
            for m in self.catalog.available.get(prov, []):
                if m in seen or m in exclude:
                    continue
                fam = self.suggestions.detect_family(m)
                if fam is not None and fam.family == target_fam.family:
                    seen.add(m)
                    same.append(m)
        if not same:
            return None
        # max() returns the FIRST maximal element → first-seen tie-break (same is first-seen).
        return max(same, key=self._version_key)

    @staticmethod
    def _version_key(model_id: str) -> tuple:
        """Digit groups of the normalized id as an int tuple, for 'newest' comparison:
        'glm-5.1' → (5, 1) > 'glm-5' → (5,) > 'glm-4.6' → (4, 6)."""
        return tuple(int(n) for n in re.findall(r"\d+", normalize_model_id(model_id)))

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
