"""Tests for GET /api/gallery.

The gallery is the timeline's data source. results.json is written once by the
pipeline and never rewritten as photos are curated, so the endpoint has to
reconcile it against current decisions.
"""

import json

import pytest
from fastapi.testclient import TestClient

import server
from projects import ProjectContext
from utils import DELETED, FAVORITE, KEPT, TO_DELETE, save_decisions


@pytest.fixture()
def api(tmp_path, monkeypatch):
    folder = tmp_path / "h0" / "trips" / "japan"
    folder.mkdir(parents=True)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    monkeypatch.setattr(server, "PRIMARY_ROOT", tmp_path / "h0")
    monkeypatch.setattr(server, "MIRROR_ROOT", tmp_path / "h1" / "h0")
    monkeypatch.setattr(
        server, "_active", ProjectContext(folder=folder, output_dir=output_dir)
    )
    return TestClient(server.app, base_url="http://localhost"), folder, output_dir


def _results(output_dir, *paths):
    (output_dir / "results.json").write_text(json.dumps({"clusters": [{
        "cluster_id": 1,
        "best_image": str(paths[0]),
        "images": [
            {"path": str(p), "score": 0.5, "centrality": 1.0, "rank": i + 1}
            for i, p in enumerate(paths)
        ],
    }]}))


def _paths(body):
    return [ph["path"] for ph in body["photos"]]


def _status(body, path):
    for ph in body["photos"]:
        if ph["path"] == str(path):
            return ph["status"]
    return None


def test_applied_deletes_are_hidden(api):
    client, folder, output_dir = api
    kept, gone = folder / "a.jpg", folder / "b.jpg"
    _results(output_dir, kept, gone)
    save_decisions(output_dir, {str(kept): KEPT, str(gone): DELETED})

    body = client.get("/api/gallery").json()

    # results.json still lists it, but the file is off the disk — showing it
    # would mean a broken thumbnail and a day count the user cannot act on.
    assert _paths(body) == [str(kept)]


def test_pending_deletes_are_still_shown(api):
    client, folder, output_dir = api
    doomed = folder / "a.jpg"
    _results(output_dir, doomed)
    save_decisions(output_dir, {str(doomed): TO_DELETE})

    body = client.get("/api/gallery").json()

    # Still on disk and still restorable, so it stays visible.
    assert _paths(body) == [str(doomed)]
    assert _status(body, doomed) == "delete"


def test_status_mapping(api):
    client, folder, output_dir = api
    a, b, c, d = (folder / n for n in ("a.jpg", "b.jpg", "c.jpg", "d.jpg"))
    _results(output_dir, a, b, c, d)
    save_decisions(output_dir, {str(a): KEPT, str(b): FAVORITE, str(c): TO_DELETE})

    body = client.get("/api/gallery").json()

    assert _status(body, a) == "keep"
    assert _status(body, b) == "keep"  # a star is a keep
    assert _status(body, c) == "delete"
    assert _status(body, d) == "undecided"


def test_everything_deleted_yields_an_empty_gallery(api):
    client, folder, output_dir = api
    a, b = folder / "a.jpg", folder / "b.jpg"
    _results(output_dir, a, b)
    save_decisions(output_dir, {str(a): DELETED, str(b): DELETED})

    assert client.get("/api/gallery").json()["photos"] == []


def test_requires_a_pipeline_run(api):
    client, _folder, _output_dir = api
    assert client.get("/api/gallery").status_code == 400
