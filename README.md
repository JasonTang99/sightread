# 📸 Sightread — Photo Curation Tool

A local Streamlit web UI for browsing clusters of similar images, comparing them side-by-side, and safely deleting unwanted photos.

## Setup

```bash
pip install -r requirements.txt
```

## Generate Sample Data

Create a sample `outputs/results.json` from the images in `demo_photos/`:

```bash
python scripts/generate_sample_data.py
```

## Run the App

```bash
streamlit run ui/app.py
```

Then open the URL printed in the terminal (usually `http://localhost:8501`).

## Features

- **Cluster grid** — 4-column layout with rank, score, and ⭐ best-image highlight
- **Per-image controls** — Mark individual images for deletion
- **Compare mode** — Side-by-side comparison of any two images
- **Auto-select** — Score threshold slider to bulk-mark low-scoring images
- **Keep Best Only** — One-click button to keep only the top-ranked image
- **Bulk delete** — Delete all marked images at once
- **Safe deletion** — Images are moved to `outputs/trash/`, never permanently deleted

## Expected `results.json` Schema

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

- `score` — float between 0 and 1 (higher is better)
- `rank` — integer starting at 1 (lower is better)
- `path` — relative to the repo root (working directory when running the app)
