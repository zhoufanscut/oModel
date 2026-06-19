"""Availability from the `opencode` CLI.  DESIGN.md §catalog.py / §Data sources.

FROZEN CONTRACT — owned by the Core-logic specialist. Implement the bodies; do not
change the public signatures or the `Catalog` shape without the Lead reconciling
CONTRACTS.md (app.py and resolve.py depend on these exact shapes).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass

# Header line for `opencode models <provider> --verbose` output.
_HEADER_RE = re.compile(r"^(?P<prov>[a-z0-9_-]+)/(?P<model>\S+)$")


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
        return [p for p in self.connected if model_id in self.available.get(p, [])]

    def detail(self, model_id: str):
        """On-demand `opencode models <prov> --verbose` for the RESOLVED provider
        (providers_for(model_id)[0]); brace-count + json.loads each record; pick the one
        whose header == `<prov>/<model_id>`. Returns a DISPLAY-ONLY dict
        {"context": int|None, "cost": {...}, "reasoning": bool, "image": bool} or None.
        NEVER read `--verbose.variants`/`.family` (decision #14)."""
        providers = self.providers_for(model_id)
        if not providers:
            return None
        prov = providers[0]

        try:
            result = subprocess.run(
                ["opencode", "models", prov, "--verbose"],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return None
        if result.returncode != 0:
            return None

        # Parse multi-record output: split on header lines at column 0, brace-count blocks.
        target_header = f"{prov}/{model_id}"
        lines = result.stdout.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            m = _HEADER_RE.match(line)
            if m:
                header = line.strip()
                # Collect the JSON block for this record via brace counting.
                i += 1
                brace_depth = 0
                block_lines = []
                while i < len(lines):
                    bl = lines[i]
                    brace_depth += bl.count("{") - bl.count("}")
                    block_lines.append(bl)
                    i += 1
                    if brace_depth <= 0 and block_lines:
                        break

                if header == target_header:
                    try:
                        record = json.loads("\n".join(block_lines))
                    except (json.JSONDecodeError, ValueError):
                        return None
                    # Extract display-only fields; NEVER read .variants/.family.
                    context = None
                    limit = record.get("limit") or {}
                    if isinstance(limit, dict):
                        context = limit.get("context")

                    cost = record.get("cost")
                    caps = record.get("capabilities") or {}
                    reasoning = bool(caps.get("reasoning"))
                    image_caps = caps.get("input") or {}
                    image = bool(image_caps.get("image"))

                    return {
                        "context": context,
                        "cost": cost,
                        "reasoning": reasoning,
                        "image": image,
                    }
            else:
                i += 1

        return None


def load(opencode_bin: str = "opencode") -> Catalog:
    """`opencode models` → Catalog. Split each line on the FIRST `/`.
    Error rule (DESIGN §Data sources — the single definition, used by catalog.load too):
      * `opencode` not on PATH        → return Catalog(available={}, connected=[])
      * exit != 0 OR zero lines parsed → raise CatalogUnavailable
    Tests must NOT hard-assert the model count (varies)."""
    if shutil.which(opencode_bin) is None:
        return Catalog(available={}, connected=[])

    try:
        result = subprocess.run(
            [opencode_bin, "models"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return Catalog(available={}, connected=[])

    if result.returncode != 0:
        raise CatalogUnavailable(
            f"`{opencode_bin} models` exited with code {result.returncode}"
        )

    available: dict = {}
    connected: list = []

    for line in result.stdout.splitlines():
        line = line.strip()
        if "/" not in line:
            continue
        # Split on the FIRST '/' only.
        provider, model_id = line.split("/", 1)
        provider = provider.strip()
        model_id = model_id.strip()
        if not provider or not model_id:
            continue
        if provider not in available:
            available[provider] = []
            connected.append(provider)
        if model_id not in available[provider]:
            available[provider].append(model_id)

    if not available:
        raise CatalogUnavailable(
            f"`{opencode_bin} models` produced zero provider/model lines"
        )

    return Catalog(available=available, connected=connected)
