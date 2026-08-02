"""Tests for GET /api/video-poster.

The timeline draws videos as still frames rather than <video> elements: media
elements load eagerly, and at six connections per origin they starve the lazy
photo thumbnails further down the page, which then never load at all.
"""

import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

import server
import video as video_mod
from projects import ProjectContext

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


@pytest.fixture()
def api(tmp_path, monkeypatch):
    folder = tmp_path / "photos"
    folder.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    monkeypatch.setattr(
        server, "_active", ProjectContext(folder=folder, output_dir=output_dir)
    )
    monkeypatch.setattr(server._transcode_executor, "submit", lambda *a, **k: None)
    return TestClient(server.app, base_url="http://localhost"), folder, output_dir


def _make_real_video(folder, name="a.mp4", seconds=3):
    """A tiny synthetic clip — ffmpeg has to decode a real frame out of it."""
    path = folder / name
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=10:duration={seconds}",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return path


def test_returns_jpeg_and_caches_it(api):
    client, folder, out = api
    video = _make_real_video(folder)

    resp = client.get("/api/video-poster", params={"path": str(video.resolve()), "w": 160})

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content[:2] == b"\xff\xd8"  # JPEG SOI
    assert video_mod.poster_path(out, video.resolve(), 160).exists()


def test_second_request_is_served_from_cache(api, monkeypatch):
    client, folder, _ = api
    video = _make_real_video(folder)
    client.get("/api/video-poster", params={"path": str(video.resolve()), "w": 160})

    def _boom(*a, **k):
        raise AssertionError("cached poster should not be re-extracted")

    monkeypatch.setattr(server, "extract_poster", _boom)
    resp = client.get("/api/video-poster", params={"path": str(video.resolve()), "w": 160})

    assert resp.status_code == 200


def test_shorter_than_the_seek_point_still_yields_a_frame(api):
    """A clip that ends before the 1s seek falls back to its first frame."""
    client, folder, _ = api
    video = _make_real_video(folder, "blink.mp4", seconds=1)

    resp = client.get("/api/video-poster", params={"path": str(video.resolve()), "w": 160})

    assert resp.status_code == 200
    assert resp.content[:2] == b"\xff\xd8"


def test_path_outside_the_project_is_refused(api, tmp_path):
    client, _, _ = api
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"not in the project")

    resp = client.get("/api/video-poster", params={"path": str(outside)})

    assert resp.status_code == 403


def test_missing_file_is_404(api, folder=None):
    client, folder, _ = api

    resp = client.get("/api/video-poster", params={"path": str(folder / "nope.mp4")})

    assert resp.status_code == 404
