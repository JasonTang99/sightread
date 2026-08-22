"""Load-latency guards: the view path must not redo work already on disk.

Sources live on a slow NAS mount, so every avoidable open of an original file
shows up as first-load lag on a few hundred photos.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import server
from projects import ProjectContext


@pytest.fixture()
def api(tmp_path, monkeypatch):
    folder = tmp_path / "trip"
    folder.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    monkeypatch.setattr(
        server, "_active", ProjectContext(folder=folder, output_dir=output_dir)
    )
    server._shot_times_mem.clear()
    return TestClient(server.app, base_url="http://localhost"), folder, output_dir


def _photo(folder, name):
    p = folder / name
    Image.new("RGB", (64, 48), "red").save(p)
    return p


def test_gallery_uses_pipeline_timestamps_without_reopening_photos(api, monkeypatch):
    client, folder, output_dir = api
    a, b = _photo(folder, "a.jpg"), _photo(folder, "b.jpg")
    stamps = {str(a): 1786099270.0, str(b): 1786099280.0}
    (output_dir / "results.json").write_text(json.dumps({"clusters": [{
        "cluster_id": 1,
        "best_image": str(a),
        "images": [
            {"path": str(p), "score": 0.5, "centrality": 1.0, "rank": i + 1,
             "exif_timestamp": ts}
            for i, (p, ts) in enumerate(stamps.items())
        ],
    }]}))

    def _boom(path):  # the pipeline already read this EXIF; reading it again is the bug
        raise AssertionError(f"re-read EXIF for {path}")

    monkeypatch.setattr(server, "_read_shot_time", _boom)
    body = client.get("/api/gallery").json()

    assert [ph["shot_at"] for ph in body["photos"]] == [
        datetime.fromtimestamp(stamps[str(a)]).isoformat(),
        datetime.fromtimestamp(stamps[str(b)]).isoformat(),
    ]
    # and they land in the shared cache, so /api/videos' pass is free too
    cached = json.loads((output_dir / "shot_times.json").read_text())
    assert cached[str(a)] == datetime.fromtimestamp(stamps[str(a)]).isoformat()


def test_gallery_still_reads_exif_when_pipeline_recorded_none(api):
    client, folder, output_dir = api
    a = _photo(folder, "a.jpg")
    (output_dir / "results.json").write_text(json.dumps({"clusters": [{
        "cluster_id": 1,
        "best_image": str(a),
        "images": [{"path": str(a), "score": 0.5, "centrality": 1.0, "rank": 1}],
    }]}))

    body = client.get("/api/gallery").json()

    assert body["photos"][0]["shot_at"] is not None  # mtime fallback


def test_concurrent_requests_render_a_thumbnail_once(api, monkeypatch):
    """The prewarm pool and an on-screen tile race for the same cold thumbnail.

    Without the in-flight guard both decode and resize the same original —
    the duplicated work lands exactly when the machine is busiest, on a cold
    project whose sources are still coming off the NAS.
    """
    client, folder, _ = api
    photo = _photo(folder, "a.jpg")
    calls = []
    real = server._render_thumb

    def slow(path, w):
        calls.append(str(path))
        time.sleep(0.2)  # long enough for the other threads to arrive
        return real(path, w)

    monkeypatch.setattr(server, "_render_thumb", slow)
    url = f"/api/image?path={photo}&w=160"
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = [f.result().status_code for f in
                 [pool.submit(client.get, url) for _ in range(4)]]

    assert codes == [200, 200, 200, 200]
    assert calls == [str(photo)]


def test_failed_transcode_is_not_retried_on_every_video_listing(api, monkeypatch):
    _, folder, output_dir = api
    src = folder / "clip.MOV"
    src.write_bytes(b"not a video")
    dest = output_dir / "video_cache" / "clip.mp4"
    attempts = []

    def failing(s, d):
        attempts.append(s)
        raise RuntimeError("ffmpeg failed")

    monkeypatch.setattr(server, "transcode_for_web", failing)
    server._transcode_failed.discard(src)
    try:
        for _ in range(3):
            server._transcode_bg(src, dest)
        assert attempts == [src]
    finally:
        server._transcode_failed.discard(src)


def _wait_for(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_prewarm_covers_the_compare_width_the_cluster_view_asks_for(api, monkeypatch):
    """The cluster view requests w=2400, the grid w=800.

    Prewarming only the grid width left every first visit to a cluster paying a
    ~0.9s render per photo, in a view whose whole job is flipping between
    near-identical frames quickly.
    """
    _, folder, _ = api
    a = _photo(folder, "a.jpg")
    seen = []
    monkeypatch.setattr(server, "_prewarm_one", lambda ctx, p, w: seen.append((p, w)))

    server._start_thumb_prewarm(server._active, [str(a)])

    assert _wait_for(lambda: len(seen) == 2), seen
    assert sorted(w for _, w in seen) == [
        server.TIMELINE_THUMB_WIDTH,
        server.COMPARE_THUMB_WIDTH,
    ]


def test_state_starts_the_prewarm_for_cluster_first_users(api, monkeypatch):
    """The app lands in the cluster view, not the timeline.

    While /api/gallery was the only caller, a user who never opened the
    timeline rendered every photo on demand, one cluster at a time.
    """
    client, folder, output_dir = api
    a, b = _photo(folder, "a.jpg"), _photo(folder, "b.jpg")
    (output_dir / "results.json").write_text(json.dumps({"clusters": [{
        "cluster_id": 1,
        "best_image": str(a),
        "images": [
            {"path": str(p), "score": 0.5, "centrality": 1.0, "rank": i + 1}
            for i, p in enumerate((a, b))
        ],
    }]}))
    started = []
    monkeypatch.setattr(
        server, "_start_thumb_prewarm", lambda ctx, paths: started.append(paths)
    )

    client.get("/api/state")

    assert started == [[str(a), str(b)]]
