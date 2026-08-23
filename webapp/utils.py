"""Data loading, curation-state, and result-management helpers.

Curation state is one file per project — `decisions.json` — holding a single
status per photo path:

    {"schema_version": 2, "photos": {"/abs/path.JPG": "kept", ...}}

    (absent)     never reviewed
    kept         reviewed, staying
    favorite     starred; a stronger `kept` that deletion must never touch
    to_delete    marked for deletion, not yet applied — this *is* the queue
    deleted      unlinked from the primary drive; the record of what went

Status is keyed by photo path rather than cluster id because cluster ids are
assigned per pipeline run, so re-running renumbers them and strands every
decision. It is a single slot rather than parallel files because the previous
split — decisions.json plus to_delete.txt plus favorites.json — had no
mechanism keeping the three in agreement, and they drifted in practice: photos
marked kept sat in the delete queue regardless.
"""

import json
import logging
import shutil
import threading
from pathlib import Path

log = logging.getLogger(__name__)

SINGLETON_DELETE_THRESHOLD = 0.4

SCHEMA_VERSION = 2

KEPT = "kept"
FAVORITE = "favorite"
TO_DELETE = "to_delete"
DELETED = "deleted"
_STATUSES = frozenset({KEPT, FAVORITE, TO_DELETE, DELETED})

# Statuses that protect a photo from the delete sweep. Only TO_DELETE is ever
# acted on, but naming the safe set makes the guard explicit at its call sites.
PROTECTED = frozenset({KEPT, FAVORITE, DELETED})

DECISIONS_FILENAME = "decisions.json"
LEGACY_DELETE_LIST = "to_delete.txt"
LEGACY_FAVORITES = "favorites.json"


# results.json is written once by the pipeline and read on every /api/state and
# /api/gallery call, so the same ~200 KB was being re-parsed for each of them.
# Keyed on (mtime_ns, size) rather than mtime alone so a same-second rewrite by
# a re-run pipeline still invalidates.
_results_cache: dict[Path, tuple[tuple[int, int], dict]] = {}
_results_cache_lock = threading.Lock()


def load_results(path) -> dict:
    """Parse results.json, reusing the previous parse while the file is unchanged.

    The returned dict is shared between callers — treat it as read-only. Callers
    that need to reshape it (filtering clusters, attaching shot times) must build
    their own containers rather than mutating this one.
    """
    path = Path(path)
    try:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None  # let the open() below raise the real error
    if key is not None:
        with _results_cache_lock:
            hit = _results_cache.get(path)
        if hit is not None and hit[0] == key:
            return hit[1]
    with open(path) as f:
        data = json.load(f)
    if key is not None:
        with _results_cache_lock:
            _results_cache[path] = (key, data)
    return data


def invalidate_results_cache(path=None) -> None:
    """Drop cached parses. Called when a pipeline run rewrites results.json."""
    with _results_cache_lock:
        if path is None:
            _results_cache.clear()
        else:
            _results_cache.pop(Path(path), None)


def cluster_shot_at(cluster: dict) -> float | None:
    """First EXIF timestamp in a cluster, or None when no image carries one."""
    ts = cluster.get("cluster_timestamp")
    if ts is not None:
        return float(ts)
    # results.json written before cluster_timestamp existed: derive it.
    shot = [img["exif_timestamp"] for img in cluster.get("images", []) if img.get("exif_timestamp") is not None]
    return min(shot) if shot else None


def sort_clusters_chronologically(clusters: list[dict]) -> list[dict]:
    """Oldest first. Clusters with no EXIF have no place on the timeline, so
    they trail the rest ordered by score."""
    return sorted(
        clusters,
        key=lambda c: (
            cluster_shot_at(c) is None,
            cluster_shot_at(c) or 0.0,
            -c.get("cluster_score", 0.0),
        ),
    )


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------
def _write_json_atomic(path: Path, payload) -> None:
    """Write via temp file + rename so a crash can't truncate the real file.

    Same pattern clips.py and the video-highlights writer already use. It
    matters most here: a truncated decisions.json is unparseable, and the
    recovery path below discards it, taking the whole curation session with it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
def decisions_file(output_dir) -> Path:
    return Path(output_dir) / DECISIONS_FILENAME


def _read_raw(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        # Don't let a later save silently overwrite a corrupt file
        backup = path.with_suffix(path.suffix + ".corrupt")
        try:
            shutil.copy2(path, backup)
        except OSError:
            pass
        log.warning("Could not parse %s; backed up to %s and starting fresh", path, backup)
        return None


def merge_legacy_state(
    legacy_decisions: dict,
    queued: set[str],
    favorites: set[str],
    still_on_disk,
) -> dict[str, str]:
    """Fold the old three-file state into one status per photo.

    Conflicts resolve away from deletion, never toward it: a star beats
    everything, and an explicit keep beats a stale queue entry. A legacy
    `deleted` record whose file is already gone becomes DELETED; one whose file
    survives was still pending, so it becomes TO_DELETE.
    """
    flat: dict[str, str] = {}
    for value in legacy_decisions.values():
        if not isinstance(value, dict):
            continue
        for p in value.get("kept", []):
            flat[p] = KEPT
        for p in value.get("deleted", []):
            flat[p] = DELETED

    merged: dict[str, str] = {}
    for p in set(flat) | queued | favorites:
        if p in favorites:
            merged[p] = FAVORITE
        elif flat.get(p) == KEPT:
            merged[p] = KEPT  # an explicit keep outranks a stale queue entry
        elif p in queued or flat.get(p) == DELETED:
            merged[p] = TO_DELETE if still_on_disk(p) else DELETED
        else:
            merged[p] = KEPT
    return merged


def _legacy_state(output_dir: Path, raw) -> dict[str, str]:
    queued: set[str] = set()
    delete_list = output_dir / LEGACY_DELETE_LIST
    if delete_list.exists():
        queued = {ln for ln in delete_list.read_text().splitlines() if ln.strip()}

    favorites: set[str] = set()
    fav_file = output_dir / LEGACY_FAVORITES
    if fav_file.exists():
        try:
            favorites = set(json.loads(fav_file.read_text()))
        except Exception:
            log.warning("Could not parse %s; ignoring", fav_file)

    return merge_legacy_state(
        raw if isinstance(raw, dict) else {},
        queued,
        favorites,
        lambda p: Path(p).exists(),
    )


def load_decisions(output_dir) -> dict[str, str]:
    """Return {photo path: status}. Reads legacy state without rewriting it.

    Conversion is read-only here so that a plain GET never mutates a project;
    `migrate_project_state` does the durable rewrite when a project is opened.
    """
    output_dir = Path(output_dir)
    raw = _read_raw(decisions_file(output_dir))
    if raw is None:
        # decisions.json absent, but a legacy queue or favorites file may not be
        if (output_dir / LEGACY_DELETE_LIST).exists() or (output_dir / LEGACY_FAVORITES).exists():
            return _legacy_state(output_dir, {})
        return {}
    if not isinstance(raw, dict):
        return {}
    if raw.get("schema_version") == SCHEMA_VERSION:
        photos = raw.get("photos", {})
        return {k: v for k, v in photos.items() if v in _STATUSES}
    return _legacy_state(output_dir, raw)


def save_decisions(output_dir, updates: dict[str, str | None]) -> dict[str, str]:
    """Apply status updates; a None value clears the photo back to undecided."""
    decisions = load_decisions(output_dir)
    for path, status in updates.items():
        if status is None:
            decisions.pop(path, None)
        elif status in _STATUSES:
            decisions[path] = status
        else:
            raise ValueError(f"Unknown decision status: {status!r}")
    _write_json_atomic(
        decisions_file(output_dir),
        {"schema_version": SCHEMA_VERSION, "photos": decisions},
    )
    return decisions


def migrate_project_state(output_dir) -> bool:
    """Rewrite legacy state as one versioned file. Returns True if it converted.

    The superseded files are renamed rather than removed — this is the only
    copy of a curation session, and the merge resolves real conflicts.
    """
    output_dir = Path(output_dir)
    target = decisions_file(output_dir)
    raw = _read_raw(target)
    if isinstance(raw, dict) and raw.get("schema_version") == SCHEMA_VERSION:
        return False
    delete_list = output_dir / LEGACY_DELETE_LIST
    fav_file = output_dir / LEGACY_FAVORITES
    if raw is None and not delete_list.exists() and not fav_file.exists():
        return False

    merged = _legacy_state(output_dir, raw if isinstance(raw, dict) else {})
    _write_json_atomic(target, {"schema_version": SCHEMA_VERSION, "photos": merged})
    for old in (delete_list, fav_file):
        if old.exists():
            old.replace(old.with_suffix(old.suffix + ".migrated"))
    log.info("Migrated %s to schema %d (%d photos)", output_dir, SCHEMA_VERSION, len(merged))
    return True


# ---------------------------------------------------------------------------
# Derived views
# ---------------------------------------------------------------------------
def paths_with_status(decisions: dict[str, str], status: str) -> list[str]:
    return [p for p, v in decisions.items() if v == status]


def pending_deletes(output_dir) -> list[str]:
    """The delete queue — derived, so it cannot disagree with the record."""
    return paths_with_status(load_decisions(output_dir), TO_DELETE)


def favorites(output_dir) -> list[str]:
    return paths_with_status(load_decisions(output_dir), FAVORITE)


# One shot on disk is several files: the JPEG the pipeline ranked, the camera's
# raw beside it, and an `.xmp` per file once anything has touched them in a raw
# editor. Only the JPEG is ever in the decision queue, so deleting a shot means
# finding the rest by name.
RAW_EXTENSIONS = {
    ".raf", ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2",
    ".dng", ".orf", ".rw2", ".raw", ".pef", ".srw", ".3fr", ".erf",
    ".mef", ".mos", ".iiq", ".x3f",
}
SIDECAR_SUFFIX = ".xmp"


def sidecars_of(src: Path) -> list[Path]:
    """Every other file on disk belonging to the same shot as `src`.

    Matched by stem within the one directory rather than by scanning: a trip
    folder holds thousands of entries and this runs per queued shot. Both
    sidecar spellings are covered — `DSCF1234.JPG.xmp` (appended, what most
    editors write) and `DSCF1234.xmp` (replaced).
    """
    out: list[Path] = []
    seen = {src}

    def add(candidate: Path) -> None:
        if candidate not in seen and candidate.is_file():
            seen.add(candidate)
            out.append(candidate)

    add(src.with_name(src.name + SIDECAR_SUFFIX))
    add(src.with_suffix(SIDECAR_SUFFIX))
    for ext in RAW_EXTENSIONS:
        for raw in (src.with_suffix(ext), src.with_suffix(ext.upper())):
            if not raw.is_file():
                continue
            add(raw)
            add(raw.with_name(raw.name + SIDECAR_SUFFIX))
    return out
