"""City subtrips: Japan-like trips split; iPhone dumps join by shot time."""
import json
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ExifTags

import media
import projects
from utils import KEPT, TO_DELETE, load_decisions, save_decisions


def _jpeg(path: Path, when: str | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (16, 12), (10, 90, 40))
    if when:
        exif = Image.Exif()
        exif.get_ifd(ExifTags.IFD.Exif)[36867] = when
        img.save(path, "JPEG", exif=exif)
    else:
        img.save(path, "JPEG")
    return path


def _japan(tmp_path):
    trip = tmp_path / "Trips" / "2024" / "2024_01_Japan"
    _jpeg(trip / "canon" / "01_Hakodate" / "c1.JPG")
    _jpeg(trip / "canon" / "02_Sapporo" / "c2.JPG")
    _jpeg(trip / "iphone" / "01_Hakodate" / "p1.JPG")
    _jpeg(trip / "iphone" / "02_Sapporo" / "p2.JPG")
    return trip


def test_japan_like_layout_lists_cities(tmp_path):
    trip = _japan(tmp_path)
    assert projects.city_subtrips(trip) == ["01_Hakodate", "02_Sapporo"]


def test_hoh_device_folders_do_not_split(tmp_path):
    trip = tmp_path / "Trips" / "2026_09_Hoh_River_Trail"
    _jpeg(trip / "xt5" / "a.JPG")
    _jpeg(trip / "iphone" / "b.JPG")
    assert projects.city_subtrips(trip) == []


def test_korea_date_folders_do_not_split(tmp_path):
    trip = tmp_path / "Trips" / "2024" / "2024_02_Korea"
    _jpeg(trip / "canon" / "02-06" / "c.JPG")
    _jpeg(trip / "iphone" / "IMG.JPG")
    assert projects.city_subtrips(trip) == []


def test_flat_iphone_next_to_city_canon_still_splits(tmp_path):
    trip = tmp_path / "Trips" / "2024" / "2024_01_Japan"
    _jpeg(trip / "canon" / "01_Hakodate" / "c1.JPG")
    _jpeg(trip / "canon" / "02_Sapporo" / "c2.JPG")
    _jpeg(trip / "iphone" / "IMG.JPG")
    assert projects.city_subtrips(trip) == ["01_Hakodate", "02_Sapporo"]


def test_walk_prunes_to_one_city(tmp_path):
    trip = _japan(tmp_path)
    names = {
        Path(dirpath, name).name
        for dirpath, names in media.walk_media(trip, "01_Hakodate")
        for name in names
        if media.is_image(name)
    }
    assert names == {"c1.JPG", "p1.JPG"}


def test_output_dir_hash_differs_per_city(tmp_path):
    trip = _japan(tmp_path)
    a = projects.project_output_dir_name(trip, "01_Hakodate")
    b = projects.project_output_dir_name(trip, "02_Sapporo")
    whole = projects.project_output_dir_name(trip)
    assert len({a, b, whole}) == 3


def test_iphone_dump_joins_city_by_shot_time(tmp_path):
    trip = tmp_path / "Trips" / "2024" / "2024_01_Japan"
    hak = _jpeg(trip / "canon" / "01_Hakodate" / "c1.JPG", "2024:01:05 12:00:00")
    sap = _jpeg(trip / "canon" / "02_Sapporo" / "c2.JPG", "2024:01:08 12:00:00")
    phone = _jpeg(trip / "iphone" / "IMG_1.JPG", "2024:01:05 12:30:00")
    later = _jpeg(trip / "iphone" / "IMG_2.JPG", "2024:01:08 13:00:00")
    assigned = projects.unfiled_assignment(trip)
    assert assigned[str(phone.resolve())] == "01_Hakodate"
    assert assigned[str(later.resolve())] == "02_Sapporo"
    files = {Path(p).name for p in projects.image_files_in(trip, "01_Hakodate")}
    assert files == {"c1.JPG", "IMG_1.JPG"}
    assert hak.exists() and sap.exists()


def test_picker_replaces_trip_with_city_rows(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(projects, "DATA_DIR", data_dir)
    monkeypatch.setattr(projects, "RECENTS_FILE", tmp_path / "recents.json")
    trip = _japan(tmp_path)
    entries = projects.expand_city_subtrips([{
        "folder": str(trip),
        "output_dir": str(data_dir / "abc"),
        "last_opened": None,
        "last_pipeline_run": None,
        "image_count": 4,
    }])
    assert [e["subtrip"] for e in entries] == ["01_Hakodate", "02_Sapporo"]
    assert all(e["folder"] == str(trip) for e in entries)


def test_split_copies_embeddings_and_decisions(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(projects, "DATA_DIR", data_dir)
    monkeypatch.setattr(projects, "RECENTS_FILE", tmp_path / "recents.json")
    trip = tmp_path / "Trips" / "2024" / "2024_01_Japan"
    hak = _jpeg(trip / "canon" / "01_Hakodate" / "c1.JPG", "2024:01:05 12:00:00")
    sap = _jpeg(trip / "canon" / "02_Sapporo" / "c2.JPG", "2024:01:08 12:00:00")
    phone = _jpeg(trip / "iphone" / "IMG_1.JPG", "2024:01:05 12:30:00")

    parent = projects.project_output_dir(trip)
    parent.mkdir(parents=True)
    paths = [str(hak.resolve()), str(sap.resolve()), str(phone.resolve())]
    emb = np.eye(3, 4, dtype=np.float32)
    np.save(parent / "embeddings_dinov3_mpcls_tta.npy", emb)
    (parent / "embeddings_dinov3_mpcls_tta.paths.json").write_text(json.dumps(paths))
    np.savez(parent / "scores_ensemble.npz", musiq=np.array([1.0, 2.0, 3.0], dtype=np.float32))
    (parent / "scores_ensemble.paths.json").write_text(json.dumps(paths))
    save_decisions(parent, {paths[0]: KEPT, paths[1]: TO_DELETE, paths[2]: KEPT})
    (parent / "results.json").write_text(json.dumps({
        "clusters": [{
            "images": [
                {"path": paths[0], "exif_timestamp": datetime(2024, 1, 5, 12).timestamp()},
                {"path": paths[1], "exif_timestamp": datetime(2024, 1, 8, 12).timestamp()},
                {"path": paths[2], "exif_timestamp": datetime(2024, 1, 5, 12, 30).timestamp()},
            ]
        }]
    }))

    reports = projects.split_city_caches(trip)
    by_city = {r["subtrip"]: r for r in reports}
    assert by_city["01_Hakodate"]["photos"] == 2
    assert by_city["02_Sapporo"]["photos"] == 1
    assert by_city["01_Hakodate"]["decisions"] == 2

    hak_out = projects.project_output_dir(trip, "01_Hakodate")
    hak_paths = json.loads((hak_out / "embeddings_dinov3_mpcls_tta.paths.json").read_text())
    assert set(hak_paths) == {paths[0], paths[2]}
    assert np.load(hak_out / "embeddings_dinov3_mpcls_tta.npy").shape == (2, 4)
    hak_decisions = load_decisions(hak_out)
    assert hak_decisions[paths[0]] == KEPT
    assert hak_decisions[paths[2]] == KEPT
    assert paths[1] not in hak_decisions

    sap_out = projects.project_output_dir(trip, "02_Sapporo")
    assert load_decisions(sap_out) == {paths[1]: TO_DELETE}
    meta = json.loads((hak_out / "project.json").read_text())
    assert meta == {"folder": str(trip.resolve()), "subtrip": "01_Hakodate"}
