#!/usr/bin/env python3
"""Delete images listed in the to-delete file from the primary drive.

Photos live on two drives: the primary (small, curated) and the mirror (large,
kept whole). Reclaiming space means actually removing files from the primary, so
each listed image is unlinked — but only after its copy on the mirror has been
shown to exist at a matching size. Anything failing that check is left alone and
reported, and stays in the delete list for a later run.

The mirror keeps every file. Each mirrored directory gets an appended
`.sightread_deleted.txt` naming what was removed from the primary, so the mirror
can be pruned later.

Drive roots come from $SIGHTREAD_PRIMARY_ROOT (default /mnt/h0) and
$SIGHTREAD_MIRROR_ROOT (default /mnt/h1/h0), and must match webapp/server.py.

Usage:
    python scripts/delete_marked.py --dry-run
    python scripts/delete_marked.py
    python scripts/delete_marked.py --delete-file outputs/to_delete.txt --root /path/to/photos
"""

import argparse
import json
import os
from pathlib import Path

_OUTPUT_DIR = Path(os.environ.get("SIGHTREAD_OUTPUT_DIR", "outputs"))
PRIMARY_ROOT = Path(os.environ.get("SIGHTREAD_PRIMARY_ROOT", "/mnt/h0"))
MIRROR_ROOT = Path(os.environ.get("SIGHTREAD_MIRROR_ROOT", "/mnt/h1/h0"))
MIRROR_MANIFEST_NAME = ".sightread_deleted.txt"


def _check_path_contained(src: Path, root: Path) -> bool:
    """Return True if src is under root (prevents stale list from escaping project dir)."""
    try:
        src.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def mirror_path(src: Path) -> Path | None:
    """Where `src` lives on the mirror drive, or None if it isn't on the primary."""
    try:
        return MIRROR_ROOT / src.resolve().relative_to(PRIMARY_ROOT)
    except ValueError:
        return None


def _mirror_verdict(src: Path) -> tuple[Path | None, str | None]:
    """Return (mirror, reason_it_is_unusable). A usable mirror has reason None."""
    mirror = mirror_path(src)
    if mirror is None:
        return None, f"not under primary root {PRIMARY_ROOT}"
    if not mirror.is_file():
        return mirror, f"no mirror copy at {mirror}"
    if mirror.stat().st_size != src.stat().st_size:
        return mirror, f"mirror size differs ({mirror.stat().st_size} vs {src.stat().st_size})"
    return mirror, None


def _load_favorites(output_dir: Path) -> set[str]:
    p = output_dir / "favorites.json"
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text()))
    except Exception:
        return set()


def _append_manifests(by_dir: dict[Path, list[str]]) -> None:
    for mirror_dir, names in by_dir.items():
        manifest = mirror_dir / MIRROR_MANIFEST_NAME
        with manifest.open("a") as fh:
            for name in names:
                fh.write(f"{name}\n")
        print(f"  Listed {len(names)} name(s) in {manifest}")


def delete_marked(
    delete_file: str = "outputs/to_delete.txt",
    dry_run: bool = False,
    root: str | None = None,
) -> None:
    """Unlink each listed file from the primary drive once its mirror is verified."""
    delete_path = Path(delete_file)
    if not delete_path.exists():
        print(f"No delete file found at {delete_path}. Nothing to do.")
        return

    paths = [line.strip() for line in delete_path.read_text().splitlines() if line.strip()]
    if not paths:
        print("Delete file is empty. Nothing to do.")
        return

    root_path = Path(root).resolve() if root else None
    favorites = _load_favorites(delete_path.parent)

    print(f"Found {len(paths)} image(s) to delete")
    print(f"Primary: {PRIMARY_ROOT}   Mirror: {MIRROR_ROOT}")
    if dry_run:
        print("DRY RUN — nothing will be deleted\n")

    deleted = 0
    skipped = 0
    freed_bytes = 0
    unmirrored: list[str] = []
    manifest_by_dir: dict[Path, list[str]] = {}

    for p in paths:
        src = Path(p)

        if p in favorites:
            print(f"  Skip (starred): {p}")
            skipped += 1
            continue

        if root_path is not None and not _check_path_contained(src, root_path):
            print(f"  Skip (outside root {root_path}): {p}")
            skipped += 1
            continue

        if not src.is_file():
            print(f"  Skip (not found): {p}")
            skipped += 1
            continue

        mirror, problem = _mirror_verdict(src)
        if problem is not None:
            print(f"  KEEP (unverified): {p} — {problem}")
            unmirrored.append(p)
            continue

        size = src.stat().st_size
        if dry_run:
            print(f"  Would delete: {p}  (mirror ok: {mirror})")
        else:
            src.unlink()
            print(f"  Deleted: {p}")
            manifest_by_dir.setdefault(mirror.parent, []).append(src.name)
        deleted += 1
        freed_bytes += size

    gb = freed_bytes / 1024 ** 3
    if dry_run:
        print(f"\n🔍 Would delete {deleted} image(s), freeing {gb:.2f} GB")
        print(f"   {skipped} skipped, {len(unmirrored)} kept for want of a verified mirror")
        return

    _append_manifests(manifest_by_dir)
    # Everything considered is settled except the unverified files, which stay
    # pending so a later run can retry them once their mirror is in place.
    delete_path.write_text("".join(f"{p}\n" for p in unmirrored))
    print(f"\n✅ Deleted {deleted} image(s) from {PRIMARY_ROOT}, freeing {gb:.2f} GB ({skipped} skipped)")
    if unmirrored:
        print(f"⚠️  Kept {len(unmirrored)} image(s) with no verified mirror — still listed in {delete_path}")
    else:
        print(f"Cleared {delete_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Delete images listed in the to-delete file from the primary drive"
    )
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
        help="Preview without deleting anything",
    )
    args = parser.parse_args()
    delete_marked(args.delete_file, args.dry_run, root=args.root)


if __name__ == "__main__":
    main()
