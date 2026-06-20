"""Marker so `omodel.data` is a REGULAR package, not a namespace one. Do not delete.

Required for the **Python 3.9** floor: `importlib.resources.files("omodel.data")` — used by
config_io (default-config.jsonc) and suggestions (omo-suggestions.json) — only resolves on a
regular package under 3.9; namespace-package support for `files()` landed in 3.10. Without this
file every bundled-data read raises `TypeError: expected str, bytes or os.PathLike object, not
NoneType` on 3.9 (passes on 3.10+). The data files still ship via the package tree — see
pyproject `[tool.hatch.build.targets.wheel]` (no force-include needed)."""
