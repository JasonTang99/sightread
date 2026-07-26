"""FastAPI backend for Sightread webapp."""
import hashlib
import io
import json
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel

# Must precede the local imports below so `uvicorn webapp.server:app` (run from
# the repo root, e.g. by the test suite) resolves them.
sys.path.insert(0, str(Path(__file__).parent))

from utils import (
    SINGLETON_DELETE_THRESHOLD,
    append_to_delete_list,
    load_decisions,
    load_favorites,
    load_results,
    read_delete_list,
    remove_decision,
    remove_from_delete_list,
    save_decision,
    toggle_favorite,
)

from projects import (
    IMAGE_EXTENSIONS,
    ProjectContext,
    image_files_in,
    load_recents,
    project_output_dir,
    project_status,
    upsert_recent,
)
from jobs import JobState, current_job, start_pipeline
from video import cache_path as video_cache_path, transcode_for_web
from clips import (
    EXPORT_DIR_NAME,
    ClipExportError,
    delete_user_clips,
    export_clips,
    save_user_clips,
    user_clips_for,
)

log = logging.getLogger(__name__)

import concurrent.futures
import threading

_transcode_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="transcode")
_transcode_inflight: set[Path] = set()
_transcode_lock = threading.Lock()


def _transcode_bg(src: Path, dest: Path) -> None:
    with _transcode_lock:
        if src in _transcode_inflight or dest.exists():
            return
        _transcode_inflight.add(src)
    try:
        transcode_for_web(src, dest)
        log.info("transcoded %s", src.name)
    except Exception as exc:
        log.warning("transcode failed %s: %s", src.name, exc)
    finally:
        with _transcode_lock:
            _transcode_inflight.discard(src)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

app = FastAPI()

# Only loopback hosts may talk to this server. Blocks DNS-rebinding (Host header)
# and CSRF from malicious websites (Origin header on cross-site requests).
_LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}


@app.middleware("http")
async def _reject_non_local(request: Request, call_next):
    host = urlparse(f"//{request.headers.get('host', '')}").hostname
    if host not in _LOCAL_HOSTNAMES:
        return JSONResponse({"detail": "Forbidden host"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and urlparse(origin).hostname not in _LOCAL_HOSTNAMES:
        return JSONResponse({"detail": "Forbidden origin"}, status_code=403)
    return await call_next(request)


_active: ProjectContext | None = None
_undo_stack: list[dict] = []

# Every endpoint that mutates to_delete.txt, decisions.json, favorites.json or the
# undo stack does a read-modify-write, so two overlapping requests can drop one
# side's edit entirely. Uvicorn runs sync handlers on a threadpool, and the
# thumbnail prewarmer adds background load on top, so the overlap is routine
# rather than theoretical. Serialise the writers; readers are left alone.
_curation_lock = threading.Lock()

# Width the timeline grid requests; kept in sync with TimelineView.tsx.
TIMELINE_THUMB_WIDTH = 600
_prewarm_lock = threading.Lock()
_prewarm_started: set[str] = set()


def _require_active() -> ProjectContext:
    if _active is None:
        raise HTTPException(400, "No active project")
    return _active


def _push_undo(
    delete_paths: list[str],
    cluster_id: int | None = None,
    cluster_ids: list[int] | None = None,
) -> None:
    _undo_stack.append({
        "delete_paths": list(delete_paths),
        "cluster_id": cluster_id,
        "cluster_ids": list(cluster_ids or []),
    })
    if len(_undo_stack) > 10:
        _undo_stack.pop(0)


# ---------------------------------------------------------------------------
# Curation endpoints
# ---------------------------------------------------------------------------

@app.get("/api/state")
def get_state():
    if _active is None:
        return {"no_project": True}
    ctx = _active
    results_path = ctx.output_dir / "results.json"
    if not results_path.exists():
        return {"no_project": False, "needs_pipeline": True}
    data = load_results(results_path)
    delete_list_path = ctx.output_dir / "to_delete.txt"
    decisions_path = ctx.output_dir / "decisions.json"
    decisions = load_decisions(decisions_path)
    clusters = [c for c in data["clusters"] if len(c["images"]) > 1]
    singletons = [c for c in data["clusters"] if len(c["images"]) == 1]
    pending = read_delete_list(delete_list_path)
    favorites_path = ctx.output_dir / "favorites.json"
    return {
        "no_project": False,
        "needs_pipeline": False,
        "clusters": clusters,
        "singletons": singletons,
        "singleton_delete_threshold": SINGLETON_DELETE_THRESHOLD,
        "pending_delete_count": len(pending),
        "undo_available": len(_undo_stack) > 0,
        "cluster_decisions": decisions,
        "favorites": load_favorites(favorites_path),
    }


class SingletonDecision(BaseModel):
    cluster_id: int
    kept: list[str] = []
    deleted: list[str] = []


class ConfirmRequest(BaseModel):
    cluster_id: int | None = None
    delete_paths: list[str]
    all_paths: list[str] = []
    singleton_decisions: list[SingletonDecision] = []


@app.post("/api/confirm")
def confirm(req: ConfirmRequest):
    ctx = _require_active()
    for p in req.delete_paths:
        if not _in_allowed_dirs(_resolve_project_path(ctx, p), ctx):
            raise HTTPException(400, f"Path outside project: {p}")
    delete_list_path = ctx.output_dir / "to_delete.txt"
    decisions_path = ctx.output_dir / "decisions.json"
    with _curation_lock:
        favs = set(load_favorites(ctx.output_dir / "favorites.json"))
        delete_paths = [p for p in req.delete_paths if p not in favs]
        if delete_paths:
            append_to_delete_list(delete_paths, delete_list_path)
        singleton_ids = [d.cluster_id for d in req.singleton_decisions]
        _push_undo(delete_paths, req.cluster_id, singleton_ids)
        if req.cluster_id is not None:
            deleted_set = set(req.delete_paths)
            kept = [p for p in req.all_paths if p not in deleted_set]
            save_decision(decisions_path, req.cluster_id, kept, req.delete_paths)
        for d in req.singleton_decisions:
            save_decision(decisions_path, d.cluster_id, d.kept, d.deleted)
    return {"ok": True}


@app.post("/api/undo")
def undo():
    ctx = _require_active()
    delete_list_path = ctx.output_dir / "to_delete.txt"
    decisions_path = ctx.output_dir / "decisions.json"
    with _curation_lock:
        if not _undo_stack:
            raise HTTPException(400, "Nothing to undo")
        entry = _undo_stack.pop()
        if entry["delete_paths"]:
            remove_from_delete_list(set(entry["delete_paths"]), delete_list_path)
        if entry.get("cluster_id") is not None:
            remove_decision(decisions_path, entry["cluster_id"])
        for cid in entry.get("cluster_ids", []):
            remove_decision(decisions_path, cid)
    return {"ok": True}


class RestoreRequest(BaseModel):
    paths: list[str]


@app.post("/api/restore")
def restore(req: RestoreRequest):
    ctx = _require_active()
    delete_list_path = ctx.output_dir / "to_delete.txt"
    with _curation_lock:
        n = remove_from_delete_list(set(req.paths), delete_list_path)
    return {"ok": True, "restored": n}


@app.get("/api/trash")
def get_trash():
    ctx = _require_active()
    delete_list_path = ctx.output_dir / "to_delete.txt"
    return {"paths": read_delete_list(delete_list_path)}


@app.post("/api/apply-deletes")
def apply_deletes():
    """Delete every pending-delete file from the primary drive.

    The primary drive (h0) is the small one, so applying deletes has to actually
    reclaim its space rather than shuffle files into a trash folder. The mirror
    drive (h1) is left whole and becomes the sole remaining copy, so a file is
    only unlinked once its mirror has been shown to exist at a matching size —
    anything that fails that check is skipped and reported, never deleted.

    Each project's mirror directory also gets an appended plain-text list of the
    filenames deleted from the primary, so the mirror can be pruned later.
    """
    ctx = _require_active()
    delete_list_path = ctx.output_dir / "to_delete.txt"

    deleted: list[str] = []
    unmirrored: list[str] = []
    skipped = 0
    starred = 0
    freed_bytes = 0
    # Held across the whole run: a confirm landing mid-sweep would otherwise have
    # its append erased by the delete-list rewrite below, or get its file unlinked
    # before the user ever saw it in the pending list.
    with _curation_lock:
        favs = set(load_favorites(ctx.output_dir / "favorites.json"))
        all_entries = read_delete_list(delete_list_path)
        for entry in all_entries:
            if entry in favs:
                starred += 1  # starred after being marked; leave it alone
                continue
            src = _resolve_project_path(ctx, entry)
            if not _in_allowed_dirs(src, ctx) or not src.is_file():
                skipped += 1
                continue
            mirror = _mirror_path(src)
            if mirror is None or not mirror.is_file() or mirror.stat().st_size != src.stat().st_size:
                unmirrored.append(entry)
                continue
            size = src.stat().st_size
            src.unlink()
            freed_bytes += size
            deleted.append(src.name)

        manifest = _append_mirror_manifest(ctx, deleted)
        # Everything the run considered is settled except the unmirrored files, which
        # stay pending so a later run can retry them once their mirror is in place.
        remove_from_delete_list(set(all_entries) - set(unmirrored), delete_list_path)
        # Undo entries reference files that are no longer on disk — drop them.
        _undo_stack.clear()
    return {
        "ok": True,
        "deleted": len(deleted),
        "skipped": skipped,
        "starred": starred,
        "unmirrored": unmirrored,
        "freed_bytes": freed_bytes,
        "manifest": str(manifest) if manifest else None,
    }


# Photos live on two drives: the primary (small, curated) and the mirror (large,
# kept whole). The mirror reproduces the primary's tree under a prefix, so the
# path mapping is a single prefix swap.
PRIMARY_ROOT = Path(os.environ.get("SIGHTREAD_PRIMARY_ROOT", "/mnt/h0"))
MIRROR_ROOT = Path(os.environ.get("SIGHTREAD_MIRROR_ROOT", "/mnt/h1/h0"))
MIRROR_MANIFEST_NAME = ".sightread_deleted.txt"


def _mirror_path(src: Path) -> Optional[Path]:
    """Where `src` lives on the mirror drive, or None if it isn't on the primary."""
    try:
        return MIRROR_ROOT / src.relative_to(PRIMARY_ROOT)
    except ValueError:
        return None


def _append_mirror_manifest(ctx: ProjectContext, names: list[str]) -> Optional[Path]:
    """Record deleted filenames alongside the project's copy on the mirror drive."""
    if not names:
        return None
    mirror_dir = _mirror_path(ctx.folder)
    if mirror_dir is None or not mirror_dir.is_dir():
        return None
    manifest = mirror_dir / MIRROR_MANIFEST_NAME
    with manifest.open("a") as fh:
        for name in names:
            fh.write(f"{name}\n")
    return manifest


class FavoriteRequest(BaseModel):
    path: str


@app.post("/api/favorite")
def toggle_fav(req: FavoriteRequest):
    ctx = _require_active()
    abs_path = _resolve_project_path(ctx, req.path)
    if not _in_allowed_dirs(abs_path, ctx):
        raise HTTPException(400, f"Path outside project: {req.path}")
    favorites_path = ctx.output_dir / "favorites.json"
    with _curation_lock:
        favorited = toggle_favorite(favorites_path, req.path)
    return {"ok": True, "favorited": favorited}


# No ".ts": MPEG-TS shares the extension with TypeScript sources, so any code
# folder would show up full of bogus "videos". AVCHD cameras use .mts/.m2ts.
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mts", ".m2ts", ".webm"}


MIN_CLIP_SCORE_DEFAULT = 0.1


def _min_clip_score() -> float:
    try:
        return float(os.environ.get("SIGHTREAD_MIN_CLIP_SCORE", MIN_CLIP_SCORE_DEFAULT))
    except ValueError:
        return MIN_CLIP_SCORE_DEFAULT


def _load_highlights_for(output_dir: Path, paths: list[str]) -> dict:
    """Highlight clips from the pipeline's video_highlights.json, filtered to paths.

    Clips scoring below SIGHTREAD_MIN_CLIP_SCORE (default 0.1) are dropped, and
    videos with no remaining clips are omitted — not every video is clip-worthy.

    Returns {path: {"duration": float, "clips": [...]}} — empty dict when the
    file is missing or corrupt so the UI degrades cleanly.
    """
    try:
        data = json.loads((output_dir / "video_highlights.json").read_text())
        videos = data.get("videos", {})
        if not isinstance(videos, dict):
            return {}
    except Exception:
        return {}
    threshold = _min_clip_score()
    result = {}
    for p in paths:
        entry = videos.get(p)
        if isinstance(entry, dict) and isinstance(entry.get("clips"), list):
            clips = [
                c for c in entry["clips"]
                if isinstance(c, dict)
                and isinstance(c.get("score"), (int, float))
                and c["score"] >= threshold
            ]
            if clips:
                result[p] = {"duration": entry.get("duration"), "clips": clips}
    return result


def _is_exported_clip(abs_path: Path, folder: Path) -> bool:
    """True for files under <folder>/clips/ — user-exported cuts, not sources.

    Only the `clips` directory directly inside the project folder counts;
    a nested `sub/clips/` is someone's real footage folder.
    """
    try:
        rel = abs_path.relative_to(folder)
    except ValueError:
        return False
    return rel.parts[:1] == (EXPORT_DIR_NAME,)


@app.get("/api/videos")
def list_videos():
    ctx = _require_active()
    pending = set(read_delete_list(ctx.output_dir / "to_delete.txt"))
    folder = ctx.folder.resolve()
    paths = sorted(
        str(rp)
        for p in ctx.folder.rglob("*")
        if p.is_file()
        and p.suffix.lower() in VIDEO_EXTENSIONS
        and str(rp := p.resolve()) not in pending
        and not _is_exported_clip(rp, folder)
    )
    shot_times = _get_shot_times(ctx, paths)
    highlights = _load_highlights_for(ctx.output_dir, paths)
    user_clips = user_clips_for(ctx.output_dir, paths)
    # Kick off background faststart transcoding for any uncached videos
    for p_str in paths:
        p = Path(p_str)
        cached = video_cache_path(ctx.output_dir, p)
        if not cached.exists():
            _transcode_executor.submit(_transcode_bg, p, cached)
    return {
        "paths": paths,
        "shot_times": shot_times,
        "highlights": highlights,
        "user_clips": user_clips,
    }


# ---------------------------------------------------------------------------
# User-editable clips
# ---------------------------------------------------------------------------

def _validate_clip_video_path(ctx: ProjectContext, path: str) -> Path:
    """Resolve path and require an existing video file inside the project folder."""
    abs_path = _resolve_project_path(ctx, path)
    if not _is_under(abs_path, ctx.folder.resolve()):
        raise HTTPException(400, f"Path outside project: {path}")
    if not abs_path.is_file() or abs_path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(400, f"Not an existing video file: {path}")
    return abs_path


def _validate_clip_ranges(clips: list) -> list[dict]:
    """Each clip must be {"start": num, "end": num} with 0 <= start < end."""
    out = []
    for i, c in enumerate(clips):
        if not isinstance(c, dict):
            raise HTTPException(400, f"Clip {i}: must be an object with start/end")
        for field in ("start", "end"):
            v = c.get(field)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise HTTPException(400, f"Clip {i}: {field} must be a number")
        if not (0 <= c["start"] < c["end"]):
            raise HTTPException(400, f"Clip {i}: requires 0 <= start < end")
        out.append({"start": float(c["start"]), "end": float(c["end"])})
    return out


class ClipsPutRequest(BaseModel):
    path: str
    clips: list


@app.put("/api/clips")
def put_clips(req: ClipsPutRequest):
    """Overwrite the user's clip list for one video (empty list = no clips)."""
    ctx = _require_active()
    abs_path = _validate_clip_video_path(ctx, req.path)
    clips = _validate_clip_ranges(req.clips)
    stored = save_user_clips(ctx.output_dir, str(abs_path), clips)
    return {"ok": True, "clips": stored}


@app.delete("/api/clips")
def delete_clips(path: str = Query(...)):
    """Drop the user's clip list for one video, reverting it to pipeline
    suggestions."""
    ctx = _require_active()
    abs_path = _validate_clip_video_path(ctx, path)
    existed = delete_user_clips(ctx.output_dir, str(abs_path))
    return {"ok": True, "existed": existed}


class ClipsExportRequest(BaseModel):
    path: str
    mode: str = "reencode"


@app.post("/api/clips/export")
def export_clips_endpoint(req: ClipsExportRequest):
    """Cut a video's clips into <project folder>/clips/ with ffmpeg.

    Uses the user's clips when the video has a user_clips entry (an explicit
    empty list means "no clips" and is a 400, not a fallback); otherwise falls
    back to the pipeline's suggested highlight clips. Always cuts from the
    original file, never the web-transcode cache.
    """
    ctx = _require_active()
    if req.mode not in ("reencode", "copy"):
        raise HTTPException(400, f"Unknown mode: {req.mode}")
    abs_path = _validate_clip_video_path(ctx, req.path)
    key = str(abs_path)
    user = user_clips_for(ctx.output_dir, [key])
    if key in user:
        clips = user[key]["clips"]
    else:
        clips = _load_highlights_for(ctx.output_dir, [key]).get(key, {}).get("clips", [])
    if not clips:
        raise HTTPException(400, "No clips to export for this video")
    out_dir = ctx.folder / EXPORT_DIR_NAME
    try:
        files = export_clips(abs_path, clips, out_dir, mode=req.mode)
    except ClipExportError as exc:
        raise HTTPException(500, str(exc))
    return {"ok": True, "files": [str(f) for f in files]}


def _read_shot_time(path: str) -> str | None:
    EXIF_DATETIME_ORIGINAL = 36867
    EXIF_DATETIME = 306
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            for tag in (EXIF_DATETIME_ORIGINAL, EXIF_DATETIME):
                val = exif.get(tag)
                if val and isinstance(val, str):
                    try:
                        return datetime.strptime(val.strip(), "%Y:%m:%d %H:%M:%S").isoformat()
                    except ValueError:
                        pass
    except Exception:
        pass
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
    except Exception:
        return None


def _get_shot_times(ctx: ProjectContext, paths: list[str]) -> dict[str, str | None]:
    cache_path = ctx.output_dir / "shot_times.json"
    cache: dict[str, str | None] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except Exception:
            pass
    missing = [p for p in paths if p not in cache]
    if missing:
        for p in missing:
            cache[p] = _read_shot_time(p)
        try:
            cache_path.write_text(json.dumps(cache))
        except Exception:
            pass
    return {p: cache.get(p) for p in paths}


@app.get("/api/gallery")
def get_gallery():
    ctx = _require_active()
    results_path = ctx.output_dir / "results.json"
    if not results_path.exists():
        raise HTTPException(400, "Run pipeline first")
    data = load_results(results_path)
    decisions = load_decisions(ctx.output_dir / "decisions.json")

    all_photos = []
    for cluster in data["clusters"]:
        cid = cluster["cluster_id"]
        csize = len(cluster["images"])
        decision = decisions.get(str(cid))
        kept_set = set(decision["kept"]) if decision else set()
        deleted_set = set(decision["deleted"]) if decision else set()
        for img in cluster["images"]:
            p = img["path"]
            if decision is not None:
                status = "keep" if p in kept_set else "delete" if p in deleted_set else "undecided"
            else:
                status = "undecided"
            all_photos.append({"path": p, "cluster_id": cid, "cluster_size": csize, "status": status})

    shot_times = _get_shot_times(ctx, [ph["path"] for ph in all_photos])
    for ph in all_photos:
        ph["shot_at"] = shot_times.get(ph["path"])

    all_photos.sort(key=lambda p: (p["shot_at"] is None, p["shot_at"] or ""))
    _start_thumb_prewarm(ctx, [ph["path"] for ph in all_photos])
    return {"photos": all_photos}


@app.get("/api/video")
def serve_video(path: str = Query(...), cached_only: bool = False):
    ctx = _require_active()
    abs_path = _resolve_project_path(ctx, path)
    if not _in_allowed_dirs(abs_path, ctx):
        raise HTTPException(403, "Path outside project")
    if not abs_path.exists():
        raise HTTPException(404, "Not found")
    # Serve the browser-playable 1440p transcode if it's been pre-baked (see
    # video.py / the convert_videos script). Falls back to the (silent, 4K,
    # full-bitrate) original otherwise — unless the caller only wants the cache
    # (e.g. background preloads, which shouldn't pull a ~190Mbps original just
    # to warm the browser's buffer).
    cached = video_cache_path(ctx.output_dir, abs_path)
    if cached.exists():
        return FileResponse(cached, media_type="video/mp4", headers=_CACHE_HEADERS)
    if cached_only:
        raise HTTPException(404, "Not cached yet")
    return FileResponse(abs_path, headers=_CACHE_HEADERS)


_CACHE_HEADERS = {"Cache-Control": "private, max-age=86400"}


@app.get("/api/image")
def serve_image(path: str = Query(...), w: Optional[int] = None):
    ctx = _require_active()
    abs_path = _resolve_project_path(ctx, path)
    if not _in_allowed_dirs(abs_path, ctx):
        raise HTTPException(403, "Path outside project")

    if not abs_path.exists():
        raise HTTPException(404, "Not found")
    if w is None:
        return FileResponse(abs_path, headers=_CACHE_HEADERS)

    cache_file = _thumb_cache_file(ctx, abs_path, w)
    if cache_file.exists():
        return FileResponse(cache_file, media_type="image/jpeg", headers=_CACHE_HEADERS)

    data = _render_thumb(abs_path, w)
    _write_thumb(cache_file, data)
    return Response(data, media_type="image/jpeg", headers=_CACHE_HEADERS)


# Resized thumbnails are cached on disk, keyed by source path/mtime/width
def _thumb_cache_file(ctx: ProjectContext, abs_path: Path, w: int) -> Path:
    key = hashlib.sha1(
        f"{abs_path}|{abs_path.stat().st_mtime_ns}|{w}".encode()
    ).hexdigest()
    return ctx.output_dir / "thumb_cache" / f"{key}.jpg"


def _render_thumb(abs_path: Path, w: int) -> bytes:
    img = ImageOps.exif_transpose(Image.open(abs_path))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((w, w * 3), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _write_thumb(cache_file: Path, data: bytes) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    # Thread id as well as pid: the prewarm pool has several threads writing
    # thumbnails at once, and a shared temp name would let them clobber.
    tmp = cache_file.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(cache_file)


def _prewarm_one(ctx: ProjectContext, raw_path: str, w: int) -> None:
    try:
        abs_path = _resolve_project_path(ctx, raw_path)
        if not _in_allowed_dirs(abs_path, ctx) or not abs_path.is_file():
            return
        cache_file = _thumb_cache_file(ctx, abs_path, w)
        if cache_file.exists():
            return
        _write_thumb(cache_file, _render_thumb(abs_path, w))
    except Exception:
        pass  # a thumbnail that fails here is regenerated on demand by /api/image


def _start_thumb_prewarm(ctx: ProjectContext, raw_paths: list[str]) -> None:
    """Build the timeline's thumbnails in the background.

    Generating one is ~0.5s of LANCZOS resize, and the browser only opens six
    connections, so a cold project leaves the tail of the timeline grid empty
    for a long while. Prewarming turns those requests into cache hits.
    """
    key = str(ctx.output_dir)
    with _prewarm_lock:
        if key in _prewarm_started:
            return
        _prewarm_started.add(key)

    def _run() -> None:
        # Two workers: enough to stay ahead of scrolling, few enough to leave
        # the request threadpool free for tiles already on screen.
        with ThreadPoolExecutor(max_workers=2) as pool:
            for p in raw_paths:
                pool.submit(_prewarm_one, ctx, p, TIMELINE_THUMB_WIDTH)

    threading.Thread(target=_run, daemon=True).start()


def _resolve_project_path(ctx: ProjectContext, path: str) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p.resolve()
    candidate = (ctx.folder / p).resolve()
    return candidate if candidate.exists() else (PROJECT_ROOT / p).resolve()


def _in_allowed_dirs(abs_path: Path, ctx: ProjectContext) -> bool:
    # Project folder, output dir, or project root (legacy relative paths)
    return any(
        _is_under(abs_path, base) for base in (ctx.folder, ctx.output_dir, PROJECT_ROOT)
    )


def _is_under(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


if os.getenv("SIGHTREAD_TEST"):
    class _TestProjectRequest(BaseModel):
        folder: str
        output_dir: str

    @app.post("/api/_test_reset")
    def test_reset():
        _undo_stack.clear()
        return {"ok": True}

    @app.post("/api/_test_set_project")
    def test_set_project(req: _TestProjectRequest):
        global _active, _undo_stack
        _active = ProjectContext(folder=Path(req.folder), output_dir=Path(req.output_dir))
        _undo_stack.clear()
        return {"ok": True}


# ---------------------------------------------------------------------------
# Project management
# ---------------------------------------------------------------------------

class FolderRequest(BaseModel):
    folder: str


@app.get("/api/projects")
def list_projects():
    entries = load_recents()
    result = []
    for e in entries:
        folder = Path(e["folder"])
        out_dir = Path(e["output_dir"])
        if folder.exists():
            status = project_status(folder, out_dir)
        else:
            status = "never_run"
        job = current_job()
        if job and job.running and job.folder == e["folder"]:
            status = "running"
        result.append({
            "folder": e["folder"],
            "display_name": folder.name,
            "last_opened": e.get("last_opened"),
            "last_pipeline_run": e.get("last_pipeline_run"),
            "image_count": e.get("image_count", 0),
            "status": status,
        })
    return result


@app.post("/api/projects/open")
def open_project(req: FolderRequest):
    global _active, _undo_stack
    folder = Path(req.folder).resolve()
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(400, f"Not a directory: {folder}")
    out_dir = project_output_dir(folder)
    out_dir.mkdir(parents=True, exist_ok=True)
    _active = ProjectContext(folder=folder, output_dir=out_dir)
    _undo_stack.clear()
    with _prewarm_lock:
        _prewarm_started.discard(str(out_dir))  # let the next gallery load top it up
    upsert_recent(folder, out_dir)
    status = project_status(folder, out_dir)
    return {"folder": str(folder), "output_dir": str(out_dir), "status": status}


@app.post("/api/projects/run-pipeline")
def run_pipeline_endpoint(req: FolderRequest):
    global _active, _undo_stack
    folder = Path(req.folder).resolve()
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(400, f"Not a directory: {folder}")
    job = current_job()
    if job and job.running:
        raise HTTPException(409, "Pipeline already running")
    out_dir = project_output_dir(folder)
    _active = ProjectContext(folder=folder, output_dir=out_dir)
    _undo_stack.clear()
    start_pipeline(
        folder, out_dir, PROJECT_ROOT,
        on_success=lambda: upsert_recent(folder, out_dir, pipeline_ran=True),
    )
    upsert_recent(folder, out_dir)
    return {"ok": True, "folder": str(folder)}


@app.get("/api/projects/job-status")
def job_status():
    job = current_job()
    if job is None:
        return {
            "running": False, "done": False, "error": None,
            "last_line": None, "lines": [], "folder": None,
        }
    return {
        "running": job.running,
        "done": job.done,
        "error": job.error,
        "last_line": job.last_line,
        "lines": job.tail,
        "folder": job.folder,
    }


# ---------------------------------------------------------------------------
# Filesystem browser
# ---------------------------------------------------------------------------

@app.get("/api/fs/list")
def fs_list(path: str = Query(default=str(Path.home()))):
    target = Path(path).resolve()
    if not target.exists() or not target.is_dir():
        raise HTTPException(400, "Not a directory")
    parent = str(target.parent) if target != target.parent else None
    entries = []
    try:
        children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for child in children:
            if child.name.startswith(".") or not child.is_dir():
                continue
            try:
                img_count = sum(
                    1 for f in child.iterdir()
                    if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
                )
            except PermissionError:
                img_count = 0
            entries.append({
                "name": child.name,
                "path": str(child),
                "is_dir": True,
                "image_count": img_count,
            })
    except PermissionError:
        raise HTTPException(403, "Permission denied")
    return {"path": str(target), "parent": parent, "entries": entries}


# ---------------------------------------------------------------------------
# Serve built frontend
# ---------------------------------------------------------------------------

_dist = Path(__file__).parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="static")
