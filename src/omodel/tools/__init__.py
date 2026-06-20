"""Marker so `omodel.tools` is a REGULAR package, not a namespace one. Do not delete.

Same Python 3.9 reason as `omodel.data/__init__.py`: refresh.py reads the bundled
`snapshot_omo.ts` via `importlib.resources.files("omodel.tools")`, which only resolves on a
regular package under 3.9 (namespace `files()` support landed in 3.10). snapshot_omo.ts still
ships via the package tree."""
