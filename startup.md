# Startup notes

## Intake folder
Main folder for photos that need curation: `~/h0/Editing/imports/`

## Pipeline cache
Stored at `~/.local/share/sightread/projects/<md5-of-folder-path>/` (one dir per project).
Key files per project:
- `embeddings_dinov3_mpcls_tta.npy` + `.paths.json` — CLIP/DINOv3 embedding cache
- `scores_ensemble.npz` + `.paths.json` — IQA score cache
- `results.json` — clustered output consumed by the webapp
- `decisions.json` — all curation state: one status per photo path
  (`kept` / `favorite` / `to_delete` / `deleted`). `to_delete` is the pending
  delete queue; `deleted` records what has already been unlinked.
