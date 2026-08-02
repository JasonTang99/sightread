"""Tests for POST /api/auto-keep-best.

The sweep applies, in bulk, the choice the cluster view already pre-selects:
top-ranked image kept, the rest queued for deletion. It only touches clusters
nobody has looked at, and it lands as one undo entry so a hundred swept clusters
can be taken back in one step rather than overflowing the ten-deep stack.
"""

import json

import pytest
from fastapi.testclient import TestClient

import server
from projects import ProjectContext
from utils import FAVORITE, KEPT, TO_DELETE, load_decisions, save_decisions


@pytest.fixture()
def api(tmp_path, monkeypatch):
    folder = tmp_path / "photos"
    folder.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    monkeypatch.setattr(
        server, "_active", ProjectContext(folder=folder, output_dir=output_dir)
    )
    monkeypatch.setattr(server, "_undo_stack", [])
    return TestClient(server.app, base_url="http://localhost"), folder, output_dir


def _cluster(cid, paths, scores=None):
    scores = scores or [0.9] * len(paths)
    return {
        "cluster_id": cid,
        "best_image": str(paths[0]),
        "images": [
            {"path": str(p), "score": s, "centrality": 1.0, "rank": i + 1}
            for i, (p, s) in enumerate(zip(paths, scores))
        ],
    }


def _write_results(output_dir, *clusters):
    (output_dir / "results.json").write_text(json.dumps({"clusters": list(clusters)}))


def _photos(folder, *names):
    out = []
    for n in names:
        p = folder / n
        p.write_bytes(b"jpeg")
        out.append(str(p.resolve()))
    return out


class TestSweep:
    def test_keeps_rank_one_and_queues_the_rest(self, api):
        client, folder, output_dir = api
        a, b, c = _photos(folder, "a.jpg", "b.jpg", "c.jpg")
        _write_results(output_dir, _cluster(1, [a, b, c]))

        resp = client.post("/api/auto-keep-best", json={})

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "clusters": 1, "kept": 1, "queued": 2}
        decisions = load_decisions(output_dir)
        assert decisions[a] == KEPT
        assert decisions[b] == TO_DELETE
        assert decisions[c] == TO_DELETE

    def test_singletons_are_left_alone(self, api):
        client, folder, output_dir = api
        (only,) = _photos(folder, "solo.jpg")
        _write_results(output_dir, _cluster(1, [only]))

        body = client.post("/api/auto-keep-best", json={}).json()

        assert body["clusters"] == 0
        assert load_decisions(output_dir) == {}

    def test_skips_clusters_already_touched(self, api):
        client, folder, output_dir = api
        a, b = _photos(folder, "a.jpg", "b.jpg")
        c, d = _photos(folder, "c.jpg", "d.jpg")
        _write_results(output_dir, _cluster(1, [a, b]), _cluster(2, [c, d]))
        # One image decided is enough to make the cluster the user's, not the
        # sweep's — a partly-reviewed cluster must not be finished automatically.
        save_decisions(output_dir, {b: FAVORITE})

        body = client.post("/api/auto-keep-best", json={}).json()

        assert body["clusters"] == 1
        decisions = load_decisions(output_dir)
        assert decisions[b] == FAVORITE
        assert a not in decisions
        assert decisions[c] == KEPT
        assert decisions[d] == TO_DELETE

    def test_min_score_leaves_weak_clusters_for_the_human(self, api):
        client, folder, output_dir = api
        strong = _photos(folder, "s1.jpg", "s2.jpg")
        weak = _photos(folder, "w1.jpg", "w2.jpg")
        _write_results(
            output_dir,
            _cluster(1, strong, scores=[0.8, 0.7]),
            _cluster(2, weak, scores=[0.3, 0.2]),
        )

        body = client.post("/api/auto-keep-best", json={"min_score": 0.5}).json()

        assert body["clusters"] == 1
        decisions = load_decisions(output_dir)
        assert decisions[strong[0]] == KEPT
        assert weak[0] not in decisions
        assert weak[1] not in decisions

    def test_requires_a_pipeline_run(self, api):
        client, _, _ = api
        assert client.post("/api/auto-keep-best", json={}).status_code == 400


class TestSweepUndo:
    def test_whole_sweep_is_one_undo_entry(self, api):
        client, folder, output_dir = api
        a, b = _photos(folder, "a.jpg", "b.jpg")
        c, d = _photos(folder, "c.jpg", "d.jpg")
        _write_results(output_dir, _cluster(1, [a, b]), _cluster(2, [c, d]))

        client.post("/api/auto-keep-best", json={})
        assert len(server._undo_stack) == 1

        assert client.post("/api/undo").status_code == 200
        assert load_decisions(output_dir) == {}

    def test_no_undo_entry_when_nothing_was_swept(self, api):
        client, folder, output_dir = api
        (only,) = _photos(folder, "solo.jpg")
        _write_results(output_dir, _cluster(1, [only]))

        client.post("/api/auto-keep-best", json={})

        assert server._undo_stack == []
