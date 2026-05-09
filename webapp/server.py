"""FastAPI backend for Sightread webapp."""
import copy
import io
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel

from utils import (
    SINGLETON_DELETE_THRESHOLD,
    append_to_delete_list,
    load_results,
    read_delete_list,
    remove_from_delete_list,
    remove_images_from_results,
    save_results,
)

sys.path.insert(0, str(Path(__file__).parent))
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

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

app = FastAPI()

_active: ProjectContext | None = None
_undo_stack: list[dict] = []


def _require_active() -> ProjectContext:
    if _active is None:
        raise HTTPException(400, "No active project")
    return _active


def _push_undo(results_snap: dict, delete_paths: list[str]) -> None:
    _undo_stack.append({"results": copy.deepcopy(results_snap), "delete_paths": list(delete_paths)})
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
    clusters = [c for c in data["clusters"] if len(c["images"]) > 1]
    singletons = [c for c in data["clusters"] if len(c["images"]) == 1]
    delete_list_path = ctx.output_dir / "to_delete.txt"
    pending = read_delete_list(delete_list_path)
    return {
        "no_project": False,
        "needs_pipeline": False,
        "clusters": clusters,
        "singletons": singletons,
        "singleton_delete_threshold": SINGLETON_DELETE_THRESHOLD,
        "pending_delete_count": len(pending),
        "undo_available": len(_undo_stack) > 0,
    }


class ConfirmRequest(BaseModel):
    delete_paths: list[str]
    all_paths: list[str] = []


@app.post("/api/confirm")
def confirm(req: ConfirmRequest):
    ctx = _require_active()
    results_path = ctx.output_dir / "results.json"
    delete_list_path = ctx.output_dir / "to_delete.txt"
    data = load_results(results_path)
    paths_to_remove = set(req.all_paths) if req.all_paths else set(req.delete_paths)
    if req.delete_paths:
        append_to_delete_list(req.delete_paths, delete_list_path)
    if paths_to_remove:
        _push_undo(data, req.delete_paths)
        remove_images_from_results(data, paths_to_remove)
        save_results(data, results_path)
    return {"ok": True}


@app.post("/api/undo")
def undo():
    ctx = _require_active()
    if not _undo_stack:
        raise HTTPException(400, "Nothing to undo")
    entry = _undo_stack.pop()
    results_path = ctx.output_dir / "results.json"
    delete_list_path = ctx.output_dir / "to_delete.txt"
    save_results(entry["results"], results_path)
    if entry["delete_paths"]:
        remove_from_delete_list(set(entry["delete_paths"]), delete_list_path)
    return {"ok": True}


class RestoreRequest(BaseModel):
    paths: list[str]


@app.post("/api/restore")
def restore(req: RestoreRequest):
    ctx = _require_active()
    delete_list_path = ctx.output_dir / "to_delete.txt"
    n = remove_from_delete_list(set(req.paths), delete_list_path)
    return {"ok": True, "restored": n}


@app.get("/api/trash")
def get_trash():
    ctx = _require_active()
    delete_list_path = ctx.output_dir / "to_delete.txt"
    return {"paths": read_delete_list(delete_list_path)}


@app.get("/api/image")
def serve_image(path: str = Query(...), w: Optional[int] = None):
    ctx = _require_active()
    p = Path(path)
    if p.is_absolute():
        abs_path = p.resolve()
    else:
        candidate = (ctx.folder / p).resolve()
        abs_path = candidate if candidate.exists() else (PROJECT_ROOT / p).resolve()

    # Must be under project folder, output dir, or project root (legacy)
    allowed = (ctx.folder, ctx.output_dir, PROJECT_ROOT)
    if not any(_is_under(abs_path, base) for base in allowed):
        raise HTTPException(403, "Path outside project")

    if not abs_path.exists():
        raise HTTPException(404, "Not found")
    if w is None:
        return FileResponse(abs_path)
    img = ImageOps.exif_transpose(Image.open(abs_path))
    img.thumbnail((w, w * 3), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return Response(buf.getvalue(), media_type="image/jpeg")


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
    start_pipeline(folder, out_dir, PROJECT_ROOT)
    upsert_recent(folder, out_dir)
    return {"ok": True, "folder": str(folder)}


@app.get("/api/projects/job-status")
def job_status():
    job = current_job()
    if job is None:
        return {"running": False, "done": False, "error": None, "last_line": None, "folder": None}
    return {
        "running": job.running,
        "done": job.done,
        "error": job.error,
        "last_line": job.last_line,
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
