"""Data loading, safe-deletion, and result-management helpers."""

import json
import shutil
from pathlib import Path

TRASH_DIR = Path("outputs/trash")
RESULTS_PATH = Path("outputs/results.json")


def load_results(path: str = "outputs/results.json") -> dict:
    """Load and return parsed results JSON."""
    with open(path) as f:
        return json.load(f)


def save_results(data: dict, path: str = "outputs/results.json") -> None:
    """Write results dict back to JSON."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def delete_image_safe(image_path: str) -> str:
    """Move an image to the trash directory. Never permanently deletes.

    Returns the new path in the trash directory.
    """
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    src = Path(image_path)
    if not src.exists():
        raise FileNotFoundError(f"Image not found: {src}")
    dest = TRASH_DIR / src.name

    # Handle name collisions by appending a counter
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        counter = 1
        while dest.exists():
            dest = TRASH_DIR / f"{stem}_{counter}{suffix}"
            counter += 1

    shutil.move(str(src), str(dest))
    return str(dest)


def remove_images_from_results(
    data: dict, paths_to_remove: set[str]
) -> dict:
    """Remove deleted images from results and re-rank remaining images.

    Returns the updated results dict (also mutates in-place).
    """
    for cluster in data["clusters"]:
        cluster["images"] = [
            img for img in cluster["images"] if img["path"] not in paths_to_remove
        ]
        # Re-rank remaining images
        for new_rank, img in enumerate(
            sorted(cluster["images"], key=lambda x: x["score"], reverse=True), start=1
        ):
            img["rank"] = new_rank
        # Update best_image
        if cluster["images"]:
            cluster["best_image"] = cluster["images"][0]["path"]
        else:
            cluster["best_image"] = None

    # Remove empty clusters
    data["clusters"] = [c for c in data["clusters"] if c["images"]]
    return data
