# Startup notes

## Intake folder
Main folder for photos that need curation: `~/h0/Editing/imports/`

## Pipeline cache
Stored at `~/.local/share/sightread/projects/<md5-of-folder-path>/` (one dir per project).
Key files per project:
- `embeddings_dinov3_mpcls_tta.npy` + `.paths.json` — CLIP/DINOv3 embedding cache
- `scores_ensemble.npz` + `.paths.json` — IQA score cache
- `results.json` — clustered output consumed by the webapp
- `to_delete.txt` — pending deletion list
