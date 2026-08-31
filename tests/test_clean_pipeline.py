"""Pipeline cache cleanup for the finish-trip flow."""
import json

import pytest
from fastapi.testclient import TestClient

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


def test_clean_pipeline_removes_model_outputs_but_keeps_decisions(api):
    client, _, out = api
    keep = {
        "decisions.json": '{"schema_version": 2, "photos": {}}',
        "shot_times.json": "{}",
    }
    remove = {
        "results.json": '{"clusters": []}',
        "clusters.json": "[]",
        "embeddings_dinov3_mpcls_tta.paths.json": "[]",
        "video_highlights.json": "{}",
    }
    for name, text in {**keep, **remove}.items():
        (out / name).write_text(text)
    (out / "video_highlights_cache").mkdir()
    (out / "video_highlights_cache" / "a.json").write_text("{}")

    body = client.post("/api/projects/clean-pipeline").json()

    assert body["ok"] is True
    assert "results.json" in body["removed"]
    for name in keep:
        assert (out / name).exists(), name
    for name in remove:
        assert not (out / name).exists(), name
    assert not (out / "video_highlights_cache").exists()


def test_finish_preview_reports_counts(api, monkeypatch):
    client, folder, out = api
    (out / "results.json").write_text(json.dumps({"clusters": []}))
    (out / "decisions.json").write_text(json.dumps({
        "schema_version": 2,
        "photos": {
            "a.jpg": "to_delete",
            "b.jpg": "favorite",
        },
    }))
    monkeypatch.setattr(server, "EXPORTS_ROOT", out / "exports")
    (server.EXPORTS_ROOT).mkdir()

    preview = client.get("/api/finish/preview").json()

    assert preview["pending_deletes"] == 1
    assert preview["favorites"] == 1
    assert preview["pipeline_cache_bytes"] > 0


def test_marking_done_still_keeps_decisions_after_pipeline_clean(api):
    client, _, out = api
    (out / "decisions.json").write_text('{"schema_version": 2, "photos": {}}')
    (out / "results.json").write_text('{"clusters": []}')

    client.post("/api/projects/clean-pipeline")
    client.post("/api/projects/done", json={"done": True})

    assert (out / "decisions.json").exists()
    assert projects.is_done(out) is not None
