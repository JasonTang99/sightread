"""Unit tests for scripts/pipeline.py compute_video_highlights.

clipfarm is mocked via sys.modules — no model load, no real video decoding.
"""

import json
import os
import sys
import types
from pathlib import Path

import pytest

import pipeline

FAKE_RESULT = {
    "duration": 34.5,
    "clips": [
        {
            "start": 0.0,
            "end": 4.0,
            "score": 0.9,
            "scores": {"motion": 0.8, "scene_change": 0.7, "novelty": 0.6},
        },
        {
            "start": 12.0,
            "end": 16.0,
            "score": 0.7,
            "scores": {"motion": 0.5, "scene_change": 0.6, "novelty": 0.4},
        },
    ],
}


@pytest.fixture(autouse=True)
def _no_model_load(monkeypatch):
    """The DINOv3 model must never load in unit tests (only lazily on embed)."""

    def _boom(*args, **kwargs):
        raise AssertionError("embedding model must not be loaded in unit tests")

    monkeypatch.setattr(pipeline, "_load_embedding_model", _boom)


@pytest.fixture()
def fake_clipfarm(monkeypatch):
    """Install a fake clipfarm.lib.suggest_clips; returns the call log."""
    calls: list[dict] = []

    def suggest_clips(video_path, workdir, embed_frames, force=False, **kwargs):
        calls.append({
            "video": str(video_path),
            "workdir": Path(workdir),
            "embed_frames": embed_frames,
            "force": force,
        })
        return json.loads(json.dumps(FAKE_RESULT))  # fresh copy per call

    lib = types.ModuleType("clipfarm.lib")
    lib.suggest_clips = suggest_clips
    pkg = types.ModuleType("clipfarm")
    pkg.lib = lib
    monkeypatch.setitem(sys.modules, "clipfarm", pkg)
    monkeypatch.setitem(sys.modules, "clipfarm.lib", lib)
    return calls


def _make_video(folder: Path, name: str = "clip.mp4", content: bytes = b"fake video") -> Path:
    path = folder / name
    path.write_bytes(content)
    return path.resolve()


def _load_json(output_dir: Path) -> dict:
    return json.loads((output_dir / "video_highlights.json").read_text())


class TestComputeVideoHighlights:
    def test_writes_highlights_json(self, tmp_path, fake_clipfarm):
        photos = tmp_path / "photos"
        photos.mkdir()
        video = _make_video(photos, name="a.mp4")
        out = tmp_path / "out"

        pipeline.compute_video_highlights(str(photos), out)

        data = _load_json(out)
        assert data["schema_version"] == 1
        entry = data["videos"][str(video)]
        st = video.stat()
        assert entry["fingerprint"] == f"{st.st_size}:{st.st_mtime_ns}"
        assert entry["duration"] == 34.5
        assert entry["clips"] == FAKE_RESULT["clips"]
        assert len(fake_clipfarm) == 1
        assert fake_clipfarm[0]["workdir"] == out / "video_highlights_cache"
        assert fake_clipfarm[0]["force"] is False

    def test_fingerprint_skip_on_second_run(self, tmp_path, fake_clipfarm):
        photos = tmp_path / "photos"
        photos.mkdir()
        _make_video(photos)
        out = tmp_path / "out"

        pipeline.compute_video_highlights(str(photos), out)
        pipeline.compute_video_highlights(str(photos), out)

        assert len(fake_clipfarm) == 1  # unchanged fingerprint → cached

    def test_changed_video_recomputed(self, tmp_path, fake_clipfarm):
        photos = tmp_path / "photos"
        photos.mkdir()
        video = _make_video(photos)
        out = tmp_path / "out"

        pipeline.compute_video_highlights(str(photos), out)
        video.write_bytes(b"different bytes entirely")
        os.utime(video, ns=(video.stat().st_atime_ns, video.stat().st_mtime_ns + 10**9))
        pipeline.compute_video_highlights(str(photos), out)

        assert len(fake_clipfarm) == 2

    def test_force_recomputes_and_propagates(self, tmp_path, fake_clipfarm):
        photos = tmp_path / "photos"
        photos.mkdir()
        _make_video(photos)
        out = tmp_path / "out"

        pipeline.compute_video_highlights(str(photos), out)
        pipeline.compute_video_highlights(str(photos), out, force=True)

        assert len(fake_clipfarm) == 2
        assert fake_clipfarm[1]["force"] is True

    def test_missing_clipfarm_skips_gracefully(self, tmp_path, monkeypatch, capsys):
        photos = tmp_path / "photos"
        photos.mkdir()
        _make_video(photos)
        out = tmp_path / "out"
        # None in sys.modules forces ImportError even if clipfarm is installed
        monkeypatch.setitem(sys.modules, "clipfarm", None)
        monkeypatch.setitem(sys.modules, "clipfarm.lib", None)

        pipeline.compute_video_highlights(str(photos), out)

        assert "clipfarm not installed" in capsys.readouterr().out
        assert not (out / "video_highlights.json").exists()

    def test_stale_entries_pruned(self, tmp_path, fake_clipfarm):
        photos = tmp_path / "photos"
        photos.mkdir()
        video = _make_video(photos)
        out = tmp_path / "out"
        out.mkdir()
        gone = str(photos / "deleted.mp4")
        (out / "video_highlights.json").write_text(json.dumps({
            "schema_version": 1,
            "videos": {gone: {"fingerprint": "1:1", "duration": 5.0, "clips": []}},
        }))

        pipeline.compute_video_highlights(str(photos), out)

        data = _load_json(out)
        assert gone not in data["videos"]
        assert str(video) in data["videos"]

    def test_failed_video_warns_and_continues(self, tmp_path, fake_clipfarm, monkeypatch):
        photos = tmp_path / "photos"
        photos.mkdir()
        bad = _make_video(photos, name="bad.mp4")
        good = _make_video(photos, name="good.mp4")
        out = tmp_path / "out"

        real = sys.modules["clipfarm.lib"].suggest_clips

        def flaky(video_path, **kwargs):
            if video_path.name == "bad.mp4":
                raise RuntimeError("decode boom")
            return real(video_path, **kwargs)

        monkeypatch.setattr(sys.modules["clipfarm.lib"], "suggest_clips", flaky)

        with pytest.warns(UserWarning, match="decode boom"):
            pipeline.compute_video_highlights(str(photos), out)

        data = _load_json(out)
        assert str(bad) not in data["videos"]
        assert str(good) in data["videos"]

    def test_corrupt_existing_json_recomputed(self, tmp_path, fake_clipfarm):
        photos = tmp_path / "photos"
        photos.mkdir()
        video = _make_video(photos)
        out = tmp_path / "out"
        out.mkdir()
        (out / "video_highlights.json").write_text("{not json")

        with pytest.warns(UserWarning, match="Corrupt"):
            pipeline.compute_video_highlights(str(photos), out)

        assert str(video) in _load_json(out)["videos"]

    def test_atomic_write_leaves_no_tmp(self, tmp_path, fake_clipfarm):
        photos = tmp_path / "photos"
        photos.mkdir()
        _make_video(photos)
        out = tmp_path / "out"

        pipeline.compute_video_highlights(str(photos), out)

        assert (out / "video_highlights.json").exists()
        assert not list(out.glob("*.tmp"))

    def test_no_videos_writes_empty(self, tmp_path, fake_clipfarm):
        photos = tmp_path / "photos"
        photos.mkdir()
        out = tmp_path / "out"

        pipeline.compute_video_highlights(str(photos), out)

        assert _load_json(out)["videos"] == {}
        assert len(fake_clipfarm) == 0
