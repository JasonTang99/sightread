"""End-to-end smoke test for the video-highlights pipeline step.

Uses the REAL clipfarm.lib.suggest_clips over a real synthetic ffmpeg video
(frame extraction, motion/scene scoring, window merging all exercised).
Only the DINOv3 embedder is stubbed: pipeline._make_video_frame_embedder is
monkeypatched to a deterministic fake that returns L2-normalized random
vectors seeded by frame path — same (embed_frames, release) contract.
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

import pipeline

clipfarm_lib = pytest.importorskip("clipfarm.lib", reason="clipfarm not installed")

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)

_EMBED_DIM = 64


def _make_synthetic_video(dest: Path) -> None:
    """8s 320x240@10fps mp4: 4s moving testsrc + 4s static smptebars.

    The concat boundary gives a real scene change and testsrc's moving
    elements give non-zero motion, so clipfarm scores are non-trivial.
    """
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=10",
        "-f", "lavfi", "-i", "smptebars=duration=4:size=320x240:rate=10",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]",
        "-map", "[v]", "-pix_fmt", "yuv420p",
        str(dest),
    ]
    subprocess.run(cmd, check=True, timeout=120)


@pytest.fixture(scope="module")
def synthetic_video(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("video_src") / "synthetic.mp4"
    _make_synthetic_video(path)
    return path


@pytest.fixture()
def fake_embedder(monkeypatch):
    """Replace pipeline._make_video_frame_embedder with a deterministic fake.

    Matches the (embed_frames, release) contract; embed_frames maps each frame
    path to an L2-normalized vector seeded by the path string. Returns a call
    log: one list of frame paths per embed_frames invocation.
    """
    calls: list[list[str]] = []

    def _vector_for(path_str: str) -> np.ndarray:
        seed = int.from_bytes(hashlib.sha1(path_str.encode()).digest()[:8], "little")
        vec = np.random.default_rng(seed).standard_normal(_EMBED_DIM).astype(np.float32)
        return vec / np.linalg.norm(vec)

    def embed_frames(frame_paths) -> np.ndarray:
        paths = [str(p) for p in frame_paths]
        calls.append(paths)
        return np.stack([_vector_for(p) for p in paths])

    def factory(*args, **kwargs):
        return embed_frames, lambda: None

    monkeypatch.setattr(pipeline, "_make_video_frame_embedder", factory)
    # The real DINOv3 model must never load in this test.
    monkeypatch.setattr(
        pipeline, "_load_embedding_model",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("real embedding model must not load in e2e test")
        ),
    )
    return calls


@pytest.fixture()
def suggest_clips_spy(monkeypatch):
    """Count calls into the real clipfarm.lib.suggest_clips (pipeline imports
    it at call time, so patching the module attribute is the right seam)."""
    calls: list[str] = []
    real = clipfarm_lib.suggest_clips

    def spy(video_path, *args, **kwargs):
        calls.append(str(video_path))
        return real(video_path, *args, **kwargs)

    monkeypatch.setattr(clipfarm_lib, "suggest_clips", spy)
    return calls


def _load_json(output_dir: Path) -> dict:
    return json.loads((output_dir / "video_highlights.json").read_text())


class TestEndToEndVideoHighlights:
    def test_full_run_then_cache_noop(
        self, tmp_path, synthetic_video, fake_embedder, suggest_clips_spy
    ):
        photos = tmp_path / "photos"
        photos.mkdir()
        video = (photos / "clip.mp4").resolve()
        shutil.copy2(synthetic_video, video)
        out = tmp_path / "out"

        # --- First run: real clipfarm over the synthetic video ---
        pipeline.compute_video_highlights(str(photos), out)

        data = _load_json(out)
        assert data["schema_version"] == 1
        assert list(data["videos"].keys()) == [str(video)]
        entry = data["videos"][str(video)]

        st = video.stat()
        assert entry["fingerprint"] == f"{st.st_size}:{st.st_mtime_ns}"
        assert entry["duration"] == pytest.approx(8.0, abs=1.0)

        clips = entry["clips"]
        assert isinstance(clips, list) and len(clips) >= 1
        for clip in clips:
            assert set(clip.keys()) == {"start", "end", "score", "scores"}
            assert 0.0 <= clip["start"] < clip["end"] <= entry["duration"] + 0.5
            assert 0.0 <= clip["score"] <= 1.0
            assert set(clip["scores"].keys()) == {"motion", "scene_change", "novelty"}
            for v in clip["scores"].values():
                assert 0.0 <= v <= 1.0

        # testsrc motion / concat scene-cut → at least one non-trivial component
        assert any(v > 0.0 for c in clips for v in c["scores"].values())

        assert len(suggest_clips_spy) == 1
        assert len(fake_embedder) >= 1
        frames_embedded = sum(len(c) for c in fake_embedder)
        assert frames_embedded >= 1

        # --- Second run: fingerprint unchanged → full cache no-op ---
        pipeline.compute_video_highlights(str(photos), out)

        assert len(suggest_clips_spy) == 1, "cached video must not be re-suggested"
        assert sum(len(c) for c in fake_embedder) == frames_embedded, (
            "embedder must not be called again on cache hit"
        )
        assert _load_json(out) == data

    def test_embeddings_are_deterministic_and_normalized(self, fake_embedder):
        embed_frames = pipeline._make_video_frame_embedder()[0]
        paths = ["/x/w0000_f0.jpg", "/x/w0000_f1.jpg"]
        a = embed_frames(paths)
        b = embed_frames(paths)
        assert np.allclose(a, b)
        assert np.allclose(np.linalg.norm(a, axis=1), 1.0, atol=1e-5)
        assert not np.allclose(a[0], a[1])  # different paths → different vectors
