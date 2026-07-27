# 📸 Sightread — Photo Curation Tool

Browse clusters of similar photos, compare them head-to-head, select your keepers, and safely delete the rest.

## Quick Start

```bash
pip install -r requirements.txt
./run.sh /path/to/photos
```

This runs the clustering/scoring pipeline, then launches the webapp at http://127.0.0.1:8765.

## Usage

### 1. Run the pipeline separately

```bash
python scripts/pipeline.py --image-dir /path/to/photos
```

Options:
- `--output-dir outputs` — where to write results (default: `outputs/`)
- `--batch-size 32` — embedding batch size
- `--tight 0.18` — near-duplicate threshold (lower = stricter)
- `--loose 0.35` — same-scene threshold

### 2. Launch the UI separately

```bash
cd webapp && python -m uvicorn server:app --host 127.0.0.1 --port 8765
```

Then open http://127.0.0.1:8765 in a browser.

### 3. One command

```bash
./run.sh /path/to/photos
```

## Features

- **DINOv3 + agglomerative clustering** — groups visually similar photos automatically
- **4-metric IQA ensemble** (MUSIQ, NIMA, CLIP-IQA+, LAION-Aes + sharpness/exposure/face) — ranks images by quality
- **Select keepers** — click to keep, unselected images get deleted
- **Tournament compare** — step through head-to-head matchups, pick winners
- **Manual compare** — choose any two images for side-by-side comparison
- **Cluster-by-cluster** — navigate with Prev/Next or jump with dropdown
- **Mirror-verified deletion** — confirmed deletions go to a pending list; apply them from the Trash panel (or `scripts/delete_marked.py`) to reclaim space on the primary drive. See below.
- **Photo-first UI** — minimal chrome, images fill the screen

## Deletion and the two drives

Photos live on two drives: a **primary** (small, curated) and a **mirror** (large, kept whole).

Applying deletes reclaims space on the primary by unlinking files there. A file is
only unlinked once its copy on the mirror has been verified to exist at a matching
size; anything that fails the check is left in place and reported, and stays on the
pending list for a later run. Starred photos are never deleted.

The mirror is never pruned by this tool — it keeps every file, and remains the copy
you recover from. Each mirrored directory gets an appended `.sightread_deleted.txt`
naming what was removed from the primary, so you can prune the mirror yourself later.

Drive roots default to `/mnt/h0` and `/mnt/h1/h0` and are configurable:

```bash
export SIGHTREAD_PRIMARY_ROOT=/mnt/h0
export SIGHTREAD_MIRROR_ROOT=/mnt/h1/h0
```

Preview before committing:

```bash
python scripts/delete_marked.py --dry-run
```

## Cache

The pipeline caches DINOv3 embeddings and IQA scores so re-runs skip the expensive compute steps. To force a full recomputation:

```bash
python scripts/clean_cache.py
```

This removes `embeddings_dinov3_mpcls_tta.npy`, `scores_ensemble.npz`, `clusters.json`, `results.json`, and the `thumb_cache/` and `video_highlights_cache/` directories. Curation decisions (`decisions.json`) are left alone.

## Curation state

All curation state lives in one file per project, `decisions.json`, holding a
single status per photo path:

| status | meaning |
| --- | --- |
| *(absent)* | never reviewed |
| `kept` | reviewed, staying |
| `favorite` | starred; a stronger `kept` that deletion never touches |
| `to_delete` | marked for deletion, not yet applied — this *is* the queue |
| `deleted` | already unlinked from the primary drive |

It is keyed by photo path rather than cluster id because cluster ids are
assigned per pipeline run, so re-running renumbers them and would strand every
decision. It is one field rather than several files because the earlier split —
`decisions.json` plus `to_delete.txt` plus `favorites.json` — had nothing
keeping the three in agreement, and they drifted in practice: photos marked
kept sat in the delete queue and would have been deleted.

Projects still on the old layout are converted the first time they are opened.
The superseded files are renamed to `*.migrated` rather than removed. Conflicts
resolve away from deletion: a star beats everything, and an explicit keep beats
a stale queue entry.

## `results.json` Schema

```json
{
  "clusters": [
    {
      "cluster_id": 0,
      "best_image": "path/to/best.jpg",
      "images": [
        { "path": "path/to/image.jpg", "score": 0.92, "rank": 1 },
        { "path": "path/to/image2.jpg", "score": 0.85, "rank": 2 }
      ]
    }
  ]
}
```

- `score` — float, higher is better (weighted ensemble: MUSIQ/NIMA/CLIP-IQA+/LAION-Aes + sharpness/exposure/face)
- `rank` — integer starting at 1, lower is better
- `centrality` — cosine similarity to cluster centroid
- `path` — absolute path to image

## Tests

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium   # or use --browser-channel chrome
python -m pytest webapp/tests -q --browser-channel chrome
```

## Requirements

- Python 3.10+
- GPU recommended for pipeline (DINOv3 + pyiqa); CPU works but is slower
- See `requirements.txt` for full dependency list
