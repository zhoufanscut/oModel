"""Bundled omo suggestion data + family heuristics.  DESIGN.md §suggestions.py.

FROZEN CONTRACT — owned by the Core-logic specialist. `FAMILY_VENDOR` below is part of
the contract (verified complete: its 15 keys == the 15 families in the bundled data).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Optional

# Hardcoded in oModel (omo has NO such table). 15-family → vendor map (DESIGN §suggestions.py).
# Models whose detect_family is None contribute NO vendor — do not invent a family for them.
FAMILY_VENDOR = {
    "claude-opus": "anthropic", "claude-non-opus": "anthropic",
    "openai-reasoning": "openai", "gpt-5": "openai", "gpt-legacy": "openai",
    "gemini": "google", "grok": "xai",
    "kimi-thinking": "moonshot", "kimi": "moonshot",
    "glm": "zhipu", "minimax": "minimax", "deepseek": "deepseek",
    "qwen": "alibaba", "mistral": "mistral", "llama": "meta",
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

    def detect_family(self, model_id: str) -> "Optional[Family]":
        """Faithful port of omo `detectHeuristicModelFamily` → Family | None.
        Run normalize_model_id() first, then ORDERED iteration of `families`; within each
        entry `pattern` is tested BEFORE `includes`; FIRST match wins. (Parity:
        openai-reasoning before gpt-5, kimi-thinking before kimi, claude-opus before
        claude-non-opus.)"""
        normalized = normalize_model_id(model_id)
        for fam in self.families:
            # Within each family: pattern first, THEN includes (omo parity). A family may
            # carry BOTH (kimi-thinking does), and omo checks `includes` even when a `pattern`
            # is present — so `includes` must NOT be gated behind `pattern is None`, or an
            # include the pattern fails to cover would be silently skipped and the id would
            # fall through to the wrong family downstream.
            if fam.pattern is not None and fam.pattern.search(normalized):
                return fam
            for inc in fam.includes:
                if inc in normalized:
                    return fam
        return None

    def vendor_for(self, model_id: str) -> "Optional[str]":
        """vendor(self.detect_family(model_id)) → str | None."""
        return vendor(self.detect_family(model_id))


def vendor(family: "Optional[Family]") -> "Optional[str]":
    """FAMILY_VENDOR.get(family.family) for a Family, else None (handles family=None)."""
    if family is None:
        return None
    return FAMILY_VENDOR.get(family.family)


def normalize_model_id(s: str) -> str:
    r"""re.sub(r'\.(\d+)', r'-\1', s).lower()  →  'kimi-k2.7' → 'kimi-k2-7'."""
    return re.sub(r"\.(\d+)", r"-\1", s).lower()


def _generated_at(data_str: str) -> str:
    """meta.generatedAt from a suggestions JSON blob, or "" if the blob is unparseable, not a
    JSON object, or missing/non-string generatedAt. "" sorts older than any real ISO-8601
    timestamp, so a plain string comparison gives "missing/unparseable → oldest" for free."""
    try:
        parsed = json.loads(data_str)
    except ValueError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    meta = parsed.get("meta")
    generated = meta.get("generatedAt") if isinstance(meta, dict) else None
    return generated if isinstance(generated, str) else ""


def _newer_of_xdg_and_bundled() -> str:
    """The newer of $XDG_DATA_HOME/omodel/omo-suggestions.json (written by a past
    `--refresh-omo`) and the bundled resource, compared by meta.generatedAt (ISO-8601 string
    compare — chronologically correct for this format). Ties, a missing/unparseable
    generatedAt on either side, or an unreadable/corrupt XDG file all resolve to the bundled
    resource — a stale XDG snapshot must never permanently shadow a newer bundled release
    after an app upgrade."""
    bundled_str = (files("omodel.data") / "omo-suggestions.json").read_text(encoding="utf-8")

    xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    xdg_path = os.path.join(xdg_data, "omodel", "omo-suggestions.json")
    try:
        with open(xdg_path, "r", encoding="utf-8") as f:
            xdg_str = f.read()
    except (OSError, ValueError):
        return bundled_str

    return xdg_str if _generated_at(xdg_str) > _generated_at(bundled_str) else bundled_str


def load(path: str = None) -> Suggestions:
    """Load order: explicit `path` → $OMODEL_SUGGESTIONS → the NEWER of
    $XDG_DATA_HOME/omodel/omo-suggestions.json and the bundled
    importlib.resources.files('omodel.data')/'omo-suggestions.json', compared by
    meta.generatedAt (ISO-8601 string compare; missing/unparseable/unreadable → oldest; ties
    → bundled) — so a stale XDG snapshot from an old `--refresh-omo` run can't permanently
    shadow a newer bundled release after an app upgrade.
    Each Family.pattern is re.compile()d from the JSON `pattern` string (or None) at load."""
    data_str: "Optional[str]" = None

    if path is not None:
        with open(path, "r", encoding="utf-8") as f:
            data_str = f.read()
    else:
        env_path = os.environ.get("OMODEL_SUGGESTIONS")
        if env_path:
            with open(env_path, "r", encoding="utf-8") as f:
                data_str = f.read()
        else:
            data_str = _newer_of_xdg_and_bundled()

    raw = json.loads(data_str)

    families = []
    for fd in raw["families"]:
        pat_src = fd.get("pattern")
        compiled = re.compile(pat_src) if pat_src is not None else None
        families.append(Family(
            family=fd["family"],
            pattern=compiled,
            includes=fd.get("includes", []),
            variants=fd.get("variants", []),
            reasoning_efforts=fd.get("reasoningEfforts", []),
            reasoning_effort_aliases=fd.get("reasoningEffortAliases", {}),
            supports_thinking=fd.get("supportsThinking", False),
        ))

    return Suggestions(
        meta=raw.get("meta", {}),
        agents=raw.get("agents", {}),
        categories=raw.get("categories", {}),
        families=families,
        known_variants=raw.get("knownVariants", []),
    )
