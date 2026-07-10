# Plan: ClipFarm 2D Clip Suggestions → Sightread Video Review

Goal: sightread's video review UI shows **suggested highlight clips on each video's
timeline**, computed during sightread's preprocessing pipeline using clipfarm's 2D
clip-generation code, consumed as a library (not copy-paste).

Date: 2026-07-09. Projects: `~/Projects/clipfarm`, `~/Projects/sightread`.

---

## Current state (verified by reading both codebases)

**clipfarm** (`clipfarm/` package):
- 2D ("regular") video path works end-to-end: ingest → views passthrough → segment
  (4s windows, 1s overlap) → frames (3/clip) → CLIP embed → score
  (motion/scene/novelty/ml) → rank. Scoring math is pure cosine ops on embeddings.
- **Blocker 1:** `clipfarm/config.py` hardcodes all cache paths relative to clipfarm's
  own repo root (`PROJECT_ROOT/cache`). Pipeline modules read these constants directly;
  only the FastAPI layer honors `CLIPFARM_CACHE_DIR`.
- **Blocker 2:** `clipfarm/score.py` imports `clipfarm.embed` (→ `open_clip`, heavy) at
  module level, so the pure scoring functions can't be imported without the CLIP stack.
- **Blocker 3:** pipeline functions scan global dirs (`STREAMS_DIR`, `VIEWS_DIR`) rather
  than operating on a single video path. No per-video entry point.
- Packaging: `pyproject.toml` exists but `build-backend` is a broken legacy string and
  `streamlit` sits in core deps.

**sightread**:
- `scripts/pipeline.py` — photo-only (DINOv3 embed → cluster → IQA score → rank).
  Run as subprocess by `webapp/jobs.py`. Outputs per-project to
  `~/.local/share/sightread/projects/<md5>/`.
- Videos are **not** touched by the pipeline; `webapp/server.py:/api/videos` lists them
  live from the folder and background-transcodes for browser playback (`webapp/video.py`).
- `VideoView.tsx` plays with native `<video controls>` — no custom scrubber.
- Python 3.11.15; already has torch/transformers/faiss. Missing: `open-clip-torch`.

---

## Design decisions (defaults chosen; see Open Questions)

1. **Embedding backbone for video frames: reuse sightread's DINOv3.**
   clipfarm's motion/scene/novelty scores are backbone-agnostic (cosine distances on
   L2-normed embeddings). DINOv3 is already installed, already warm in sightread's
   pipeline, and avoids adding the ~2GB open-clip ViT-H-14 stack. Cost: the `ml`
   prompt-similarity score needs a text tower DINOv3 lacks → drop `ml`, renormalize
   composite weights over motion/scene/novelty (clipfarm's `compute_composite_score`
   already handles arbitrary weights; pass ml=0-weight variant).
2. **Consume clipfarm via editable install** (`pip install -e ~/Projects/clipfarm`)
   with a new lightweight `clipfarm.lib` module importable without open_clip/streamlit.
3. **UI: highlight strip under the video in `VideoView`** (native controls kept),
   segments positioned by `start/duration` fractions, colored by score, click-to-seek.
   Plus a small "✨ n highlights" badge on video cards in `TimelineView`.

---

## Phase 1 — clipfarm: expose a library API

Files: `clipfarm/pyproject.toml`, `clipfarm/clipfarm/{config,score,segment}.py`, new
`clipfarm/clipfarm/lib.py`, new tests.

1. **Fix packaging** (`pyproject.toml`):
   - `build-backend = "setuptools.build_meta"`.
   - Move `streamlit`, `open-clip-torch`, `bitsandbytes`, `accelerate` to
     `[project.optional-dependencies] full = [...]`; core deps become
     numpy/opencv/tqdm/Pillow only. `pip install -e .` stays light.
   - Add `[tool.setuptools] packages = ["clipfarm", "clipfarm.api"]` (exclude `run.py`
     script module or keep as-is — verify wheel builds).
2. **Break the heavy import chain**: in `score.py`, make
   `from clipfarm.embed import load_frame_embeddings` lazy (inside `score_all_clips`).
   Pure functions (`compute_motion_score`, `compute_scene_change_score`,
   `compute_novelty_score`, `compute_composite_score`, `_consecutive_distances`)
   become importable with numpy alone. (Also fixes `tests/test_score.py` collection
   in envs without open_clip.)
3. **New `clipfarm/lib.py`** — single-video, path-parameterized API, no global config
   paths:
   ```python
   def suggest_clips(
       video_path: Path,
       workdir: Path,                      # caller-owned cache dir
       embed_frames: Callable[[list[Path]], np.ndarray],  # pluggable backbone
       *,
       clip_duration: float = 4.0,
       clip_overlap: float = 1.0,
       frames_per_clip: int = 3,
       seconds_per_suggestion: float = 30.0,   # duration-scaled N
       max_suggestions: int = 12,
       force: bool = False,
   ) -> list[dict]:
       """Returns [{start, end, score, scores:{motion,scene_change,novelty}}] ranked."""
   ```
   Internals reuse existing helpers: `segment._time_windows` (parameterized — add
   optional duration/overlap args defaulting to config constants), frame extraction via
   cv2 seek (reuse `frames.extract_frames` logic parameterized on out dir), scoring via
   the pure functions, ranking = sort by composite desc, take top-N, merge adjacent
   overlapping windows.
   - **No re-encoding**: suggestions are (start,end) metadata only — no clip mp4s cut.
     Frame jpgs + embeddings cached in `workdir` keyed by video path+mtime fingerprint
     (same `_fingerprint` approach as `export.py`).
4. **Tests**: `tests/test_lib.py` — synthetic embeddings through `suggest_clips` with a
   fake `embed_frames`, window math, caching (second call no re-embed), force flag.

## Phase 2 — sightread: pipeline preprocessing step

Files: `scripts/pipeline.py`, `requirements.txt`, maybe new `scripts/video_highlights.py`.

1. Add `clipfarm @ file:///home/jason/Projects/clipfarm` (or `-e` in requirements-dev;
   document `pip install -e ../clipfarm`) to requirements.
2. New pipeline step after photo scoring (keeps GPU model reuse simple):
   - Scan `image_dir` for videos (reuse the extension set from `webapp/server.py` —
     lift into a shared constant).
   - `embed_frames` adapter: wrap sightread's DINOv3 `_run_embedding_model` (already
     takes a list of image paths → normalized np array). Model loaded once for all
     videos, released after.
   - For each video: `suggest_clips(video, workdir=output_dir/"video_highlights_cache",
     embed_frames=...)`.
   - Write `output_dir/video_highlights.json`:
     `{video_abs_path: {"duration": float, "clips": [{start, end, score, scores}]}}`.
   - Incremental: skip videos whose path+mtime fingerprint already in the JSON
     (mirrors existing embeddings/IQA cache pattern). `--no-video-highlights` opt-out
     flag; step is fully fault-isolated (a failed video logs + continues).
3. `scripts/clean_cache.py`: also clear `video_highlights*`.

## Phase 3 — sightread: server

Files: `webapp/server.py`.

- Extend `/api/videos` response: `highlights: {path: {duration, clips[]}}` loaded from
  `output_dir/video_highlights.json` (empty dict when missing — UI degrades cleanly).
  No new endpoint needed; payload is small (top-8 windows per video).

## Phase 4 — sightread: frontend

Files: `webapp/frontend/src/components/VideoView.tsx`, `TimelineView.tsx`, `types.ts`,
`App.tsx`.

1. `types.ts`: `VideoHighlight {start,end,score}`, `VideoHighlights` map type.
2. `App.tsx`: keep highlights from `/api/videos` in state; pass to `VideoView` +
   `TimelineView`.
3. `VideoView`: **HighlightStrip** component rendered between toolbar and video (or as
   thin overlay bar at the video's bottom edge):
   - Horizontal track = video duration; each suggested clip = a segment
     (`left = start/duration %`, `width = (end-start)/duration %`).
   - Opacity/color by score (e.g. amber, stronger = better).
   - Click segment → `videoRef.currentTime = start`.
   - Keyboard: `n` / `p` jump to next/prev highlight start.
   - Live playhead tick synced via `timeupdate` so the strip doubles as a scrubber.
4. `TimelineView`: video cards get "✨ N" badge when highlights exist for that path.

## Phase 5 — verification

- clipfarm: new unit tests + existing 81 pass.
- sightread: run pipeline on a small folder containing 2–3 videos (e.g. Vegas trip),
  confirm `video_highlights.json` written, strip renders, click-seek works.
- Re-run pipeline → highlight step is a cache no-op.

---

## Effort estimate

| Phase | Size |
|---|---|
| 1 clipfarm lib API + packaging | ~half day |
| 2 pipeline step | ~2–3 h |
| 3 server | ~30 min |
| 4 frontend | ~2–3 h |
| 5 verify | ~1 h |

## Decisions (locked 2026-07-09, per Jason)

1. **Backbone: DINOv3** — reuse sightread's model; drop CLIP `ml` score, renormalize
   composite over motion/scene/novelty.
2. **Density: duration-scaled** — ~1 suggestion per 30s of video, capped at 12,
   minimum 1 (when any window scores > 0).
3. **UI: both** — highlight strip in VideoView + "✨ N" badge on TimelineView video cards.
4. **Rollout: on by default** — incremental cache makes re-runs cheap; `--no-video-highlights`
   opt-out kept as escape hatch.
