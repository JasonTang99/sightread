"""Project registry: context, recents, staleness detection."""
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from media import (  # noqa: F401 — IMAGE_EXTENSIONS re-exported
    CITY_DIR_RE, IMAGE_EXTENSIONS, TRIP_EXPORTS_DIR, is_image, is_video, walk_media,
)

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
    # Numbered city folder under each camera, e.g. `01_Hakodate`. None when
    # the project is the whole trip (or a single camera folder).
    subtrip: str | None = None


def project_output_dir(folder: Path, subtrip: str | None = None) -> Path:
    return DATA_DIR / project_output_dir_name(folder.resolve(), subtrip)


def project_display_name(folder: Path, subtrip: str | None = None) -> str:
    if subtrip:
        return f"{folder.name} / {subtrip}"
    return folder.name


def walk_project(folder: Path, subtrip: str | None = None):
    """Media walk for a project, city folders plus time-assigned extras."""
    extras = unfiled_paths_for_subtrip(folder, subtrip) if subtrip else None
    return walk_media(folder, subtrip, extra_paths=extras)


def image_files_in(folder: Path, subtrip: str | None = None) -> set[str]:
    """Every photo the pipeline would scan — the export tree excluded."""
    return {
        str(Path(dirpath, name).resolve())
        for dirpath, names in walk_project(folder, subtrip)
        for name in names
        if is_image(name)
    }


def count_images(folder: Path, cap: int = 20_000, subtrip: str | None = None) -> int:
    """How many photos a folder holds, its camera folders included.

    The picker offers trip folders as well as camera folders, and a trip holds
    its photos one level down — counted only at the top level it would read as
    empty. Capped because this runs for every entry in a listing.
    """
    n = 0
    for _dirpath, names in walk_project(folder, subtrip):
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


def estimate_pipeline(folder: Path, output_dir: Path | None, cap: int = 20_000,
                      subtrip: str | None = None) -> PipelineEstimate:
    """How many photos a folder holds and how long running the pipeline would take.

    Only photos the project's caches do not cover count towards the time, so a
    stale trip is estimated for its new folder alone. One walk serves both
    numbers, since the picker needs the count anyway. Capped like count_images.
    """
    cached = _cached_paths(output_dir)
    if subtrip:
        cached |= _cached_paths(project_output_dir(folder))
    n = pending = 0
    pending_bytes = 0
    for dirpath, names in walk_project(folder, subtrip):
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


def project_status(folder: Path, output_dir: Path, subtrip: str | None = None) -> ProjectStatus:
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
    current = image_files_in(folder, subtrip)
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


def upsert_recent(folder: Path, output_dir: Path, pipeline_ran: bool = False,
                  subtrip: str | None = None) -> None:
    entries = load_recents()
    folder_str = str(folder.resolve())
    now = datetime.now(timezone.utc).isoformat()
    match = next(
        (e for e in entries
         if e["folder"] == folder_str and (e.get("subtrip") or None) == subtrip),
        None,
    )
    if match:
        match["last_opened"] = now
        if pipeline_ran:
            match["last_pipeline_run"] = now
            match["image_count"] = len(image_files_in(folder, subtrip))
        if subtrip:
            match["subtrip"] = subtrip
    else:
        entries.insert(0, {
            "folder": folder_str,
            "output_dir": str(output_dir),
            "last_opened": now,
            "last_pipeline_run": now if pipeline_ran else None,
            "image_count": len(image_files_in(folder, subtrip)),
            **({"subtrip": subtrip} if subtrip else {}),
        })
    entries.sort(key=lambda e: e.get("last_opened", ""), reverse=True)
    save_recents(entries[:20])


PROJECT_META = "project.json"


def write_project_meta(out_dir: Path, folder: Path, subtrip: str | None = None) -> None:
    payload = {"folder": str(Path(folder).resolve())}
    if subtrip:
        payload["subtrip"] = subtrip
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / PROJECT_META).write_text(json.dumps(payload))


def _project_meta(out_dir: Path) -> dict | None:
    p = out_dir / PROJECT_META
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _folder_for_output_dir(out_dir: Path) -> Path | None:
    """Recover the source folder a pipeline output dir belongs to.

    Prefer `project.json` (city subtrips hash folder+city, which cannot be
    reversed from the embeddings paths alone). Fall back to walking up from
    the embeddings' common ancestor and re-hashing.
    """
    meta = _project_meta(out_dir)
    if meta and meta.get("folder"):
        return Path(meta["folder"])
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


def project_output_dir_name(folder: Path, subtrip: str | None = None) -> str:
    key = str(folder)
    if subtrip:
        key = f"{key}#{subtrip}"
    return hashlib.md5(key.encode()).hexdigest()


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
        meta = _project_meta(out_dir) or {}
        entry = {
            "folder": str(folder),
            "output_dir": str(out_dir),
            "last_opened": None,
            "last_pipeline_run": _pipeline_run_time(out_dir),
            "image_count": image_count,
        }
        if meta.get("subtrip"):
            entry["subtrip"] = meta["subtrip"]
        found.append(entry)
    return found


_TRIP_NAME = re.compile(r"^\d{4}_\d{2}_")


def trip_folder(folder: Path) -> Path | None:
    """The trip a folder belongs to: its outermost `YYYY_MM_Name` ancestor, or itself."""
    for i, part in enumerate(folder.parts):
        if _TRIP_NAME.match(part):
            return Path(*folder.parts[: i + 1])
    return None


def roll_up_to_trips(entries: list[dict]) -> list[dict]:
    """One picker entry per trip, not one per device folder under it.

    Device folders (`xt5/`, `iphone/`) were run as projects before whole trips
    were, and opening the trip adopts the review done on them, so listing them
    beside it only offers a second, partial copy of the same trip. Their
    activity still counts toward when the trip was last used. A trip that has
    only device-folder projects gets an entry for the trip folder itself.

    City subtrips (`folder` + `subtrip`) are already the unit of review; they
    must not collapse into the parent trip.
    """
    by_key: dict[tuple[str, str], dict] = {}
    children: list[tuple[str, dict]] = []
    for entry in entries:
        sub = entry.get("subtrip") or ""
        if sub:
            by_key[(entry["folder"], sub)] = dict(entry)
            continue
        trip = trip_folder(Path(entry["folder"]))
        if trip is None or str(trip) == entry["folder"]:
            by_key[(entry["folder"], "")] = dict(entry)
        else:
            children.append((str(trip), entry))
    for trip, child in children:
        key = (trip, "")
        parent = by_key.get(key)
        if parent is None:
            parent = by_key[key] = {
                "folder": trip,
                "output_dir": str(DATA_DIR / project_output_dir_name(Path(trip))),
                "last_opened": None,
                "last_pipeline_run": None,
                "image_count": 0,
            }
        for k in ("last_opened", "last_pipeline_run"):
            parent[k] = max(filter(None, [parent.get(k), child.get(k)]), default=None)
    return list(by_key.values())


def _entry_id(entry: dict) -> tuple[str, str]:
    return (entry["folder"], entry.get("subtrip") or "")


_SKIP_LAYOUT = {TRIP_EXPORTS_DIR, "clips", "trash"}


def city_subtrips(trip: Path) -> list[str]:
    """`NN_City` folder names if the trip is organized that way; else empty.

    Japan 2024 is `canon/01_Hakodate`, `iphone/01_Hakodate`. A trip splits
    when *at least one* camera is city-organized (2+ `NN_City` dirs, no
    other subdirs). A flat iPhone dump next to city-split Canon still
    splits — those phone files join a city by shot time.

    Korea's `02-06` date folders and a camera-only pile of JPGs do not
    qualify, so those trips stay one picker row.
    """
    try:
        devices = sorted(p for p in trip.iterdir() if p.is_dir())
    except OSError:
        return []
    names: set[str] = set()
    saw_city_device = False
    for device in devices:
        if device.name.startswith(".") or device.name.lower() in _SKIP_LAYOUT:
            continue
        try:
            children = list(device.iterdir())
        except OSError:
            continue
        cities = {p.name for p in children if p.is_dir() and CITY_DIR_RE.match(p.name)}
        other_dirs = [
            p for p in children
            if p.is_dir() and p.name not in cities
            and p.name.lower() not in _SKIP_LAYOUT
            and not p.name.startswith(".")
        ]
        if cities and not other_dirs:
            names |= cities
            saw_city_device = True
    if not saw_city_device or len(names) < 2:
        return []
    return sorted(names)


def expand_city_subtrips(entries: list[dict]) -> list[dict]:
    """Replace a city-split trip with one picker row per city."""
    existing = {_entry_id(e): e for e in entries}
    out: list[dict] = []
    included: set[tuple[str, str]] = set()
    for e in entries:
        if e.get("subtrip"):
            continue
        folder = Path(e["folder"])
        cities = city_subtrips(folder)
        if not cities:
            out.append(e)
            included.add(_entry_id(e))
            continue
        for city in cities:
            key = (e["folder"], city)
            got = existing.get(key)
            if got is None:
                got = {
                    "folder": e["folder"],
                    "subtrip": city,
                    "output_dir": str(project_output_dir(folder, city)),
                    "last_opened": None,
                    "last_pipeline_run": None,
                    "image_count": 0,
                }
            out.append(got)
            included.add(key)
    for e in entries:
        key = _entry_id(e)
        if e.get("subtrip") and key not in included:
            out.append(e)
    return out


def _folder_city(path: Path, root: Path, cities: set[str]) -> str | None:
    """City folder a file already sits in, or None if it is unfiled."""
    try:
        parts = Path(path).resolve().relative_to(root.resolve()).parts
    except ValueError:
        return None
    for part in parts:
        if part in cities:
            return part
    return None


def _exif_unix(path: Path) -> float | None:
    try:
        from PIL import Image, ExifTags
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            raw = None
            try:
                raw = exif.get_ifd(ExifTags.IFD.Exif).get(36867)
            except Exception:
                pass
            raw = raw or exif.get(36867) or exif.get(306)
            if not raw:
                return None
            return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        return None


def _times_from_parent(trip: Path) -> dict[str, float]:
    """Shot times already paid for: parent results.json, then shot_times.json."""
    parent = project_output_dir(trip)
    times: dict[str, float] = {}
    results = parent / "results.json"
    if results.exists():
        try:
            data = json.loads(results.read_text())
        except Exception:
            data = {}
        for cluster in data.get("clusters", []):
            for img in cluster.get("images", []):
                ts = img.get("exif_timestamp")
                p = img.get("path")
                if p and ts is not None:
                    times[p] = float(ts)
    shot = parent / "shot_times.json"
    if shot.exists():
        try:
            payload = json.loads(shot.read_text())
        except Exception:
            payload = {}
        raw = payload.get("times", payload) if isinstance(payload, dict) else {}
        for p, iso in (raw or {}).items():
            if p in times or not iso:
                continue
            try:
                times[p] = datetime.fromisoformat(iso).timestamp()
            except ValueError:
                continue
    return times


def _nearest_city(t: float, ranges: dict[str, tuple[float, float]]) -> str:
    containing = [c for c, (lo, hi) in ranges.items() if lo <= t <= hi]
    if len(containing) == 1:
        return containing[0]
    if len(containing) > 1:
        return min(containing, key=lambda c: abs(t - (ranges[c][0] + ranges[c][1]) / 2))
    return min(
        ranges,
        key=lambda c: min(abs(t - ranges[c][0]), abs(t - ranges[c][1])),
    )


def unfiled_assignment(trip: Path, cities: list[str] | None = None,
                       timestamps: dict[str, float] | None = None) -> dict[str, str]:
    """Map unfiled files (no `NN_City` in the path) to a city by shot time.

    City time ranges come from files that already sit in a city folder.
    iPhone dumps and Canon `2024_02_Amsterdam` folders have no matching
    name, so they land in whichever city's range they were shot in — or
    the nearest range, for the hours between cities.
    """
    trip = Path(trip).resolve()
    cities = cities or city_subtrips(trip)
    if not cities:
        return {}
    city_set = set(cities)
    times = dict(timestamps or ())
    times.update({k: v for k, v in _times_from_parent(trip).items() if k not in times})

    filed: dict[str, list[float]] = {c: [] for c in cities}
    unfiled: list[Path] = []
    for dirpath, names in walk_media(trip):
        for name in names:
            if not (is_image(name) or is_video(name)):
                continue
            path = Path(dirpath, name)
            city = _folder_city(path, trip, city_set)
            key = str(path.resolve())
            if city:
                t = times.get(key)
                if t is None:
                    t = _exif_unix(path)
                    if t is not None:
                        times[key] = t
                if t is not None:
                    filed[city].append(t)
            else:
                unfiled.append(path)
    ranges = {c: (min(ts), max(ts)) for c, ts in filed.items() if ts}
    if not ranges or not unfiled:
        return {}
    assigned: dict[str, str] = {}
    for path in unfiled:
        key = str(path.resolve())
        t = times.get(key)
        if t is None:
            t = _exif_unix(path)
        if t is None:
            continue
        assigned[key] = _nearest_city(t, ranges)
    return assigned


def unfiled_paths_for_subtrip(trip: Path, subtrip: str) -> list[str]:
    return [p for p, city in unfiled_assignment(trip).items() if city == subtrip]


def _slice_json_by_paths(src: Path, dest: Path, keep: set[str],
                         subtrip: str | None = None) -> None:
    """Copy path-keyed JSON, keeping keys in `keep` or whose path is this city."""
    if not src.exists():
        return
    try:
        data = json.loads(src.read_text())
    except Exception:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)

    def wanted(path: str) -> bool:
        return path in keep or (subtrip is not None and subtrip in Path(path).parts)

    if src.name == "shot_times.json" and isinstance(data, dict) and "times" in data:
        times = {p: t for p, t in data["times"].items() if wanted(p)}
        dest.write_text(json.dumps({"v": data.get("v", 2), "times": times}))
        return
    if src.name == "video_tags.json" and isinstance(data, dict):
        videos = {p: t for p, t in data.get("videos", {}).items() if wanted(p)}
        dest.write_text(json.dumps({
            "schema_version": data.get("schema_version", 1),
            "tags": data.get("tags", []),
            "videos": videos,
        }))
        return
    if src.name == "user_clips.json" and isinstance(data, dict):
        videos = {p: e for p, e in data.get("videos", {}).items() if wanted(p)}
        dest.write_text(json.dumps({
            "schema_version": data.get("schema_version", 1),
            "videos": videos,
        }))
        return
    dest.write_text(json.dumps(data))


def split_city_caches(trip: Path) -> list[dict]:
    """Slice a whole-trip project's caches into one output dir per city.

    Embeddings, scores, decisions, shot times, video tags and clips are
    copied by path. Thumbnails that already exist are hardlinked. GPU work
    is not repeated. `results.json` is not copied — clustering a few hundred
    photos from the sliced embeddings is cheap and picks up later threshold
    changes.
    """
    import numpy as np

    trip = Path(trip).resolve()
    cities = city_subtrips(trip)
    if not cities:
        return []
    parent = project_output_dir(trip)
    emb_path = parent / "embeddings_dinov3_mpcls_tta.npy"
    emb_side = parent / "embeddings_dinov3_mpcls_tta.paths.json"
    if not (emb_path.exists() and emb_side.exists()):
        return []

    from utils import load_decisions, save_decisions

    emb_paths: list[str] = json.loads(emb_side.read_text())
    embeddings = np.load(str(emb_path))
    if len(embeddings) != len(emb_paths):
        return []
    emb_index = {p: i for i, p in enumerate(emb_paths)}

    score_path = parent / "scores_ensemble.npz"
    score_side = parent / "scores_ensemble.paths.json"
    scores = score_index = score_keys = None
    if score_path.exists() and score_side.exists():
        score_paths = json.loads(score_side.read_text())
        score_data = np.load(str(score_path))
        if all(len(score_data[k]) == len(score_paths) for k in score_data.files):
            scores = {k: score_data[k] for k in score_data.files}
            score_index = {p: i for i, p in enumerate(score_paths)}
            score_keys = list(scores)

    parent_decisions = load_decisions(parent)
    assignment = unfiled_assignment(trip, cities)
    reports = []

    try:
        from thumbs import COMPARE_WIDTH, GRID_MAX_WIDTH, cache_file as thumb_file
        from video import cache_path as video_file
    except ImportError:
        thumb_file = None
        video_file = None

    for city in cities:
        out = project_output_dir(trip, city)
        folder_paths = {
            str(Path(dirpath, name).resolve())
            for dirpath, names in walk_media(trip, city)
            for name in names
            if is_image(name) or is_video(name)
        }
        extra = {p for p, c in assignment.items() if c == city}
        keep_set = folder_paths | extra
        for path in parent_decisions:
            if city in Path(path).parts or path in extra:
                keep_set.add(path)

        photo_keep = [p for p in emb_paths if p in keep_set]
        out.mkdir(parents=True, exist_ok=True)
        write_project_meta(out, trip, city)

        if photo_keep:
            rows = embeddings[[emb_index[p] for p in photo_keep]]
            tmp = out / "embeddings_dinov3_mpcls_tta.npy.tmp"
            with open(tmp, "wb") as f:
                np.save(f, rows)
            os.replace(tmp, out / "embeddings_dinov3_mpcls_tta.npy")
            (out / "embeddings_dinov3_mpcls_tta.paths.json").write_text(json.dumps(photo_keep))

        if scores is not None and score_index is not None and score_keys is not None:
            score_keep = [p for p in photo_keep if p in score_index]
            if score_keep:
                payload = {
                    k: np.array([scores[k][score_index[p]] for p in score_keep], dtype=np.float32)
                    for k in score_keys
                }
                tmp = out / "scores_ensemble.npz.tmp"
                with open(tmp, "wb") as f:
                    np.savez(f, **payload)
                os.replace(tmp, out / "scores_ensemble.npz")
                (out / "scores_ensemble.paths.json").write_text(json.dumps(score_keep))

        city_decisions = {p: s for p, s in parent_decisions.items() if p in keep_set}
        if city_decisions:
            save_decisions(out, city_decisions)

        for name in ("shot_times.json", "video_tags.json", "user_clips.json"):
            _slice_json_by_paths(parent / name, out / name, keep_set, city)

        if thumb_file is not None:
            for p in photo_keep:
                src_p = Path(p)
                if not src_p.is_file():
                    continue
                try:
                    st = src_p.stat()
                except OSError:
                    continue
                for w in (GRID_MAX_WIDTH, COMPARE_WIDTH):
                    src = thumb_file(parent, src_p, w, st=st)
                    if not src.is_file():
                        continue
                    dest = thumb_file(out, src_p, w, st=st)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if dest.exists():
                        continue
                    try:
                        os.link(src, dest)
                    except OSError:
                        shutil.copy2(src, dest)

        if video_file is not None:
            for p in keep_set:
                src_p = Path(p)
                if not src_p.is_file() or not is_video(p):
                    continue
                try:
                    src = video_file(parent, src_p)
                except OSError:
                    continue
                if not src.is_file():
                    continue
                try:
                    dest = video_file(out, src_p)
                except OSError:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    continue
                try:
                    os.link(src, dest)
                except OSError:
                    shutil.copy2(src, dest)

        reports.append({
            "subtrip": city,
            "output_dir": str(out),
            "photos": len(photo_keep),
            "decisions": len(city_decisions),
        })
    return reports


def ensure_city_caches(trip: Path) -> list[dict]:
    """Split parent caches into city dirs when the parent has embeddings.

    Idempotent: a city dir that already has embeddings is left alone.
    """
    cities = city_subtrips(trip)
    if not cities:
        return []
    if not (project_output_dir(trip) / "embeddings_dinov3_mpcls_tta.npy").exists():
        return []
    missing = [
        c for c in cities
        if not (project_output_dir(trip, c) / "embeddings_dinov3_mpcls_tta.npy").exists()
    ]
    if not missing:
        return []
    return split_city_caches(trip)


def known_projects() -> list[dict]:
    """Recents merged with pipeline output found on disk, newest first."""
    merged: dict[tuple[str, str], dict] = {}
    for entry in discover_pipeline_projects():
        merged[_entry_id(entry)] = entry
    for entry in load_recents():
        key = _entry_id(entry)
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(entry)
            continue
        existing["last_opened"] = entry.get("last_opened")
        existing["last_pipeline_run"] = max(
            filter(None, [existing.get("last_pipeline_run"), entry.get("last_pipeline_run")]),
            default=None,
        )
        if entry.get("image_count"):
            existing["image_count"] = entry["image_count"]
    rolled = roll_up_to_trips(list(merged.values()))
    seen: set[str] = set()
    for entry in rolled:
        folder = entry["folder"]
        if folder in seen:
            continue
        seen.add(folder)
        try:
            ensure_city_caches(Path(folder))
        except OSError:
            pass
    entries = expand_city_subtrips(rolled)
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


def _path_in_subtrip(path: str, subtrip: str | None) -> bool:
    if not subtrip:
        return True
    return subtrip in Path(path).parts


def adopt_subfolder_reviews(folder: Path, out_dir: Path,
                            subtrip: str | None = None) -> dict[str, int]:
    """Fold reviews of a trip's device folders into the trip project.

    A project is identified by its folder path, so opening `<trip>` is a
    different project from opening `<trip>/xt5` — and reviewing the trip after
    reviewing one camera means meeting all of that camera's keepers again as
    undecided. The decisions are right there on disk under the child's own
    output dir; this brings them across.

    A city subtrip also pulls matching paths from the whole-trip project, so
    review done on Japan 2024 as one pile is not thrown away when Hakodate
    becomes its own project.

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

    sources = list(_device_folders(folder))
    if subtrip:
        parent_out = project_output_dir(folder)
        if parent_out != out_dir and parent_out.is_dir():
            sources.append(folder)

    for child in sources:
        child_out = project_output_dir(child) if child != folder else project_output_dir(folder)
        if child_out == out_dir or not child_out.is_dir():
            continue
        adopted["folders"] += 1
        for path, status in load_decisions(child_out).items():
            if path in own or path in new_decisions or not path.startswith(str(folder)):
                continue
            if not _path_in_subtrip(path, subtrip):
                continue
            new_decisions[path] = status
        adopted["video_tags"] += _adopt_video_tags(child_out, out_dir, folder, subtrip)
        adopted["clips"] += _adopt_user_clips(child_out, out_dir, folder, subtrip)
    if new_decisions:
        save_decisions(out_dir, new_decisions)
        adopted["photos"] = len(new_decisions)
    return adopted


def _adopt_video_tags(child_out: Path, out_dir: Path, folder: Path,
                      subtrip: str | None = None) -> int:
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
        and _path_in_subtrip(p, subtrip)
    }
    if not added:
        return 0
    save_video_tags(out_dir, {
        "schema_version": ours["schema_version"],
        "tags": tags,
        "videos": {**ours["videos"], **added},
    })
    return len(added)


def _adopt_user_clips(child_out: Path, out_dir: Path, folder: Path,
                      subtrip: str | None = None) -> int:
    from clips import load_user_clips, save_user_clips

    theirs = load_user_clips(child_out)
    if not theirs:
        return 0
    ours = load_user_clips(out_dir)
    added = 0
    for path, entry in theirs.items():
        if path in ours or not path.startswith(str(folder)):
            continue
        if not _path_in_subtrip(path, subtrip):
            continue
        if isinstance(entry, dict) and isinstance(entry.get("clips"), list):
            save_user_clips(out_dir, path, entry["clips"])
            added += 1
    return added
