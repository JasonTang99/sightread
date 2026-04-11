"""Data loading and safe-deletion helpers."""

import json
import os
import shutil
from pathlib import Path

TRASH_DIR = Path("outputs/trash")


def load_results(path: str = "outputs/results.json") -> dict:
    """Load and return parsed results JSON."""
    with open(path) as f:
        return json.load(f)


def delete_image_safe(image_path: str) -> str:
    """Move an image to the trash directory. Never permanently deletes.

    Returns the new path in the trash directory.
    """
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    src = Path(image_path)
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
