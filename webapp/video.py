"""Browser-playable video cache.

Many camera .MOV files carry LPCM (uncompressed PCM) audio, which browsers
cannot decode — they play the H.264 video silently. We also re-encode
all-intra camera captures (e.g. Fujifilm's 4K All-I mode runs ~190 Mbps) down
to a streamable bitrate and resolution.

Playing the cache at 4K is what makes the browser stall: measured on this
project's footage, 4K H.264 decodes at only ~1.3x realtime on one core, and
browsers here decode in software (Firefox reports "Hw codec disabled by
gfxVars" — snap Firefox ships no proprietary-NVIDIA VA-API driver). One seek or
neighbour preload then pushes it under realtime. Capping the long edge at 1440p
buys ~3x headroom. Bandwidth was never the constraint.

This cache is for preview scrubbing only: clip export always cuts from the
original file (see server.py), so the downscale costs nothing in exported
quality. Originals are never modified.
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


# Cap the long edge at 1440p. Both orientations occur in real projects (the
# X-T5 shoots 3840x2160 and 2160x3840), so this bounds whichever edge is longer
# rather than assuming landscape: 3840x2160 -> 2560x1440, 2160x3840 -> 1440x2560.
# The min() terms mean sub-1440p sources are left alone instead of upscaled, and
# force_divisible_by keeps both edges even, which yuv420p requires.
_MAX_LONG_EDGE = 2560
_SCALE_FILTER = (
    f"scale=w='min({_MAX_LONG_EDGE},iw)':h='min({_MAX_LONG_EDGE},ih)"
    f"':force_original_aspect_ratio=decrease:force_divisible_by=2"
)

# 15 Mbps is generous for 1440p H.264 — far above the ~7 Mbps CRF 21 actually
# lands on for this footage; it only exists to bound pathological scenes.
_MAX_VIDEO_BITRATE = "15M"
_VBV_BUFSIZE = "30M"

# A seek can only start at a keyframe, so the player must decode from the
# preceding one up to the target. Left to itself x264 emits keyframes up to 250
# frames (~8s) apart, which at 4K is seconds of decoding per scrub. One keyframe
# per second bounds that, at a few percent size cost.
_GOP_SECONDS = 1


def transcode_for_web(src: Path, dest: Path) -> None:
    """Re-encode src into dest: downscale to 1440p, cap bitrate, AAC audio, faststart.

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
            "-vf", _SCALE_FILTER,
            "-c:v", "libx264", "-preset", "medium", "-crf", "21",
            "-maxrate", _MAX_VIDEO_BITRATE, "-bufsize", _VBV_BUFSIZE,
            "-force_key_frames", f"expr:gte(t,n_forced*{_GOP_SECONDS})",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(tmp),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    tmp.replace(dest)
