#!/usr/bin/env python3
"""Delete images listed in the to-delete file.

Reads outputs/to_delete.txt and moves each listed image to outputs/trash/.
Never permanently deletes files.

Usage:
    python scripts/delete_marked.py
    python scripts/delete_marked.py --delete-file outputs/to_delete.txt --root /path/to/photos
    python scripts/delete_marked.py --dry-run
"""

import argparse
import os
import shutil
from pathlib import Path

_OUTPUT_DIR = Path(os.environ.get("SIGHTREAD_OUTPUT_DIR", "outputs"))
TRASH_DIR = _OUTPUT_DIR / "trash"


def _check_path_contained(src: Path, root: Path) -> bool:
    """Return True if src is under root (prevents stale list from escaping project dir)."""
    try:
        src.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def delete_marked(
    delete_file: str = "outputs/to_delete.txt",
    dry_run: bool = False,
    root: str | None = None,
) -> None:
    """Read the delete list and move each file to trash."""
    delete_path = Path(delete_file)
    if not delete_path.exists():
        print(f"No delete file found at {delete_path}. Nothing to do.")
        return

    paths = [line.strip() for line in delete_path.read_text().splitlines() if line.strip()]
    if not paths:
        print("Delete file is empty. Nothing to do.")
        return

    root_path = Path(root).resolve() if root else None

    print(f"Found {len(paths)} image(s) to delete")
    if dry_run:
        print("DRY RUN — no files will be moved\n")

    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    moved = 0
    skipped = 0

    for p in paths:
        src = Path(p)

        if root_path is not None and not _check_path_contained(src, root_path):
            print(f"  Skip (outside root {root_path}): {p}")
            skipped += 1
            continue

        if not src.exists():
            print(f"  Skip (not found): {p}")
            skipped += 1
            continue

        if dry_run:
            print(f"  Would move: {p} → {TRASH_DIR}/")
            moved += 1
            continue

        dest = TRASH_DIR / src.name
        if dest.exists():
            stem, suffix = dest.stem, dest.suffix
            counter = 1
            while dest.exists():
                dest = TRASH_DIR / f"{stem}_{counter}{suffix}"
                counter += 1

        shutil.move(str(src), str(dest))
        print(f"  Moved: {p} → {dest}")
        moved += 1

    if not dry_run:
        delete_path.write_text("")
        print(f"\n✅ Moved {moved} image(s) to {TRASH_DIR}/ ({skipped} skipped)")
        print(f"Cleared {delete_path}")
    else:
        print(f"\n🔍 Would move {moved} image(s), skip {skipped}")


def main():
    parser = argparse.ArgumentParser(description="Delete images listed in the to-delete file")
    parser.add_argument(
        "--delete-file", default=str(_OUTPUT_DIR / "to_delete.txt"),
        help="Path to the delete list file (default: $SIGHTREAD_OUTPUT_DIR/to_delete.txt)",
    )
    parser.add_argument(
        "--root", default=None,
        help="Only allow deleting images under this directory (path containment guard)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview without actually moving files",
    )
    args = parser.parse_args()
    delete_marked(args.delete_file, args.dry_run, root=args.root)


if __name__ == "__main__":
    main()
