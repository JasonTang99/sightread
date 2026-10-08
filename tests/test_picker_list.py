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


# ---------------------------------------------------------------------------
# Review left: undecided clusters, singles and videos per project
# ---------------------------------------------------------------------------


def _seed_review(tmp_path, monkeypatch):
    folder, out = _seed_project(tmp_path, monkeypatch)
    paths = {}
    for name in ("b1.jpg", "b2.jpg", "c1.jpg", "c2.jpg", "s1.jpg", "s2.jpg", "v1.MOV", "v2.MOV"):
        (folder / name).write_bytes(b"\0")
        paths[name] = str((folder / name).resolve())
    (out / "results.json").write_text(json.dumps({"clusters": [
        {"cluster_id": 0, "images": [{"path": paths["b1.jpg"]}, {"path": paths["b2.jpg"]}]},
        {"cluster_id": 1, "images": [{"path": paths["c1.jpg"]}, {"path": paths["c2.jpg"]}]},
        {"cluster_id": 2, "images": [{"path": paths["s1.jpg"]}]},
        {"cluster_id": 3, "images": [{"path": paths["s2.jpg"]}]},
    ]}))
    return folder, out, paths


def test_review_left_counts_what_the_tabs_would_show(tmp_path, monkeypatch):
    folder, out, paths = _seed_review(tmp_path, monkeypatch)
    # Cluster 0 fully decided, cluster 1 half decided: one left. One single
    # decided, one video marked for deletion: one of each left.
    server.save_decisions(out, {
        paths["b1.jpg"]: server.KEPT, paths["b2.jpg"]: server.TO_DELETE,
        paths["c1.jpg"]: server.KEPT, paths["s1.jpg"]: server.FAVORITE,
        paths["v1.MOV"]: server.TO_DELETE,
    })
    client = TestClient(server.app, base_url="http://localhost")

    left = client.get("/api/projects/review-left", params={"folder": str(folder)}).json()

    assert left == {"clusters": 1, "singles": 1, "videos": 1}


def test_review_left_walks_again_only_after_a_decision(tmp_path, monkeypatch):
    folder, out, paths = _seed_review(tmp_path, monkeypatch)
    calls = {"n": 0}
    real = server._scan_videos

    def counting(ctx):
        calls["n"] += 1
        return real(ctx)

    monkeypatch.setattr(server, "_scan_videos", counting)
    client = TestClient(server.app, base_url="http://localhost")
    get = lambda: client.get("/api/projects/review-left", params={"folder": str(folder)}).json()

    assert get()["videos"] == 2
    assert get()["videos"] == 2
    assert calls["n"] == 1
    server.save_decisions(out, {paths["v2.MOV"]: server.KEPT})
    assert get()["videos"] == 1
    assert calls["n"] == 2


def test_review_left_404s_without_pipeline_output(tmp_path, monkeypatch):
    folder, out = _seed_project(tmp_path, monkeypatch, with_results=False)
    client = TestClient(server.app, base_url="http://localhost")

    res = client.get("/api/projects/review-left", params={"folder": str(folder)})

    assert res.status_code == 404
