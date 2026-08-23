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
- **Mirror-verified deletion** — confirmed deletions go to a pending list; apply them from the Trash panel (or `scripts/delete_marked.py`) to reclaim space on the primary drive. Each shot takes its raw and `.xmp` sidecars with it. See below.
- **Photo-first UI** — minimal chrome, images fill the screen

## Deletion and the two drives — what to expect

Photos live on two drives: a **primary** (small, curated) and a **mirror** (large, kept whole).

### What actually gets deleted

**You curate JPEGs; the tool deletes shots.** The pipeline ranks the camera JPEG, so
that is the only path in the delete queue — but on disk one shot is several files, and
all of them go together:

| file | example | deleted? |
| --- | --- | --- |
| the JPEG you reviewed | `DSCF4526.JPG` | yes |
| its raw | `DSCF4526.RAF` | yes |
| `.xmp` sidecars, either spelling | `DSCF4526.JPG.xmp`, `DSCF4526.RAF.xmp`, `DSCF4526.xmp` | yes |
| a **neighbouring** shot's raw | `DSCF4527.RAF` | no — matched by stem, not by scanning |
| the web transcode / thumb / poster caches | under the project's output dir | no — separate step, see below |

Raw extensions recognised: `.raf .cr2 .cr3 .crw .nef .nrw .arw .srf .sr2 .dng .orf .rw2
.raw .pef .srw .3fr .erf .mef .mos .iiq .x3f`, either case.

**This is where the space is.** On a 349-shot Fujifilm queue, measured:

```
JPG 349   6.64 GB     <- all that a JPEG-only delete would reclaim
RAF 348  56.48 GB     <- the actual payload
xmp 697   ~0    GB
        --------
total    63.13 GB
```

### The safety rules

- **Mirror-verified, per file.** Nothing is unlinked until its copy on the mirror is
  confirmed present *at a matching size*.
- **All or nothing, per shot.** If any member of the group fails that check — raw not
  mirrored, sizes differ — the *entire* shot is left in place and stays queued for a
  later run. A shot is never left half on one drive and half on the other.
- **Starred photos are unreachable.** `favorite` and `to_delete` are one status field,
  so a starred shot cannot be in the queue at all. Its raw is safe for the same reason.
- **Refuses to run unmounted.** If the project folder is not present, the whole call
  fails with a `409` rather than recording an entire project as deleted.
- **Not undoable in-app.** Applying deletes clears the undo stack, on disk as well as in
  memory. Recovery means copying back from the mirror.

The mirror is never pruned by this tool — it keeps every file, and remains the copy
you recover from. Each mirrored directory gets an appended `.sightread_deleted.txt`
naming **every file** removed from the primary, raws and sidecars included, so you can
prune the mirror yourself later.

### What is *not* deleted here

Applying deletes never touches the derived caches (`thumb_cache/`, `poster_cache/`,
`video_cache/`). Those live under the project's output dir in `~/.local/share/sightread/`
— usually a different physical drive from the photos — and are reclaimed separately by
marking a project done (`POST /api/projects/done`) or by `scripts/clean_cache.py`.
Freeing the photo drive and freeing the cache drive are two different operations.

### Drive roots

Defaults are `/mnt/h0` and `/mnt/h1/h0`, and are configurable. The mirror root must be
the directory whose tree *reproduces* the primary's, so that a photo at
`$PRIMARY_ROOT/rest/of/path` mirrors to `$MIRROR_ROOT/rest/of/path`:

```bash
export SIGHTREAD_PRIMARY_ROOT=/mnt/h0
export SIGHTREAD_MIRROR_ROOT=/mnt/h1/h0
```

Get this wrong and nothing is destroyed — every file simply fails verification and stays
queued, reported as unmirrored. Check with a dry run before trusting it.

Preview before committing:

```bash
python scripts/delete_marked.py --dry-run
```

The dry run names the sidecars it would take alongside each JPEG, and totals the bytes.

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
