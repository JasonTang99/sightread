"""Project registry: context, recents, staleness detection."""
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from media import IMAGE_EXTENSIONS, is_image, walk_media  # noqa: F401 — IMAGE_EXTENSIONS re-exported

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
    """Every photo the pipeline would scan — the export tree excluded."""
    return {
        str(Path(dirpath, name).resolve())
        for dirpath, names in walk_media(folder)
        for name in names
        if is_image(name)
    }


def count_images(folder: Path, cap: int = 20_000) -> int:
    """How many photos a folder holds, its camera folders included.

    The picker offers trip folders as well as camera folders, and a trip holds
    its photos one level down — counted only at the top level it would read as
    empty. Capped because this runs for every entry in a listing.
    """
    n = 0
    for _dirpath, names in walk_media(folder):
        for name in names:
            if is_image(name):
                n += 1
                if n >= cap:
                    return n
    return n


# What a pipeline run costs, least-squares fitted to whole-run wall time of the
# batch of 2026-09-13 on the RTX 3060 Ti:
#
#   run                        pending      MB   actual   fit    old fit
#   2026_03_Alyeska                 85     232     96 s   91 s     83 s
#   2026_04_Mammoth_Easter         106     231    118 s  107 s     93 s
#   2026_01_Panorama               210     363    169 s  186 s    148 s
#   2026_07_Climbing                98    1632    121 s  123 s    180 s
#   2026_07_Hawaii (google photos) 731    1944    597 s  594 s    485 s
#
# The 2026-09-12 fit (30 s + 0.45 s/photo + 0.065 s/MB) was built from per-photo
# scoring rates and ran 12-21% low on every trip here while overcharging big
# files by half: Climbing's 17 MB photos cost barely more than Alyeska's 3 MB
# ones. Per-photo work now carries more of the cost, partly because each shooting
# day starts its own DataLoader since the per-day checkpoints (52a6cd3), roughly
# 2-3 s a day per stage. The estimate cannot see day counts without reading EXIF,
# so that folds into the per-photo rate.
#
# Every run in the batch shared the GPU with ~2.9 GB of other processes and hit
# the clipiqa+ per-image OOM fallback, so a run with the card to itself should
# come in under this. Within 10% on these five, but three constants fitted to five
# runs is thin — still expect ±30% elsewhere.
ETA_FIXED_S = 25.0
ETA_PER_PHOTO_S = 0.74
ETA_PER_MB_S = 0.016


@dataclass
class PipelineEstimate:
    image_count: int
    pending: int  # photos the caches do not cover yet
    eta_s: float | None  # None when there is nothing to run


def _cached_paths(output_dir: Path | None) -> set[str]:
    """Photos a rerun would load from cache rather than score again.

    The scores sidecar is written after the embeddings one, so a photo in it
    has been through both expensive stages.
    """
    if output_dir is None:
        return set()
    try:
        return set(json.loads((output_dir / "scores_ensemble.paths.json").read_text()))
    except Exception:
        return set()


def estimate_pipeline(folder: Path, output_dir: Path | None, cap: int = 20_000) -> PipelineEstimate:
    """How many photos a folder holds and how long running the pipeline would take.

    Only photos the project's caches do not cover count towards the time, so a
    stale trip is estimated for its new folder alone. One walk serves both
    numbers, since the picker needs the count anyway. Capped like count_images.
    """
    cached = _cached_paths(output_dir)
    n = pending = 0
    pending_bytes = 0
    for dirpath, names in walk_media(folder):
        for name in names:
            if not is_image(name):
                continue
            n += 1
            path = Path(dirpath, name)
            if cached and str(path.resolve()) in cached:
                continue
            pending += 1
            try:
                pending_bytes += path.stat().st_size
            except OSError:
                pass
        if n >= cap:
            break
    has_results = output_dir is not None and (output_dir / "results.json").exists()
    if n == 0 or (pending == 0 and has_results):
        return PipelineEstimate(n, pending, None)
    eta = ETA_FIXED_S + pending * ETA_PER_PHOTO_S + pending_bytes / 1e6 * ETA_PER_MB_S
    return PipelineEstimate(n, pending, eta)


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


# ---------------------------------------------------------------------------
# Adopting reviews done on a device folder
# ---------------------------------------------------------------------------
# How deep under a trip folder a device folder can sit. One level covers
# `xt5/`, two covers the dated subfolders older imports used (`canon/02-06/`).
_ADOPT_DEPTH = 2
# Folders that are output, not camera input.
_ADOPT_SKIP = {"_exports", "clips", "trash"}


def _device_folders(folder: Path, depth: int = _ADOPT_DEPTH):
    """Subfolders of a trip that could have been reviewed as their own project."""
    if depth <= 0:
        return
    try:
        children = sorted(p for p in folder.iterdir() if p.is_dir())
    except OSError:
        return
    for child in children:
        if child.name.startswith(".") or child.name.lower() in _ADOPT_SKIP:
            continue
        yield child
        yield from _device_folders(child, depth - 1)


def adopt_subfolder_reviews(folder: Path, out_dir: Path) -> dict[str, int]:
    """Fold reviews of a trip's device folders into the trip project.

    A project is identified by its folder path, so opening `<trip>` is a
    different project from opening `<trip>/xt5` — and reviewing the trip after
    reviewing one camera means meeting all of that camera's keepers again as
    undecided. The decisions are right there on disk under the child's own
    output dir; this brings them across.

    The trip's own decisions always win, so this is safe to run on every open
    and safe to run twice: it only ever fills in paths the trip has no opinion
    about. Deletes that were already applied come across too — they name files
    that are gone, which is exactly what stops them being re-proposed.
    """
    from utils import load_decisions, save_decisions  # local: utils imports media, not projects

    adopted = {"photos": 0, "video_tags": 0, "clips": 0, "folders": 0}
    folder = Path(folder).resolve()
    out_dir = Path(out_dir)
    own = load_decisions(out_dir)
    new_decisions: dict[str, str] = {}
    for child in _device_folders(folder):
        child_out = project_output_dir(child)
        if child_out == out_dir or not child_out.is_dir():
            continue
        adopted["folders"] += 1
        for path, status in load_decisions(child_out).items():
            # A path the child decided about but that does not belong to this
            # trip would be someone else's business; in practice they match.
            if path in own or path in new_decisions or not path.startswith(str(folder)):
                continue
            new_decisions[path] = status
        # Tags and clips are adopted whether or not that folder holds photo
        # decisions: a camera folder can be all video.
        adopted["video_tags"] += _adopt_video_tags(child_out, out_dir, folder)
        adopted["clips"] += _adopt_user_clips(child_out, out_dir, folder)
    if new_decisions:
        save_decisions(out_dir, new_decisions)
        adopted["photos"] = len(new_decisions)
    return adopted


def _adopt_video_tags(child_out: Path, out_dir: Path, folder: Path) -> int:
    from video_tags import load_video_tags, save_video_tags

    theirs = load_video_tags(child_out)
    if not theirs["videos"]:
        return 0
    ours = load_video_tags(out_dir)
    # A tag the child used has to exist here before an assignment to it will
    # load — load_video_tags drops assignments naming an unknown tag.
    tags = list(ours["tags"]) + [t for t in theirs["tags"] if t not in ours["tags"]]
    added = {
        p: t for p, t in theirs["videos"].items()
        if p not in ours["videos"] and p.startswith(str(folder))
    }
    if not added:
        return 0
    save_video_tags(out_dir, {
        "schema_version": ours["schema_version"],
        "tags": tags,
        "videos": {**ours["videos"], **added},
    })
    return len(added)


def _adopt_user_clips(child_out: Path, out_dir: Path, folder: Path) -> int:
    from clips import load_user_clips, save_user_clips

    theirs = load_user_clips(child_out)
    if not theirs:
        return 0
    ours = load_user_clips(out_dir)
    added = 0
    for path, entry in theirs.items():
        if path in ours or not path.startswith(str(folder)):
            continue
        if isinstance(entry, dict) and isinstance(entry.get("clips"), list):
            save_user_clips(out_dir, path, entry["clips"])
            added += 1
    return added
