import sys

# Absolute (not `from .cli`): PyInstaller runs this file as the top-level `__main__` with no
# parent package, so a relative import raises "attempted relative import with no known parent
# package" in the frozen binary. Absolute works both there and under `python -m omodel`.
from omodel.cli import main

if __name__ == "__main__":
    sys.exit(main())
