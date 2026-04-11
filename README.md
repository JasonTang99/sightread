# 📸 Sightread — Photo Curation Tool

Browse clusters of similar photos, compare them head-to-head, select your keepers, and safely delete the rest.

## Quick Start

```bash
pip install -r requirements.txt
./run.sh /path/to/photos
```

This runs the clustering/scoring pipeline, then launches the curation UI.

## Usage

### 1. Run the pipeline separately

```bash
python scripts/pipeline.py --image-dir /path/to/photos
```

Options:
- `--output-dir outputs` — where to write results (default: `outputs/`)
- `--batch-size 32` — CLIP batch size
- `--min-cluster-size 3` — HDBSCAN minimum cluster size

### 2. Launch the UI separately

```bash
streamlit run ui/app.py
```

Reads `outputs/results.json` produced by the pipeline.

### 3. One command

```bash
./run.sh /path/to/photos
```

## Features

- **CLIP + HDBSCAN clustering** — groups visually similar photos automatically
- **pyiqa quality scoring** — ranks images by aesthetic/technical quality
- **Select keepers** — click to keep, unselected images get deleted
- **Tournament compare** — step through head-to-head matchups, pick winners
- **Manual compare** — choose any two images for side-by-side comparison
- **Cluster-by-cluster** — navigate with Prev/Next or jump with dropdown
- **Safe deletion** — images moved to `outputs/trash/`, never permanently deleted
- **Photo-first UI** — minimal chrome, images fill the screen

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

- `score` — float, higher is better (pyiqa topiq_nr)
- `rank` — integer starting at 1, lower is better
- `path` — relative to working directory

## Requirements

- Python 3.10+
- GPU recommended for pipeline (CLIP + pyiqa); CPU works but is slower
- See `requirements.txt` for full dependency list
