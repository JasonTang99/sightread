"""Tests for the undo stack surviving a server restart.

The stack used to be memory-only, so a restart — or the reload a dev server does
on any edit — silently took undo with it while decisions.json stayed fully
written. A restart is simulated here the way it actually happens: the in-memory
list is emptied, then the project is made active again.
"""

import json

import pytest
from fastapi.testclient import TestClient

import server
from projects import ProjectContext
from utils import KEPT, TO_DELETE, load_decisions, save_decisions


@pytest.fixture()
def api(tmp_path, monkeypatch):
    folder = tmp_path / "h0" / "photos"
    folder.mkdir(parents=True)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    monkeypatch.setattr(server, "PRIMARY_ROOT", tmp_path / "h0")
    monkeypatch.setattr(server, "MIRROR_ROOT", tmp_path / "h1" / "h0")
    monkeypatch.setattr(
        server, "_active", ProjectContext(folder=folder, output_dir=output_dir)
    )
    monkeypatch.setattr(server, "_undo_stack", [])
    return TestClient(server.app, base_url="http://localhost"), folder, output_dir


def _photos(folder, *names):
    out = []
    for n in names:
        p = folder / n
        p.write_bytes(b"jpeg")
        out.append(str(p.resolve()))
    return out


def _restart(output_dir):
    """Drop in-memory state, then re-activate the project as a fresh boot does."""
    server._undo_stack.clear()
    server._load_undo(server._active)


class TestPersistence:
    def test_confirm_writes_the_stack_to_disk(self, api):
        client, folder, output_dir = api
        a, b = _photos(folder, "a.jpg", "b.jpg")

        client.post("/api/confirm", json={"delete_paths": [b], "decided_paths": [a, b]})

        lines = (output_dir / server.UNDO_FILENAME).read_text().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["previous"] == {a: None, b: None}

    def test_undo_survives_a_restart(self, api):
        client, folder, output_dir = api
        a, b = _photos(folder, "a.jpg", "b.jpg")
        client.post("/api/confirm", json={"delete_paths": [b], "decided_paths": [a, b]})

        _restart(output_dir)

        assert len(server._undo_stack) == 1
        assert client.post("/api/undo").status_code == 200
        assert load_decisions(output_dir) == {}

    def test_popping_shortens_the_file(self, api):
        client, folder, output_dir = api
        a, b = _photos(folder, "a.jpg", "b.jpg")
        client.post("/api/confirm", json={"delete_paths": [], "decided_paths": [a]})
        client.post("/api/confirm", json={"delete_paths": [b], "decided_paths": [b]})

        client.post("/api/undo")

        lines = (output_dir / server.UNDO_FILENAME).read_text().splitlines()
        assert len(lines) == 1
        # The surviving entry is the older one; the second undo takes it.
        _restart(output_dir)
        assert client.post("/api/undo").status_code == 200
        assert client.post("/api/undo").status_code == 400

    def test_stack_stays_bounded_on_disk(self, api):
        client, folder, output_dir = api
        photos = _photos(folder, *[f"p{i}.jpg" for i in range(15)])
        for p in photos:
            client.post("/api/confirm", json={"delete_paths": [], "decided_paths": [p]})

        lines = (output_dir / server.UNDO_FILENAME).read_text().splitlines()
        assert len(lines) == server._UNDO_DEPTH

    def test_no_undo_file_means_an_empty_stack(self, api):
        client, _, output_dir = api

        _restart(output_dir)

        assert server._undo_stack == []
        assert client.post("/api/undo").status_code == 400

    def test_corrupt_undo_file_is_ignored(self, api):
        client, folder, output_dir = api
        (output_dir / server.UNDO_FILENAME).write_text("{not json\n")

        _restart(output_dir)

        assert server._undo_stack == []

    def test_applying_deletes_clears_the_file(self, api, monkeypatch):
        client, folder, output_dir = api
        (a,) = _photos(folder, "a.jpg")
        # Mirror the file so apply-deletes is willing to unlink it.
        mirror = server.MIRROR_ROOT / "photos" / "a.jpg"
        mirror.parent.mkdir(parents=True)
        mirror.write_bytes(b"jpeg")
        client.post("/api/confirm", json={"delete_paths": [a], "decided_paths": [a]})
        assert (output_dir / server.UNDO_FILENAME).exists()

        resp = client.post("/api/apply-deletes")

        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1
        # Undo entries point at files that are gone; they must not come back.
        assert not (output_dir / server.UNDO_FILENAME).exists()
        _restart(output_dir)
        assert server._undo_stack == []
