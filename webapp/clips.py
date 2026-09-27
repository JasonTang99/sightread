"""User-editable video clips: persistence + ffmpeg export.

Persistence lives in <output_dir>/user_clips.json:

    {"schema_version": 1, "videos": {"<abs path>": {"clips": [{"start": 1.2, "end": 5.6}]}}}

The presence of a video key means the user has taken over clip editing for
that video (it overrides pipeline suggestions in the UI); an empty clips list
means the user explicitly wants no clips for it.

Exports cut from the ORIGINAL video file (never the web-transcode cache) into
<trip>/_exports/clips/ (see exports.export_dir_for).
"""
import json
import shutil
import subprocess
from pathlib import Path

USER_CLIPS_FILENAME = "user_clips.json"
EXPORT_DIR_NAME = "clips"
FFMPEG_TIMEOUT_PER_CLIP_S = 300


class ClipExportError(RuntimeError):
    """ffmpeg missing, failed, or timed out during clip export."""


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_user_clips(output_dir: Path) -> dict:
    """The videos mapping from user_clips.json.

    Returns {} when the file is missing or corrupt so callers degrade cleanly.
    """
    try:
        data = json.loads((output_dir / USER_CLIPS_FILENAME).read_text())
        videos = data.get("videos", {})
        if not isinstance(videos, dict):
            return {}
    except Exception:
        return {}
    return videos


def user_clips_for(output_dir: Path, paths: list[str]) -> dict:
    """User clip entries filtered to paths: {path: {"clips": [...]}}.

    A present key with an empty clips list is preserved — it means the user
    explicitly wants no clips for that video (overriding suggestions).
    """
    videos = load_user_clips(output_dir)
    result = {}
    for p in paths:
        entry = videos.get(p)
        if isinstance(entry, dict) and isinstance(entry.get("clips"), list):
            result[p] = {"clips": entry["clips"]}
    return result


def save_user_clips(output_dir: Path, video_path: str, clips: list[dict]) -> list[dict]:
    """Overwrite the clips entry for video_path (empty list allowed and stored).

    Clips are normalized to {"start": float, "end": float}, sorted by start.
    The file is written atomically (temp file + os.replace) so a crash never
    leaves a truncated JSON behind. Returns the stored clip list.
    """
    normalized = sorted(
        ({"start": float(c["start"]), "end": float(c["end"])} for c in clips),
        key=lambda c: (c["start"], c["end"]),
    )
    videos = load_user_clips(output_dir)
    videos[video_path] = {"clips": normalized}
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / USER_CLIPS_FILENAME
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"schema_version": 1, "videos": videos}, indent=2) + "\n")
    tmp.replace(target)
    return normalized


def delete_user_clips(output_dir: Path, video_path: str) -> bool:
    """Remove the clips entry for video_path, reverting the video to pipeline
    suggestions in the UI. Returns True if an entry existed.

    Written atomically like save_user_clips.
    """
    videos = load_user_clips(output_dir)
    if video_path not in videos:
        return False
    del videos[video_path]
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / USER_CLIPS_FILENAME
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"schema_version": 1, "videos": videos}, indent=2) + "\n")
    tmp.replace(target)
    return True


# ---------------------------------------------------------------------------
# ffmpeg export
# ---------------------------------------------------------------------------

def _fmt_seconds(t: float) -> str:
    """Seconds at 1 decimal with the dot replaced by 'p' (3.0 -> '3p0')."""
    return f"{t:.1f}".replace(".", "p")


def clip_filename(stem: str, index: int, start: float, end: float) -> str:
    """<stem>_c<NN>_<start>s-<end>s.mp4, e.g. beach_c01_1p2s-5p6s.mp4."""
    return f"{stem}_c{index:02d}_{_fmt_seconds(start)}s-{_fmt_seconds(end)}s.mp4"


def export_clips(
    src: Path,
    clips: list[dict],
    out_dir: Path,
    mode: str = "reencode",
    timeout_per_clip: int = FFMPEG_TIMEOUT_PER_CLIP_S,
) -> list[Path]:
    """Cut each clip out of src into out_dir; returns the output paths.

    Runs ffmpeg synchronously per clip, with -ss placed before -i (fast input
    seek — frame-accurate when re-encoding). Existing outputs are overwritten.

    Modes:
      - "reencode" (default): libx264 CRF 18 + AAC, frame-accurate cuts.
      - "copy": stream copy (-c copy) — near-instant and lossless, but cut
        points snap to the nearest keyframe, so boundaries can be off by up
        to a GOP length and the first frames may be blank in some players.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ClipExportError("ffmpeg not found on PATH")
    out_dir.mkdir(parents=True, exist_ok=True)
    if mode == "copy":
        codec_args = ["-c", "copy"]
    else:
        codec_args = ["-c:v", "libx264", "-crf", "18", "-preset", "fast", "-c:a", "aac"]
    outputs: list[Path] = []
    ordered = sorted(clips, key=lambda c: (float(c["start"]), float(c["end"])))
    for i, clip in enumerate(ordered, start=1):
        start, end = float(clip["start"]), float(clip["end"])
        out_path = out_dir / clip_filename(src.stem, i, start, end)
        cmd = [
            ffmpeg, "-y",
            "-ss", f"{start:.3f}",
            "-i", str(src),
            "-t", f"{end - start:.3f}",
            *codec_args,
            "-movflags", "+faststart",
            str(out_path),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_per_clip
            )
        except subprocess.TimeoutExpired:
            raise ClipExportError(
                f"ffmpeg timed out after {timeout_per_clip}s cutting clip {i} of {src.name}"
            )
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip()[-2000:]
            raise ClipExportError(
                f"ffmpeg failed (exit {proc.returncode}) cutting clip {i} of {src.name}: {tail}"
            )
        outputs.append(out_path.resolve())
    return outputs
