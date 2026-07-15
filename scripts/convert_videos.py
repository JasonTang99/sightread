#!/usr/bin/env python3
"""Pre-bake browser-playable copies of a folder's videos.

Camera .MOV files often pair LPCM audio browsers can't decode with a bitrate
and resolution they can't scrub smoothly. This transcodes each clip into the
project's video_cache using the settings in webapp/video.py. Originals are
never touched.

Usage: python scripts/convert_videos.py /path/to/folder [--force]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "webapp"))

from projects import project_output_dir  # noqa: E402
from video import VIDEO_EXTENSIONS, cache_path, transcode_for_web  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", help="Project folder containing videos")
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-transcode videos that are already cached (use after changing "
             "the encode settings in webapp/video.py)",
    )
    args = ap.parse_args()

    folder = Path(args.folder).resolve()
    if not folder.is_dir():
        print(f"Not a directory: {folder}", file=sys.stderr)
        return 1

    output_dir = project_output_dir(folder)
    output_dir.mkdir(parents=True, exist_ok=True)

    videos = sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not videos:
        print("No videos found.")
        return 0

    print(f"{len(videos)} videos → {output_dir / 'video_cache'}")
    converted = skipped = failed = 0
    for i, src in enumerate(videos, 1):
        dest = cache_path(output_dir, src)
        if dest.exists() and not args.force:
            skipped += 1
            continue
        print(f"[{i}/{len(videos)}] {src.name} …", flush=True)
        try:
            transcode_for_web(src, dest)
            converted += 1
        except Exception as e:  # keep going on a bad file
            failed += 1
            print(f"  failed: {e}", file=sys.stderr)

    print(f"Done: {converted} converted, {skipped} already cached, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
