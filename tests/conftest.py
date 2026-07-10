"""Path setup for sightread unit tests (scripts/ pipeline + webapp helpers)."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

for _sub in ("scripts", "webapp"):
    _p = str(PROJECT_ROOT / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)
