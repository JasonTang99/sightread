"""HEIC support and Live Photo pairing.

A Live Photo is a still plus a ~3s motion file with the same stem. The motion
file must stay out of video review, be deleted with its still, and follow its
still's star into the export. The dangerous direction is the delete: a real
video that happens to share a still's name must never be swept up as one, which
is why pairing also requires the video to be short — and why a video whose
duration cannot be read is never paired.
"""

import json
import os
import struct

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import exports
import media
import server
from exports import export_shots, export_trip, plan_export
from projects import ProjectContext, image_files_in
from utils import FAVORITE, TO_DELETE, load_decisions, save_decisions, sidecars_of
from video_tags import update_video_tags


# ---------------------------------------------------------------------------
# Fixture media
# ---------------------------------------------------------------------------
def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def _mov(seconds: float, *, version: int = 0, moov_last: bool = False, large_mdat: bool = False) -> bytes:
    """A minimal QuickTime file whose only real content is its mvhd duration."""
    timescale = 600
    ticks = int(round(seconds * timescale))
    if version == 1:
        fields = struct.pack(">QQIQ", 0, 0, timescale, ticks)
    else:
        fields = struct.pack(">IIII", 0, 0, timescale, ticks)
    mvhd = _box(b"mvhd", bytes([version, 0, 0, 0]) + fields + b"\0" * 80)
    moov = _box(b"moov", _box(b"udta", b"\0" * 12) + mvhd)
    data = b"\0" * 256
    if large_mdat:
        mdat = struct.pack(">I4sQ", 1, b"mdat", 16 + len(data)) + data
    else:
        mdat = _box(b"mdat", data)
    ftyp = _box(b"ftyp", b"qt  \0\0\0\0")
    return ftyp + (mdat + moov if moov_last else moov + mdat)


def _jpeg(path, size=(64, 48), color=(10, 120, 200)):
    Image.new("RGB", size, color).save(path, format="JPEG")
    return path


def _heif(path, size=(64, 48), color=(200, 30, 30)):
    Image.new("RGB", size, color).save(path, format="HEIF")
    return path


def _live(folder, stem, *, still_ext=".HEIC", video_ext=".MOV", seconds=3.0, real_heif=True):
    """A Live Photo pair. Returns (still, motion)."""
    still = folder / f"{stem}{still_ext}"
    if still_ext.lower() in media.HEIF_EXTENSIONS and real_heif:
        _heif(still)
    else:
        _jpeg(still)
    motion = folder / f"{stem}{video_ext}"
    motion.write_bytes(_mov(seconds))
    return still, motion


# ---------------------------------------------------------------------------
# Duration from the container header
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kwargs",
    [{}, {"version": 1}, {"moov_last": True}, {"large_mdat": True, "moov_last": True}],
    ids=["v0", "v1", "moov-last", "64bit-mdat"],
)
def test_duration_is_read_from_mvhd(tmp_path, kwargs):
    f = tmp_path / "clip.MOV"
    f.write_bytes(_mov(2.95, **kwargs))
    assert media.video_duration(f) == pytest.approx(2.95, abs=0.01)


def test_unreadable_container_has_no_duration(tmp_path):
    f = tmp_path / "clip.MOV"
    f.write_bytes(b"not a quicktime file at all")
    assert media.video_duration(f) is None


def test_duration_follows_a_rewritten_file(tmp_path):
    f = tmp_path / "clip.MOV"
    f.write_bytes(_mov(3.0))
    assert media.video_duration(f) == pytest.approx(3.0)
    f.write_bytes(_mov(30.0) + b"\0")  # different size, so a different memo key
    assert media.video_duration(f) == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------
def test_short_video_beside_a_still_is_its_motion(tmp_path):
    still, motion = _live(tmp_path, "IMG_1")
    assert media.motion_names(tmp_path, os.listdir(tmp_path)) == {motion.name}
    assert media.motion_of(still) == motion


def test_google_photos_mp4_pairs_too(tmp_path):
    still, motion = _live(tmp_path, "IMG_2", video_ext=".MP4")
    assert media.motion_of(still) == motion


def test_long_video_beside_a_still_is_real_footage(tmp_path):
    """A camera that reuses its counter must not have a clip read as motion."""
    still, video = _live(tmp_path, "DSCF1", still_ext=".JPG", seconds=45.0)
    assert media.motion_names(tmp_path, os.listdir(tmp_path)) == set()
    assert media.motion_of(still) is None


def test_video_of_unknown_length_is_never_paired(tmp_path):
    still = _jpeg(tmp_path / "IMG_3.JPG")
    (tmp_path / "IMG_3.MOV").write_bytes(b"garbage")
    assert media.motion_of(still) is None


def test_short_video_with_no_still_is_footage(tmp_path):
    (tmp_path / "IMG_4.MOV").write_bytes(_mov(1.2))
    assert media.motion_names(tmp_path, os.listdir(tmp_path)) == set()


def test_motion_map_pairs_across_directories(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    s1, m1 = _live(a, "IMG_1")
    s2 = _jpeg(b / "IMG_2.JPG")
    assert media.motion_map([str(s1), str(s2)]) == {str(s1): str(m1)}


def test_heic_counts_as_a_photo(tmp_path):
    still, _ = _live(tmp_path, "IMG_5")
    assert image_files_in(tmp_path) == {str(still.resolve())}


# ---------------------------------------------------------------------------
# Deletion takes the whole Live Photo
# ---------------------------------------------------------------------------
def test_sidecars_of_a_live_still_include_motion_and_edits(tmp_path):
    still, motion = _live(tmp_path, "IMG_6")
    aae = tmp_path / "IMG_6.AAE"
    aae.write_bytes(b"<plist/>")
    assert sorted(sidecars_of(still)) == sorted([motion, aae])


def test_sidecars_of_a_still_never_include_real_footage(tmp_path):
    still, video = _live(tmp_path, "DSCF2", still_ext=".JPG", seconds=90.0)
    assert sidecars_of(still) == []


def test_sidecars_of_a_video_never_include_a_still(tmp_path):
    still, motion = _live(tmp_path, "IMG_7")
    assert still not in sidecars_of(motion)


@pytest.fixture()
def drives(tmp_path, monkeypatch):
    primary_root = tmp_path / "h0"
    mirror_root = tmp_path / "h1" / "h0"
    folder = primary_root / "trips" / "hoh" / "iphone"
    mirror = mirror_root / "trips" / "hoh" / "iphone"
    folder.mkdir(parents=True)
    mirror.mkdir(parents=True)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    monkeypatch.setattr(server, "PRIMARY_ROOT", primary_root)
    monkeypatch.setattr(server, "MIRROR_ROOT", mirror_root)
    monkeypatch.setattr(server, "_active", ProjectContext(folder=folder, output_dir=output_dir))
    return TestClient(server.app, base_url="http://localhost"), folder, mirror, output_dir


def _mirror(src, mirror):
    (mirror / src.name).write_bytes(src.read_bytes())


def test_applying_a_delete_removes_the_motion_file(drives):
    client, folder, mirror, output_dir = drives
    still, motion = _live(folder, "IMG_8")
    _mirror(still, mirror)
    _mirror(motion, mirror)
    save_decisions(output_dir, {str(still): TO_DELETE})

    data = client.post("/api/apply-deletes").json()

    assert data["deleted"] == 1 and data["companions"] == 1
    assert not still.exists() and not motion.exists()


def test_unmirrored_motion_defers_the_whole_live_photo(drives):
    client, folder, mirror, output_dir = drives
    still, motion = _live(folder, "IMG_9")
    _mirror(still, mirror)  # the motion file never reached h1
    save_decisions(output_dir, {str(still): TO_DELETE})

    data = client.post("/api/apply-deletes").json()

    assert data["deleted"] == 0 and data["unmirrored"] == [str(still)]
    assert still.exists() and motion.exists()
    assert load_decisions(output_dir)[str(still)] == TO_DELETE


# ---------------------------------------------------------------------------
# Review surfaces
# ---------------------------------------------------------------------------
def test_motion_files_are_not_listed_as_videos(drives, monkeypatch):
    client, folder, _, _ = drives
    monkeypatch.setattr(server._transcode_executor, "submit", lambda *a, **k: None)
    _live(folder, "IMG_10")
    real = folder / "IMG_11.MOV"
    real.write_bytes(_mov(40.0))

    paths = client.get("/api/videos").json()["paths"]

    assert paths == [str(real.resolve())]


def _results(output_dir, images):
    clusters = [
        {"cluster_id": i, "cluster_score": 0.5, "best_image": img["path"], "images": [img]}
        for i, img in enumerate(images)
    ]
    (output_dir / "results.json").write_text(json.dumps({"schema_version": 1, "clusters": clusters}))


def test_gallery_and_state_name_the_motion_file(drives, monkeypatch):
    client, folder, _, output_dir = drives
    submitted = []
    monkeypatch.setattr(server, "_start_thumb_prewarm", lambda *a, **k: None)
    monkeypatch.setattr(server._transcode_executor, "submit", lambda fn, src, dest: submitted.append(src))
    still, motion = _live(folder, "IMG_12")
    plain = _jpeg(folder / "IMG_13.JPG")
    _results(output_dir, [
        {"path": str(still), "score": 0.5, "centrality": 1.0, "rank": 1, "motion": str(motion)},
        {"path": str(plain), "score": 0.4, "centrality": 1.0, "rank": 1},
    ])

    photos = {p["path"]: p for p in client.get("/api/gallery").json()["photos"]}
    state = client.get("/api/state").json()

    assert photos[str(still)]["motion"] == str(motion)
    assert "motion" not in photos[str(plain)]
    assert state["singletons"][0]["images"][0]["motion"] == str(motion)
    # The LIVE badge plays a browser-safe transcode, queued up front.
    assert submitted == [motion]


def test_heif_renders_as_a_jpeg_thumbnail(drives):
    client, folder, _, _ = drives
    still, _ = _live(folder, "IMG_14")

    for query in ("&w=200", ""):  # a thumbnail, and "the original"
        resp = client.get(f"/api/image?path={still}{query}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"
        assert resp.content[:3] == b"\xff\xd8\xff"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
@pytest.fixture()
def trip(tmp_path):
    folder = tmp_path / "Trips" / "2026_09_Hoh" / "iphone"
    folder.mkdir(parents=True)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    return folder, output_dir, folder.parent / exports.TRIP_EXPORTS_DIR


def test_heic_that_is_really_a_jpeg_is_linked_under_a_jpg_name(trip):
    folder, output_dir, dest = trip
    still = _jpeg(folder / "IMG_20.HEIC")  # how Google Photos exports them

    report = export_trip(output_dir, folder).as_dict()

    out = dest / "IMG_20.JPG"
    assert report["linked"] == 1 and report["converted"] == 0
    assert out.stat().st_ino == still.stat().st_ino


def test_real_heif_is_reencoded_as_an_upright_jpeg(trip):
    folder, output_dir, dest = trip
    _heif(folder / "IMG_21.HEIC", size=(40, 80))

    report = export_trip(output_dir, folder).as_dict()

    out = dest / "IMG_21.JPG"
    assert report["converted"] == 1 and report["delivered"] == 1
    with Image.open(out) as img:
        assert img.format == "JPEG" and img.size == (40, 80)
    assert not list(dest.glob(f"*{exports.PARTIAL_SUFFIX}"))


def test_rerunning_does_not_reencode_a_heif_again(trip):
    folder, output_dir, dest = trip
    _heif(folder / "IMG_22.HEIC")
    export_trip(output_dir, folder)

    plan = plan_export(output_dir, folder)
    again = export_trip(output_dir, folder).as_dict()

    assert plan["pending"] == 0 and plan["convert"] == 0
    assert again["skipped"] == 1 and again["converted"] == 0
    assert sorted(p.name for p in dest.iterdir()) == ["IMG_22.JPG"]


def test_plan_budgets_space_for_reencoding(trip):
    folder, output_dir, _ = trip
    src = _heif(folder / "IMG_23.HEIC")

    plan = plan_export(output_dir, folder)

    assert plan["convert"] == 1
    assert plan["convert_bytes"] == int(src.stat().st_size * exports.HEIF_TO_JPEG_BUDGET)


def test_motion_file_is_not_exported_on_its_own(trip):
    folder, output_dir, _ = trip
    still, motion = _live(folder, "IMG_24")
    assert export_shots(output_dir, folder) == [still.resolve()]


def test_starred_live_photo_brings_its_motion(trip):
    folder, output_dir, dest = trip
    still, motion = _live(folder, "IMG_25", real_heif=False)
    save_decisions(output_dir, {str(still.resolve()): FAVORITE})

    export_trip(output_dir, folder)

    assert (dest / "IMG_25.JPG").is_file()
    assert (dest / exports.UNTAGGED_DIR / "IMG_25.MOV").is_file()


def test_motion_follows_its_stills_tag(trip):
    folder, output_dir, dest = trip
    still, motion = _live(folder, "IMG_26", real_heif=False)
    key = str(still.resolve())
    save_decisions(output_dir, {key: FAVORITE})
    update_video_tags(output_dir, assign={key: "vibes"})
    export_trip(output_dir, folder)

    assert (dest / "vibes" / "IMG_26.JPG").is_file()
    assert (dest / "vibes" / "IMG_26.MOV").is_file()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def test_pipeline_scans_heic_and_skips_motion_as_footage(tmp_path):
    import pipeline

    still, motion = _live(tmp_path, "IMG_30")
    real = tmp_path / "IMG_31.MOV"
    real.write_bytes(_mov(60.0))

    assert pipeline._scan_image_paths(str(tmp_path)) == [str(still)]
    assert pipeline._scan_video_paths(str(tmp_path)) == [str(real.resolve())]


def test_pipeline_reads_heif_exif_time_and_framing(tmp_path):
    import pipeline

    still = tmp_path / "IMG_32.HEIC"
    img = Image.new("RGB", (30, 60))
    exif = img.getexif()
    exif[0x0132] = "2026:09:05 11:59:33"
    img.save(still, format="HEIF", exif=exif.tobytes())

    ts, framing, _model = pipeline._read_exif_meta(str(still))

    assert ts is not None and framing == "portrait"


def test_rank_and_save_records_the_motion_file(tmp_path):
    import numpy as np
    import pipeline

    paths = [str(tmp_path / "IMG_1.HEIC"), str(tmp_path / "IMG_2.JPG")]
    emb = np.eye(2, dtype=np.float32)
    results = pipeline.rank_and_save(
        paths, {0: [0], 1: [1]}, [0.5, 0.4], emb, tmp_path / "out",
        motions={paths[0]: str(tmp_path / "IMG_1.MOV")},
    )
    images = {img["path"]: img for c in results["clusters"] for img in c["images"]}
    assert images[paths[0]]["motion"] == str(tmp_path / "IMG_1.MOV")
    assert "motion" not in images[paths[1]]
