# Photo Curation UI — Implementation Plan

## Problem
Build a local Streamlit web UI that lets a user browse clusters of similar images, compare them side-by-side, see rankings/scores, and safely delete unwanted images (moved to trash, never permanently deleted).

## Approach
Create a Streamlit app at `ui/` in the repo root with three modules (`app.py`, `components.py`, `utils.py`), a `requirements.txt`, and a sample-data generator script. The app reads `outputs/results.json` for cluster/ranking data and displays images with full curation controls.

## File Structure (final)
```
sightread/
├── ui/
│   ├── app.py            # Main Streamlit app
│   ├── components.py     # Reusable UI components (cluster view, compare mode, etc.)
│   └── utils.py          # Data loading, safe deletion, helpers
├── scripts/
│   └── generate_sample_data.py  # Creates outputs/results.json from demo_photos/
├── outputs/              # Created by generate script / backend
│   ├── results.json
│   └── trash/            # Safe-delete destination
├── demo_photos/          # Already exists (3 JPGs)
├── requirements.txt
└── README.md
```

## Todos

### 1. `setup-deps` — Create requirements.txt
- Add: `streamlit`, `pillow`, `pandas`, `numpy`
- (Skip `streamlit-image-select` — not needed for core+recommended scope)

### 2. `generate-sample-data` — Sample data generator script
- `scripts/generate_sample_data.py`
- Scans `demo_photos/`, creates a single cluster with all 3 images
- Assigns mock scores (0.92, 0.85, 0.78) and ranks (1, 2, 3)
- Writes `outputs/results.json` matching the expected schema:
  ```json
  {
    "clusters": [
      {
        "cluster_id": 0,
        "best_image": "demo_photos/DSCF4284.JPG",
        "images": [
          { "path": "demo_photos/DSCF4284.JPG", "score": 0.92, "rank": 1 },
          { "path": "demo_photos/DSCF4285.JPG", "score": 0.85, "rank": 2 },
          { "path": "demo_photos/DSCF4286.JPG", "score": 0.78, "rank": 3 }
        ]
      }
    ]
  }
  ```
- Depends on: nothing

### 3. `utils-module` — Implement `ui/utils.py`
- `load_results(path)` — load and return parsed JSON
- `delete_image_safe(path)` — move file to `outputs/trash/`, never permanent delete
- `TRASH_DIR = "outputs/trash"`
- Depends on: nothing

### 4. `components-module` — Implement `ui/components.py`
Reusable Streamlit component functions:
- `render_cluster_grid(images, on_delete_callback)` — display images in a 4-column grid, highlight best (⭐), show rank/score, per-image keep/delete buttons
- `render_compare_mode(images)` — side-by-side comparison with two selectboxes
- `render_auto_select(images)` — slider for score threshold, show auto-selected images
- `render_keep_best(images, delete_fn)` — "Keep Best Only" button that deletes all but rank 1
- Depends on: `utils-module`

### 5. `main-app` — Implement `ui/app.py`
- `st.set_page_config(layout="wide")`
- Title: "📸 Photo Curation Tool"
- Sidebar: cluster selector (selectbox)
- Main area:
  - Cluster grid (4 cols max, wrapping for large clusters)
  - Best image highlighted with ⭐ BEST
  - Per-image Keep / Delete buttons
  - Divider
  - Compare Mode toggle → side-by-side view
  - Auto-select worst images (score threshold slider)
  - "Keep Best Only" button
  - Bulk "Delete Selected Images" button
- Session state to track selected-for-deletion images across rerenders
- Depends on: `components-module`, `utils-module`

### 6. `readme` — Create README.md
- Setup instructions (`pip install -r requirements.txt`)
- How to generate sample data (`python scripts/generate_sample_data.py`)
- How to run (`streamlit run ui/app.py`)
- Expected `results.json` schema
- Depends on: `main-app`

## Safety Requirements
- **NEVER** permanently delete images
- Always `shutil.move()` to `outputs/trash/`
- Show confirmation in UI after deletion

## Key Decisions
- 4-column grid layout (wraps for large clusters, avoids horizontal overflow)
- Session state for delete selections (Streamlit rerenders lose button state otherwise)
- Compare mode is a toggle, not a separate page
- `outputs/` paths in `results.json` are relative to the repo root (CWD when running)
