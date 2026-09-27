"""A whole trip as one project — several camera folders reviewed together.

The drives store a trip as Trips/<trip>/<device>/, with the trip's own
deliverables in Trips/<trip>/_exports/. Opening the trip itself, rather than
one camera folder, is what puts the same moment shot on two cameras in one
cluster. Two things have to hold for that: the export tree must never be
scanned as source material — an export hardlinks, so every exported photo
would come back as a second copy of itself — and the export has to land inside
the trip rather than beside it.
"""

import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import exports
import media
import server
from exports import export_shots, export_trip, trip_root
from projects import ProjectContext, count_images, image_files_in
from utils import TO_DELETE, save_decisions


def _jpeg(path, color=(90, 140, 60)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (48, 32), color).save(path, format="JPEG")
    return path


@pytest.fixture()
def trip(tmp_path):
    """Trips/2026_09_Hoh/{xt5,iphone} with a photo each. Returns the trip folder."""
    folder = tmp_path / "Trips" / "2026_09_Hoh"
    _jpeg(folder / "xt5" / "DSCF1.JPG")
    _jpeg(folder / "iphone" / "IMG_1.JPG")
    return folder


# ---------------------------------------------------------------------------
# Which folder is the trip
# ---------------------------------------------------------------------------
def test_a_folder_of_camera_folders_is_the_trip(trip):
    assert trip_root(trip) == trip


def test_a_camera_folder_belongs_to_the_trip_beside_it(trip):
    assert trip_root(trip / "xt5") == trip


def test_an_existing_exports_folder_settles_it(tmp_path):
    """A trip whose cameras are all curated away is still the trip."""
    folder = tmp_path / "Trips" / "2026_01_Japan"
    (folder / exports.TRIP_EXPORTS_DIR).mkdir(parents=True)
    assert trip_root(folder) == folder


def test_trip_exports_land_inside_the_trip(trip):
    assert exports.export_dir_for(trip) == trip / exports.TRIP_EXPORTS_DIR
    assert exports.export_dir_for(trip / "xt5") == trip / exports.TRIP_EXPORTS_DIR


# ---------------------------------------------------------------------------
# The export tree is not source material
# ---------------------------------------------------------------------------
def test_scans_skip_the_export_tree(trip):
    _jpeg(trip / exports.TRIP_EXPORTS_DIR / "DSCF1.JPG")
    found = {p.rsplit("/", 1)[-1] for p in image_files_in(trip)}
    assert found == {"DSCF1.JPG", "IMG_1.JPG"}


def test_the_picker_counts_a_trips_camera_folders(trip):
    """A trip keeps its photos one level down; counted flat it reads as empty."""
    _jpeg(trip / exports.TRIP_EXPORTS_DIR / "DSCF1.JPG")
    assert count_images(trip) == 2
    assert count_images(trip / "xt5") == 1


def test_exporting_a_trip_twice_delivers_each_photo_once(trip, tmp_path):
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    first = export_trip(output_dir, trip).as_dict()
    second = export_trip(output_dir, trip).as_dict()

    dest = trip / exports.TRIP_EXPORTS_DIR
    assert first["delivered"] == 2
    # Without the skip the second run would find its own output and deliver
    # DSCF1_1.JPG, IMG_1_1.JPG, and more of them on every run after that.
    assert second["delivered"] == 0 and second["skipped"] == 2
    assert sorted(p.name for p in dest.iterdir()) == ["DSCF1.JPG", "IMG_1.JPG"]
    assert len(export_shots(output_dir, trip)) == 2


def test_pipeline_scan_skips_the_export_tree(trip):
    import pipeline

    _jpeg(trip / exports.TRIP_EXPORTS_DIR / "DSCF1.JPG")
    scanned = pipeline._scan_image_paths(str(trip))
    assert sorted(p.rsplit("/", 1)[-1] for p in scanned) == ["DSCF1.JPG", "IMG_1.JPG"]
    assert all(f"/{exports.TRIP_EXPORTS_DIR}/" not in p for p in scanned)


def test_videos_in_the_export_tree_are_not_footage(trip, tmp_path, monkeypatch):
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    monkeypatch.setattr(server._transcode_executor, "submit", lambda *a, **k: None)
    monkeypatch.setattr(server, "_active", ProjectContext(folder=trip, output_dir=output_dir))
    source = trip / "xt5" / "DSCF2.MOV"
    source.write_bytes(b"video")
    (trip / exports.TRIP_EXPORTS_DIR / "untagged").mkdir(parents=True)
    (trip / exports.TRIP_EXPORTS_DIR / "untagged" / "DSCF2.MOV").write_bytes(b"video")

    client = TestClient(server.app, base_url="http://localhost")
    assert client.get("/api/videos").json()["paths"] == [str(source.resolve())]


# ---------------------------------------------------------------------------
# Which device a shot came from
# ---------------------------------------------------------------------------
def test_device_is_the_folder_under_the_project(trip):
    assert media.device_of(trip / "xt5" / "DSCF1.JPG", trip) == "xt5"
    assert media.device_of(trip / "iphone" / "IMG_1.JPG", trip) == "iphone"


def test_a_photo_in_the_project_root_has_no_device(trip):
    assert media.device_of(trip / "loose.JPG", trip) == ""


def test_a_camera_folder_project_has_no_device_to_show(trip):
    """Every photo would answer the same thing, so nothing is badged."""
    assert media.device_of(trip / "xt5" / "DSCF1.JPG", trip / "xt5") == ""


def test_state_and_gallery_name_the_folder_and_camera(trip, tmp_path, monkeypatch):
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    monkeypatch.setattr(server, "_start_thumb_prewarm", lambda *a, **k: None)
    monkeypatch.setattr(server, "_active", ProjectContext(folder=trip, output_dir=output_dir))
    photo = str(trip / "xt5" / "DSCF1.JPG")
    (output_dir / "results.json").write_text(json.dumps({
        "schema_version": 1,
        "clusters": [{
            "cluster_id": 0,
            "cluster_score": 0.5,
            "best_image": photo,
            "images": [{"path": photo, "score": 0.5, "centrality": 1.0, "rank": 1, "model": "X-T5"}],
        }],
    }))

    client = TestClient(server.app, base_url="http://localhost")
    gallery = client.get("/api/gallery").json()
    state = client.get("/api/state").json()

    assert gallery["folder"] == str(trip)
    assert gallery["photos"][0]["model"] == "X-T5"
    assert state["folder"] == str(trip)
    assert state["singletons"][0]["images"][0]["model"] == "X-T5"


# ---------------------------------------------------------------------------
# Deleting a photo the export still links to
# ---------------------------------------------------------------------------
def test_space_held_by_an_export_link_is_not_reported_as_freed(tmp_path, monkeypatch):
    primary_root = tmp_path / "h0"
    mirror_root = tmp_path / "h1" / "h0"
    folder = primary_root / "Trips" / "hoh"
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    exported = _jpeg(folder / "xt5" / "DSCF1.JPG")
    linked = _jpeg(folder / "xt5" / "DSCF2.JPG")
    for f in (exported, linked):
        mirror = mirror_root / f.relative_to(primary_root)
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_bytes(f.read_bytes())
    # DSCF2 has already been exported, so its data has a second name.
    export_link = folder / exports.TRIP_EXPORTS_DIR / "DSCF2.JPG"
    export_link.parent.mkdir(parents=True)
    export_link.hardlink_to(linked)

    monkeypatch.setattr(server, "PRIMARY_ROOT", primary_root)
    monkeypatch.setattr(server, "MIRROR_ROOT", mirror_root)
    monkeypatch.setattr(server, "_active", ProjectContext(folder=folder, output_dir=output_dir))
    save_decisions(output_dir, {str(exported): TO_DELETE, str(linked): TO_DELETE})
    freed = exported.stat().st_size

    data = TestClient(server.app, base_url="http://localhost").post("/api/apply-deletes").json()

    assert data["deleted"] == 2
    assert data["still_linked"] == 1
    # Only the photo whose data actually went counts towards freed space.
    assert data["freed_bytes"] == freed
    assert not exported.exists() and not linked.exists()
    assert export_link.is_file()
