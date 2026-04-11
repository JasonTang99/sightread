#!/usr/bin/env python3
"""Generate sample outputs/results.json from demo_photos/."""

import json
import os
from pathlib import Path

DEMO_DIR = Path("demo_photos")
OUTPUT_DIR = Path("outputs")
OUTPUT_FILE = OUTPUT_DIR / "results.json"

MOCK_SCORES = [0.92, 0.85, 0.78]


def main():
    images = sorted(DEMO_DIR.glob("*.JPG"))
    if not images:
        images = sorted(DEMO_DIR.glob("*.jpg"))
    if not images:
        print("No images found in demo_photos/")
        return

    image_entries = []
    for rank, (img, score) in enumerate(zip(images, MOCK_SCORES), start=1):
        image_entries.append({
            "path": str(img),
            "score": score,
            "rank": rank,
        })

    results = {
        "clusters": [
            {
                "cluster_id": 0,
                "best_image": image_entries[0]["path"],
                "images": image_entries,
            }
        ]
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(results, indent=2) + "\n")
    print(f"Wrote {OUTPUT_FILE} with {len(image_entries)} images in 1 cluster")


if __name__ == "__main__":
    main()
