"""Path setup for sightread unit tests (scripts/ pipeline + webapp helpers)."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent

for _sub in ("scripts", "webapp"):
    _p = str(PROJECT_ROOT / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(autouse=True)
def _no_poster_prewarm(monkeypatch):
    """Keep /api/videos from shelling out to ffmpeg on fixture "videos".

    The prewarmer runs on a background thread, so without this its ffmpeg calls
    land in whichever test happens to be running when they fire — which is how
    a clip-export assertion ends up seeing a poster command from three tests
    ago. Tests that want a real poster call the endpoint, which extracts
    synchronously.
    """
    try:
        import server
    except ImportError:
        return  # pipeline-only test run; webapp isn't imported
    monkeypatch.setattr(server._poster_executor, "submit", lambda *a, **k: None)
