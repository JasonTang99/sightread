#!/usr/bin/env python3
"""Export the list of keeper images (all images not marked for deletion).

Reads outputs/results.json and outputs/to_delete.txt, writes a list of
keeper paths to stdout or a file.

Usage:
    python scripts/export_keepers.py
    python scripts/export_keepers.py --output keepers.txt
    python scripts/export_keepers.py --copy-to /path/to/export/dir
"""

import argparse
import shutil
from pathlib import Path


def get_keepers(results_path: Path, curation_path: Path) -> list[str]:
    import json
    data = json.loads(results_path.read_text())
    all_paths = [img["path"] for c in data["clusters"] for img in c["images"]]
    deleted: set[str] = set()
    if curation_path.exists():
        c = json.loads(curation_path.read_text())
        deleted = set(c.get("deleted", []))
    elif (curation_path.parent / "to_delete.txt").exists():
        # Legacy fallback
        lines = (curation_path.parent / "to_delete.txt").read_text().splitlines()
        deleted = {l.strip() for l in lines if l.strip()}
    return [p for p in all_paths if p not in deleted]


def main():
    parser = argparse.ArgumentParser(description="Export keeper image paths")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--output", default=None, help="Write keeper list to this file (default: stdout)")
    parser.add_argument("--copy-to", default=None, help="Copy keeper images to this directory")
    args = parser.parse_args()

    out = Path(args.output_dir)
    keepers = get_keepers(out / "results.json", out / "curation.json")

    if args.output:
        Path(args.output).write_text("\n".join(keepers) + "\n")
        print(f"Wrote {len(keepers)} keeper paths to {args.output}")
    elif not args.copy_to:
        for p in keepers:
            print(p)

    if args.copy_to:
        dest_dir = Path(args.copy_to)
        dest_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for p in keepers:
            src = Path(p)
            if src.exists():
                dest = dest_dir / src.name
                if dest.exists():
                    stem, suffix = dest.stem, dest.suffix
                    counter = 1
                    while dest.exists():
                        dest = dest_dir / f"{stem}_{counter}{suffix}"
                        counter += 1
                shutil.copy2(str(src), str(dest))
                copied += 1
            else:
                print(f"  Skip (not found): {p}")
        print(f"Copied {copied} keepers to {dest_dir}/")


if __name__ == "__main__":
    main()
