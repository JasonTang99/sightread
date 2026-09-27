"""Marking a project finished reclaims what it was only caching to review it.

Thumbnails, posters and transcodes are nearly all of what a project occupies —
one finished trip here held 298MB of thumbnails and 430MB of transcodes against
2MB of decisions, and a stale video_cache elsewhere had grown to 140GB. All of
it re-derives from originals that are still on disk, so once curation is over
none of it is worth keeping.
"""
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import projects
import server
from projects import ProjectContext


@pytest.fixture()
def api(tmp_path, monkeypatch):
    folder = tmp_path / "trip"
    folder.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(server, "_active", ProjectContext(folder=folder, output_dir=out))
    return TestClient(server.app, base_url="http://localhost"), folder, out


def _cached(out, *names):
    for name in names:
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "blob.bin").write_bytes(b"x" * 1000)


def test_marking_done_deletes_the_regenerable_caches(api):
    client, _, out = api
    _cached(out, "thumb_cache", "poster_cache", "video_cache")

    body = client.post("/api/projects/done", json={"done": True}).json()

    assert body["done_at"] is not None
    assert body["freed_bytes"] == 3000
    for name in ("thumb_cache", "poster_cache", "video_cache"):
        assert not (out / name).exists(), name


def test_marking_done_keeps_the_curation_and_the_model_output(api):
    """Decisions are the work itself; embeddings and highlights cost a pipeline
    run to rebuild, not a resize."""
    client, _, out = api
    keep = {
        "results.json": '{"clusters": []}',
        "decisions.json": '{"schema_version": 2, "photos": {}}',
        "embeddings_dinov3_mpcls_tta.paths.json": "[]",
        "shot_times.json": "{}",
        "video_highlights.json": "{}",
    }
    for name, text in keep.items():
        (out / name).write_text(text)
    (out / "video_highlights_cache").mkdir()
    (out / "video_highlights_cache" / "a.json").write_text("{}")
    _cached(out, "thumb_cache")

    client.post("/api/projects/done", json={"done": True})

    for name in keep:
        assert (out / name).exists(), name
    assert (out / "video_highlights_cache" / "a.json").exists()


def test_a_finished_project_is_not_prewarmed_back_onto_disk(api, monkeypatch):
    """Opening a done project to look something up must not rebuild what
    marking it done just deleted."""
    client, folder, out = api
    src = folder / "a.jpg"
    Image.new("RGB", (64, 48), "red").save(src)
    (out / "results.json").write_text(json.dumps({"clusters": [{
        "cluster_id": 1, "best_image": str(src),
        "images": [{"path": str(src), "score": 0.5, "centrality": 1.0, "rank": 1}],
    }]}))
    warmed = []
    monkeypatch.setattr(server, "_prewarm_one", lambda ctx, p, w: warmed.append(p))

    projects.mark_done(out)
    client.get("/api/state")
    assert warmed == []

    client.post("/api/projects/done", json={"done": False})
    client.get("/api/state")
    assert warmed, "resuming curation should prewarm again"


def test_state_and_listing_report_whether_a_project_is_finished(api):
    client, _, out = api
    (out / "results.json").write_text('{"clusters": []}')

    assert client.get("/api/state").json()["done_at"] is None
    stamp = client.post("/api/projects/done", json={"done": True}).json()["done_at"]
    assert client.get("/api/state").json()["done_at"] == stamp

    assert client.post("/api/projects/done", json={"done": False}).json()["done_at"] is None
    assert client.get("/api/state").json()["done_at"] is None


def test_marking_an_already_evicted_project_done_is_not_an_error(api):
    client, _, out = api
    assert client.post("/api/projects/done", json={"done": True}).json()["freed_bytes"] == 0


def test_rerunning_the_pipeline_unfinishes_the_project(api, monkeypatch):
    """New output means there is something to review again."""
    client, folder, out = api
    monkeypatch.setattr(projects, "DATA_DIR", out.parent)
    monkeypatch.setattr(server, "project_output_dir", lambda f, subtrip=None: out)
    monkeypatch.setattr(server, "start_pipeline", lambda *a, **k: None)
    monkeypatch.setattr(server, "upsert_recent", lambda *a, **k: None)
    projects.mark_done(out)

    r = client.post("/api/projects/run-pipeline", json={"folder": str(folder)})

    assert r.status_code == 200, r.text
    assert projects.is_done(out) is None
