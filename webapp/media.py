"""What counts as a photo, a video, and a Live Photo — for every caller.

Scripts import this as well as the webapp, so it pulls in nothing beyond the
stdlib; Pillow's HEIF support is imported lazily by `register_heif`.

**Extensions.** These sets used to be restated in four modules with comments
asking them to be kept in sync, which is how a new format gets taught to the
scan and forgotten by the export. They live here once.

**Live Photos.** An iPhone Live Photo is two files with one stem:
`IMG_1234.HEIC` and a ~3s `IMG_1234.MOV` of the moments around it. Google
Photos exports keep the pairing with an `.MP4`. The motion file is part of the
photo, not footage in its own right — shown as a video it floods review with
clips nobody shot on purpose (151 of Hoh River's 163 phone videos were these).

A video counts as a photo's motion only when both hold: a still with the same
stem sits in the same directory, and the video runs at most
LIVE_PHOTO_MAX_S. The name alone would be enough on this footage, but it is
the duration that makes the rule safe to delete by: a camera that reuses a
counter across stills and clips can put a real two-minute video beside a JPEG
of the same name, and that video must never be swept up as a sidecar.
Measured on Hoh River, every paired motion file ran 2.48–3.03s and no paired
video ran longer. A duration that cannot be read counts as "not a Live
Photo", so the failure mode is a stray clip in the Videos tab, never a video
deleted with a still.
"""
import os
import struct
import threading
from pathlib import Path
from typing import Iterable

# What a trip's deliverables folder is called. Scans skip it at any depth: its
# contents are this app's own output, and since an export hardlinks rather than
# copies, scanning it would show every exported photo a second time under a
# second name. A project opened on a whole trip contains one; a project opened
# on a single camera folder has one beside it.
TRIP_EXPORTS_DIR = "_exports"

HEIF_EXTENSIONS = {".heic", ".heif"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"} | HEIF_EXTENSIONS
# No ".ts": MPEG-TS shares the extension with TypeScript sources, so any code
# folder would show up full of bogus "videos". AVCHD cameras use .mts/.m2ts.
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mts", ".m2ts", ".webm"}

# Apple records 1.5s either side of the shutter. Measured 2.48–3.03s across
# 161 motion files; the margin covers trimmed and re-encoded exports.
LIVE_PHOTO_MAX_S = 4.0


def walk_media(root: Path | str):
    """os.walk over a project, minus the directories that are not source media.

    Skips `_exports` and dot-directories in place, so a trip folder can be
    opened as one project — camera folders and all — without the export tree
    reading back as hundreds of extra photos.
    """
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != TRIP_EXPORTS_DIR and not d.startswith("."))
        yield dirpath, names


def device_of(path: Path | str, root: Path | str) -> str:
    """Which camera folder under `root` a file came from.

    The drives group a trip by where the files came from —
    `Trips/<trip>/{xt5,canon,iphone,google photos}` — so for a project opened
    on the trip, the first path segment is the device. Empty for a file
    sitting directly in the project folder, and for a project opened on one
    camera folder, where every file would answer the same thing anyway.
    """
    try:
        rel = Path(path).relative_to(root).parts
    except ValueError:
        return ""
    return rel[0] if len(rel) > 1 else ""


def is_image(path: Path | str) -> bool:
    return os.path.splitext(str(path))[1].lower() in IMAGE_EXTENSIONS


def is_video(path: Path | str) -> bool:
    return os.path.splitext(str(path))[1].lower() in VIDEO_EXTENSIONS


_heif_registered = False


def register_heif() -> None:
    """Teach Pillow to open HEIF/HEIC. Idempotent.

    A hard dependency, not an optional one: a HEIC that fails to decode is
    scored as a blank frame, lands at the bottom of its cluster, and is exactly
    what keep-best marks for deletion.
    """
    global _heif_registered
    if _heif_registered:
        return
    from pillow_heif import register_heif_opener

    register_heif_opener()
    _heif_registered = True


# ---------------------------------------------------------------------------
# Video duration, read from the container header
# ---------------------------------------------------------------------------
def _find_box(f, start: int, end: int, want: bytes) -> tuple[int, int] | None:
    """(payload start, box end) of the first `want` box between start and end."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        size, kind = struct.unpack(">I4s", f.read(8))
        header = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header:
            return None
        if kind == want:
            return pos + header, pos + size
        pos += size
    return None


def _mvhd_duration(path: Path) -> float | None:
    with open(path, "rb") as f:
        end = f.seek(0, os.SEEK_END)
        moov = _find_box(f, 0, end, b"moov")
        if moov is None:
            return None
        mvhd = _find_box(f, moov[0], moov[1], b"mvhd")
        if mvhd is None:
            return None
        f.seek(mvhd[0])
        version = f.read(4)[0]
        if version == 1:
            f.seek(16, os.SEEK_CUR)
            timescale, duration = struct.unpack(">IQ", f.read(12))
        else:
            f.seek(8, os.SEEK_CUR)
            timescale, duration = struct.unpack(">II", f.read(8))
    return duration / timescale if timescale else None


_duration_lock = threading.Lock()
_duration_memo: dict[tuple[str, int, int], float | None] = {}


def video_duration(path: Path | str) -> float | None:
    """Seconds, from the MOV/MP4 `mvhd` box, or None when it can't be read.

    A header walk rather than ffprobe: pairing runs on every timeline open and
    over a hundred-odd phone clips, and spawning a process per file off a
    spinning disk is seconds where this is a few small reads. Other containers
    return None, which pairing treats as "not a Live Photo" — the safe answer.
    Memoised on (path, mtime, size), since a file's duration only changes
    when the file does.
    """
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _duration_lock:
        if key in _duration_memo:
            return _duration_memo[key]
    try:
        seconds = _mvhd_duration(path)
    except (OSError, struct.error, IndexError):
        seconds = None
    with _duration_lock:
        _duration_memo[key] = seconds
    return seconds


def _is_short(path: Path) -> bool:
    seconds = video_duration(path)
    return seconds is not None and seconds <= LIVE_PHOTO_MAX_S


# ---------------------------------------------------------------------------
# Live Photo pairing
# ---------------------------------------------------------------------------
def motion_names(directory: Path | str, names: Iterable[str]) -> set[str]:
    """The names in one directory listing that are Live Photo motion files.

    Takes the listing the caller already has from os.walk, so the only disk
    access is the header read on videos that share a still's stem.
    """
    names = list(names)
    still_stems = {os.path.splitext(n)[0] for n in names if is_image(n)}
    return {
        n for n in names
        if is_video(n)
        and os.path.splitext(n)[0] in still_stems
        and _is_short(Path(directory, n))
    }


def motion_of(still: Path) -> Path | None:
    """The Live Photo motion file belonging to `still`, or None."""
    if not is_image(still):
        return None
    for ext in VIDEO_EXTENSIONS:
        for candidate in (still.with_suffix(ext.upper()), still.with_suffix(ext)):
            if candidate.is_file() and _is_short(candidate):
                return candidate
    return None


def motion_map(paths: Iterable[str]) -> dict[str, str]:
    """{still path: motion path} for every still in `paths` that has one.

    Lists each directory once instead of probing sixteen spellings per photo.
    """
    by_dir: dict[str, list[str]] = {}
    for p in paths:
        by_dir.setdefault(os.path.dirname(p), []).append(p)
    out: dict[str, str] = {}
    for directory, stills in by_dir.items():
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        by_stem = {os.path.splitext(n)[0]: n for n in motion_names(directory, names)}
        for p in stills:
            name = by_stem.get(os.path.splitext(os.path.basename(p))[0])
            if name is not None:
                out[p] = os.path.join(directory, name)
    return out
