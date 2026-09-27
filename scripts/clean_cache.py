#!/usr/bin/env python3
"""Remove cached pipeline outputs so the next run recomputes everything.

Usage:
    python scripts/clean_cache.py
    python scripts/clean_cache.py --output-dir outputs
"""

import argparse
import shutil
from pathlib import Path

CACHE_FILES = [
    "embeddings_dinov3_mpcls_tta.npy",
    "embeddings_dinov3_mpcls_tta.paths.json",
    "embeddings_dinov3_mpcls_tta.hash",  # legacy
    "scores_ensemble.npz",
    "scores_ensemble.paths.json",
    "clusters.json",
    "results.json",
    "video_highlights.json",
]


def clean(output_dir: str = "outputs") -> None:
    out = Path(output_dir)
    removed = []
    for name in CACHE_FILES:
        p = out / name
        if p.exists():
            p.unlink()
            removed.append(str(p))

    thumb_cache = out / "thumb_cache"
    if thumb_cache.is_dir():
        n = sum(1 for _ in thumb_cache.iterdir())
        shutil.rmtree(thumb_cache)
        removed.append(f"{thumb_cache}/ ({n} files)")

    vh_cache = out / "video_highlights_cache"
    if vh_cache.is_dir():
        n = sum(1 for _ in vh_cache.iterdir())
        shutil.rmtree(vh_cache)
        removed.append(f"{vh_cache}/ ({n} entries)")

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
