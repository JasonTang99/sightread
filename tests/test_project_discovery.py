"""Pipeline output found on disk shows up in the picker's recents list."""

import json

import projects


def _fake_project(data_dir, folder, images):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in images:
        img = folder / name
        img.write_bytes(b"")
        paths.append(str(img))
    out_dir = data_dir / projects.project_output_dir_name(folder)
    out_dir.mkdir(parents=True)
    (out_dir / "embeddings_dinov3_mpcls_tta.paths.json").write_text(json.dumps(paths))
    return out_dir


def test_discovers_folder_never_opened_in_ui(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(projects, "DATA_DIR", data_dir)
    monkeypatch.setattr(projects, "RECENTS_FILE", tmp_path / "recents.json")
    folder = tmp_path / "trips" / "portugal"
    _fake_project(data_dir, folder, ["a.jpg", "b.jpg"])

    entries = projects.known_projects()

    assert [e["folder"] for e in entries] == [str(folder)]
    assert entries[0]["image_count"] == 2
    assert entries[0]["last_pipeline_run"] is not None
    assert entries[0]["last_opened"] is None


def test_single_image_project_maps_back_to_its_folder(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(projects, "DATA_DIR", data_dir)
    monkeypatch.setattr(projects, "RECENTS_FILE", tmp_path / "recents.json")
    folder = tmp_path / "trips" / "rattlesnake"
    _fake_project(data_dir, folder, ["only.jpg"])

    assert [e["folder"] for e in projects.known_projects()] == [str(folder)]


def test_recents_entry_keeps_last_opened_and_gains_disk_run_time(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    recents = tmp_path / "recents.json"
    monkeypatch.setattr(projects, "DATA_DIR", data_dir)
    monkeypatch.setattr(projects, "RECENTS_FILE", recents)
    folder = tmp_path / "trips" / "hawaii"
    out_dir = _fake_project(data_dir, folder, ["a.jpg"])
    recents.write_text(json.dumps([{
        "folder": str(folder),
        "output_dir": str(out_dir),
        "last_opened": "2026-01-01T00:00:00+00:00",
        "last_pipeline_run": None,
        "image_count": 1,
    }]))

    entries = projects.known_projects()

    assert len(entries) == 1
    assert entries[0]["last_opened"] == "2026-01-01T00:00:00+00:00"
    # the CLI run on disk is newer than anything recents.json recorded
    assert entries[0]["last_pipeline_run"] is not None


def test_output_dir_without_embeddings_is_ignored(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(projects, "DATA_DIR", data_dir)
    monkeypatch.setattr(projects, "RECENTS_FILE", tmp_path / "recents.json")
    (data_dir / "deadbeef").mkdir(parents=True)

    assert projects.known_projects() == []
