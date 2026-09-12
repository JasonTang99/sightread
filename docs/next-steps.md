# sightread — next steps

_Last updated: 2026-09-12 (evening)._

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
  `cluster_id`. Singles and videos still advance by index — same shape of bug,
  not yet hit.

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

## 2. Three trips are stale — a `google photos/` folder appeared in them

**Blocked on a go-ahead.** As of this evening the picker reads:

| Trip | processed | in folder now | unprocessed, all in `google photos/` |
|---|---:|---:|---:|
| `2026_07_Hawaii` | 442 | 1173 | 731 |
| `2026_02_Yellowstone` | 43 | 739 | 696 |
| `2026_05_Vegas` | 6 | 268 | 262 |

"Stale" is `projects.py:project_status` finding the folder's image set different
from the paths in the embeddings sidecar. Nothing is missing in these three —
they are pure additions, a folder that landed after today's runs scanned only
`xt5/` and `iphone/`.

Re-running is cheaper than it looks: `compute_embeddings` is incremental
(`scripts/pipeline.py:288`), so the stills already done load from cache and only
the new ones are embedded. Cluster ids get renumbered, but decisions are keyed by
photo path, so review survives.

**First action:** ask whether to re-run those three. Hawaii is the interesting
one — with `google photos/` it becomes a three-device trip, which is a better
test of the cross-camera merge than anything in §4.

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
recorded rather than fixed.

## 4. Open questions on the cross-camera merge

- The 300s window and 0.22 ceiling were fitted to Japan and Hoh and sanity-checked
  on Hawaii. Portugal contributed nothing to the fit (19 phone stills, none close
  in time to a camera frame). A re-run of Hawaii with `google photos/` (§2) would
  be the first near-even three-device measurement.
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
