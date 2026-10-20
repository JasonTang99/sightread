# Data storage: what `~/.local/share/sightread` holds and where it could live

_Audited 2026-10-20. Nothing was moved or deleted during the audit._

## What is there

`DATA_DIR` is `$XDG_DATA_HOME/sightread/projects` (`webapp/projects.py`), one
folder per project, named by the md5 of the trip folder. All 80 projects point
at originals on `/mnt/h0/Trips/...`. **Nothing here is an original.** Every
byte is either curation state or something derived from the originals on h0.

`du` reports 81 GB. Counting each inode once, 86.7 GB are in use: 40,708
thumbnails are hard links shared between projects (a trip and its city
sub-projects), so `du` over the whole tree undercounts them, and `du` per
project overcounts them.

| What | Size | Files | Rebuild cost | Read pattern |
|---|---|---|---|---|
| `thumb_cache`, 2400 px compare renders (files > 800 KB, about 1.85 MB each) | 56.5 GB | 30,486 | ~0.9 s per photo from h0 (`_start_thumb_prewarm` docstring) | one file per "next" in the cluster/singletons compare, plus prewarm writes |
| `thumb_cache`, 800 px grid tiles (< 300 KB) | 4.5 GB | 33,170 | ~0.5 s per photo | hundreds of small random reads when the timeline fills, 6 at a time (browser connection limit) |
| `thumb_cache`, other widths (200/400, mid sizes) | 3.3 GB | 6,101 | same as above | Trash/Favorites panels |
| `video_cache` (1440p H.264, CRF 21, ≤ 15 Mbps) | 21.3 GB | 672 | an ffmpeg re-encode per clip, minutes per GB of camera footage | sequential range reads while playing, a seek on scrub |
| `video_highlights_cache` (frames + `.npy`) | 0.6 GB | 10,255 | a pipeline run (model inference) | read when the video highlights view opens |
| `poster_cache` | 0.04 GB | 407 | one ffmpeg frame grab each | small random reads on the timeline |
| embeddings `.npy`, `scores_ensemble.npz` | 0.38 GB | 157 | a pipeline run on the GPU (DINOv3 + scorers), the expensive one | loaded whole once per project open |
| `decisions.json`, `results.json`, `clusters.json`, `undo.jsonl`, `project.json`, … | 0.03 GB | 391 | **decisions cannot be rebuilt.** They are the curation itself. The rest comes from a pipeline run. | read whole on open, rewritten on each decision |
| `../backups` | 5.2 MB | | decision backups, keep | rare |

Split by project state: the 16 finished projects (`curation_done.json`) hold
only 3.1 GB, because `POST /api/projects/done` already drops their caches.
The other 83 GB belong to projects still open.

### Free space that needs no move

- `scripts/sweep_orphan_caches.py --include-widthkeyed` (dry run) finds
  **11,364 unreachable cache files, 8.4 GB**: their source was deleted or
  re-encoded, or they were written with an old key. Running it with
  `--apply` loses nothing: no request can reach those keys.
- Marking finished trips as done frees their caches the same way the other
  16 were freed.

## Is an HDD fast enough? Measured

Method (`bench.py` in the session scratchpad, numbers below): one real project's
`thumb_cache` (Stratford, 1,110 files, 1.6 GB) and five `video_cache`
transcodes (1.1 GB) were copied to `/mnt/h1`. The same files were then read
cold from the NVMe originals and from the h1 copy: page cache dropped first
with `posix_fadvise(DONTNEED)`, two runs each. The reads follow the app's
access patterns. This measures storage only; the server adds the same
overhead on both. The copy has since been moved to h1's trash
(`/mnt/h1/.Trash-1000`).

| Operation | Samsung 980 NVMe | h1 (WD, 5400-class) |
|---|---|---|
| Timeline fill: 375 grid tiles (68 MB), random order, 6 parallel | 0.04–0.05 s total; 0.6 ms median per tile | **2.5–2.9 s** total; 25–28 ms median, p95 127–149 ms |
| Compare: step through 60 renders at 2400 px (2.7 MB each), one at a time | 2.2 ms median | **44–49 ms** median, p95 85–102 ms |
| Video: open + first 2 MB of a 1 GB transcode | 2 ms | 58 ms |
| Video: 2 MB seek at a random offset | 2 ms median | 46–53 ms median, p95 75–84 ms |
| Video: sequential read | ~1,470 MB/s | 162–171 MB/s (playback needs ≤ 2 MB/s) |

What that means in use:

- **Video on h1 is fine.** Playback needs at most 15 Mbps (about 2 MB/s)
  against 160+ MB/s. A scrub costs about 50 ms of disk time, below what the
  1440p software decode already costs.
- **Compare renders on h1 are fine.** A 45 ms read against a
  0.9 s re-render on a cache miss is still about 20 times better than a miss, and
  prewarm keeps the next one ahead of you.
- **Grid tiles are where an HDD shows.** A cold timeline is about 50 times
  slower: a 60-tile screen arrives in about 0.3–0.5 s instead of instantly,
  with stragglers at about 150 ms. On a big trip (5–10k photos) a fast scroll
  to the end would visibly fill in. Tiles are 4.5 GB in total, so keeping them
  on NVMe is cheap.
- The machine has 30 GB of RAM, and about 18 GB of it is page cache, so a
  second visit to a project hits memory whatever the disk.
- These are best-case numbers for the HDD. A fresh copy is laid out
  contiguously, but a cache written piece by piece over months by the prewarm
  pool fragments more. A spun-down drive also adds a few seconds of spin-up
  on the first read.
- **h1, not h0.** Thumbnail renders and clip exports read the originals on h0.
  Putting the caches on the same spindle would make the prewarm pool's reads
  (originals) and writes (cache) seek against each other. h1 is the mirror
  drive and is otherwise idle while curating. The catch: the mirror is
  then also holding scratch data. That is fine, since it is all
  regenerable, but `delete_marked.py`'s mirror checks look under
  `/mnt/h1/h0`, so the caches should go in a sibling folder like
  `/mnt/h1/sightread-cache`, never inside the mirror tree.

## Recommendation

Hot on NVMe (about 6 GB): every project's JSON files and embeddings, the
grid-width thumbnails (≤ 800 px), posters and `video_highlights_cache`.

Cold on h1 (about 78 GB, all regenerable): `video_cache` (21 GB) and the
2400 px compare renders (56 GB).

How, in order of effort:

1. **First, free space without moving anything:** run the orphan sweep
   (8.4 GB) and mark finished trips done. No code.
2. **video_cache → h1, no code change:** per project, move `video_cache`
   to `/mnt/h1/sightread-cache/<project>/video_cache` and leave a symlink.
   The code only ever does `output_dir / "video_cache" / key`, so a symlinked
   directory is transparent. That is 21 GB off the NVMe. If h1 is unmounted,
   the symlink dangles: `cached.exists()` is false, the player's HEAD
   probe queues a transcode, the write into the missing directory fails,
   and the clip is probably marked "transcode failed; the file may be
   corrupt" until the server restarts (from reading `/api/video`, not
   tested). That message would be misleading, so step 3 should check that
   the cold root is mounted and say so.
3. **Compare renders → h1, small code change:** they share `thumb_cache`
   with the grid tiles (same directory, width only inside the hash), so a
   symlink cannot split them. Add an optional `SIGHTREAD_COLD_CACHE_ROOT`.
   When it is set, `thumbs.cache_file` puts widths above `GRID_MAX_WIDTH` under
   `<root>/<project>/thumb_cache_large/`, and `video.cache_path` puts
   transcodes under the same root. Then move the existing > 800 px files
   across once (by measured image width, not file size). That is another
   56 GB, and it replaces step 2's symlinks with one config path.
   `sweep_orphan_caches.py`, `clean_cache.py` and the done-project cleanup
   (`DERIVED_CACHE_DIRS`) need to know the second root too.
4. When moving anything, use `rsync -aH` (or `cp -a` of a whole tree):
   40,708 thumbnails are hard links shared across projects, and a copy that
   does not preserve them takes up far more space than the 86.7 GB in use.

Not recommended: moving the whole `projects/` folder to h1 with one symlink.
It works, but it puts the grid tiles and `decisions.json` (rewritten on every
keypress) on a spinning disk to save the last 6 GB.
