"""FastAPI backend for Sightread webapp."""
import hashlib
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
from PIL import Image
from pydantic import BaseModel

# Must precede the local imports below so `uvicorn webapp.server:app` (run from
# the repo root, e.g. by the test suite) resolves them.
sys.path.insert(0, str(Path(__file__).parent))

import thumbs
from utils import (
    DELETED,
    FAVORITE,
    KEPT,
    SINGLETON_DELETE_THRESHOLD,
    TO_DELETE,
    invalidate_results_cache,
    load_decisions,
    load_results,
    migrate_project_state,
    paths_with_status,
    save_decisions,
    sort_clusters_chronologically,
)

from projects import (
    IMAGE_EXTENSIONS,
    ProjectContext,
    image_files_in,
    known_projects,
    project_output_dir,
    project_status,
    upsert_recent,
)
from jobs import JobState, current_job, start_pipeline
from video import (
    cache_path as video_cache_path,
    extract_poster,
    poster_path as video_poster_path,
    transcode_for_web,
)
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
from contextlib import contextmanager

def _deprioritise() -> None:
    """Drop this worker thread, and every ffmpeg it spawns, below normal.

    A single 4K HEVC transcode measured 81 threads and 1307% CPU on this
    16-core box and two run at once, so while the queue drains it is competing
    with the thumbnails the user is actually waiting on. Measured with two
    transcodes running, a 2400px render took 997ms against 334ms idle; at
    nice 15 it takes 509ms. Capping ffmpeg's own thread count instead only got
    it to 818ms, so this is the knob that matters.

    Linux niceness is per-thread and is inherited across fork/exec, so setting
    it in the pool's initializer covers the subprocesses without touching the
    request threads.
    """
    os.nice(15)


# Transcodes are pure lookahead — nothing plays until the user clicks a video —
# so they yield to everything else.
_transcode_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="transcode", initializer=_deprioritise
)
# Poster frames get their own worker: one ffmpeg keyframe grab is quick, but
# queueing it behind a transcode would leave the timeline grid blank for as
# long as that transcode runs. Left at normal priority — unlike a transcode,
# a poster is a tile the user is looking at right now.
_poster_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="poster")
_transcode_inflight: set[Path] = set()
_transcode_lock = threading.Lock()

# Same idea as _transcode_inflight, one entry per destination cache file: the
# background prewarm and a tile the user is looking at routinely ask for the
# same uncached thumbnail or poster at once, and without this both pay the
# decode (~0.5s of LANCZOS) or the ffmpeg spin to write identical bytes.
_render_inflight: set[Path] = set()
_render_lock = threading.Condition()


@contextmanager
def _render_once(dest: Path):
    """Yield True if this caller should render dest, False if it is now cached.

    A caller that loses the race waits for the winner rather than returning
    early: /api/image has to answer with the bytes either way, and waiting on
    a render already in flight beats starting a second one.
    """
    with _render_lock:
        while dest in _render_inflight:
            _render_lock.wait()
        if dest.exists():
            yield False
            return
        _render_inflight.add(dest)
    try:
        yield True
    finally:
        with _render_lock:
            _render_inflight.discard(dest)
            _render_lock.notify_all()


# A file ffmpeg cannot transcode fails the same way every time, and /api/videos
# resubmits every uncached video on each switch to the timeline or videos tab —
# so without a memory of the failure one bad file spins ffmpeg over the NAS on
# every tab switch, forever. Forgotten on restart, which is when the file or
# ffmpeg may have changed.
_transcode_failed: set[Path] = set()


def _transcode_bg(src: Path, dest: Path) -> None:
    with _transcode_lock:
        if src in _transcode_inflight or src in _transcode_failed or dest.exists():
            return
        _transcode_inflight.add(src)
    failed = False
    try:
        transcode_for_web(src, dest)
        log.info("transcoded %s", src.name)
    except Exception as exc:
        failed = True
        log.warning("transcode failed %s: %s", src.name, exc)
    finally:
        with _transcode_lock:
            _transcode_inflight.discard(src)
            if failed:
                _transcode_failed.add(src)

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

# Every endpoint that mutates decisions.json or the undo stack does a
# read-modify-write, so two overlapping requests can drop one side's edit entirely. Uvicorn runs sync handlers on a threadpool, and the
# thumbnail prewarmer adds background load on top, so the overlap is routine
# rather than theoretical. Serialise the writers; readers are left alone.
_curation_lock = threading.Lock()

# Widths the two review surfaces request; kept in sync with TimelineView.tsx
# (the grid) and ClusterView.tsx (the side-by-side compare).
TIMELINE_THUMB_WIDTH = thumbs.GRID_MAX_WIDTH
COMPARE_THUMB_WIDTH = thumbs.COMPARE_WIDTH

# Thumbnailing is libjpeg decode plus PIL resampling, and both release the GIL,
# so the prewarm pool scales with cores instead of sitting at two: on this
# machine 24 cold 40MP frames take 15.4s one at a time and 2.6s twelve at a
# time. Half the box, capped, keeps enough cores free for the request
# threadpool to serve a tile the user is actually looking at.
_PREWARM_WORKERS = max(2, min(8, (os.cpu_count() or 4) // 2))
_prewarm_lock = threading.Lock()
_prewarm_started: set[str] = set()


def _require_active() -> ProjectContext:
    if _active is None:
        raise HTTPException(400, "No active project")
    return _active


UNDO_FILENAME = "undo.jsonl"
_UNDO_DEPTH = 10


def _undo_file(ctx: ProjectContext) -> Path:
    return ctx.output_dir / UNDO_FILENAME


def _save_undo(ctx: ProjectContext) -> None:
    """Mirror the in-memory stack to disk, one entry per line.

    The stack used to be memory-only, so an accidental restart — or the reload
    the dev server does on any edit — silently took undo with it while leaving
    decisions.json fully written. Rewriting the whole file each time keeps disk
    and memory in step without an append/trim split; at ten entries of a few
    paths each it is well under a kilobyte.
    """
    path = _undo_file(ctx)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(json.dumps(e) + "\n" for e in _undo_stack))
        tmp.replace(path)
    except OSError as exc:
        # Losing the persistence is not worth failing the confirm that earned it.
        log.warning("Could not persist undo stack to %s: %s", path, exc)


def _load_undo(ctx: ProjectContext) -> None:
    """Restore the stack for a newly-active project; empty if there is none."""
    entries: list[dict] = []
    path = _undo_file(ctx)
    try:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if isinstance(entry, dict) and isinstance(entry.get("previous"), dict):
                entries.append(entry)
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read undo stack from %s: %s", path, exc)
    _undo_stack[:] = entries[-_UNDO_DEPTH:]


def _clear_undo(ctx: ProjectContext) -> None:
    _undo_stack.clear()
    _undo_file(ctx).unlink(missing_ok=True)


def _push_undo(ctx: ProjectContext, previous: dict[str, str | None]) -> None:
    """Remember each touched photo's prior status so undo can restore it.

    A status is a single slot now, so undo has to put back what was there —
    clearing to undecided would silently discard an earlier decision.
    """
    _undo_stack.append({"previous": dict(previous)})
    if len(_undo_stack) > _UNDO_DEPTH:
        _undo_stack.pop(0)
    _save_undo(ctx)


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
    decisions = load_decisions(ctx.output_dir)
    clusters = sort_clusters_chronologically(
        [c for c in data["clusters"] if len(c["images"]) > 1]
    )
    singletons = [c for c in data["clusters"] if len(c["images"]) == 1]
    # The app lands in the cluster view, so a user who never opens the timeline
    # would otherwise never trigger a prewarm and would pay for every render as
    # they reached it. Review order, so the pool stays ahead of the cursor.
    _start_thumb_prewarm(
        ctx,
        [
            img["path"]
            for c in clusters + singletons
            for img in c["images"]
            if decisions.get(img["path"]) != DELETED
        ],
    )
    return {
        "no_project": False,
        "needs_pipeline": False,
        "clusters": clusters,
        "singletons": singletons,
        "singleton_delete_threshold": SINGLETON_DELETE_THRESHOLD,
        "pending_delete_count": len(paths_with_status(decisions, TO_DELETE)),
        "undo_available": len(_undo_stack) > 0,
        "photo_decisions": decisions,
        "favorites": paths_with_status(decisions, FAVORITE),
    }


class ConfirmRequest(BaseModel):
    delete_paths: list[str] = []
    # Every photo this confirmation decides on, deletes included. Whatever is
    # not in delete_paths is recorded as kept.
    decided_paths: list[str] = []


@app.post("/api/confirm")
def confirm(req: ConfirmRequest):
    ctx = _require_active()
    for p in req.delete_paths:
        if not _in_allowed_dirs(_resolve_project_path(ctx, p), ctx):
            raise HTTPException(400, f"Path outside project: {p}")
    marked = set(req.delete_paths)
    decided = list(dict.fromkeys([*req.decided_paths, *req.delete_paths]))
    with _curation_lock:
        current = load_decisions(ctx.output_dir)
        updates: dict[str, str | None] = {}
        previous: dict[str, str | None] = {}
        for p in decided:
            was = current.get(p)
            # A star outranks a delete mark; an applied delete is history and
            # must not be resurrected as a pending one.
            if was in (FAVORITE, DELETED):
                continue
            now = TO_DELETE if p in marked else KEPT
            if was == now:
                continue
            updates[p] = now
            previous[p] = was
        if updates:
            save_decisions(ctx.output_dir, updates)
            _push_undo(ctx, previous)
    return {"ok": True}


class AutoKeepBestRequest(BaseModel):
    # Only sweep clusters whose best image scores at least this well; the rest
    # are left for the human. None means every undecided cluster.
    min_score: Optional[float] = None


@app.post("/api/auto-keep-best")
def auto_keep_best(req: AutoKeepBestRequest):
    """Keep each undecided cluster's top-ranked image and queue the rest.

    This is the choice the cluster view already pre-selects, applied in bulk to
    the clusters nobody has looked at yet. It pushes a single undo entry for the
    whole sweep — undoing a hundred clusters one confirm at a time would blow
    past the ten-deep stack and strand most of it.

    Singletons are excluded: there is no "best" among one image, so the same
    action there would just be a blind delete.
    """
    ctx = _require_active()
    results_path = ctx.output_dir / "results.json"
    if not results_path.exists():
        raise HTTPException(400, "Run pipeline first")
    data = load_results(results_path)

    with _curation_lock:
        current = load_decisions(ctx.output_dir)
        updates: dict[str, str | None] = {}
        previous: dict[str, str | None] = {}
        clusters_touched = 0
        for cluster in data["clusters"]:
            images = cluster["images"]
            if len(images) < 2:
                continue
            # "Undecided" means untouched: a cluster the user has partly worked
            # through is theirs, not the sweep's.
            if any(img["path"] in current for img in images):
                continue
            if req.min_score is not None and max(img["score"] for img in images) < req.min_score:
                continue
            clusters_touched += 1
            for img in images:
                p = img["path"]
                now = KEPT if img["rank"] == 1 else TO_DELETE
                updates[p] = now
                previous[p] = current.get(p)  # None — nothing decided here yet
        if updates:
            save_decisions(ctx.output_dir, updates)
            _push_undo(ctx, previous)

    kept = sum(1 for v in updates.values() if v == KEPT)
    return {
        "ok": True,
        "clusters": clusters_touched,
        "kept": kept,
        "queued": len(updates) - kept,
    }


@app.post("/api/undo")
def undo():
    ctx = _require_active()
    with _curation_lock:
        if not _undo_stack:
            raise HTTPException(400, "Nothing to undo")
        entry = _undo_stack.pop()
        save_decisions(ctx.output_dir, entry["previous"])
        _save_undo(ctx)
    return {"ok": True}


class RestoreRequest(BaseModel):
    paths: list[str]


@app.post("/api/restore")
def restore(req: RestoreRequest):
    """Pull photos back out of the delete queue."""
    ctx = _require_active()
    with _curation_lock:
        current = load_decisions(ctx.output_dir)
        updates: dict[str, str | None] = {
            p: KEPT for p in req.paths if current.get(p) == TO_DELETE
        }
        if updates:
            save_decisions(ctx.output_dir, updates)
    return {"ok": True, "restored": len(updates)}


@app.get("/api/trash")
def get_trash():
    ctx = _require_active()
    return {"paths": paths_with_status(load_decisions(ctx.output_dir), TO_DELETE)}


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
    # An unmounted primary drive makes every source file look already-gone, and
    # the sweep would settle the entire queue as `deleted` — photos that are
    # still perfectly intact, now hidden from the gallery. Refuse to run at all
    # rather than record a project-wide deletion that never happened.
    if not ctx.folder.is_dir():
        raise HTTPException(409, f"Project folder unavailable: {ctx.folder}. Is the drive mounted?")

    deleted: list[str] = []
    unmirrored: list[str] = []
    skipped = 0
    freed_bytes = 0
    # Held across the whole run: a confirm landing mid-sweep would otherwise be
    # erased by the status rewrite below, or get its file unlinked before the
    # user ever saw it in the pending list.
    with _curation_lock:
        # Only TO_DELETE is ever swept. A starred photo cannot hold that status
        # — the two are one slot — so protection is structural, not a filter.
        queue = paths_with_status(load_decisions(ctx.output_dir), TO_DELETE)
        updates: dict[str, str | None] = {}
        for entry in queue:
            src = _resolve_project_path(ctx, entry)
            if not _in_allowed_dirs(src, ctx):
                skipped += 1
                continue
            if not src.is_file():
                # Already gone — settle the record rather than re-queue it
                # forever, but still report it so the count covers every entry
                # that left the queue.
                updates[entry] = DELETED
                skipped += 1
                continue
            mirror = _mirror_path(src)
            if mirror is None or not mirror.is_file() or mirror.stat().st_size != src.stat().st_size:
                # Stays queued so a later run retries once the mirror is in place.
                unmirrored.append(entry)
                continue
            size = src.stat().st_size
            src.unlink()
            freed_bytes += size
            deleted.append(src.name)
            updates[entry] = DELETED

        manifest = _append_mirror_manifest(ctx, deleted)
        if updates:
            save_decisions(ctx.output_dir, updates)
        # Undo entries reference files that are no longer on disk — drop them,
        # on disk as well as in memory, so a restart can't resurrect them.
        _clear_undo(ctx)
    return {
        "ok": True,
        "deleted": len(deleted),
        "skipped": skipped,
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
    """Star or unstar. Starring outranks a pending delete and cancels it.

    Unstarring falls back to `kept` rather than to whatever the photo was
    before: a star is a stronger keep, so releasing it should never hand the
    photo back to the delete queue.
    """
    ctx = _require_active()
    abs_path = _resolve_project_path(ctx, req.path)
    if not _in_allowed_dirs(abs_path, ctx):
        raise HTTPException(400, f"Path outside project: {req.path}")
    with _curation_lock:
        current = load_decisions(ctx.output_dir)
        if current.get(req.path) == FAVORITE:
            favorited = False
            save_decisions(ctx.output_dir, {req.path: KEPT})
        elif current.get(req.path) == DELETED:
            favorited = False  # already unlinked; nothing left to protect
        else:
            favorited = True
            save_decisions(ctx.output_dir, {req.path: FAVORITE})
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


def _scan_videos(ctx: ProjectContext) -> list[str]:
    """Every video on disk under the project folder, exported cuts excluded.

    Pending deletes are included: the timeline shows a video's decision the way
    it shows a photo's, so it needs the marked ones too. Callers that only
    review undecided footage filter on `statuses`.

    Filters on the filename before touching the filesystem. The previous
    `rglob("*")` + `is_file()` stat'd every entry in the tree — thousands of
    JPEGs and sidecars per trip — to find a few dozen videos, and this runs on
    every timeline open, not just the first.
    """
    folder = ctx.folder.resolve()
    found: list[str] = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if os.path.splitext(name)[1].lower() not in VIDEO_EXTENSIONS:
                continue
            rp = Path(root, name).resolve()
            if not _is_exported_clip(rp, folder):
                found.append(str(rp))
    return sorted(found)


@app.get("/api/videos")
def list_videos():
    ctx = _require_active()
    decisions = load_decisions(ctx.output_dir)
    paths = _scan_videos(ctx)
    statuses = {p: _grid_status(decisions.get(p)) for p in paths}
    shot_times = _get_shot_times(ctx, paths)
    highlights = _load_highlights_for(ctx.output_dir, paths)
    user_clips = user_clips_for(ctx.output_dir, paths)
    # Kick off background faststart transcoding for any uncached videos
    for p_str in paths:
        p = Path(p_str)
        cached = video_cache_path(ctx.output_dir, p)
        if not cached.exists():
            _transcode_executor.submit(_transcode_bg, p, cached)
    _start_poster_prewarm(ctx, paths)
    return {
        "paths": paths,
        "statuses": statuses,
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


# EXIF timestamps never change for a file that hasn't been rewritten, so the
# on-disk cache is authoritative once warm. Holding it in memory too means the
# common case — every path already known — costs nothing at all, where before
# each /api/gallery and /api/videos call re-parsed the whole JSON.
_shot_times_lock = threading.Lock()
_shot_times_mem: dict[Path, tuple[tuple[int, int] | None, dict[str, str | None]]] = {}


def _get_shot_times(
    ctx: ProjectContext,
    paths: list[str],
    known: dict[str, str | None] | None = None,
) -> dict[str, str | None]:
    """Shot time per path, reading EXIF only for paths not already cached.

    ``known`` supplies timestamps the caller already has — the pipeline reads
    every photo's EXIF anyway and records it in results.json, so the gallery
    passes those in rather than making us reopen a few hundred files over the
    NAS to learn what is already on disk. They are still written to the cache,
    so a later /api/videos call over the same paths costs nothing either.

    Serialised: the gallery and the video list both extend the same file, and
    two unsynchronised read-modify-writes meant whichever finished last dropped
    the other's freshly-read timestamps — re-reading that EXIF on every
    subsequent request. The write is atomic for the same reason decisions.json
    is: a half-written cache is unparseable and gets discarded wholesale.
    """
    cache_path = ctx.output_dir / "shot_times.json"
    with _shot_times_lock:
        try:
            st = cache_path.stat()
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            key = None
        hit = _shot_times_mem.get(cache_path)
        if hit is not None and hit[0] == key:
            cache = hit[1]
        else:
            cache = {}
            if key is not None:
                try:
                    loaded = json.loads(cache_path.read_text())
                    if isinstance(loaded, dict):
                        cache = loaded
                except (OSError, json.JSONDecodeError):
                    pass  # rebuilt from EXIF below

        missing = [p for p in paths if p not in cache]
        if missing:
            for p in missing:
                supplied = known.get(p) if known else None
                cache[p] = supplied if supplied is not None else _read_shot_time(p)
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(cache))
                tmp.replace(cache_path)
                st = cache_path.stat()
                key = (st.st_mtime_ns, st.st_size)
            except OSError:
                key = None  # don't memoise against a stat we couldn't take
        _shot_times_mem[cache_path] = (key, cache)
        return {p: cache.get(p) for p in paths}


# The grid only distinguishes surviving from doomed; a star is a keep.
_GRID_STATUS = {KEPT: "keep", FAVORITE: "keep", TO_DELETE: "delete"}


def _grid_status(decision: str | None) -> str:
    """Collapse a stored decision to what the timeline grid draws."""
    return _GRID_STATUS.get(decision, "undecided")


@app.get("/api/gallery")
def get_gallery():
    ctx = _require_active()
    results_path = ctx.output_dir / "results.json"
    if not results_path.exists():
        raise HTTPException(400, "Run pipeline first")
    data = load_results(results_path)
    decisions = load_decisions(ctx.output_dir)

    all_photos = []
    pipeline_times: dict[str, str | None] = {}
    for cluster in data["clusters"]:
        cid = cluster["cluster_id"]
        csize = len(cluster["images"])
        for img in cluster["images"]:
            p = img["path"]
            ts = img.get("exif_timestamp")
            if ts is not None:
                pipeline_times[p] = datetime.fromtimestamp(ts).isoformat()
            # Applied deletes are gone from disk — results.json still lists them,
            # but showing them would mean broken thumbnails and day counts that
            # include photos the user can no longer act on.
            if decisions.get(p) == DELETED:
                continue
            status = _grid_status(decisions.get(p))
            all_photos.append({"path": p, "cluster_id": cid, "cluster_size": csize, "status": status})

    shot_times = _get_shot_times(ctx, [ph["path"] for ph in all_photos], known=pipeline_times)
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


@app.get("/api/video-poster")
def serve_video_poster(request: Request, path: str = Query(...), w: int = TIMELINE_THUMB_WIDTH):
    """One still frame per video, so grids can show footage without loading it.

    A timeline day full of <video> elements starves the lazy <img> loads around
    it — the browser only opens six connections and media takes priority, so
    photos further down the page never get requested at all. Posters keep the
    grid to plain images.
    """
    ctx = _require_active()
    abs_path = _resolve_project_path(ctx, path)
    if not _in_allowed_dirs(abs_path, ctx):
        raise HTTPException(403, "Path outside project")
    try:
        st = abs_path.stat()
    except OSError:
        raise HTTPException(404, "Not found")

    etag = _media_etag(abs_path, "poster", w, st=st)
    headers = {**_CACHE_HEADERS, "ETag": etag}
    if _not_modified(request, etag):
        return Response(status_code=304, headers=headers)

    cache_file = video_poster_path(ctx.output_dir, abs_path, w, st=st)
    if not cache_file.exists():
        with _render_once(cache_file) as mine:
            if mine:
                try:
                    extract_poster(abs_path, cache_file, w)
                except Exception as e:
                    raise HTTPException(500, f"Poster extraction failed: {e}")
    return FileResponse(cache_file, media_type="image/jpeg", headers=headers)


_CACHE_HEADERS = {"Cache-Control": "private, max-age=86400"}


def _media_etag(abs_path: Path, *parts, st: os.stat_result | None = None) -> str:
    """A strong ETag over the source's identity, mtime and any render options.

    Derived rather than stored: every cache key in this file is already
    (path, mtime_ns, size...), so the tag changes exactly when the bytes would.

    Callers that have already stat()ed the source pass it in: the sources live
    on a spinning-disk NAS, and every tile request otherwise stats the same
    file for its existence check, its ETag and its cache key.
    """
    if st is None:
        st = abs_path.stat()
    raw = "|".join(str(p) for p in (abs_path, st.st_mtime_ns, st.st_size, *parts))
    return f'"{hashlib.sha1(raw.encode()).hexdigest()}"'


def _not_modified(request: Request, etag: str) -> bool:
    """True when the client already holds this exact entity.

    max-age alone stops re-requests only until it lapses, and a revalidation
    without an ETag re-sends the whole JPEG. If-None-Match turns that into a
    304 — the timeline revalidates hundreds of tiles at once, so the difference
    is a few hundred bytes against a few hundred megabytes.
    """
    header = request.headers.get("if-none-match")
    if not header:
        return False
    # A client may send several tags, and a cache may have weakened ours.
    return any(t.strip().removeprefix("W/") == etag for t in header.split(","))


@app.get("/api/image")
def serve_image(request: Request, path: str = Query(...), w: Optional[int] = None):
    ctx = _require_active()
    abs_path = _resolve_project_path(ctx, path)
    if not _in_allowed_dirs(abs_path, ctx):
        raise HTTPException(403, "Path outside project")

    try:
        st = abs_path.stat()
    except OSError:
        raise HTTPException(404, "Not found")

    etag = _media_etag(abs_path, w, st=st)
    if _not_modified(request, etag):
        return Response(status_code=304, headers={**_CACHE_HEADERS, "ETag": etag})
    headers = {**_CACHE_HEADERS, "ETag": etag}

    if w is None:
        return FileResponse(abs_path, headers=headers, stat_result=st)

    cache_file = _thumb_cache_file(ctx, abs_path, w, st=st)
    if cache_file.exists():
        return FileResponse(cache_file, media_type="image/jpeg", headers=headers)

    with _render_once(cache_file) as mine:
        if not mine:
            return FileResponse(cache_file, media_type="image/jpeg", headers=headers)
        data = _render_thumb(abs_path, w)
        _write_thumb(cache_file, data)
    return Response(data, media_type="image/jpeg", headers=headers)


# Both the webapp and the pipeline write this cache, so the key scheme and the
# resize/quality choices live in thumbs.py. These stay as module-level names
# because the prewarm path and the tests reach for them.
def _thumb_cache_file(
    ctx: ProjectContext, abs_path: Path, w: int, st: os.stat_result | None = None
) -> Path:
    return thumbs.cache_file(ctx.output_dir, abs_path, w, st=st)


def _render_thumb(abs_path: Path, w: int) -> bytes:
    return thumbs.render(abs_path, w)


def _write_thumb(cache_file: Path, data: bytes) -> None:
    thumbs.write(cache_file, data)


def _prewarm_one(ctx: ProjectContext, raw_path: str, w: int) -> None:
    try:
        abs_path = _resolve_project_path(ctx, raw_path)
        if not _in_allowed_dirs(abs_path, ctx) or not abs_path.is_file():
            return
        cache_file = _thumb_cache_file(ctx, abs_path, w)
        if cache_file.exists():
            return
        with _render_once(cache_file) as mine:
            if mine:
                _write_thumb(cache_file, _render_thumb(abs_path, w))
    except Exception:
        pass  # a thumbnail that fails here is regenerated on demand by /api/image


def _start_thumb_prewarm(ctx: ProjectContext, raw_paths: list[str]) -> None:
    """Build both review surfaces' thumbnails in the background.

    A cold 40MP frame costs ~0.5s at grid width and ~0.9s at compare width, and
    the browser only opens six connections, so without this the tail of the
    timeline stays empty and the first visit to every cluster waits on renders
    the server could have done while the user was reading the one before it.
    Prewarming turns both into cache hits.
    """
    key = str(ctx.output_dir)
    with _prewarm_lock:
        if key in _prewarm_started:
            return
        _prewarm_started.add(key)

    def _run() -> None:
        with ThreadPoolExecutor(max_workers=_PREWARM_WORKERS) as pool:
            # Grid width first and to completion. It is what fills the screen on
            # arrival, each one is half the work of a compare render, and the
            # compare pass is only ever ahead of the user, never blocking them.
            for w in (TIMELINE_THUMB_WIDTH, COMPARE_THUMB_WIDTH):
                # _prewarm_one swallows its own failures, so nothing raises here.
                for f in [pool.submit(_prewarm_one, ctx, p, w) for p in raw_paths]:
                    f.result()

    threading.Thread(target=_run, daemon=True).start()


def _prewarm_poster(ctx: ProjectContext, raw_path: str, w: int) -> None:
    try:
        abs_path = _resolve_project_path(ctx, raw_path)
        if not _in_allowed_dirs(abs_path, ctx) or not abs_path.is_file():
            return
        cache_file = video_poster_path(ctx.output_dir, abs_path, w)
        if cache_file.exists():
            return
        with _render_once(cache_file) as mine:
            if mine:
                extract_poster(abs_path, cache_file, w)
    except Exception:
        pass  # regenerated on demand by /api/video-poster


def _start_poster_prewarm(ctx: ProjectContext, raw_paths: list[str]) -> None:
    """Same idea as the thumbnail prewarmer, for the timeline's video tiles.

    Kept on its own started-set key so a project whose photos are already
    prewarmed still gets its posters built, and on its own single-worker
    executor so posters aren't stuck behind minutes of transcoding — the grid
    needs them immediately, the transcode only matters once playback starts.
    """
    key = f"posters:{ctx.output_dir}"
    with _prewarm_lock:
        if key in _prewarm_started:
            return
        _prewarm_started.add(key)

    for p in raw_paths:
        _poster_executor.submit(_prewarm_poster, ctx, p, TIMELINE_THUMB_WIDTH)


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
        if _active is not None:
            _clear_undo(_active)
        else:
            _undo_stack.clear()
        return {"ok": True}

    @app.post("/api/_test_set_project")
    def test_set_project(req: _TestProjectRequest):
        global _active
        _active = ProjectContext(folder=Path(req.folder), output_dir=Path(req.output_dir))
        _load_undo(_active)
        return {"ok": True}


# ---------------------------------------------------------------------------
# Project management
# ---------------------------------------------------------------------------

class FolderRequest(BaseModel):
    folder: str


@app.get("/api/projects")
def list_projects():
    entries = known_projects()
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
    # Opening a project is the only way it becomes active, so it is the one
    # place legacy curation state has to be folded into decisions.json.
    with _curation_lock:
        migrate_project_state(out_dir)
    _active = ProjectContext(folder=folder, output_dir=out_dir)
    # Undo survives a restart now, so reopening a project picks its stack back
    # up rather than starting blank.
    _load_undo(_active)
    with _prewarm_lock:
        # let the next gallery / video load top these up
        _prewarm_started.discard(str(out_dir))
        _prewarm_started.discard(f"posters:{out_dir}")
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
    with _curation_lock:
        migrate_project_state(out_dir)
    _active = ProjectContext(folder=folder, output_dir=out_dir)
    # A re-run renumbers clusters but decisions and undo are keyed by photo
    # path, so the stack stays meaningful across it.
    _load_undo(_active)
    invalidate_results_cache(out_dir / "results.json")
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
def fs_list(path: str = Query(default=str(PRIMARY_ROOT / "Editing" / "imports"))):
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
