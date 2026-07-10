"""Browser-playable video cache.

Many camera .MOV files carry LPCM (uncompressed PCM) audio, which browsers
cannot decode — they play the H.264 video silently. We pre-transcode each clip
into an MP4 with AAC audio (video stream copied, so no quality loss) and serve
that cached copy. Originals are never modified.
"""
import hashlib
import shutil
import subprocess
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mts", ".m2ts", ".webm"}


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def cache_path(output_dir: Path, src: Path) -> Path:
    """Deterministic cache location keyed by source path + mtime."""
    key = hashlib.sha1(
        f"{src.resolve()}|{src.stat().st_mtime_ns}".encode()
    ).hexdigest()
    return output_dir / "video_cache" / f"{key}.mp4"


def transcode_for_web(src: Path, dest: Path) -> None:
    """Remux src into dest: copy video, re-encode audio to AAC, faststart.

    Writes to a temp file then atomically renames, so a partial/cached file is
    never observed.
    """
    ff = _ffmpeg()
    if ff is None:
        raise RuntimeError("ffmpeg not found on PATH")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.mp4")
    subprocess.run(
        [
            ff, "-y", "-i", str(src),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(tmp),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    tmp.replace(dest)
