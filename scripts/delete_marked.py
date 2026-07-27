#!/usr/bin/env python3
"""Delete every photo marked `to_delete` in a project's decisions.json.

Photos live on two drives: the primary (small, curated) and the mirror (large,
kept whole). Reclaiming space means actually removing files from the primary, so
each listed image is unlinked — but only after its copy on the mirror has been
shown to exist at a matching size. Anything failing that check is left alone and
reported, and keeps its `to_delete` status for a later run.

Only the `to_delete` status is ever swept. A starred photo holds `favorite`
instead, so it cannot reach the queue — protection is structural rather than a
filter this script has to remember to apply.

The mirror keeps every file. Each mirrored directory gets an appended
`.sightread_deleted.txt` naming what was removed from the primary, so the mirror
can be pruned later.

Drive roots come from $SIGHTREAD_PRIMARY_ROOT (default /mnt/h0) and
$SIGHTREAD_MIRROR_ROOT (default /mnt/h1/h0), and must match webapp/server.py.

Usage:
    python scripts/delete_marked.py --dry-run
    python scripts/delete_marked.py
    python scripts/delete_marked.py --output-dir outputs --root /path/to/photos
"""

import argparse
import os
import sys
from pathlib import Path

# The webapp owns the decisions schema and its legacy migration. Duplicating
# either here would mean two implementations of the rules that decide what gets
# unlinked, so this reuses them the same way pipeline.py reuses project paths.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webapp"))
from utils import (  # noqa: E402
    DELETED,
    TO_DELETE,
    load_decisions,
    migrate_project_state,
    paths_with_status,
    save_decisions,
)

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


def _append_manifests(by_dir: dict[Path, list[str]]) -> None:
    for mirror_dir, names in by_dir.items():
        manifest = mirror_dir / MIRROR_MANIFEST_NAME
        with manifest.open("a") as fh:
            for name in names:
                fh.write(f"{name}\n")
        print(f"  Listed {len(names)} name(s) in {manifest}")


def delete_marked(
    output_dir: str = "outputs",
    dry_run: bool = False,
    root: str | None = None,
) -> None:
    """Unlink each marked file from the primary drive once its mirror is verified."""
    out = Path(output_dir)
    if not (out / "decisions.json").exists() and not out.is_dir():
        print(f"No project state found at {out}. Nothing to do.")
        return

    # Fold any legacy to_delete.txt / favorites.json in before reading, so the
    # CLI and the webapp can never disagree about what is queued.
    if not dry_run and migrate_project_state(out):
        print(f"Migrated legacy curation state in {out}")

    paths = paths_with_status(load_decisions(out), TO_DELETE)
    if not paths:
        print("Nothing marked for deletion. Nothing to do.")
        return

    # An unmounted primary drive makes every source file look already-gone, and
    # the sweep would settle the whole queue as `deleted` — intact photos
    # recorded as destroyed. Refuse rather than write that down.
    if not PRIMARY_ROOT.is_dir():
        raise SystemExit(f"Primary drive not mounted at {PRIMARY_ROOT}. Refusing to run.")

    root_path = Path(root).resolve() if root else None

    print(f"Found {len(paths)} image(s) to delete")
    print(f"Primary: {PRIMARY_ROOT}   Mirror: {MIRROR_ROOT}")
    if dry_run:
        print("DRY RUN — nothing will be deleted\n")

    deleted = 0
    skipped = 0
    freed_bytes = 0
    unmirrored: list[str] = []
    manifest_by_dir: dict[Path, list[str]] = {}

    settled: dict[str, str | None] = {}

    for p in paths:
        src = Path(p)

        if root_path is not None and not _check_path_contained(src, root_path):
            print(f"  Skip (outside root {root_path}): {p}")
            skipped += 1
            continue

        if not src.is_file():
            # Already gone — settle the record rather than re-queue it forever.
            print(f"  Skip (not found): {p}")
            settled[p] = DELETED
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
            settled[p] = DELETED
        deleted += 1
        freed_bytes += size

    gb = freed_bytes / 1024 ** 3
    if dry_run:
        print(f"\n🔍 Would delete {deleted} image(s), freeing {gb:.2f} GB")
        print(f"   {skipped} skipped, {len(unmirrored)} kept for want of a verified mirror")
        return

    _append_manifests(manifest_by_dir)
    # Everything considered is settled except the unverified files, which keep
    # their to_delete status so a later run retries them once mirrored.
    if settled:
        save_decisions(out, settled)
    print(f"\n✅ Deleted {deleted} image(s) from {PRIMARY_ROOT}, freeing {gb:.2f} GB ({skipped} skipped)")
    if unmirrored:
        print(f"⚠️  Kept {len(unmirrored)} image(s) with no verified mirror — still marked to_delete")
    else:
        print("Delete queue is now empty")


def main():
    parser = argparse.ArgumentParser(
        description="Delete photos marked to_delete from the primary drive"
    )
    parser.add_argument(
        "--output-dir", default=str(_OUTPUT_DIR),
        help="Project output dir holding decisions.json (default: $SIGHTREAD_OUTPUT_DIR)",
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
    delete_marked(args.output_dir, args.dry_run, root=args.root)


if __name__ == "__main__":
    main()
