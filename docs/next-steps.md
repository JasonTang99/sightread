# sightread — next steps

_Last updated: 2026-09-13 (evening)._

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
revisited in the camera folder will be offered again. Nobody has hit this yet —
it needs someone to open a camera folder after reviewing its trip — so it is
recorded rather than fixed. Since `e83d51c` the picker no longer lists device
folders at all, so reaching one takes "Other folder…"; that makes this gap even
less likely to bite.

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

  **First action:** decide what a cross-camera merge is for. If it is "Jason's
  phone and Jason's camera on the same subject", the simplest fix is to allow it
  only between the trip's own device folders and never into `google photos/`.
  The one-cluster-per-camera cap is the model-agnostic alternative. Either
  changes clusters on already-reviewed trips; decisions are keyed by path, so
  review survives a re-cluster.
- A subject revisited hours later — Portugal's 0.089 pair, four hours apart — is
  deliberately left in separate clusters, since `MAX_CLUSTER_GAP_S` is an hour.
  Whether trip review wants a looser "same place, another day" grouping is a
  product question, not a threshold one, and nothing in the UI expresses it yet.
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
review flow. `--video-highlights` has been off by default since 2026-09-10 because
the clipfarm step costs more than it returned.

**First action:** decide what a trip project should say about a standalone clip at
all. Even shot time and a device badge in the gallery would beat the current
silence, and that needs no model.

## 6. Cached shot times predate the EXIF fix

`shot_times.json` in each project dir was written by the old reader, and nothing
invalidates it on a code change. Every value happens to be identical under the new
reader — every source in the archive writes 306 and `DateTimeOriginal` the same —
so there is nothing to repair today. A file that arrives with the two disagreeing
would be read correctly but then served from a stale cache entry, so if such a
file ever shows up, delete the project's `shot_times.json` rather than debugging
the reader.
