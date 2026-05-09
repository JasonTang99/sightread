# Sightread — Improvement Plan

Updated: 2026-04-29

---

## Bugs fixed this session

- `tests/conftest.py`: `reset_output_dir` fixture didn't clear `curation.json` between tests → state bleed causing cascade failures. Fixed by unlinking it before each test.
- `tests/test_ui.py`: 3 stale assertions — `test_confirm_removes_cluster_from_results` checked `results.json` but app now uses `curation.json`; `test_completion_shows_rm_command` / `test_completion_code_block_has_copy_button` looked for `rm -rf` but completion screen shows `delete_marked.py`.
- `webapp/server.py`: Failed to start — `remove_images_from_results` missing from `ui/utils.py`. Added it.
- `webapp/server.py`: Undo was broken — passed results dict to curation-specific `snapshot_state`/`restore_snapshot`. Rewrote undo to store `{results, delete_paths}` entries and restore both.

**Test baseline after fixes:**
- `tests/test_utils.py`: 54/54
- `tests/test_ui.py` (Streamlit): 33/33
- `webapp/tests/test_ui.py`: 36/36

---

## Planned improvements (Opus review, 2026-04-29)

### P0 — Security / correctness

- [ ] **Race condition on confirm** — `/api/confirm` does read-modify-write with no locking. Concurrent requests or double-clicks can lose deletions. Fix: `threading.Lock` on all mutating endpoints (`/api/confirm`, `/api/undo`, `/api/restore`). Also make `save_results` atomic via temp-file + `os.replace`.

- [ ] **Path traversal bypass** — `str(abs_path).startswith(str(PROJECT_ROOT))` is bypassable by sibling directories (e.g. `sightread2/`). Fix: use `abs_path.relative_to(PROJECT_ROOT)` in a try/except; also reject `Path(path).is_absolute()` early.

### P1 — UX / throughput

- [ ] **Keyboard shortcuts** — biggest single win. `1-9` toggle keep/delete for Nth image, `Enter` confirm, `←/→` prev/next cluster, `K` keep-best, `B` skip, `U` undo, `?` help overlay. Show digit on each card's rank badge. Would 3-5× curation throughput.

- [ ] **Auto tab-switch hijacks user** — `setTab("singles")` inside `reload()` runs on every confirm, pulling user off clusters mid-session. Fix: use a `ref` to only auto-switch once on initial mount.

- [ ] **Bulk operations + session progress** — no "X/Y reviewed" counter, no jump-to-unreviewed, no auto-keep-best above threshold. Fix: `reviewed: Set<clusterId>` in client state, show progress in header, add `/api/auto_keep_best` endpoint (one undo entry for all).

### P2 — Performance

- [ ] **Image caching** — thumbnails re-encoded on every request, no `Cache-Control`/`ETag`. Fix: `Cache-Control: public, max-age=86400, immutable` + `ETag` on `(path, mtime, w)`, disk thumbnail cache under `webapp/.thumb_cache/{w}/{hash}.jpg`, `304` on `If-None-Match`.

- [ ] **`load_results` on every API call** — re-parses JSON every `/api/state` GET. Fix: mtime-based in-memory cache; invalidate on confirm/undo.

### P3 — Reliability

- [ ] **Undo stack lost on restart** — in-memory only; server restart silently breaks undo. Fix: persist stack to `outputs/undo.jsonl`, or switch to diff-based undo (store only removed images + cluster indices rather than full snapshots). Document that undo doesn't restore already-trashed files.
