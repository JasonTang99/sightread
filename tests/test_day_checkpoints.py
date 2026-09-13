"""Embedding and scoring checkpoint their cache after each shooting day.

A 9,500-photo trip scores for about two hours, and the caches used to be
written once, at the end of each stage — a crash at 90% lost all of it. These
pin that a run which dies partway keeps every finished day, and that the rerun
only does the days that are left.
"""

import json
from datetime import datetime

import numpy as np
import pytest

import pipeline


def _ts(day, hour=12):
    return datetime(2024, 3, day, hour).timestamp()


@pytest.fixture()
def trip():
    """Three days out of order, plus a photo with no EXIF time."""
    paths = ["/t/d2a.jpg", "/t/d1a.jpg", "/t/none.jpg", "/t/d3a.jpg", "/t/d1b.jpg", "/t/d2b.jpg"]
    timestamps = [_ts(2), _ts(1, 9), None, _ts(3), _ts(1, 23), _ts(2, 8)]
    return paths, timestamps


def _vec(path):
    rng = np.random.default_rng(abs(hash(path)) % 2**32)
    v = rng.normal(size=4).astype(np.float32)
    return v / np.linalg.norm(v)


class _Crash(RuntimeError):
    pass


def test_day_chunks_are_in_day_order_with_undated_last(trip):
    paths, timestamps = trip
    assert pipeline._day_chunks(paths, timestamps) == [
        ("2024-03-01", ["/t/d1a.jpg", "/t/d1b.jpg"]),
        ("2024-03-02", ["/t/d2a.jpg", "/t/d2b.jpg"]),
        ("2024-03-03", ["/t/d3a.jpg"]),
        ("undated", ["/t/none.jpg"]),
    ]


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

@pytest.fixture()
def embedder(monkeypatch):
    """Fake DINOv3: records what it embeds, crashes on a chosen day."""
    state = {"calls": [], "crash_on": None, "loads": 0}

    def load(*a, **k):
        state["loads"] += 1
        return object(), object(), 1

    def embed(chunk, *a, desc="", **k):
        if state["crash_on"] and state["crash_on"] in desc:
            raise _Crash(desc)
        state["calls"].append(list(chunk))
        return np.stack([_vec(p) for p in chunk])

    monkeypatch.setattr(pipeline, "_load_embedding_model", load)
    monkeypatch.setattr(pipeline, "_embed_with_model", embed)
    return state


def test_embedding_crash_keeps_finished_days_and_rerun_does_the_rest(trip, tmp_path, embedder):
    paths, timestamps = trip
    cache = tmp_path / "emb.npy"

    embedder["crash_on"] = "2024-03-02"
    with pytest.raises(_Crash):
        pipeline.compute_embeddings(paths, cache, timestamps=timestamps)
    assert json.loads(cache.with_suffix(".paths.json").read_text()) == ["/t/d1a.jpg", "/t/d1b.jpg"]
    assert np.load(cache).shape == (2, 4)

    embedder.update(crash_on=None, calls=[])
    result = pipeline.compute_embeddings(paths, cache, timestamps=timestamps)

    assert embedder["calls"] == [["/t/d2a.jpg", "/t/d2b.jpg"], ["/t/d3a.jpg"], ["/t/none.jpg"]]
    np.testing.assert_allclose(result, np.stack([_vec(p) for p in paths]))


def test_embedding_loads_the_model_once_per_run_not_per_day(trip, tmp_path, embedder):
    paths, timestamps = trip
    pipeline.compute_embeddings(paths, tmp_path / "emb.npy", timestamps=timestamps)
    assert embedder["loads"] == 1
    assert len(embedder["calls"]) == 4


def test_fully_cached_embeddings_never_load_the_model(trip, tmp_path, embedder):
    paths, timestamps = trip
    cache = tmp_path / "emb.npy"
    pipeline.compute_embeddings(paths, cache, timestamps=timestamps)
    embedder.update(loads=0, calls=[])

    pipeline.compute_embeddings(paths, cache, timestamps=timestamps)

    assert (embedder["loads"], embedder["calls"]) == (0, [])


def test_rows_that_disagree_with_the_sidecar_are_recomputed(trip, tmp_path, embedder):
    """What a crash between the two renames would leave behind."""
    paths, timestamps = trip
    cache = tmp_path / "emb.npy"
    np.save(cache, np.zeros((3, 4), dtype=np.float32))
    cache.with_suffix(".paths.json").write_text(json.dumps(paths[:2]))

    with pytest.warns(UserWarning, match="does not match its sidecar"):
        result = pipeline.compute_embeddings(paths, cache, timestamps=timestamps)

    assert sorted(p for c in embedder["calls"] for p in c) == sorted(paths)
    np.testing.assert_allclose(result, np.stack([_vec(p) for p in paths]))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@pytest.fixture()
def scorer(monkeypatch):
    state = {"calls": [], "crash_on": None, "loads": 0}

    def load(*a, **k):
        state["loads"] += 1
        return {"musiq": object()}, None

    def score(chunk, metrics, detector, device=None, thumb_dir=None, desc=""):
        if state["crash_on"] and state["crash_on"] in desc:
            raise _Crash(desc)
        state["calls"].append(list(chunk))
        keys = list(metrics) + pipeline.SCORE_EXTRA_KEYS
        return {k: np.array([_vec(p)[i] for p in chunk], dtype=np.float32) for i, k in enumerate(keys)}

    monkeypatch.setattr(pipeline, "_load_score_models", load)
    monkeypatch.setattr(pipeline, "_score_with_models", score)
    return state


def test_scoring_crash_keeps_finished_days_and_rerun_does_the_rest(trip, tmp_path, scorer):
    paths, timestamps = trip
    cache = tmp_path / "scores.npz"

    scorer["crash_on"] = "2024-03-03"
    with pytest.raises(_Crash):
        pipeline.score_images(paths, cache, timestamps=timestamps)
    assert json.loads(cache.with_suffix(".paths.json").read_text()) == [
        "/t/d2a.jpg", "/t/d1a.jpg", "/t/d1b.jpg", "/t/d2b.jpg",
    ]

    scorer.update(crash_on=None, calls=[])
    _, components = pipeline.score_images(paths, cache, timestamps=timestamps)

    assert scorer["calls"] == [["/t/d3a.jpg"], ["/t/none.jpg"]]
    np.testing.assert_allclose(components["musiq"], [_vec(p)[0] for p in paths])
    np.testing.assert_allclose(components["face_bonus"], [_vec(p)[3] for p in paths])

    scorer.update(loads=0, calls=[])
    pipeline.score_images(paths, cache, timestamps=timestamps)
    assert (scorer["loads"], scorer["calls"]) == (0, [])


def test_a_metric_that_stops_loading_rescores_everything(trip, tmp_path, scorer, monkeypatch):
    paths, timestamps = trip
    cache = tmp_path / "scores.npz"
    pipeline.score_images(paths[:3], cache, timestamps=timestamps[:3])
    scorer["calls"] = []

    monkeypatch.setattr(pipeline, "_load_score_models", lambda *a, **k: ({"nima": object()}, None))
    _, components = pipeline.score_images(paths, cache, timestamps=timestamps)

    assert sorted(p for c in scorer["calls"] for p in c) == sorted(paths)
    assert "musiq" not in components and "nima" in components
