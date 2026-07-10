"""Data loading, safe-deletion, and result-management helpers."""

import json
import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

SINGLETON_DELETE_THRESHOLD = 0.4


def load_results(path) -> dict:
    with open(path) as f:
        return json.load(f)


def read_delete_list(path) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    lines = p.read_text().splitlines()
    return [ln for ln in lines if ln.strip()]


def append_to_delete_list(paths: list[str], delete_file) -> int:
    existing = set(read_delete_list(delete_file))
    new_paths = [p for p in paths if p not in existing]
    if new_paths:
        with open(delete_file, "a") as f:
            for p in new_paths:
                f.write(p + "\n")
    return len(new_paths)


def remove_from_delete_list(paths: set[str], delete_file) -> int:
    existing = read_delete_list(delete_file)
    kept = [p for p in existing if p not in paths]
    removed = len(existing) - len(kept)
    Path(delete_file).write_text("\n".join(kept) + ("\n" if kept else ""))
    return removed


def load_decisions(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        # Don't let a later save_decision silently overwrite a corrupt file
        backup = p.with_suffix(p.suffix + ".corrupt")
        try:
            shutil.copy2(p, backup)
        except OSError:
            pass
        log.warning("Could not parse %s; backed up to %s and starting fresh", p, backup)
        return {}


def save_decision(path, cluster_id: int, kept: list[str], deleted: list[str]) -> None:
    decisions = load_decisions(path)
    decisions[str(cluster_id)] = {"kept": kept, "deleted": deleted}
    Path(path).write_text(json.dumps(decisions, indent=2))


def remove_decision(path, cluster_id: int) -> None:
    decisions = load_decisions(path)
    decisions.pop(str(cluster_id), None)
    Path(path).write_text(json.dumps(decisions, indent=2))


def load_favorites(path) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []


def toggle_favorite(path, image_path: str) -> bool:
    """Add or remove image_path from favorites. Returns True if now favorited."""
    favs = load_favorites(path)
    if image_path in favs:
        favs = [f for f in favs if f != image_path]
        now_fav = False
    else:
        favs.append(image_path)
        now_fav = True
    Path(path).write_text(json.dumps(favs, indent=2))
    return now_fav
