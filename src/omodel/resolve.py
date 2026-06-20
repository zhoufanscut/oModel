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

# Hardcoded omo-id aliases — oModel deliberately OVERRIDES omo here (omo has no such table).
# `k2p5` is a provider's dot-free spelling of kimi-k2.5 ("p" = the decimal point), but omo's
# detect heuristic files any `k2…p<digit>` id under the kimi-THINKING family (see suggestions.py
# kimi-thinking pattern), which would otherwise route it to a kimi-k2-thinking model. We treat
# k2p5 as exactly kimi-k2.5 so it exact-matches your kimi-k2.5 and never pulls in a thinking
# model. Applied to fallbackChain entry ids in candidates() ONLY — detect_family() and
# normalize_model_id() stay a faithful port of omo.
_OMO_MODEL_ALIASES = {"k2p5": "kimi-k2.5"}


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
        candidates() no longer calls this (it shows EVERY serving provider via
        _ordered_providers); kept for the add-model modal's bare-id auto-prefix."""
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

    def _ordered_providers(self, model_id: str) -> list:
        """Connected providers serving `model_id`, dedicated-first: every single-vendor
        (dedicated) provider — first-seen — before every aggregator/gateway
        (vendors_served >= 2), also first-seen. [] when no connected provider serves it.
        candidates() emits ONE ROW per provider in this order, so you pick the prefix by
        choosing the row (e.g. openai/gpt-5.5 before opencode/gpt-5.5)."""
        cands = self.catalog.providers_for(model_id)
        dedicated = [p for p in cands if p not in self.gateways]
        gateways = [p for p in cands if p in self.gateways]
        return dedicated + gateways

    def candidates(self, target: str) -> list:
        """One pick list of candidate-row dicts — a single filtered pass over `target`'s
        fallbackChain (CONTRACTS.md / DESIGN §candidates). For each entry, in chain order:
          1. EXACT — the entry's model is served verbatim by >= 1 connected provider → that
             model; substitute_for=None.
          2. SAME-LINE — else the newest connected model of the SAME detect_family
             (version-agnostic: glm-5 → glm-5.1); substitute_for=<the omo model id>. If that
             newest same-line model is itself an exactly-available chain entry, this entry is
             SKIPPED (deferred to that model's own exact row) — never demoted to an OLDER one.
          3. else SKIP — neither exact nor same-line connected (truly unavailable: hidden).
        For the resolved model, ONE ROW PER serving provider is emitted, ordered
        dedicated-first (every single-vendor provider before any aggregator/gateway,
        first-seen within each tier — see _ordered_providers). So a model served by both a
        dedicated provider and an aggregator shows TWO rows (e.g. openai/gpt-5.5 then
        opencode/gpt-5.5) and you pick whichever you want.
        Rows are deduped by resolved 'provider/model' (higher-priority entry/provider wins).
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
        # Entry ids run through _canonical_omo_id first (k2p5 → kimi-k2.5; see _OMO_MODEL_ALIASES).
        exact_chain_models = {
            self._canonical_omo_id(e["model"]) for e in fallback_chain
            if e.get("model") and self.catalog.providers_for(self._canonical_omo_id(e["model"]))
        }

        rows: list = []
        seen_keys: set = set()  # resolved 'provider/model' — dedup within the chain

        for entry in fallback_chain:
            model_id = self._canonical_omo_id(entry.get("model", ""))
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
                substitute_for = None
            else:
                resolved_model = self._same_line_match(model_id)
                # No same-line model, OR the newest one is itself an exactly-available chain
                # entry: skip this entry (defer to that model's own exact row) rather than
                # demote to an OLDER same-line model. Surfacing a strictly-worse version as
                # this entry's substitute would be misleading (e.g. minimax-m3 → m2.7, the
                # newest you have, not m2.5 just because m2.7 also has its own chain entry).
                if resolved_model is None or resolved_model in exact_chain_models:
                    continue
                substitute_for = model_id

            # warn: variant only (unavailable entries are skipped, never shown). Identical for
            # every provider of this model, so compute it once and copy onto each row.
            warn: list = []
            if variant is not None:
                fam = self.suggestions.detect_family(resolved_model)
                if fam is not None and variant not in fam.variants:
                    warn.append("variant")

            # One row per serving provider, dedicated (single-vendor) before aggregator
            # (gateway). You pick the prefix by choosing the row — no `p` cycling.
            for provider in self._ordered_providers(resolved_model):
                key = f"{provider}/{resolved_model}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                rows.append({
                    "source": "omo",
                    "model": resolved_model,
                    "provider": provider,
                    "variant": variant,
                    "entry": entry,
                    "substitute_for": substitute_for,
                    "warn": list(warn),
                })

        return rows

    def _same_line_match(self, model_id: str) -> "Optional[str]":
        """Newest connected model sharing `model_id`'s detect_family, else None.
        'Newest' = highest version tuple (digit groups in the normalized id); ties resolve
        to first-seen (catalog order). Returns None when the omo model has no family (never
        substitute blindly across unknown ids) or no connected model shares its family.
        Returns the TRUE newest including chain entries — `candidates()` decides whether that
        match (when it is itself an exact chain entry) means 'defer to its own exact row'."""
        target_fam = self.suggestions.detect_family(model_id)
        if target_fam is None:
            return None
        same: list = []
        seen: set = set()
        for prov in self.catalog.connected:
            for m in self.catalog.available.get(prov, []):
                if m in seen:
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
    def _canonical_omo_id(model_id: str) -> str:
        """Apply a hardcoded omo-id alias (see _OMO_MODEL_ALIASES); identity if none.
        Lets oModel treat e.g. omo's `k2p5` as exactly `kimi-k2.5`."""
        return _OMO_MODEL_ALIASES.get(model_id, model_id)

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
