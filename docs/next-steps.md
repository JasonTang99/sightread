# sightread — next steps

_Last updated: 2026-09-11._

Creating this file for the first time; there was no next-steps doc in the repo before.

## 1. `_read_shot_time` reads `DateTimeOriginal` from the wrong IFD

`webapp/server.py:930` looks for tag 36867 on `Image.getexif()`, which returns IFD0.
`DateTimeOriginal` lives in the EXIF sub-IFD (`0x8769`), so the lookup always misses:

```python
EXIF_DATETIME_ORIGINAL = 36867
exif = img.getexif()
val = exif.get(tag)          # always None for 36867
```

Verified on `Trips/2026_09_Hoh_River_Trail/xt5/DSCF5642.JPG`:

| read | value |
|---|---|
| `getexif().get(36867)` | `None` ← what the server reads |
| `getexif().get(306)` | `2026:09:06 13:48:52` |
| `get_ifd(ExifTags.IFD.Exif).get(36867)` | `2026:09:06 13:48:52` ← the real value |

**Nothing is currently wrong in the UI.** The function falls through to tag 306
(`DateTime`), and every source in the archive writes it — checked all 11 iPhone 14 stills
and all 368 Google Photos stills in the Hoh River trip, plus the X-T5 above: tag 306
matched `DateTimeOriginal` in every case. So this is latent, not a live defect.

**Why fix it anyway:** 306 is semantically "last modified", not "captured". Anything that
rewrites a file — an editor, a re-export, a stripped-metadata download — can move it while
`DateTimeOriginal` stays put, and then shot times silently shift. The failure is invisible:
there is no error, the photos just sort wrong. `scripts/pipeline.py:100` already does it
correctly via `exif.get_ifd(ExifTags.IFD.Exif)`, so the two code paths can disagree about
the same file today.

**First action:** change `_read_shot_time` to read the sub-IFD first and keep 306 only as
a fallback, mirroring `scripts/pipeline.py:97-100`. A regression test wants a fixture whose
306 and 36867 deliberately differ — none of the real archive files have that, which is
exactly why the bug survived.

## 2. Exercise trip projects on an `xt5/` + `iphone/` pairing

The device clustering shipped in `ae276ba` and tuned in `a52f2f9` was validated only on
`2026_09_Hoh_River_Trail`, where the second device folder was `google photos/` — 531
phone files against 114 camera files, and the cross-device pairs were re-shoots of the
same moment.

As of 2026-09-11 six more trips on h0 have an `iphone/` folder next to `xt5/`, which is
the shape most trips will have from now on and has not been through the pipeline:

| Trip | `xt5/` | `iphone/` |
|---|---:|---:|
| `2026_01_Japan` | 454 | 469 |
| `2026_08_Portugal` | 606 | 41 |
| `2026_07_Hawaii` | 778 | 163 |
| `2026_02_Yellowstone` | 66 | 53 |

**Why it matters:** stage 4's portrait/landscape re-shoot merge is what produced the
74-photo cascade that `a52f2f9` fixed, and its span cap was tuned against one trip's
cross-device distances. A near-even two-device split (Japan) stresses it differently from
a 5:1 split.

**First action:** open `Trips/2026_01_Japan` as a trip project, check the largest clusters
and the cross-device merge count the way the Hoh River analysis did. Hawaii next, as the
most lopsided.

Live Photo handling gets exercised for free here — the six `iphone/` folders carry their
`.MOV` motion files and `.AAE` sidecars, where Hoh River had only 33 files to test against.
