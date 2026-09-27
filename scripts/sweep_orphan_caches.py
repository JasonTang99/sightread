#!/usr/bin/env python3
"""Delete derived-cache entries no current source file maps to.

`POST /api/projects/done` already reclaims a *finished* project's caches by
removing the directories whole. This is the other half: a project still being
curated, where most of the cache is live and only some of it is dead. Entries
go dead when a source file is deleted or re-encoded (the key carries mtime), or
when the key scheme itself moves and the old entries become unreachable — the
case that put 140GB of 4K/194Mbps transcodes on this machine's **system disk**,
written by a converter whose keys the current code no longer computes. Nothing
will ever read them, and nothing was ever going to notice.

Note which drive this frees. The photo drives are h0/h1; these caches live under
$XDG_DATA_HOME (~/.local/share/sightread/projects/<md5 of folder>), i.e. the
nvme. Freeing the photo drive is `delete_marked.py`; this is a different
operation on a different disk.

## What counts as reachable

`video_cache` is keyed `sha1(resolved_path|mtime_ns)` with no width in it, so
the reachable set is exactly computable: every video under the project folder,
hashed. An entry outside that set cannot be addressed by any request the code
can make. That is the default target, and it is where essentially all of the
bytes are.

`thumb_cache` and `poster_cache` put a caller-supplied width *inside* the key,
and the width arrives as a query parameter, so "every reachable key" is not a
finite set this script can derive. It uses the widths the frontend and pipeline
actually ask for (see --widths). A width in use but not listed would look dead,
so those two directories are opt-in behind --include-widthkeyed. The cost of
getting it wrong there is a re-render, not a lost file — but a 2400px re-render
is ~1.3s per photo and it is better to say so than to discover it.

## Refusing rather than guessing

An unmounted source drive makes every key look unreachable, and the sweep would
then delete every cache it can see. That is the same failure `delete_marked.py`
guards against, for the same reason, and it is guarded the same way: if the
project's folder is not there, the project is skipped, loudly. It is never read
as "nothing maps to these any more".

Usage:
    python scripts/sweep_orphan_caches.py                     # every project, dry run
    python scripts/sweep_orphan_caches.py --apply             # delete
    python scripts/sweep_orphan_caches.py --output-dir <dir>  # one project
    python scripts/sweep_orphan_caches.py --include-widthkeyed --apply
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# webapp modules import each other by bare name (`from media import ...`).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))

from webapp import projects as P  # noqa: E402
from media import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS  # noqa: E402

# What the UI and the pipeline actually request: TrashPanel 200, FavoritesView
# 400, TimelineView + pipeline prewarm 800, ClusterView/SingletonsView + pipeline
# 2400. Posters are requested at the timeline width only, but the same set is
# used for both — an extra candidate key costs nothing, a missing one costs a file.
#
# A width leaving this list is the main way entries go dead here, and it has
# happened once already: `THUMB_W` was 600 until 09609f4 raised it to 800, and
# every 600px thumbnail and poster written before that became unreachable the
# moment the constant changed. Verify a suspicious orphan count by probing the
# files' actual width before deleting — if it is a width still in use, the
# whitelist is wrong, not the cache.
DEFAULT_WIDTHS = (200, 400, 800, 2400)

PRIMARY_ROOT = Path(os.environ.get("SIGHTREAD_PRIMARY_ROOT", "/mnt/h0"))


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024.0
    return f"{n:.1f}TB"


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()


def reachable_keys(folder: Path, widths: tuple[int, ...]) -> dict[str, set[str]]:
    """Every cache key the current code could compute for this project's files.

    Both the resolved and unresolved spellings of each path are hashed.
    `thumbs.cache_file` keys on the path as handed to it while `video.cache_path`
    resolves first, and a project reached through a symlink would otherwise have
    its live thumbnails read as unreachable.
    """
    video: set[str] = set()
    poster: set[str] = set()
    thumb: set[str] = set()

    for f in folder.rglob("*"):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext not in IMAGE_EXTENSIONS and ext not in VIDEO_EXTENSIONS:
            continue
        try:
            mtime = f.stat().st_mtime_ns
        except OSError:
            continue
        spellings = {str(f), str(f.resolve())}
        for p in spellings:
            if ext in VIDEO_EXTENSIONS:
                video.add(_sha1(f"{p}|{mtime}"))
                for w in widths:
                    poster.add(_sha1(f"{p}|{mtime}|{w}"))
            else:
                for w in widths:
                    thumb.add(_sha1(f"{p}|{mtime}|{w}"))

    return {"video_cache": video, "poster_cache": poster, "thumb_cache": thumb}


# A cached transcode is only useful if it matches the policy the current code
# would produce. `video.transcode_for_web` caps the long edge at 2560 and the
# bitrate at 15M; anything well beyond that was written under an older policy and
# is worse than useless — serving it is the 4K decode stall `webapp/video.py`
# exists to avoid, and it occupies the system disk to do it. Bitrate is allowed a
# wide margin because the cap is a ceiling, not a target.
_POLICY_MAX_LONG_EDGE = 2560
_POLICY_MAX_BITRATE = 25_000_000


def probe(path: Path) -> tuple[int, int, int] | None:
    """(width, height, bitrate) of a cached file, or None if ffprobe cannot say."""
    import json as _json
    import shutil
    import subprocess

    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-show_entries", "format=bit_rate",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, check=True).stdout
        d = _json.loads(out)
        st = d["streams"][0]
        return int(st["width"]), int(st["height"]), int(d["format"]["bit_rate"])
    except Exception:
        return None


def stale_policy_in(cache_dir: Path, keys: set[str]) -> tuple[list[Path], int]:
    """Reachable transcodes the current encode policy would not have produced."""
    found: list[Path] = []
    total = 0
    if not cache_dir.is_dir():
        return found, total
    for f in sorted(cache_dir.iterdir()):
        if not f.is_file() or f.stem not in keys:
            continue  # unreachable entries are the orphan sweep's job, not this one
        info = probe(f)
        if info is None:
            continue  # cannot judge it — leave it alone
        w, h, bitrate = info
        if max(w, h) <= _POLICY_MAX_LONG_EDGE and bitrate <= _POLICY_MAX_BITRATE:
            continue
        try:
            total += f.stat().st_size
        except OSError:
            pass
        found.append(f)
    return found, total


def orphans_in(cache_dir: Path, keys: set[str]) -> tuple[list[Path], int]:
    """Files in cache_dir whose stem is not a reachable key, and their bytes."""
    found: list[Path] = []
    total = 0
    if not cache_dir.is_dir():
        return found, total
    for f in sorted(cache_dir.iterdir()):
        if not f.is_file():
            continue
        if f.stem in keys:
            continue
        try:
            total += f.stat().st_size
        except OSError:
            pass
        found.append(f)
    return found, total


def sweep_project(out_dir: Path, dirs: tuple[str, ...], widths: tuple[int, ...],
                  apply: bool, stale_policy: bool = False) -> tuple[int, int]:
    folder = P._folder_for_output_dir(out_dir)
    label = f"{out_dir.name}"
    if folder is None:
        print(f"  {label}: cannot recover the source folder — skipped "
              f"(no embeddings sidecar, or its paths no longer resolve)")
        return 0, 0
    if not folder.is_dir():
        under_primary = str(folder).startswith(str(PRIMARY_ROOT))
        why = ("drive not mounted at " + str(PRIMARY_ROOT)) if under_primary else "folder is gone"
        print(f"  {label} -> {folder}: {why}. SKIPPED — refusing to read an "
              f"absent folder as 'nothing maps to these'.")
        return 0, 0

    keys = reachable_keys(folder, widths)
    freed = 0
    count = 0
    print(f"  {label} -> {folder}")
    for name in dirs:
        cache_dir = out_dir / name
        if not cache_dir.is_dir():
            continue
        live = sum(1 for f in cache_dir.iterdir()
                   if f.is_file() and f.stem in keys[name])
        dead, dead_bytes = orphans_in(cache_dir, keys[name])
        if not dead:
            print(f"      {name:14} {live:6d} live, 0 orphaned")
            continue
        print(f"      {name:14} {live:6d} live, {len(dead):6d} orphaned "
              f"({_human(dead_bytes)})")
        if apply:
            for f in dead:
                try:
                    f.unlink()
                except OSError as e:
                    print(f"        could not remove {f.name}: {e}")
        freed += dead_bytes
        count += len(dead)

    if stale_policy:
        cache_dir = out_dir / "video_cache"
        stale, stale_bytes = stale_policy_in(cache_dir, keys["video_cache"])
        if stale:
            print(f"      video_cache    {len(stale):6d} reachable but written under "
                  f"an older encode policy ({_human(stale_bytes)})")
            if apply:
                for f in stale:
                    try:
                        f.unlink()
                    except OSError as e:
                        print(f"        could not remove {f.name}: {e}")
            freed += stale_bytes
            count += len(stale)
    return count, freed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output-dir", help="one project's output dir (default: all)")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default: report only)")
    ap.add_argument("--include-widthkeyed", action="store_true",
                    help="also sweep thumb_cache and poster_cache, whose keys "
                         "embed a caller-supplied width (see --widths)")
    ap.add_argument("--widths", type=int, nargs="+", default=list(DEFAULT_WIDTHS),
                    help="widths treated as reachable for the width-keyed caches")
    ap.add_argument("--stale-policy", action="store_true",
                    help="also evict reachable transcodes the current encode "
                         "policy would not produce (>2560 long edge or >25Mbps). "
                         "They re-encode on next view, or in bulk via "
                         "scripts/convert_videos.py")
    args = ap.parse_args()

    dirs = ("video_cache",)
    if args.include_widthkeyed:
        dirs = ("video_cache", "poster_cache", "thumb_cache")

    if args.output_dir:
        out_dirs = [Path(args.output_dir).resolve()]
    else:
        out_dirs = sorted(d for d in P.DATA_DIR.iterdir() if d.is_dir())

    print(f"Sweeping {', '.join(dirs)} in {len(out_dirs)} project(s)"
          + ("" if args.apply else "  [DRY RUN]"))
    if args.include_widthkeyed:
        print(f"Width-keyed caches treated as reachable at: "
              f"{', '.join(str(w) for w in args.widths)}")
    print()

    total_files = 0
    total_bytes = 0
    for out_dir in out_dirs:
        n, b = sweep_project(out_dir, dirs, tuple(args.widths), args.apply,
                             stale_policy=args.stale_policy)
        total_files += n
        total_bytes += b

    print()
    verb = "Removed" if args.apply else "Would remove"
    print(f"{verb} {total_files} file(s), {_human(total_bytes)}")
    if total_files and not args.apply:
        print("Re-run with --apply to delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
