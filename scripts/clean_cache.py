#!/usr/bin/env python3
"""Remove cached pipeline outputs so the next run recomputes everything.

Usage:
    python scripts/clean_cache.py
    python scripts/clean_cache.py --output-dir outputs
"""

import argparse
import shutil
from pathlib import Path

CACHE_FILES = ["embeddings.npy", "clusters.json", "results.json"]


def clean(output_dir: str = "outputs") -> None:
    out = Path(output_dir)
    removed = []
    for name in CACHE_FILES:
        p = out / name
        if p.exists():
            p.unlink()
            removed.append(str(p))

    trash = out / "trash"
    if trash.is_dir() and any(trash.iterdir()):
        n = sum(1 for _ in trash.iterdir())
        shutil.rmtree(trash)
        removed.append(f"{trash}/ ({n} files)")

    if removed:
        for r in removed:
            print(f"  Removed: {r}")
        print(f"✅ Cleaned {len(removed)} item(s)")
    else:
        print("Nothing to clean.")


def main():
    parser = argparse.ArgumentParser(description="Clean pipeline cache")
    parser.add_argument("--output-dir", default="outputs")
    args = parser.parse_args()
    clean(args.output_dir)


if __name__ == "__main__":
    main()
