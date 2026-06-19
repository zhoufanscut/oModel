"""Bundled omo suggestion data + family heuristics.  DESIGN.md §suggestions.py.

FROZEN CONTRACT — owned by the Core-logic specialist. `FAMILY_VENDOR` below is part of
the contract (verified complete: its 14 keys == the 14 families in the bundled data).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Hardcoded in oModel (omo has NO such table). 14-family → vendor map (DESIGN §suggestions.py).
# Models whose detect_family is None contribute NO vendor — do not invent a family for them.
FAMILY_VENDOR = {
    "claude-opus": "anthropic", "claude-non-opus": "anthropic",
    "openai-reasoning": "openai", "gpt-5": "openai", "gpt-legacy": "openai",
    "gemini": "google", "grok": "xai",
    "kimi-thinking": "moonshot", "kimi": "moonshot",
    "glm": "zhipu", "minimax": "minimax", "deepseek": "deepseek",
    "mistral": "mistral", "llama": "meta",
}


@dataclass
class Family:
    family: str
    pattern: object          # re.Pattern | None — compiled from the JSON `pattern` string at load
    includes: list
    variants: list
    reasoning_efforts: list
    reasoning_effort_aliases: dict
    supports_thinking: bool


@dataclass
class Suggestions:
    meta: dict
    agents: dict       # {name: {"fallbackChain":[{"providers":[],"model":"","variant"?:""}], "variant"?:"", requires*}}
    categories: dict   # {name: {"fallbackChain":[...], "variant"?:""}}
    families: list     # [Family, ...]  ORDERED — iteration order is significant for detect_family
    known_variants: list

    def detect_family(self, model_id: str):
        """Faithful port of omo `detectHeuristicModelFamily` → Family | None.
        Run normalize_model_id() first, then ORDERED iteration of `families`; within each
        entry `pattern` is tested BEFORE `includes`; FIRST match wins. (Parity:
        openai-reasoning before gpt-5, kimi-thinking before kimi, claude-opus before
        claude-non-opus.)"""
        raise NotImplementedError

    def vendor_for(self, model_id: str):
        """vendor(self.detect_family(model_id)) → str | None."""
        raise NotImplementedError


def vendor(family):
    """FAMILY_VENDOR.get(family.family) for a Family, else None (handles family=None)."""
    raise NotImplementedError


def normalize_model_id(s: str) -> str:
    """re.sub(r'\\.(\\d+)', r'-\\1', s).lower()  →  'kimi-k2.7' → 'kimi-k2-7'."""
    raise NotImplementedError


def load(path: str = None) -> Suggestions:
    """Load order: explicit `path` → $OMODEL_SUGGESTIONS → $XDG_DATA_HOME/omodel/omo-suggestions.json
    → bundled importlib.resources.files('omodel.data')/'omo-suggestions.json'.
    Each Family.pattern is re.compile()d from the JSON `pattern` string (or None) at load."""
    raise NotImplementedError
