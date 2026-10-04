"""Marker so `omodel.tools` is a REGULAR package, not a namespace one. Do not delete.

Same reason as `omodel.data/__init__.py`: refresh.py reads the bundled `snapshot_omo.ts` via
`importlib.resources.files("omodel.tools")`. snapshot_omo.ts still ships via the package tree."""
