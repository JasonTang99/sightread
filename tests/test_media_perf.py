"""Load-latency guards: the view path must not redo work already on disk.

Sources live on a slow NAS mount, so every avoidable open of an original file
shows up as first-load lag on a few hundred photos.
"""

import json
import concurrent.futures
import os
import subprocess
import threading
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
    assert cached["times"][str(a)] == datetime.fromtimestamp(stamps[str(a)]).isoformat()


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


def test_serve_video_returns_422_after_transcode_failed(api, monkeypatch):
    client, folder, output_dir = api
    src = folder / "clip.MOV"
    src.write_bytes(b"not a video")
    dest = output_dir / "video_cache" / "clip.mp4"

    def failing(_s, _d):
        raise RuntimeError("ffmpeg failed")

    monkeypatch.setattr(server, "transcode_for_web", failing)
    server._transcode_failed.discard(src)
    try:
        server._transcode_bg(src, dest)
        assert src in server._transcode_failed
        resp = client.get("/api/video", params={"path": str(src)})
        assert resp.status_code == 422
        assert "corrupt" in resp.json()["detail"].lower()
        # The player probes with HEAD. A GET-only route misses, and HEAD then
        # falls through to the static mount's 404 instead of this 422.
        head = client.head("/api/video", params={"path": str(src)})
        assert head.status_code == 422
    finally:
        server._transcode_failed.discard(src)


def test_head_probe_waits_while_transcode_is_running(api, monkeypatch):
    """First open probes with HEAD and must wait, not fail, until the cache exists.

    HEAD on a GET-only route does not match, so it falls through to the static
    mount and comes back 404 Not Found. The player treats that as final. 503
    means the transcode is still running; 200 is only the finished cache.
    """
    client, folder, output_dir = api
    src = folder / "clip.MOV"
    src.write_bytes(b"not a video")
    started = threading.Event()
    release = threading.Event()

    def blocked(_s, dest):
        started.set()
        release.wait(timeout=5)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"cached-mp4")

    monkeypatch.setattr(server, "transcode_for_web", blocked)
    server._transcode_failed.discard(src.resolve())
    try:
        missing = client.head("/api/video", params={"path": str(folder / "nope.MOV")})
        assert missing.status_code == 404

        head = client.head("/api/video", params={"path": str(src)})
        assert head.status_code == 503, head.status_code
        assert started.wait(timeout=5)
        assert client.head("/api/video", params={"path": str(src)}).status_code == 503
        # Neighbour preloads must keep skipping an uncached file, not download it.
        pre = client.get("/api/video", params={"path": str(src), "cached_only": "1"})
        assert pre.status_code == 404

        release.set()
        assert _wait_for(lambda: (output_dir / "video_cache").exists() and any(
            (output_dir / "video_cache").glob("*.mp4")
        ))
        ready = client.get("/api/video", params={"path": str(src)})
        assert ready.status_code == 200
        assert ready.content == b"cached-mp4"
        assert client.head("/api/video", params={"path": str(src)}).status_code == 200
    finally:
        release.set()
        server._transcode_failed.discard(src.resolve())


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


def test_transcodes_run_below_the_renders_they_compete_with(tmp_path):
    """Transcoding is lookahead; nobody is waiting on it.

    Two of them saturate this box — a 2400px thumbnail render, which the user
    *is* waiting on, went from 334ms idle to 997ms while they ran. Dropping the
    pool's priority brings that back to 509ms. Linux niceness is per-thread and
    survives fork/exec, so the ffmpeg children inherit it and the request
    threads keep their own.
    """
    server._deprioritise  # the pool's initializer

    def observed():
        return os.nice(0)  # returns the caller's niceness without changing it

    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, initializer=server._deprioritise
    )
    try:
        worker_nice = pool.submit(observed).result()
        child_nice = pool.submit(
            lambda: int(
                subprocess.run(
                    ["sh", "-c", "ps -o ni= -p $$"], capture_output=True, text=True
                ).stdout
            )
        ).result()
    finally:
        pool.shutdown()

    assert worker_nice > 0, "transcode workers must not run at normal priority"
    assert child_nice == worker_nice, "ffmpeg would not inherit the lowered priority"
    assert os.nice(0) == 0, "the calling thread's priority must be untouched"
