# Sightread — Improvement Plan

Updated: 2026-09-01

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

---

## Next: finishing a trip end to end (built; Japan not run)

Spec added 2026-08-23; all three steps built by 2026-08-31. The gap this closes: curation
produces decisions, but turning those decisions into *outcomes* — space reclaimed on h0,
favourites delivered somewhere useful — was either buried or missing.

### Step 1 — Apply the deletes. **Built. Not yet run on Japan.**

`POST /api/apply-deletes` (`webapp/server.py`) unlinks every `to_delete` file from the
primary drive once its h1 copy is confirmed at a matching size, refuses if the project
folder is unmounted, and appends `.sightread_deleted.txt` to the mirror directory.

Two things landed 2026-08-23 that the original spec missed:

- **A queued entry is a shot, not a file** (`6b1c427`). The queue holds JPEGs, but the
  camera wrote a raw beside each one and editors leave `.xmp` next to both. Deleting only
  the JPEG reclaimed a tenth of the space and orphaned the raw — on Japan's 349-shot queue,
  6.64 GB of 63.13 GB, with 348 stranded `.RAF` files. `utils.sidecars_of` now groups them,
  both deleters share it, and the group is verified and unlinked atomically: any member
  failing the mirror check defers the whole shot, because a shot split across two drives is
  worse than one deferred.
- **A guard for an unreachable mirror root** (`c28deb4`). An unmountable mirror fails every
  size check and defers the entire queue — safe, but reported identically to having nothing
  to do. Both deleters now refuse up front and name `SIGHTREAD_MIRROR_ROOT`.

**Dry run against the correctly-mounted pair, 2026-08-23:** 349 shots + 1045 sidecars,
58.79 GiB, 0 skipped, 0 unmirrored. Ready to apply; the command is

```bash
python3 scripts/delete_marked.py --output-dir ~/.local/share/sightread/projects/644ad1423896f32844c61d87c1cdcc6b
```

Still open:

- **Run it.** 1394 files, irreversible in-app (the undo stack is cleared in the same call);
  recovery means copying back from h1 by hand.
- ~~**Placement.** `TrashPanel` still renders inside the clusters, singles and timeline tabs~~
  **Done 2026-08-31.** Finish tab + `FinishTripPanel` — apply-deletes is step 1 there.

### Step 2 — Export favourites. **Built 2026-08-23 (`66ad867`). Not yet run.**

`webapp/exports.py` + `GET /api/exports/preview` + `POST /api/exports/favorites`, with a
button in `FavoritesView`. Copies to `<SIGHTREAD_EXPORTS_ROOT>/<trip folder name>/`,
defaulting to `/mnt/h0/Editing/exports`. **Tagged videos** (see below) land in
`<trip>/<tag>/`; photos and untagged favourited videos stay in `<trip>/`.

The open questions from the spec, as settled:

- **Exports root**: `/mnt/h0/Editing/exports`, configurable by env var. The h0-vs-h1
  concern was overstated in the spec — the 145GB of cache bloat is on the **nvme**
  (`~/.local/share/sightread/`), not on h0, so exporting does not meaningfully undo step 1.
  The confirm dialog prices the copy and shows free space regardless.
- **Favourited videos**: the whole original, never the `video_cache` transcode. No clips
  alongside — a starred video exports as one file.
- **Collisions**: a destination name holding a file of the same size counts as delivered and
  is skipped, which makes re-running idempotent; a name holding a *different* file gets a
  numeric suffix. Nothing is overwritten. Copies land on `.sightread-part` and are renamed
  into place so an interrupted run cannot leave a truncated file that the next run's size
  check would mistake for a finished one.

**Preview against Japan, 2026-08-23:** 16 shots, 64 files, 2.20 GiB, 452 GiB free, none missing.

Still open:

- **Run it.** Non-destructive; only ever writes into the exports tree.
- ~~**Re-arming on reload.**~~ **Done 2026-08-31.** `plan_export` now splits the favourite
  set into `delivered` / `pending` using the same size check the copy makes, so a reload
  after a successful export leaves step 2 ticked instead of demanding a no-op click before
  step 3 unlocks. The confirm dialog prices only the pending files.
- **Decide whether a favourite should export as a shot or as a JPEG.** Currently it exports
  the whole shot — JPEG + raw + both `.xmp` — on the reasoning that the raw is the file that
  actually gets edited, which is why 16 favourites are 64 files. Flagged to the user
  2026-08-23, unanswered. A JPEG-only deliverable is a one-line change to
  `exports.files_for()`.

### Step 3 — Clear the pipeline and caches. **Built 2026-08-31.**

Offered *after* a successful export, and only then — it destroys the ability to re-review.

- `POST /api/projects/done` deletes `thumb_cache`, `poster_cache` and `video_cache`
  and marks the project finished. That is the preview-cache half.
- `POST /api/projects/clean-pipeline` removes `results.json`, `clusters.json`, the
  embeddings, the score cache and `video_highlights.json` (+ `video_highlights_cache/`).
  That is the pipeline half (same set as `scripts/clean_cache.py`, minus derived dirs).
- **Finish tab UI** (`FinishTripPanel`) walks all three steps in order with gating.
  `GET /api/finish/preview` prices each step. Header "Mark done" removed — step 3 only.
- **`decisions.json` survives** — settled 2026-08-23. After the deletes are applied it is the
  only record of what was removed and why, alongside the mirror manifest. Say so on the button.

Still open:

- **Run steps 1–3 on Japan** (operational, not code).
- **Orphan cache sweep** on nvme (see Related below) — not part of the finish panel yet.

### Suggested shape

~~A "Finish trip" panel that walks the three steps~~ **Done 2026-08-31** — see Finish tab.

### Video tags for export routing — **Built 2026-09-01.**

Named tags on favourited videos, routed into export subfolders at copy time. Built on
`mirror-aware-deletes`; not yet merged to `main`.

- **Storage:** `<output_dir>/video_tags.json` — `tags` list + per-video assignments
  (`webapp/video_tags.py`).
- **Export:** `exports.py` sends tagged videos to `<EXPORTS_ROOT>/<trip>/<tag>/`; photos
  and untagged videos unchanged. `plan_export` adds a `destinations` breakdown for the
  finish-panel confirm dialog.
- **API:** `GET/PUT /api/video-tags`; `/api/videos` includes `video_tags`.
- **UI:** Videos tab — star a clip, then pick/create tags on the toolbar. `1`–`9` apply
  tags by slot (same convention as cluster rank keys); `t` cycles tag or clears.
- **Tests:** `tests/test_video_tags.py` (persistence, API, export routing); 42 export/tag
  tests pass; frontend rebuilt.

Still open:

- **Use while reviewing Japan's 62 undecided videos** — tag before export so delivery
  folders match the edit (e.g. `b-roll`, `timelapse`). First action: open Videos tab on
  Japan, create tags with `+`, star + `1`/`2`/`3` as you go.
- **Merge `mirror-aware-deletes` → `main`** after Japan finish flow validates end-to-end.

### Related, still open

- **~145GB of derived cache on the nvme**, 140GB of it Hawaii's `video_cache`: 89 files at
  4K/194Mbps, output from an older converter whose cache keys the current code no longer
  computes, so nothing will ever read them. Note this is the **system disk**, not h0 — freeing
  the photo drive and freeing the cache drive are two different operations. `POST
  /api/projects/done` reclaims them per project, but orphans in a project still being curated
  need a keyed sweep: delete any `video_cache` / `thumb_cache` / `poster_cache` entry whose key
  no source file currently maps to.
- **Japan reads 326/388 reviewed, and that number is correct.** 79 clusters + 245 singletons
  are fully decided; of 64 videos, 2 are `keep` and **62 are undecided**. Verified against the
  live server 2026-08-23 — the counter was never the bug (the localStorage "✓ reviewed" *badge*
  was, fixed in `55e630e`). Nothing in the finish flow is blocked by this: the photo queue and
  the starred set are complete and independent. It only affects whether more videos get starred
  into the export, and whether step 3 should wait. There is no bulk sweep for videos the way
  `⚡ Auto-best` works for clusters — all 62 need a look.

### Machine-level, not a code change — worth remembering

`/etc/fstab` mounted the photo drives by `/dev/sdX`, which the kernel assigns in probe order.
A plugged-in Kindle claimed `sda`, everything shifted, and **h0 was mounted at `/mnt/h1`** while
`/mnt/h0` stayed empty — so the mirror check was comparing the primary drive against itself.
Fixed 2026-08-23 by switching all three entries to `UUID=` (h0 `f392ce70…`, h1 `381a1f01…`,
shared `15EA-CBBF`; backup at `/etc/fstab.bak-20260823`). If verification ever starts failing
for everything at once, check `lsblk -f` first.
