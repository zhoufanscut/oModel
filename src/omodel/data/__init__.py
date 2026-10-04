"""Marker so `omodel.data` is a REGULAR package, not a namespace one. Do not delete.

`importlib.resources.files("omodel.data")` — used by config_io (default-config.jsonc) and
suggestions (omo-suggestions.json) — reads through it, and a regular package is the simplest,
best-trodden case for that lookup in both the wheel and the frozen PyInstaller binary
(`--collect-data omodel`). The data files still ship via the package tree — see pyproject
`[tool.hatch.build.targets.wheel]` (no force-include needed)."""
