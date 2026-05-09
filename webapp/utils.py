"""Data loading, safe-deletion, and result-management helpers."""

import json
import shutil
from pathlib import Path

SINGLETON_DELETE_THRESHOLD = 0.4


def load_results(path) -> dict:
    with open(path) as f:
        return json.load(f)


def save_results(data: dict, path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def remove_images_from_results(data: dict, paths_to_remove: set[str]) -> dict:
    for cluster in data["clusters"]:
        cluster["images"] = [
            img for img in cluster["images"] if img["path"] not in paths_to_remove
        ]
        for new_rank, img in enumerate(
            sorted(cluster["images"], key=lambda x: x["score"], reverse=True), start=1
        ):
            img["rank"] = new_rank
        if cluster["images"]:
            cluster["best_image"] = cluster["images"][0]["path"]
        else:
            cluster["best_image"] = None
    data["clusters"] = [c for c in data["clusters"] if c["images"]]
    return data


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
