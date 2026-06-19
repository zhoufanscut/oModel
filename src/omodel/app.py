"""Textual two-pane App.  DESIGN.md §Textual two-pane contract / §Layout.

FROZEN CONTRACT — owned by the TUI specialist. Consumes catalog / suggestions / resolve /
config_io against their frozen signatures. Implements the App class `OModelApp` and the
module entrypoint `run_app`.

STABLE WIDGET IDs (pilot tests in tests/test_app_pilot.py depend on these — do not rename):
  * Static#providers      — "Providers: <id · id · …>" from catalog.connected (first-seen);
                            on CatalogUnavailable shows the banner + `r` retry instead.
  * OptionList#targets     — AGENTS then CATEGORIES. Option IDs: 'agent:<name>',
                            'agent:<name>.ultrawork' / '.compaction' (indented sub-rows),
                            'cat:<name>'.
  * Static#detail          — current model/variant + catalog.detail() line.
  * OptionList#candidates  — option IDs 'cand:<i>'; LAST row 'cand:add' (+ add model…).

KEYS: ↑↓ move · enter set (dispatch by row: cand:add → add-model modal, else set model +
default variant) · v variant · p prefix (cycle providers_for) · e add · x clear ·
a add sub-target · s save (diff+confirm) · q quit (confirm if dirty).
Add-model modal: one-line Input 'provider/model' + live preview; full provider/model used
verbatim (split on FIRST '/'); bare id auto-prefixed via resolve_prefix if available, else
'⚠ unknown — add a provider/' and enter is BLOCKED until qualified.
"""
from __future__ import annotations


def run_app(config_path: str = None) -> None:
    """Build catalog / suggestions / resolver / config and run OModelApp().
    Called by cli.main() for the default (no-subcommand) invocation."""
    raise NotImplementedError  # implemented by the TUI specialist
