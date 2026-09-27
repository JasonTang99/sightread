"""Picker names must not wait on photo-tree walks."""

import json

import pytest
from fastapi.testclient import TestClient

import projects
import server


@pytest.fixture(autouse=True)
def _clear_picker_cache():
    server._invalidate_picker_details()
    yield
    server._invalidate_picker_details()


def _seed_project(tmp_path, monkeypatch, *, with_results=True, image_count=0):
    data_dir = tmp_path / "data"
    recents = tmp_path / "recents.json"
    monkeypatch.setattr(projects, "DATA_DIR", data_dir)
    monkeypatch.setattr(projects, "RECENTS_FILE", recents)
    folder = tmp_path / "Trips" / "2026_07_Hawaii"
    folder.mkdir(parents=True)
    photo = folder / "a.jpg"
    photo.write_bytes(b"\0" * 100)
    out = data_dir / projects.project_output_dir_name(folder)
    out.mkdir(parents=True)
    (out / "project.json").write_text(json.dumps({"folder": str(folder)}))
    (out / "embeddings_dinov3_mpcls_tta.paths.json").write_text(
        json.dumps([str(photo.resolve())])
    )
    if with_results:
        (out / "results.json").write_text('{"clusters": []}')
    recents.write_text(json.dumps([{
        "folder": str(folder),
        "output_dir": str(out),
        "last_opened": "2026-01-01T00:00:00+00:00",
        "last_pipeline_run": None,
        "image_count": image_count,
    }]))
    return folder, out


def test_names_list_does_not_walk_photos(tmp_path, monkeypatch):
    folder, _out = _seed_project(tmp_path, monkeypatch, image_count=1173)

    def boom(*_a, **_k):
        raise AssertionError("names list must not walk photos")

    monkeypatch.setattr(server, "estimate_pipeline", boom)
    monkeypatch.setattr(server, "project_status", boom)
    monkeypatch.setattr(server, "ensure_city_caches", boom)
    monkeypatch.setattr(projects, "image_files_in", boom)

    client = TestClient(server.app, base_url="http://localhost")
    rows = client.get("/api/projects").json()

    assert [r["display_name"] for r in rows] == ["2026_07_Hawaii"]
    assert rows[0]["folder"] == str(folder)
    assert rows[0]["image_count"] == 1173
    assert rows[0]["status"] == "ready"
    assert rows[0]["pending_count"] == 0
    assert rows[0]["eta_s"] is None


def test_details_walks_once_then_serves_cache(tmp_path, monkeypatch):
    folder, out = _seed_project(tmp_path, monkeypatch)
    (out / "scores_ensemble.paths.json").write_text(
        json.dumps([str((folder / "a.jpg").resolve())])
    )
    calls = {"n": 0}
    real = server.estimate_pipeline

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(server, "estimate_pipeline", counting)

    client = TestClient(server.app, base_url="http://localhost")
    first = client.get("/api/projects/details").json()
    second = client.get("/api/projects/details").json()

    assert calls["n"] == 1
    assert first == second
    assert first[0]["display_name"] == "2026_07_Hawaii"
    assert first[0]["image_count"] == 1
    assert first[0]["eta_s"] is None  # cached photo, nothing pending
