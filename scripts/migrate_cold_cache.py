#!/usr/bin/env python3
"""Move existing compare renders and transcodes to the cold-cache root.

webapp/cachedirs.py decides where new entries go; this moves the ones written
before the root was configured, so they are hits rather than re-renders.

    python scripts/migrate_cold_cache.py            # dry run
    python scripts/migrate_cold_cache.py --apply

What moves, per project: every `video_cache/*.mp4`, and the `thumb_cache`
entries whose key is a reachable compare-width (2400px) key, computed from the
project's source folder the way sweep_orphan_caches.py does it. File size or
pixel width cannot tell a 2400 render of a small original from a grid tile;
the key can. Projects whose source folder is missing are skipped, loudly.

Many thumbnails are hard links shared between a trip and its city
sub-projects. A per-file move across drives would turn each link into its own
copy, so the first link of an inode is copied and the rest are linked to that
copy on the cold drive. Each file is copied to a temp name, its size checked,
renamed into place, and only then removed from the system disk.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
import cachedirs  # noqa: E402
import sweep_orphan_caches as sweep  # noqa: E402
import thumbs  # noqa: E402
from webapp import projects as P  # noqa: E402


def plan_project(out_dir: Path) -> list[tuple[Path, Path]] | None:
    """(source, destination) pairs for one project, or None if skipped."""
    cold = cachedirs.cold_dir(out_dir)
    moves = []
    vids = out_dir / "video_cache"
    if vids.is_dir():
        moves += [(f, cold / "video_cache" / f.name) for f in sorted(vids.glob("*.mp4"))]
    thumb_dir = out_dir / "thumb_cache"
    if thumb_dir.is_dir():
        folder = P._folder_for_output_dir(out_dir)
        if folder is None or not folder.is_dir():
            print(f"  {out_dir.name}: source folder {folder} missing, thumbnails skipped")
            return moves or None
        large = sweep.reachable_keys(folder, (thumbs.COMPARE_WIDTH,))["thumb_cache"]
        moves += [(f, cold / "thumb_cache" / f.name) for f in sorted(thumb_dir.glob("*.jpg"))
                  if f.stem in large]
    return moves


def move(src: Path, dest: Path, first_copy: dict[tuple[int, int], Path]) -> int:
    """Move one file, linking to an earlier copy of the same inode. Bytes copied."""
    st = src.stat()
    ident = (st.st_dev, st.st_ino)
    cachedirs.ensure_parent(dest)
    copied = 0
    if dest.exists():
        if dest.stat().st_size != st.st_size:
            raise RuntimeError(f"{dest} exists with a different size")
    elif ident in first_copy:
        os.link(first_copy[ident], dest)
    else:
        tmp = dest.with_name(dest.name + ".migrating")
        shutil.copy2(src, tmp)
        if tmp.stat().st_size != st.st_size:
            tmp.unlink()
            raise RuntimeError(f"short copy of {src}")
        tmp.replace(dest)
        copied = st.st_size
    first_copy.setdefault(ident, dest)
    src.unlink()
    return copied


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="move (default: report only)")
    args = ap.parse_args()

    root = cachedirs.cold_root()
    if root is None:
        print(f"No cold root: set ${cachedirs.ENV} or write {cachedirs.CONFIG_FILE}")
        return 1
    if not root.is_dir():
        print(cachedirs.missing_message())
        return 1

    plans = {}
    for out_dir in sorted(d for d in P.DATA_DIR.iterdir() if d.is_dir()):
        moves = plan_project(out_dir)
        if moves:
            plans[out_dir] = moves
    n = sum(len(m) for m in plans.values())
    print(f"{n} files in {len(plans)} projects -> {root}" + ("" if args.apply else "  [DRY RUN]"))
    if not args.apply:
        for out_dir, moves in plans.items():
            print(f"  {out_dir.name}: {len(moves)}")
        return 0

    first_copy: dict[tuple[int, int], Path] = {}
    done = copied = 0
    for out_dir, moves in plans.items():
        for src, dest in moves:
            copied += move(src, dest, first_copy)
            done += 1
            if done % 2000 == 0:
                print(f"  {done}/{n} files, {copied / 1e9:.1f} GB copied", flush=True)
        print(f"  {out_dir.name}: {len(moves)} moved", flush=True)
    print(f"Moved {done} files, {copied / 1e9:.1f} GB copied (the rest were hard links)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
