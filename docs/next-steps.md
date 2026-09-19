# sightread — next steps

_Last updated: 2026-09-19._

## Done since the last update

- **`_read_shot_time` read the wrong IFD** (`d7b7e98`). It now reads
  `DateTimeOriginal` out of the EXIF sub-IFD and keeps `DateTime` (306) only as a
  fallback, matching `scripts/pipeline.py:_parse_exif_timestamp`. The regression
  fixture has to synthesise a photo whose 306 and 36867 disagree — no file in the
  archive does, which is why the bug survived.
- **The re-shoot merge now fires across cameras, not only across framings**
  (`96d13df`). See §1 for what opening the `iphone/` + `xt5/` trips turned up.
- **A trip adopts the review already done on its device folders** (`07b0288`).
  Opening Japan pulled in 512 decisions, 53 video tags and 1 clip from the
  `xt5/` project.
- **The header says which trip is open, and skips what is decided**
  (`dae874b`, `e45354a`, `11a9204`). On Japan the skip filter takes the queue
  from 56/205/86 to 51/132/34.
- **Cluster size is legible at a glance** (`bc49c69`, `d2b92dd`) — a coloured
  badge on the bar, blue to three and red past twenty, with the picker no longer
  repeating it.
- **The LIVE badge's hover playback is under test** (`fe465b1`). Seven Playwright
  tests, checked against two deliberate breakages of the component.
- **Confirm no longer skips a cluster when hide-reviewed is on** (`9c3b49f`).
  Enter used to advance by index *and* drop the row you just decided, so the
  cluster that slid into its place was walked past. Landing is now by
  `cluster_id`. Singles and videos still advance by index, but since `f5e50e1`
  made skip-reviewed navigation-only nothing drops out of their lists, so the
  index no longer slides — that half of the bug is gone rather than fixed.
- **Every trip in the picker has a time estimate** (`185d959`, refit in
  `cea0354` to 25 s + 0.74 s/photo + 0.016 s/MB). Over today's 25-trip batch it
  landed within 10% on nearly every run; the misses were small trips, where the
  fixed 25 s dominates (Revelstoke 65 s against 92 s).
- **Embedding and scoring checkpoint after every shooting day** (`52a6cd3`), so
  a GPU crash mid-Europe costs one day, not two hours.
- **A partly decided cluster shows its per-photo decisions** (`dbc3a6e`) instead
  of falling back to rank for every photo until the whole cluster is decided.
- **The picker lists projects by trip date beside Recent, one entry per trip**
  (`149239b`, `e83d51c`). Device folders roll up into their trip (42 entries to
  36); a trip only ever run per device, like `2024_02_Korea/canon`, shows as the
  trip. Browse sits behind "Other folder…" — it had been returning a 400 on
  lynx because its default `/mnt/h0/Editing/imports` does not exist.
- **Shift+Space keeps only the focused photo** (`bb02680`), was §7. Starred
  photos stay kept.
- **Shot-time cache is versioned** so a reader change cannot keep serving
  DateTime (306) as if it were DateTimeOriginal. Old flat `shot_times.json`
  files rebuild on the next gallery load. Was §6.
- **Export the trip lives only on Finish.** The Favourites 📤 was leftover from
  when that tab *was* the export; it still shipped the whole trip. Finish step 2
  already did the job.
- **Own-folder vs guest-folder merge** (`02e6c38`). Stage 4
  will not join a cluster that has Jason's folders (`iphone/`, `xt5/`, `canon/`)
  to a `google photos/` or `shared/` cluster. Guest–guest still merges; his
  phone and camera of one subject still merge. Split is by folder, not EXIF
  model. **Hawaii, Hoh and Yellowstone still need a recluster to pick the
  folder split up** — their `results.json` predates it, so their cross-camera
  clusters still mix `google photos/` with `iphone/`. Long runs; not done here.
- **Exposure-bracket merge** after the 3 s burst (`02e6c38`). Consecutive
  same-parent photos 3–12 s apart at cosine ≤ 0.12 join. Japan clusters 22/23
  (Hakodate icy stairs, 6 s, ISO 800→400, cosine 0.093) were the prompt; an
  ungated 6 s burst would have chained a 20-minute Koyasan walk.
- **City subtrips** (`02e6c38`). A trip whose cameras use `NN_City` folders
  (`01_Hakodate`) becomes one picker row per city instead of one 1,200-cluster
  pile. See §7.
- **The two tests `02e6c38` left stale now pass** (`18bdedd`). That commit gave
  `project_output_dir` a `subtrip` argument and `/api/state` a `display_name`,
  but left `test_project_done` stubbing the first with a one-argument lambda
  (TypeError on the re-run test) and `test_header_names_the_open_project`
  expecting the old bare-path tooltip instead of `name\npath`. Suites are
  372 Python and 107 Playwright, all passing.

## 1. Where the seven 2026 trips landed

Every trip on h0 with an `iphone/` folder has now been through the pipeline as a
single trip project:

| Trip | stills (iphone / xt5) | clusters | largest | cross-device clusters |
|---|---|---:|---:|---:|
| `2026_01_Japan` | 246 / 101 | 261 | 6 | 8 |
| `2026_07_Hawaii` | 97 / 345 | 237 | 47 | 6 |
| `2026_09_Hoh_River_Trail` | 11 / 41 (+368 `google photos/`) | 185 | 44 | 5 |
| `2026_08_Portugal` | 19 / 268 | 167 | 10 | 0 |
| `2026_02_Yellowstone` | 25 / 18 | 33 | 4 | 0 |
| `2026_05_Vegas` | 2 / 3 (+1 `shared/`) | 5 | 2 | 0 |
| `2026_07_Rattlesnake` | 0 / 5 | 5 | 1 | 0 |

The two large clusters are both real: Hawaii's 47 is one 48-second X-T5 burst of
the same kitchen scene, and Hoh's 44 is the single-viewpoint burst that `a52f2f9`
capped. Neither grew when the cross-camera window widened, so the chain-span cap
still holds.

Yellowstone's and Portugal's zeros are correct, not misses. Their closest
cross-device pairs are a day (Yellowstone, 96,000s) and four hours (Portugal,
14,374s at distance 0.089 — the same subject revisited) apart, far outside any
merge window.

**What the Japan run exposed:** only 3 of 272 clusters mixed devices, while the
embeddings held 44 phone/camera pairs within 0.25 cosine distance, the closest
nine at 0.081-0.16. Stage 4 fired solely on a portrait/landscape difference, and
picking up the other camera does not change how you hold it — both are portrait.
The Hoh trip hid this because the phone was held portrait and the X-T3 landscape.
The fix gives cross-camera merges their own 300s window (the Japan pairs sit
126-331s apart) and the *same-scene* ceiling of 0.22 rather than the 0.38
rotation one — at 0.38 same-framing merges pulled in neighbouring compositions,
notably a 12-photo Hoh riverbed group holding three different subjects.

## 2. Preprocessing the archive

The three stale trips were re-run: Vegas on the evening of the 12th, Hawaii on the
afternoon of the 13th, and Yellowstone at the head of the evening batch. That batch
then took every 2025 trip and the smaller 2024 ones — 25 runs in 88 minutes, all rc 0 — and a second,
unbounded pass is walking the rest, newest first. Sigil (`sigil.service`,
`sigil-stt.service`) was stopped to give it the GPU: with them up, clipiqa+ ran
out of memory and fell back to per-image scoring on every trip (10-14 fallbacks
each on Yellowstone and Gothics); after, none. **Restart them when the batch is
done:** `systemctl --user start sigil-stt sigil`.

As of 18:45 on the 13th, `2024_03_Europe` (9,522 photos, estimated 2 h 10 m) is
running. Still to come: `2024_02_Taiwan`, `2024_02_Korea` (as a whole trip, 1,305
photos), `2024_02_China`, `2024_01_Japan` (6,324 photos, ~1.5 h), then 2023 back
to 2020 — about 50 small trips, 2022's LA, Stratford and Greece the only ones over
five minutes. The runner and per-trip logs are in the session scratchpad, not the
repo; the picker's estimate tag is the source of truth for what is left.

**2026-09-15:** `2024_01_Japan` did run as one project (6,324 stills, 1,275
clusters). That pile is now being split into city subtrips — §7. Do not re-GPU
it. The whole-trip output dir
`~/.local/share/sightread/projects/2870dc083f7ebafa993fd9f975258fad/` stays on
disk; the picker will hide it once the city rows exist.

## 3. Adoption covers trips, not the other direction

`adopt_subfolder_reviews` runs when a trip is opened and pulls in the decisions,
video tags and user clips of any project whose folder sits inside it. Two trips
have not been opened since it shipped and will adopt on their next open:

- `2026_09_Hoh_River_Trail` — 119 decisions (46 applied deletes, 38 favourites,
  35 kept) and 32 video tags from its `xt5/` project.
- `2026_08_Portugal` — 338 decisions from `xt5/`, of which **196 are `to_delete`,
  not yet applied**. Opening the trip therefore arrives with a delete queue of
  196 waiting in the Finish tab. That is the correct carry-over, but it is worth
  knowing before the first Confirm rather than after.

The gap: adoption is one-directional. Opening `<trip>/xt5` on its own still knows
nothing of review done at the trip level, so a decision made in the trip and then
revisited in the camera folder will be offered again.

~~Nobody has hit this yet — it needs someone to open a camera folder after
reviewing its trip — so it is recorded rather than fixed.~~ **Decided 2026-09-14:
won't fix.** Since `e83d51c` the picker no longer lists device folders; reaching
one takes "Other folder…".

## 4. Open questions on the cross-camera merge

- **The cross-camera merge over-merges on group trips.** This is the most useful
  thing the `google photos/` re-runs showed, and it wants a decision before more
  review happens on those trips.

  `google photos/` is not a third device of Jason's but a shared album: on Hawaii
  it holds nine camera models from other people (Galaxy Z Fold6 181, Xiaomi 17 Pro
  Max 215, iPhone 17 Pro 123, …) plus 7 iPhone 14 frames that fill numbering gaps
  in `iphone/` — missing frames, not duplicates (no photo matches another folder's
  on model and shot time). Counted by EXIF model, clusters mixing cameras are now
  31 of 528 on Hawaii, 29 of 331 on Yellowstone, 14 on Hoh and 8 on Vegas.

  Looked at, the big ones are not one subject. Hawaii cluster 443 is 23 photos from
  six cameras in 263 s at the lava field: a person in a crack, a man by a tree, a
  selfie, a group shot, a woman in red and two landscapes. Yellowstone 159 is 19
  photos of four or five different skiers on one trail. With keep-best's rank-1
  default that is 22 distinct shots marked for deletion in one cluster.

  Replaying Hawaii's clustering from cached embeddings pins it on stage 4's
  cross-camera path: with `cross_window_s=0` cluster 443 falls apart into 9
  clusters (largest 10), while turning off the 3 s burst fusion changes nothing.
  Tightening `CROSS_DEVICE_THRESHOLD` is not the lever — at 0.12 cluster 443 still
  keeps 19 — because the damage is chaining: when many people shoot one place at
  once, some neighbour is always within 0.22 and 300 s. Limiting a merged group to
  one cluster per camera helps partly (443 → 10/7/3/3, Yellowstone 264 28 → 18)
  and leaves Japan, the trip the rule was fitted to, at 8 cross clusters.

  **Decided 2026-09-14:** other people's cameras may still merge with each
  other; Jason's folders never join those groups. His own phone and camera of
  one subject still merge (the Japan case). The split is by folder
  (`google photos/`, `shared/` vs `iphone/` / `xt5/` / `canon/`), not EXIF
  model — `google photos/` holds six cameras, and a handful of his iPhone
  frames live in there too. Re-cluster to take effect; decisions are keyed by
  path, so review survives.
- A subject revisited hours later — Portugal's 0.089 pair, four hours apart — is
  deliberately left in separate clusters, since `MAX_CLUSTER_GAP_S` is an hour.
  ~~Whether trip review wants a looser "same place, another day" grouping is a
  product question, not a threshold one, and nothing in the UI expresses it yet.~~
  **Decided 2026-09-14:** keep them apart. Same place on another pass is a new
  cluster, not a re-shoot. No threshold change.
- The X-T5 shoots portrait more than expected (64 of 101 stills on Japan). That is
  what makes the framing-only rule miss so much, and it is worth confirming it
  holds on the next trip rather than being a Japan habit.

## 5. These trips are mostly video, and the pipeline barely looks at them

Counting files rather than stills changes the picture completely:

| Trip | stills | `.MOV` | of those, Live Photo motion |
|---|---:|---:|---:|
| `2026_01_Japan` | 347 | 272 | 186 |
| `2026_02_Yellowstone` | 43 | 58 | 10 |
| `2026_07_Rattlesnake` | 5 | 6 | 1 |

Yellowstone has more video files than stills, and Rattlesnake's single `iphone/`
file is a video. Live Photo pairing handles the motion files (186 paired on Japan,
against 33 on the trip the feature was built against), but the standalone clips —
34 on Japan's phone, 52 on its camera — get no clustering, no scoring and no
gallery row. They already have a review flow: the Videos tab. `--video-highlights`
has been off by default since 2026-09-10 because the clipfarm step costs more
than it returned.

~~**First action:** decide what a trip project should say about a standalone clip
at all. Even shot time and a device badge in the gallery would beat the current
silence, and that needs no model.~~

**Decided 2026-09-14:** Videos tab is enough. No gallery row, no pipeline
scoring. Clipfarm highlights stay off.

## 6. Cached shot times predate the EXIF fix

~~`shot_times.json` in each project dir was written by the old reader, and nothing
invalidates it on a code change. Every value happens to be identical under the new
reader — every source in the archive writes 306 and `DateTimeOriginal` the same —
so there is nothing to repair today. A file that arrives with the two disagreeing
would be read correctly but then served from a stale cache entry, so if such a
file ever shows up, delete the project's `shot_times.json` rather than debugging
the reader.~~

**Done 2026-09-14.** Cache is `{"v": 2, "times": {…}}`. A flat file, or a
different `v`, is discarded and rebuilt from EXIF. Bump `SHOT_TIMES_VERSION`
when the reader changes again.

## 7. City subtrips — Japan 2024 is the first one

The picker used to open `2024_01_Japan` as 1,275 clusters. The imports are
already `canon/01_Hakodate`, `iphone/01_Hakodate`, … so the unit of review is
now one city: both cameras, one project, hashed
`md5("{trip}#{city}")`. Folder names win when they match; iPhone files that
are *not* in a city folder (a dump next to city-split Canon) join by EXIF
time against the city's min/max from the filed photos.

Korea (`02-06` date folders) and Hoh (`xt5/` / `iphone/` only) do not split.
Europe *will*: iPhone is `01_Iceland`, `02_Amsterdam`, … which matches
`NN_City`, so the first picker load after restart will try to slice Europe
the same way and time-assign Canon's `2024_02_Amsterdam` folders. That is
probably right, but it has not been looked at.

**Caches were sliced, not recomputed.** Parent embeddings 51.8 MB, scores,
285 decisions, shot times, and existing thumbs were copied/hardlinked into
13 city dirs. `pending=0` on every city. `results.json` is *not* copied —
each city reclusters from the sliced embeddings (CPU, no GPU) so the
bracket merge and own-vs-guest rule actually apply.

| City | stills | clusters | decisions carried |
|---|---:|---:|---:|
| `00_Tokyo` | 3 | 2 | 2 |
| `01_Hakodate` | 781 | 222 | 283 |
| `02_Sapporo` | 457 | 110 | 0 |
| `03_Niseko` | 70 | 17 | 0 |
| `04_Lake_Toya` | 281 | 75 | 0 |
| `05_Noboribetsu` | 103 | 25 | 0 |
| `06_Fukuoka` | 1,949 | 587 | 0 |
| `07_Kagoshima` | 151 | 40 | 0 |
| `08_Yufuin` | 311 | 95 | 0 |
| `09_Hiroshima` | 457 | 95 | 0 |
| `10_Osaka` | 924 | 295 | 0 |
| `11_Koyasan` | 424 | 102 | 0 |
| `12_Wakayama` | 413 | 103 | 0 |

1,768 clusters across 13 cities, down from 1,275 in one pile only because
cities no longer share a cluster id space — the review queues are the
point. All 13 wrote `results.json` from cached embeddings (no GPU).

Review so far was Tokyo then Hakodate, which is why 283 of 285 decisions
landed in Hakodate. Path-keyed, so they survive the recluster.

**Recluster finished 2026-09-15 ~01:00.** Tokyo–Wakayama all have
`results.json` from cache (`Loading cached embeddings`). If a city looks
stale after restart, do not re-GPU — embeddings are already in that dir.

**Blocked on a restart.** Sightread on http://127.0.0.1:8765/ is still the
old process (whole-trip Japan, transcoding `canon/` video into the parent
`video_cache`). Do not kill it to pick up this code; restart when that
ffmpeg is idle. Frontend `dist/` is already rebuilt. Hard-refresh after
restart. Then open `2024_01_Japan / 01_Hakodate` — confirm the icy stairs
are one cluster, and that hide-reviewed skips the 283 already decided.

**All of that landed in `02e6c38`** — city split, bracket merge,
own-vs-guest, shot-time versioning and the Favourites export removal went in
as one commit on the morning of the 15th, after the notes above were written.
The "working tree, not committed" wording those notes carried was stale, not
a description of HEAD.

**First action next session:**

1. Restart sightread when ffmpeg is idle.
2. Glance at whether Europe split into `01_Iceland` / `02_Amsterdam` / …
   on picker load, and whether Canon photos showed up via time assignment.
3. Recluster Hawaii, Hoh and Yellowstone so the own-vs-guest split reaches
   them (§4). Long runs — their current `results.json` still mixes
   `google photos/` into Jason's clusters.

## 8. The working tree holds a separate, unreviewed body of work

Not the three features above — those are on HEAD. What is uncommitted on
`mirror-aware-deletes` as of 2026-09-19 is three entangled threads, sharing
`App.tsx`, `server.py` and `test_ui.py` between them:

- **Picker fast-load.** `/api/projects` drops to names and whatever
  `recents.json` already held — no photo-tree walk, no `paths.json` parse, no
  `ensure_city_caches` — and a new `/api/projects/details` fills counts, stale
  vs ready and ETA, memoised in `_picker_details` and invalidated on open, run,
  clean and done. `tests/test_picker_list.py` is new and untracked;
  `webapp/tests/test_picker_eta.py` and `tests/test_project_discovery.py` move
  with it.
- **Finish panel absorbs the trash panel.** `TrashPanel.tsx` is deleted,
  `FinishTripPanel.tsx` rewritten around `finish-delete-count` /
  `finish-remain-photos` / `finish-remain-videos`, and `/api/finish/preview`
  grew `remaining_photos` and `remaining_videos` via `_remaining_media`. The
  mirror-verify wording moves across; the mirror logic itself was already in
  `scripts/delete_marked.py`. `test_ui.py`'s `TestTrashPanel` becomes
  `TestFinishTrip`.
- **Confirm advances off the end of a list.** `nextAfterConfirm` in
  `decisions.ts` returns `"advance"` when the hop has nowhere to land, so the
  view opens the next tab rather than sitting on the row just confirmed;
  `ClusterView`, `SingletonsView` and `VideoView` use it, covered by
  `TestEnterAdvancesTab`.

Plus `run.sh` launching `google-chrome` before `xdg-open` for GPU accel, and
small `HelpOverlay` / `FavoritesView` edits.

Both suites pass with all of it in place (372 Python, 107 Playwright), so it
is not broken — but none of it was reviewed here and the three threads want a
commit each, which means hunk-level splitting of the three shared files.
`dist/` was built on the 17th and matches this tree, so the running UI already
reflects it.

The X-T5 portrait mix (§4) is still observational, on the next trip.
Hoh and Portugal still adopt on first open (§3). Remote curation stays
gated on auth (`plan.md`). Clipfarm highlights stay off.
