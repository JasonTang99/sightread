# sightread — next steps

_Last updated: 2026-10-01._

## Done since the last update

The 2026-09-20 list was pruned; those items are in `git log` by hash.

- **One header row for all review chrome** (`a295524`, `185c253`). The
  clusters tab had two stacked bars plus a progress line; singles and videos
  each had a third. Now a 36 px row: home icon, project name, icon tabs with
  counts, the skip switch, and a slot each view portals its own controls into
  (tags, per-row, next-unreviewed on clusters; status pill, reviewed pill and
  tags on videos). Undo/redo are Ctrl+Z / Ctrl+Shift+Z only, Enter is the only
  confirm, and the progress meter, size badge, position counters, filter
  toggle, shortcut cribs and the tag "+" are gone at Jason's request. Singles
  lost its bulk "Confirm all" with them. Video play/pause moved from `l` to
  `p`. UI tests read position from `data-index` on the view root now.
- **Photos per row defaults per cluster** — 3 when every frame is portrait,
  else 2, learnt from the decoded image so no re-run is needed. A manual pick
  holds for that cluster only.
- **Redo** (`320f0c9`). Undo snapshots what it overwrites; `/api/redo` puts it
  back. Memory-only: a restart or a fresh confirm drops it.
- **The clip editor is out of the Videos tab** (`185c253`), along with its
  n/p/i/o/x/u keys and the export button, because clipfarm highlights are off
  (§5). Server clip endpoints are untouched.
- **Enter on the last video waits for its write before opening the timeline**
  (`185c253`). The timeline refetched first and drew the clip undecided;
  `test_a_video_kept_in_the_reviewer_shows_green_in_the_timeline` failed 3/8
  before, 0/8 after.
- **Album duplicates are queued in the pipeline** (`5a15e3c`). A
  `google photos/` or `shared/` photo that matches one of his folders on model,
  DateTimeOriginal *and* SubSecTimeOriginal, and sits under 0.01 cosine, is left
  out of review and marked `to_delete`. A guard only: all eight trips with a
  guest folder were checked and none has one. The closest distinct
  cross-folder frames sit at 0.03–0.04, Vegas IMG_9107/9108 share a second but
  not a sub-second, and a guest's iPhone 17 Pro on Hawaii reuses his `IMG_946x`
  numbers, which is why neither name nor second-level time is enough.
- **`ui-cleanup` and `mirror-aware-deletes` are both merged into main**
  (`68d62bd`); see §8 and §8a. Keep-best, auto-best and skip were removed
  (`c742427`). Suites: 389 Python, 81 Playwright.

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

The counts above are from the runs of the 11th–13th. The four trips with a
shared album were reclustered on the 20th (§4) and their numbers moved —
Hawaii 528 → 535 clusters, Hoh 185 → 182, Yellowstone 331 → 328, Vegas
117 → 116 — while the table's stills and largest-cluster figures still hold.

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
each on Yellowstone and Gothics); after, none. ~~**Restart them when the batch is
done:** `systemctl --user start sigil-stt sigil`.~~ **Done** — both are `active`
as of 2026-09-20, and no pipeline process is running.

~~As of 18:45 on the 13th, `2024_03_Europe` (9,522 photos, estimated 2 h 10 m) is
running. Still to come: `2024_02_Taiwan`, `2024_02_Korea` (as a whole trip, 1,305
photos), `2024_02_China`, `2024_01_Japan` (6,324 photos, ~1.5 h), then 2023 back
to 2020 — about 50 small trips.~~

**Where it actually stopped, counted 2026-09-20: 38 of 68 trips on h0 have a
`results.json`; 30 do not.** Everything named above as "still to come" for 2024
did run — Europe, Taiwan, Korea, China and Japan all have output. The unbounded
pass then stopped before reaching the older years, and nothing is running now.

What is left, by year: all of **2020** (1), **2021** (6), **2022** (17),
**2023** (5), plus **`2024/2024_12_NYC`**. 2022 is most of the remainder and is
mostly small trips; LA, Stratford and Greece are the only ones over five
minutes. Reproduce the list with `project_output_dir_name` over
`/mnt/h0/Trips/*/*`, which is what produced these counts — the picker's
estimate tag says the same thing one row at a time.

**Resuming costs the GPU back.** Sigil is up again, and the reason it was
stopped has not changed: with it resident, clipiqa+ ran out of memory and fell
back to per-image scoring on every trip. Stop both services before restarting
the batch, and restart them after. Nothing here is urgent — these are old,
small trips and none is queued for review.

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

## 4. The cross-camera merge — all four questions settled

Kept in full because it is the evidence every rule in `_merge_reshoot_pairs`
and stage 3b was fitted to, and because two of the four were settled *against*
what was recorded. Nothing here is waiting on anyone. The one standing limit is
at the end: two guests carrying the same camera model cannot be told apart.

- **The cross-camera merge over-merged on group trips.** ~~This is the most useful
  thing the `google photos/` re-runs showed, and it wants a decision before more
  review happens on those trips.~~ **Closed 2026-09-20** — decided on the 14th,
  shipped in `02e6c38`, and as of `3ee68ab` it holds in every stage and has
  reached every trip it applies to. The survey below is kept because it is the
  evidence the rule was fitted to; its counts are from before the fix.

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

  **Decided 2026-09-14** (first half superseded 2026-09-20 — see the bullet
  below on photographers)**:** other people's cameras may still merge with each
  other; Jason's folders never join those groups. His own phone and camera of
  one subject still merge (the Japan case). The split is by folder
  (`google photos/`, `shared/` vs `iphone/` / `xt5/` / `canon/`), not EXIF
  model — `google photos/` holds six cameras, and a handful of his iPhone
  frames live in there too. Re-cluster to take effect; decisions are keyed by
  path, so review survives.

  **Done 2026-09-20** (`3ee68ab`). All four trips holding a shared album —
  Hawaii, Hoh, Yellowstone and Vegas — were reclustered from cached
  embeddings. No GPU, 5–23 s each; none of them had been reviewed yet, so no
  decisions were at stake. Cluster 443 came apart into 10 clusters, largest 8
  (the replay had predicted 9 and 10). Old 526 → 3, old 117 → 7.

  The recluster also showed the rule did not hold. `02e6c38` implemented it in
  `_merge_reshoot_pairs` alone, because the Hawaii replay had pinned the
  over-merging on stage 4's chaining. The stages above it know nothing about
  folders, and on a group trip two people photograph one view from nearly the
  same spot — close enough for the tight pass to call them near-duplicates,
  with no merge rule involved. Left over after that first recluster: Hawaii 6
  clusters (20 photos), Hoh 3, Vegas 1, Yellowstone 0. One was an X-T5 frame
  with five of a guest's iPhone 17 Pro 49 s later, where keep-best ranks one
  first and queues the rest, across owners, for deletion.

  A new stage 3b splits any cluster holding both, so the line is drawn once at
  the end rather than defended by each merge rule. Doing that by folder alone
  would have created a new wrong: `google photos/IMG_9253` and `IMG_9254` are
  his own iPhone 14, two seconds from `iphone/IMG_9255`, and a folder-only
  split cut them off that burst. `pipeline.own_flags` therefore takes the
  folder's verdict and then rescues a guest-folder frame whose EXIF model is
  one his own folders also carry — 7 such frames on Hawaii, 15 on Vegas, 3 on
  Hoh, 0 on Yellowstone. Only those models qualify, so the album's six other
  cameras stay guests; the split is still by folder, and the model only says
  which frames were misfiled. After the second recluster no cluster on any of
  the four mixes his photos with other people's.
- A subject revisited hours later — Portugal's 0.089 pair, four hours apart — is
  deliberately left in separate clusters, since `MAX_CLUSTER_GAP_S` is an hour.
  ~~Whether trip review wants a looser "same place, another day" grouping is a
  product question, not a threshold one, and nothing in the UI expresses it yet.~~
  **Decided 2026-09-14:** keep them apart. Same place on another pass is a new
  cluster, not a re-shoot. No threshold change.
- ~~The X-T5 shoots portrait more than expected (64 of 101 stills on Japan). That is
  what makes the framing-only rule miss so much, and it is worth confirming it
  holds on the next trip rather than being a Japan habit.~~
  **Measured 2026-09-20: it was a Japan habit, and the camera was the wrong
  thing to watch.** Across the seven 2026 trips the X-T5's portrait share
  swings by trip with no stable level — 63% on Japan (64/101), 44% on
  Yellowstone and Hoh, 21% on Portugal (57/268), 7% on Hawaii (25/345), 22%
  over all 781 stills. The iPhone 14 does not swing: 91% portrait over 423
  stills, 85–100% on every trip.

  So the thing that makes a cross-camera pair share a framing is not a habit of
  the X-T5 but the phone being near-always portrait while the camera's framing
  moves with the trip. Whether the two agree is decided by the camera alone,
  trip by trip, which is why Japan looked like a rule and Hoh hid the problem
  entirely. There is no framing assumption to lean on, so the cross-camera path
  stays unconditional — the current design is right, for a different reason
  than the one recorded. Nothing to change; re-run the count only if a trip's
  cross-camera merges look wrong again.
  (`pipeline.load_paths_and_meta` over `/mnt/h0/Trips/2026_*` reproduces it.)
- **The chaining the own-vs-guest rule was built to stop still happens inside
  `google photos/`, and nothing decides whether that matters.** The
  2026-09-14 decision deliberately lets other people's cameras merge with each
  other, so the split protects his photos and stops there. After the recluster
  Hawaii's four largest clusters that are not his are guest-only and large:
  38 photos over 183 s across a Galaxy Z Fold6 and a Xiaomi (cluster 368), 25
  over 209 s, 18, 16. Those have the same shape as old cluster 443 — several
  people, several subjects, one place — and keep-best will rank one first and
  queue the other 37 for deletion.

  ~~It is not obvious this is worth fixing, which is why it is a question rather
  than a task. These are copies of other people's photos in a shared album; if
  the local copy on h0 is disposable, an over-merged guest cluster costs
  nothing.~~

  **Answered and fixed 2026-09-20** (`8db52cf`). Deleting other people's photos
  from h0 is wanted — h1 keeps every original, and `delete_marked.py` verifies
  the mirror copy before it unlinks anything. So the album's frames need the
  same protection his do, and **the 2026-09-14 decision that other people's
  cameras may merge with each other is reversed.**

  Stage 3b now splits by *photographer* rather than by his-vs-theirs: his
  photos are one photographer whichever of his cameras took them, and each EXIF
  model inside a guest folder is another. Blocking the cross-camera merge for
  guests is not enough by itself — once a cluster already holds two models the
  sets overlap, the pair stops counting as cross, and the rotation rule merges
  it anyway — so both are in: the split draws the line and stage 4 no longer
  reaches across it.

  His side is untouched, which was the thing to protect: Hawaii still has 225
  clusters of his, largest 47/16/15/12/9/6, and his phone and camera of one
  subject still merge — the only thing the cross-camera window now does. The
  album's largest cluster goes 38 → 26. Across the four trips no cluster holds
  more than one photographer. Hawaii 535 → 578 clusters, Hoh 182 → 199,
  Yellowstone 328 → 378, Vegas 116 → 128.

  **Known limit:** two guests carrying the same model are indistinguishable
  from EXIF and still merge. Nothing available here separates them.

  **The h1 premise was checked, not assumed** (2026-09-20). h1 mirrors h0 at
  `/mnt/h1/h0/`, and `delete_marked.py` only unlinks from h0 after the mirror
  copy is present and size-matched, refusing to run at all if h1 is not
  mounted. Comparing the two trees file by file: 67,593 files under
  `h0/Trips`, 79,311 under `h1/h0/Trips`. Only 273 files exist on h0 and not
  h1, and every one of them is under `2024_01_Japan/_exports/` — sightread's
  own output, regenerable, 12G. The 11,991 files h1 has and h0 does not are
  pre-cull originals and RAW/XMP (Japan `xt5/`: 101 JPG on h0, 448 on h1). The
  guest folders match exactly, count for count: Hawaii 1,079, Yellowstone
  1,141, Hoh 531, Vegas 334. So h1 is a superset, not a mirror of h0's current
  organisation — behind on deletions, ahead on content, which is what a backup
  should be. Re-run before any large delete:
  `comm -23 <(cd /mnt/h0/Trips && find . -type f | LC_ALL=C sort) <(cd /mnt/h1/h0/Trips && find . -type f | LC_ALL=C sort)`
  — the `LC_ALL=C` is load-bearing, `comm` silently misreports without it.

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

1. ~~Restart sightread.~~ **Done 2026-10-01** — running in tmux `fleet:0.5`
   on the current main.
2. Glance at whether Europe split into `01_Iceland` / `02_Amsterdam` / …
   on picker load, and whether Canon photos showed up via time assignment.
   Half-answered: four Europe city dirs exist on disk (`01_Iceland`,
   `02_Amsterdam`, `03_Brussels`, `04_Nice`), so the slice did happen. Whether
   Canon's `2024_02_Amsterdam` folders landed in them by time assignment has
   still not been looked at.
3. ~~Recluster Hawaii, Hoh and Yellowstone so the own-vs-guest split reaches
   them (§4). Long runs — their current `results.json` still mixes
   `google photos/` into Jason's clusters.~~ **Done 2026-09-20** (`3ee68ab`),
   Vegas with them. Not long runs at all: from cached embeddings they are
   5–23 s of CPU each. The 13 Japan cities and every other trip were left
   alone — with no `google photos/` or `shared/` folder there is nothing for
   the split to do, so their `results.json` is unaffected by the change.

## 8a. UI cleanliness pass — branch `ui-cleanup`

Branched off `mirror-aware-deletes` on 2026-09-20. The chrome work was four
commits; the follow-ups below are four more. 107 Playwright and 383 Python
passing on each, type-check clean. Playwright was run with 3.11.15 first on
PATH (`conftest` spawns `python3 -m uvicorn`; a 3.10.8 pyenv global fails all
107 in fixture setup with a 30s server timeout).

- **`a002752` checkpoints §8's uncommitted tree unchanged**, so the UI diffs are
  readable on their own. §8's three threads share `App.tsx` and `server.py` with
  everything the UI work touches, and leaving them dirty meant every later diff
  carried them. **`mirror-aware-deletes` still points at `f682a74`**, so §8's
  plan to land them as three hunk-split commits is unaffected — reset this
  commit there when that happens.
- **`d7d412e` gives the header three groups** — where you are, where you can go,
  how the session is doing — separated by hairlines. It was one undivided row of
  up to thirteen items. `components/ui.tsx` is new and holds Tab, TabCount,
  Button, Status, Switch, Kbd, ShortcutBar and Divider, with the colour rule
  written at the top: blue means "where you are" and nothing else. The tab
  button's 140-character class string had been copy-pasted six times.
- **`625ba83` writes every shortcut once**, in `src/shortcuts.ts`, shared by the
  overlay and the per-view cribs. The cribs were `text-gray-300` — about 1.5:1
  on white — and ClusterView's ran to thirteen run-together items. The overlay
  covered Clusters and Singles only, so the Videos and Favorites cribs named
  keys documented nowhere.
- **`1d9be9b` moves the crib off the control row** onto its own line, and drops
  `?` from all four lists into a single overlay footer that also names Esc.
- **`450a3b9`** replaces ClusterView's two hand-rolled dividers with the shared
  one and groups its control row.
- **`07a55ef` closes the third copy.** Each shortcut entry now carries the
  KeyboardEvent chords that fire it and an id the view must implement.
  `useShortcuts` looks the event up in that list and calls the id; a missing
  handler throws on render, so a row added to the list cannot silently do
  nothing. ClusterView, SingletonsView, VideoView, FavoritesView and App no
  longer switch on `e.key`. Backspace on a selected video clip was never
  written down and still isn't — it shares `x`'s id. `?` and Esc live on
  `APP_KEYS` the same way. The alternative (a test that every `brief` key does
  something) was not taken; the handler reading the list is the thing that
  stops a key being changed in one place and left stale in the other.
- **`4a2e61f` splits ProjectPicker** into the list, folder browser, pipeline
  screen and action bar it already contained. Markup, class names and behaviour
  are unchanged.
- **`787abd1` splits TimelineView** into sidebar, day section and memoised
  tiles. `content-visibility` stays on the day wrapper. Behaviour unchanged.
- **Picker chrome uses `Button` and `Status`.** Open, Run Pipeline, Other
  folder and Back were hand-rolled: a solid blue fill on Open/Run, and a
  `border-gray-300` cousin of Button on the rest. Status chips were
  green/yellow/grey/blue, plus purple for Done — yellow is favourite, blue is
  "where you are", purple is unused elsewhere. The ETA tag was a slate chip.
  They now use the shared `Button` (`size="md"`) and `Status` (green only
  when the trip is finished; grey for Ready/Stale/Not run/Running and for
  ETA). Labels, clicks, disabled-while-busy, double-click-to-open and the
  browse toggle are unchanged. List rows and the folder-browser ✕/→ stayed
  as they were — they are not chrome buttons.

**Caught by screenshot, not by the suite.** A temporary Playwright test captured
the header, cluster toolbar and overlay at 1440x900; that is how the crib was
found sitting mid-toolbar, where chips gave reference material more weight than
the controls beside it. Both commits' tests passed either way. Nothing asserts
any of this, so **look at it before believing it**. The test was not kept.

**Follow-ups done 2026-09-26** (this session), with
`PATH="$HOME/.pyenv/versions/3.11.15/bin:$PATH" python -m pytest tests`
(383 passed) and the same PATH on `webapp/tests` (107 passed). `tsc -b` clean.
No eslint config in the frontend; nothing else to lint. The picker restyle
above was the remaining code item; same PATH and counts again after it.

**Still open on this branch:**

1. ~~The bindings themselves are still a third copy.~~ Done in `07a55ef`.
2. ~~The picker still has its own button and badge styling.~~ Done; see the
   picker-chrome bullet above. Timeline has nothing equivalent left.
3. ~~Run the Playwright suite with 3.11.15 first on PATH.~~ Done; 107 passed.
   The PATH prefix remains load-bearing for any later run from a 3.10.8 shell.
4. ~~**Not merged and not pushed.**~~ **Merged into main** (`68d62bd`). Main is
   90 commits ahead of `origin/main` and nothing has been pushed; pushing needs
   Jason's say-so. Much of this section's chrome (Status, Divider, the cribs on
   clusters/singles/videos, the grouped header) was then removed again by the
   one-row header on 2026-10-01.

## 8. A separate, unreviewed body of work

**Abandoned as a plan, 2026-10-01.** All three threads landed on main as the
single checkpoint `a002752` when `ui-cleanup` merged, and a dozen commits now
build on them. Splitting it into three commits would mean rewriting main's
history under those commits for tidiness alone, so it will not be done. Kept below as the
description of what `a002752` contains.

**Where it lives changed on 2026-09-20.** It is still uncommitted on
`mirror-aware-deletes`, which has not moved from `f682a74`. It is *also*
committed unchanged as `a002752`, the first commit of `ui-cleanup` (§8a), which
was done so the UI diffs after it were readable rather than carrying these
files in every one. Nothing below was edited. The split described here is still
the plan; do it from either place, and if from `ui-cleanup`, reset `a002752`
first.

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

## 9. Open threads from the one-row header

- **The clip editor comes back only with clipping.** If clipfarm highlights
  are turned back on (§5), restore `ClipEditor` and its keys from
  `git show 185c253^:webapp/frontend/src/components/VideoView.tsx` and the
  `VIDEO_CLIP_KEYS` / `VIDEO_EDIT_KEYS` lists from `185c253^:…/shortcuts.ts`.
  Mind `p`: it is play/pause now, and was clip-marker-prev there.
- **The duplicate guard covers stills only.** Album `.mp4`s are not compared
  with his `.MOV`s. No case has been seen, so nothing is planned; the place to
  add one is `find_duplicates` in `scripts/pipeline.py`.

Portugal still adopts on first open (§3), arriving with 196 `to_delete`
queued. Hoh has been opened, so its adoption is done. 30 old trips have no
pipeline output and resuming costs the GPU back from Sigil (§2). Main is 90
commits ahead of origin and unpushed. Remote curation stays gated on auth
(`plan.md`). Clipfarm highlights stay off.
