"""Where each derived cache lives: the project's own dir, or a cold-cache root.

A project's caches all sat under its output dir on the NVMe until 2026-10-20,
when they were 87GB of the system disk (docs/data-storage.md). Measured cold on
the h1 HDD, a 2400px compare render reads in ~45ms against a ~0.9s re-render,
and a 1440p transcode streams at 160MB/s against the ~2MB/s playback needs;
the 800px grid tiles, read hundreds at a time, were ~50x slower than NVMe. So
with a cold root configured ($SIGHTREAD_COLD_CACHE_ROOT or CONFIG_FILE),
compare-width thumbnails and transcodes go to
`<root>/<project dir name>/{thumb_cache,video_cache}` and everything else stays
put.

The root is a folder *on* the drive (/mnt/h1/sightread-cache), never the mount
point itself. When the drive is not mounted, /mnt/h1 is an empty directory on
the system disk, and mkdir(parents=True) under it would quietly put the cold
cache back on the NVMe, invisible once the drive is mounted over it. So writers
go through ensure_parent(), which refuses when the root is missing, and readers
just miss.
"""
import functools
import os
from pathlib import Path

ENV = "SIGHTREAD_COLD_CACHE_ROOT"
# The server and the pipeline must agree on where a key lives, or the
# pipeline's prewarmed renders become misses the server re-renders. A shell
# variable is easy to have in one terminal and not the other, so the setting
# also lives in a file. The variable wins when set; set to "" it turns the
# split off (the tests do that).
CONFIG_FILE = (Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
               / "sightread" / "cold_cache_root")


class ColdCacheUnavailable(OSError):
    """The cold-cache root is configured but not there (drive unmounted)."""


@functools.cache
def _configured() -> str:
    try:
        return CONFIG_FILE.read_text().strip()
    except OSError:
        return ""


def cold_root() -> Path | None:
    # The variable is read per call, so tests and scripts can set it; the file
    # once per process, since this sits on every thumbnail request.
    raw = os.environ.get(ENV)
    if raw is None:
        raw = _configured()
    return Path(raw) if raw else None


def cold_dir(output_dir: Path) -> Path | None:
    root = cold_root()
    return root / Path(output_dir).name if root else None


def cold_missing() -> bool:
    root = cold_root()
    return root is not None and not root.is_dir()


def missing_message() -> str:
    return f"Cache drive not mounted: {cold_root()} (set by ${ENV})"


def large_thumb_dir(output_dir: Path) -> Path:
    """thumb_cache for widths above the grid width."""
    cold = cold_dir(output_dir)
    return (cold or Path(output_dir)) / "thumb_cache"


def video_dir(output_dir: Path) -> Path:
    cold = cold_dir(output_dir)
    return (cold or Path(output_dir)) / "video_cache"


def ensure_parent(dest: Path) -> None:
    """mkdir dest's parent, refusing to recreate a missing cold root."""
    root = cold_root()
    if root is not None and Path(dest).is_relative_to(root) and not root.is_dir():
        raise ColdCacheUnavailable(missing_message())
    Path(dest).parent.mkdir(parents=True, exist_ok=True)


def derived_dirs(output_dir: Path) -> list[Path]:
    """Every directory holding a project's regenerable thumbnails, posters and
    transcodes, hot and cold, deduplicated, in a stable order."""
    output_dir = Path(output_dir)
    dirs = [output_dir / "thumb_cache", output_dir / "poster_cache", output_dir / "video_cache",
            large_thumb_dir(output_dir), video_dir(output_dir)]
    return list(dict.fromkeys(dirs))
