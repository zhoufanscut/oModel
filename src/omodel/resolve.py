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

# A trailing pure-digit token of >= this many digits is a compact date/build STAMP (provider
# noise, e.g. claude-haiku-4-5-20251001), not a version number (real versions are 1-2 digits) —
# so it is stripped when matching an available id to an omo chain id. The floor keeps `glm-5.1`
# from matching the bare `glm-5`: the `1` remainder is too short to be a stamp, so it stays a
# genuine version bump (a DIFFERENT model) and falls through to same-line substitution instead.
_STAMP_MIN_DIGITS = 6

# A 4-digit year opens a HYPHENATED date stamp (gpt-5.5-2026-04-24 → tokens 2026/04/24): the year
# plus its trailing 1-2 digit month/day tokens are all noise. Bounded to 19xx/20xx so a stray
# 4-digit version-like token isn't mistaken for a year (no real model version is a 4-digit year).
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")

# omo lumps EVERY non-opus Claude into one detect_family, `claude-non-opus`: its sibling
# `claude-opus` family keys on the literal "-opus" substring and is tested first, so haiku,
# sonnet, AND newer lines (fable, mythos, …) all fall through to here. That family is fine for
# omo's variant/thinking config but too coarse to be a substitution
# "line": a haiku slot must never be filled by a sonnet, nor a fable by a mythos. _same_line_match
# adds a LINE guard for this family only — a Claude carve-out (like _OMO_MODEL_ALIASES; omo has no
# such notion). Every other family maps 1:1 to a product line, so they are unaffected.


def _claude_line(model_id: str) -> "Optional[str]":
    """The Claude product-line token in `model_id` — the FIRST non-numeric token after `claude`
    (haiku/sonnet/opus/fable/mythos/…), else None. Data-free, so any future Claude line is
    distinguished automatically. Handles both id orders: claude-sonnet-4-6 and claude-3-5-sonnet."""
    after_claude = False
    for tok in normalize_model_id(model_id).split("-"):
        if tok == "claude":
            after_claude = True
        elif after_claude and tok and not tok.isdigit():
            return tok
    return None


@dataclass
class Resolver:
    catalog: Catalog
    suggestions: Suggestions
    gateways: set = field(default_factory=set)  # {p in connected : vendors_served(p) >= 2}; set in build()
    real_tokens: set = field(default_factory=set)  # non-digit tokens omo uses in chain ids; set in build()

    @classmethod
    def build(cls, catalog: Catalog, suggestions: Suggestions) -> "Resolver":
        """Construct and compute `gateways` once:
        gateways = {p for p in catalog.connected if vendors_served(p) >= 2}."""
        resolver = cls(catalog=catalog, suggestions=suggestions)
        resolver.gateways = {
            p for p in catalog.connected if resolver.vendors_served(p) >= 2
        }
        resolver.real_tokens = resolver._compute_real_tokens()
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

    def _compute_real_tokens(self) -> set:
        """The set of non-digit id tokens omo itself uses across EVERY chain entry (mini, fast,
        nano, flash, pro, plus, haiku, sonnet, glm, …). A trailing token in this set is a real
        model modifier — never stripped as noise, so gpt-5.4-mini-fast stays distinct from
        gpt-5.4-mini and glm-5-flash from glm-5. A trailing token NOT in it (a provider's
        `jibao`) is treated as a sub-version tag and stripped for matching. Data-driven: tracks
        the bundled omo snapshot, so there is no hand-maintained suffix list to drift."""
        toks: set = set()
        reqs = list(self.suggestions.agents.values()) + list(self.suggestions.categories.values())
        for req in reqs:
            for entry in req.get("fallbackChain", []):
                mid = entry.get("model") or ""
                for tok in normalize_model_id(mid).split("-"):
                    if tok and not tok.isdigit():
                        toks.add(tok)
        return toks

    def _is_noise_suffix(self, remainder: str) -> bool:
        """True iff `remainder` (the normalized tokens trailing an omo id) is ALL provider
        noise: every token is a date/build stamp (a compact >= _STAMP_MIN_DIGITS-digit run, or a
        hyphenated YYYY-MM-DD opened by a 4-digit year) or an unknown alpha sub-tag (not in
        real_tokens). False if any token is a real modifier (mini/fast/…) or a short version
        number — either makes a DIFFERENT model, not 'the same id + noise'."""
        if not remainder:
            return False
        tokens = remainder.split("-")
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if not tok:
                return False
            if tok.isdigit():
                if len(tok) >= _STAMP_MIN_DIGITS:
                    i += 1  # compact date / build stamp (e.g. 20251001)
                    continue
                if _YEAR_RE.match(tok):
                    i += 1  # hyphenated date: consume the year + its month/day tokens
                    while i < len(tokens) and tokens[i].isdigit() and len(tokens[i]) <= 2:
                        i += 1
                    continue
                return False  # a short digit run = a version bump (glm-5 vs glm-5.1)
            elif tok in self.real_tokens:
                return False  # real modifier (mini/fast/nano/flash/…) = a distinct model
            else:
                i += 1  # unknown alpha tag (e.g. `jibao`) → provider noise; keep scanning
        return True

    def _matches_omo_id(self, available_id: str, omo_id: str) -> bool:
        """Does a provider's `available_id` denote the omo `omo_id`, tolerating `.`/`-` spelling
        plus a trailing date stamp and/or sub-version tag? `claude-haiku-4-5-20251001` and
        `claude-sonnet-4-8-jibao` match `claude-haiku-4-5` / `claude-sonnet-4-8`; but `glm-5.1`
        does NOT match `glm-5`, and `gpt-5.4-mini-fast` does NOT match `gpt-5.4-mini`."""
        a = normalize_model_id(available_id)
        c = normalize_model_id(omo_id)
        if a == c:
            return True
        if a.startswith(c + "-"):
            return self._is_noise_suffix(a[len(c) + 1:])
        return False

    def _resolve_available(self, omo_id: str) -> "Optional[str]":
        """The concrete connected model id that fills `omo_id` exactly-or-by-noise, else None.
        Prefers an exactly-spelled match; otherwise the newest noise-suffixed build (e.g.
        claude-haiku-4-5 → claude-haiku-4-5-20251001). Returns the AVAILABLE id (the value that
        saves to config), never the bare omo id — the provider doesn't serve the bare id."""
        c = normalize_model_id(omo_id)
        matches: list = []
        seen: set = set()
        for prov in self.catalog.connected:
            for m in self.catalog.available.get(prov, []):
                if m in seen:
                    continue
                if self._matches_omo_id(m, omo_id):
                    seen.add(m)
                    matches.append(m)
        if not matches:
            return None
        for m in matches:
            if normalize_model_id(m) == c:
                return m  # an exact spelling beats any noise-suffixed build
        return max(matches, key=self._version_key)  # newest build (date acts as the tiebreak)

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
          1. EXACT — a connected provider serves the entry's model, tolerating `.`/`-` spelling
             plus a trailing date stamp / sub-version tag (claude-haiku-4-5 ≡
             claude-haiku-4-5-20251001) → that concrete AVAILABLE id; substitute_for=None.
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

        # Concrete available ids that fill SOME chain entry (exactly, or via a date/sub-tag
        # match): never reused as a same-line substitute — each is its own (exact) row instead.
        # Entry ids run through _canonical_omo_id first (k2p5 → kimi-k2.5; see _OMO_MODEL_ALIASES).
        exact_chain_models: set = set()
        for e in fallback_chain:
            if e.get("model"):
                filled = self._resolve_available(self._canonical_omo_id(e["model"]))
                if filled is not None:
                    exact_chain_models.add(filled)

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

            # 1. Exact (incl. date/sub-tag match) → 2. same-line substitute → 3. skip.
            filled = self._resolve_available(model_id)
            if filled is not None:
                resolved_model = filled
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
        match (when it is itself an exact chain entry) means 'defer to its own exact row'.
        Claude carve-out: within `claude-non-opus` (which lumps haiku, sonnet, fable, mythos, …)
        a substitute must also share the product-LINE token, so a haiku entry is never filled by
        a sonnet, nor a fable by a mythos."""
        target_fam = self.suggestions.detect_family(model_id)
        if target_fam is None:
            return None
        guard_line = target_fam.family == "claude-non-opus"
        target_line = _claude_line(model_id) if guard_line else None
        same: list = []
        seen: set = set()
        for prov in self.catalog.connected:
            for m in self.catalog.available.get(prov, []):
                if m in seen:
                    continue
                fam = self.suggestions.detect_family(m)
                if fam is not None and fam.family == target_fam.family:
                    if guard_line and _claude_line(m) != target_line:
                        continue  # different Claude line (haiku/sonnet/fable/…): not same-line
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
