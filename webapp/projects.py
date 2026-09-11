"""Project registry: context, recents, staleness detection."""
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from media import IMAGE_EXTENSIONS  # noqa: F401 — re-exported; callers import it from here

_xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
_xdg_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))

CONFIG_DIR = _xdg_config / "sightread"
DATA_DIR = _xdg_data / "sightread" / "projects"
RECENTS_FILE = CONFIG_DIR / "recents.json"

ProjectStatus = Literal["ready", "stale", "never_run"]


@dataclass
class ProjectContext:
    folder: Path
    output_dir: Path


def project_output_dir(folder: Path) -> Path:
    return DATA_DIR / project_output_dir_name(folder.resolve())


def image_files_in(folder: Path) -> set[str]:
    return {
        str(p.resolve())
        for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    }


def project_status(folder: Path, output_dir: Path) -> ProjectStatus:
    sidecar = output_dir / "embeddings_dinov3_mpcls_tta.paths.json"
    if not sidecar.exists():
        return "never_run"
    # Embeddings alone are not something the app can open: every review view is
    # built from results.json, and /api/state refuses without it. A run that
    # embedded and then died before ranking used to report "ready" anyway, so
    # the picker offered an Open button that led straight to "Run pipeline
    # first". Rerunning is the way out, and it is cheap — the embeddings that
    # are on disk are reused.
    if not (output_dir / "results.json").exists():
        return "never_run"
    try:
        processed = set(json.loads(sidecar.read_text()))
    except Exception:
        return "never_run"
    current = image_files_in(folder)
    return "ready" if processed == current else "stale"


def load_recents() -> list[dict]:
    if not RECENTS_FILE.exists():
        return []
    try:
        return json.loads(RECENTS_FILE.read_text())
    except Exception:
        return []


def save_recents(entries: list[dict]) -> None:
    RECENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RECENTS_FILE.write_text(json.dumps(entries, indent=2))


def upsert_recent(folder: Path, output_dir: Path, pipeline_ran: bool = False) -> None:
    entries = load_recents()
    folder_str = str(folder.resolve())
    now = datetime.now(timezone.utc).isoformat()
    match = next((e for e in entries if e["folder"] == folder_str), None)
    if match:
        match["last_opened"] = now
        if pipeline_ran:
            match["last_pipeline_run"] = now
            match["image_count"] = len(image_files_in(folder))
    else:
        entries.insert(0, {
            "folder": folder_str,
            "output_dir": str(output_dir),
            "last_opened": now,
            "last_pipeline_run": now if pipeline_ran else None,
            "image_count": len(image_files_in(folder)),
        })
    entries.sort(key=lambda e: e.get("last_opened", ""), reverse=True)
    save_recents(entries[:20])


def _folder_for_output_dir(out_dir: Path) -> Path | None:
    """Recover the source folder a pipeline output dir belongs to.

    The output dir name is an md5 of the folder path, so it cannot be reversed
    directly. The embeddings sidecar holds absolute image paths, though: their
    common ancestor is at or below the project folder, so walking up from it
    and re-hashing finds the folder that produced this dir.
    """
    sidecar = out_dir / "embeddings_dinov3_mpcls_tta.paths.json"
    if not sidecar.exists():
        return None
    try:
        paths = json.loads(sidecar.read_text())
    except Exception:
        return None
    if not paths:
        return None
    try:
        candidate = Path(os.path.commonpath([str(p) for p in paths]))
    except ValueError:  # paths on different drives — nothing sensible to do
        return None
    if candidate.suffix:  # commonpath of a single image is the image itself
        candidate = candidate.parent
    while True:
        if project_output_dir_name(candidate) == out_dir.name:
            return candidate
        if candidate.parent == candidate:
            return None
        candidate = candidate.parent


def project_output_dir_name(folder: Path) -> str:
    return hashlib.md5(str(folder).encode()).hexdigest()


def _pipeline_run_time(out_dir: Path) -> str | None:
    newest = 0.0
    for name in ("results.json", "embeddings_dinov3_mpcls_tta.paths.json"):
        f = out_dir / name
        if f.exists():
            newest = max(newest, f.stat().st_mtime)
    if not newest:
        return None
    return datetime.fromtimestamp(newest, timezone.utc).isoformat()


def discover_pipeline_projects() -> list[dict]:
    """Every folder with pipeline output on disk, recents.json or not.

    Pipelines run from the CLI never touch recents.json, so the picker used to
    hide them. Scanning the data dir picks those runs up too.
    """
    if not DATA_DIR.exists():
        return []
    found = []
    for out_dir in DATA_DIR.iterdir():
        if not out_dir.is_dir():
            continue
        folder = _folder_for_output_dir(out_dir)
        if folder is None:
            continue
        sidecar = out_dir / "embeddings_dinov3_mpcls_tta.paths.json"
        try:
            image_count = len(json.loads(sidecar.read_text()))
        except Exception:
            image_count = 0
        found.append({
            "folder": str(folder),
            "output_dir": str(out_dir),
            "last_opened": None,
            "last_pipeline_run": _pipeline_run_time(out_dir),
            "image_count": image_count,
        })
    return found


def known_projects() -> list[dict]:
    """Recents merged with pipeline output found on disk, newest first."""
    merged: dict[str, dict] = {}
    for entry in discover_pipeline_projects():
        merged[entry["folder"]] = entry
    for entry in load_recents():
        existing = merged.get(entry["folder"])
        if existing is None:
            merged[entry["folder"]] = dict(entry)
            continue
        # recents knows when it was opened; the disk knows when it last ran
        existing["last_opened"] = entry.get("last_opened")
        existing["last_pipeline_run"] = max(
            filter(None, [existing.get("last_pipeline_run"), entry.get("last_pipeline_run")]),
            default=None,
        )
        if entry.get("image_count"):
            existing["image_count"] = entry["image_count"]
    entries = list(merged.values())
    entries.sort(
        key=lambda e: max(e.get("last_opened") or "", e.get("last_pipeline_run") or ""),
        reverse=True,
    )
    return entries


# ---------------------------------------------------------------------------
# Finished projects
# ---------------------------------------------------------------------------
DONE_FILENAME = "curation_done.json"

# Everything under these is derived from files that still exist: thumbnails and
# posters re-render from the originals, transcodes re-encode from them. They are
# also nearly all of what a project occupies — one finished trip here holds
# 298MB of thumbnails and 430MB of transcodes against 2MB of actual decisions.
# Deliberately not listed: results.json, decisions.json, the embeddings, and
# video_highlights_cache. The first two are the curation itself, and the other
# two are model output that costs a pipeline run to rebuild, not a resize.
DERIVED_CACHE_DIRS = ("thumb_cache", "poster_cache", "video_cache")

# Pipeline outputs a re-run recomputes. decisions.json is kept on purpose.
PIPELINE_CACHE_FILES = (
    "embeddings_dinov3_mpcls_tta.npy",
    "embeddings_dinov3_mpcls_tta.paths.json",
    "embeddings_dinov3_mpcls_tta.hash",
    "scores_ensemble.npz",
    "scores_ensemble.paths.json",
    "clusters.json",
    "results.json",
    "video_highlights.json",
)
PIPELINE_CACHE_DIRS = ("video_highlights_cache",)


def done_file(output_dir: Path) -> Path:
    return Path(output_dir) / DONE_FILENAME


def is_done(output_dir: Path) -> str | None:
    """When curation was marked finished, or None if it wasn't."""
    try:
        return json.loads(done_file(output_dir).read_text()).get("done_at")
    except Exception:
        return None


def mark_done(output_dir: Path) -> str:
    done_at = datetime.now(timezone.utc).isoformat()
    path = done_file(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"schema_version": 1, "done_at": done_at}, indent=2) + "\n")
    tmp.replace(path)
    return done_at


def clear_done(output_dir: Path) -> None:
    done_file(output_dir).unlink(missing_ok=True)


def evict_derived_caches(output_dir: Path) -> int:
    """Delete the regenerable caches for a project. Returns bytes freed.

    Missing directories are not an error: a project may never have had videos,
    and marking an already-evicted project done again should be a no-op rather
    than a failure.
    """
    output_dir = Path(output_dir)
    freed = 0
    for name in DERIVED_CACHE_DIRS:
        d = output_dir / name
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if f.is_file():
                try:
                    freed += f.stat().st_size
                except OSError:
                    pass
        shutil.rmtree(d, ignore_errors=True)
    return freed


def pipeline_cache_inventory(output_dir: Path) -> dict:
    """Bytes and paths of removable pipeline artifacts (not derived previews)."""
    output_dir = Path(output_dir)
    files: list[str] = []
    bytes_total = 0
    for name in PIPELINE_CACHE_FILES:
        p = output_dir / name
        if not p.is_file():
            continue
        try:
            bytes_total += p.stat().st_size
        except OSError:
            pass
        files.append(name)
    for name in PIPELINE_CACHE_DIRS:
        d = output_dir / name
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if f.is_file():
                try:
                    bytes_total += f.stat().st_size
                except OSError:
                    pass
        files.append(f"{name}/")
    return {"files": files, "bytes": bytes_total}


def clean_pipeline_cache(output_dir: Path) -> dict:
    """Remove pipeline outputs so the project cannot be re-reviewed.

    decisions.json and other curation artifacts are kept. Returns a summary for
    the finish-trip UI.
    """
    output_dir = Path(output_dir)
    removed: list[str] = []
    freed = 0
    for name in PIPELINE_CACHE_FILES:
        p = output_dir / name
        if not p.is_file():
            continue
        try:
            freed += p.stat().st_size
        except OSError:
            pass
        p.unlink()
        removed.append(name)
    for name in PIPELINE_CACHE_DIRS:
        d = output_dir / name
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if f.is_file():
                try:
                    freed += f.stat().st_size
                except OSError:
                    pass
        shutil.rmtree(d, ignore_errors=True)
        removed.append(f"{name}/")
    return {"removed": removed, "freed_bytes": freed}
