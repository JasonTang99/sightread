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

- [x] **Race condition on confirm** — `/api/confirm` did read-modify-write with no locking; concurrent requests or double-clicks could lose deletions. Fixed 2026-07-26: `_curation_lock` (`webapp/server.py`) serialises `/api/confirm`, `/api/undo`, `/api/restore`, `/api/favorite` and `/api/apply-deletes`. Writes are atomic as of 2026-07-27: `_write_json_atomic` (`webapp/utils.py`) goes through a temp file + `Path.replace`, so a crash mid-write can no longer truncate `decisions.json`.

- [x] **Path traversal bypass** — `str(abs_path).startswith(str(PROJECT_ROOT))` was bypassable by sibling directories (e.g. `sightread2/`). Already fixed: `_is_under` (`webapp/server.py`) does `path.relative_to(base)` in a try/except, and `_in_allowed_dirs` applies it to each allowed base. No `startswith` path checks remain in the codebase.

### P1 — UX / throughput

- [x] **Keyboard shortcuts** — done. `ClusterView` handles `1-9` by rank, `hjkl` focus, `Space` toggle, `K` keep-best, `Enter` confirm, `←/→` clusters, `b` skip, `s` star, `u` undo; `HelpOverlay` (`?`) documents them and rank digits show on each card's badge.

- [x] **Auto tab-switch hijacks user** — done. `landedRef` (`App.tsx`) picks the landing tab once per project and never re-runs, so confirming the last cluster no longer yanks you off the tab you're in.

- [x] **Bulk operations + session progress** — done 2026-08-02. Header shows an `X/Y reviewed` meter over clusters + singles + videos (`data-testid="session-progress"`); `ClusterView` has `n` / "→ Unreviewed" to jump to the next undecided cluster and an "⚡ Auto-best" sweep (two-click confirm) behind `POST /api/auto-keep-best`. The sweep keeps rank 1 and queues the rest for every *untouched* multi-image cluster, takes an optional `min_score`, skips singletons, and pushes **one** undo entry for the whole run so it can't overflow the ten-deep stack.

### P2 — Performance

- [x] **Image caching** — done. Disk thumbnail cache at `<output_dir>/thumb_cache/{sha1(path|mtime_ns|w)}.jpg` plus `Cache-Control: private, max-age=86400`; `ETag` + `304 If-None-Match` added 2026-08-02 on `/api/image` and `/api/video-poster` (`_media_etag`/`_not_modified`), so a timeline revalidating hundreds of tiles costs a few hundred bytes instead of a few hundred MB. Weak validators (`W/"…"`) are matched. `private`, not `public, immutable`: the payload is the user's photos and the URL is not content-addressed.

  Deliberately **not** on `/api/video`: those responses are ranged, a hand-rolled 304 alongside `206` risks breaking scrubbing, and the file is already served from a local transcode cache.

- [x] **`load_results` on every API call** — done 2026-08-02. `webapp/utils.py` keeps an in-memory parse keyed on `(mtime_ns, size)`, so `/api/state` and `/api/gallery` reuse it and a pipeline re-run still invalidates. The returned dict is shared — read-only by contract. `invalidate_results_cache()` is called when a pipeline run starts.

- [x] **Media-loading hot paths** — done 2026-08-02, found while doing the above:
  - `/api/videos` walked the whole project tree with `rglob("*")` + `is_file()` — a stat per entry across thousands of JPEGs — on *every* timeline open. Now `_scan_videos` filters on the filename before touching the filesystem.
  - `_get_shot_times` re-parsed and non-atomically rewrote `shot_times.json` per call, and `/api/gallery` and `/api/videos` raced on it: whichever finished last dropped the other's freshly-read EXIF, so those timestamps were re-read forever. Now serialised under a lock, memoised in process, and written via temp + rename.

### P3 — Reliability

- [x] **Undo stack lost on restart** — done 2026-08-02. The stack persists to `<output_dir>/undo.jsonl` (rewritten whole on each push/pop, ten entries deep) and is reloaded when a project becomes active. A corrupt or absent file yields an empty stack rather than an error. Undo still cannot restore files already unlinked by `apply-deletes` — that call clears the stack *and* the file, and recovery means copying back from the mirror drive.

---

## POTENTIAL — Remote curation from a second machine (assessed 2026-07-25)

**Status: not committed. Feasibility assessment only, no work started.**

Goal: curate from a laptop that must not store the photos (e.g. a work laptop), while the
files and the destructive `apply_deletes` step stay on the home box.

### Why this is the cheap option

The decision artifacts are already separate from the pixels and already tiny:

| Artifact | Size (2026_01_Japan, 448 images) |
|---|---|
| Source photos + video | 104 GB |
| Derived cache (`thumb_cache` 449 MB + `video_cache` 1.9 GB + highlights 66 MB) | 2.4 GB |
| `/api/state` payload | 188 KB |
| `decisions.json` + `user_clips.json` | **~17 KB** |

So nothing about the data model needs to change. Serve the existing app over a tunnel;
the remote browser pulls cached thumbs/clips, and `POST /api/apply-deletes` still runs
against local disk on the home box.

Rejected alternatives (see below) all cost multiples of this.

### Sketch

1. **Transport** — Cloudflare Tunnel (nothing to install on the client, browser only) or
   Tailscale (no public exposure at all, but needs a client install that corporate MDM may
   block). Prefer Cloudflare Tunnel for a locked-down laptop.
2. **Identity** — put Cloudflare Access (or equivalent identity proxy) in front. See the
   security note below; this is not optional.
3. **Code change** — `webapp/server.py:91` `_reject_non_local` hard-rejects any non-localhost
   `Host` header. Needs an env-var allowlist (e.g. `SIGHTREAD_ALLOWED_HOSTS`) so the tunnel
   hostname passes. Default stays localhost-only.

Estimated ~1–2 hrs. No pipeline changes, no storage-layer changes.

### Security preconditions — do not skip

The app has **no authentication of any kind** today. `_reject_non_local` is the only access
control and it trusts the client-supplied `Host` header, which is trivially forged. Anyone
who can reach the port can:

- read any file under the project folder, output dir, or repo root via `/api/image?path=`
  (`webapp/server.py:549`)
- call `POST /api/apply-deletes` (`webapp/server.py:236`) and unlink every pending-delete file
  on the primary drive

**There is no mitigating factor any more.** This assessment originally rated the blast radius
as survivable because `apply-deletes` was a `shutil.move` into `<output_dir>/trash/`. Since the
mirror-aware deletion refactor it is a real `src.unlink()`, and the undo stack is cleared in the
same call. The only remaining guard is the mirror check: a file is unlinked only once a
same-sized copy is confirmed under `SIGHTREAD_MIRROR_ROOT`. That protects against data loss from
a missing mirror — it does not protect against an attacker, who can simply delete everything that
*is* mirrored and leave the h1 copy as the sole remaining one. Favourites are skipped, so the
starred set is the only thing an unauthenticated caller cannot destroy.

Therefore:

- **Do not port-forward 8765.** Ever.
- Authentication must be handled by an identity proxy in front of the app, or written and
  reviewed as a real feature. Relaxing the host check without one is strictly worse than today.
- Loosening `_reject_non_local` and adding auth are a single unit of work. Don't land the
  first without the second.
- Re-rate this before any remote-access work: exposure now means unrecoverable deletion from the
  primary drive, not a recoverable sweep into a folder.

Also worth confirming separately: whether a tunnel from a work-managed laptop to a personal
home network is permitted by employer device policy. Orthogonal to the technical design.

### Constraints to expect

- **Home upload bandwidth is the bottleneck.** A full browse of a 448-image project pulls
  ~450 MB of thumbs; watching every clip adds ~1.9 GB. Caches already exist on disk, so this
  is pure file serving — no re-encode cost.
- Timeline thumbs are `w=600` (cheap); singleton view requests `w=2400` (~1 MB each).
- Video is already 1440p / 1s keyframes / faststart, so WAN scrubbing should hold up.
- Server runs without `--reload` deliberately — in-memory active project and undo stack are
  wiped on restart. Remote sessions make an accidental restart more costly, not less.

### Alternatives assessed and rejected

- **Google Drive as backing store** — multi-day rewrite. Every file op is local-`Path` based:
  `FileResponse` (`server.py:543,559`), `Image.open` (`server.py:569`), `shutil.move`
  (`server.py:251`), `_in_allowed_dirs` containment (`server.py:591`), plus a directory-walking
  pipeline. Needs a storage abstraction across ~5 modules, and 104 GB uploaded first.
- **Google Photos as backing store** — appears to be a dead end. Since March 2025 the Library
  API only exposes media the app itself uploaded; whole-library enumeration is gone. Re-verify
  against current Google docs before fully ruling out.
- **Static thumb export + decision re-import** (~1 day) — export `w=600` thumbs to a static
  host, run a decision-only UI, sync the ~17 KB of decision files back, apply with the existing
  standalone `scripts/delete_marked.py` (already supports `--dry-run`). Home box can be offline
  during curation. Cost: no video review, no full-res compare. Keep as fallback if the tunnel
  route is blocked by device policy.
