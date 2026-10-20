"""$SIGHTREAD_COLD_CACHE_ROOT: compare renders and transcodes on another drive.

See webapp/cachedirs.py and docs/data-storage.md for why the split is where it
is. The failure that matters is an unmounted drive: nothing may be written
under the missing root (it would land on the system disk under the mount
point), and nothing may be remembered as a broken file.
"""
from fastapi.testclient import TestClient
from PIL import Image

import cachedirs
import projects
import server
import thumbs
import video as video_mod
from projects import ProjectContext


def _photo(folder, name="a.jpg"):
    p = folder / name
    Image.new("RGB", (3000, 2000), "red").save(p)
    return p


def _setup(tmp_path, monkeypatch, mounted=True):
    folder = tmp_path / "photos"
    folder.mkdir()
    out = tmp_path / "data" / "abc123"
    out.mkdir(parents=True)
    root = tmp_path / "h1" / "sightread-cache"
    if mounted:
        root.mkdir(parents=True)
    monkeypatch.setenv(cachedirs.ENV, str(root))
    return folder, out, root


def test_unset_keeps_everything_in_the_project(tmp_path):
    src = _photo(tmp_path)
    out = tmp_path / "out"
    assert thumbs.cache_file(out, src, thumbs.COMPARE_WIDTH).parent == out / "thumb_cache"
    assert video_mod.cache_path(out, src).parent == out / "video_cache"


def test_compare_width_and_transcodes_go_cold_grid_stays(tmp_path, monkeypatch):
    folder, out, root = _setup(tmp_path, monkeypatch)
    src = _photo(folder)
    assert thumbs.cache_file(out, src, thumbs.GRID_MAX_WIDTH).parent == out / "thumb_cache"
    assert thumbs.cache_file(out, src, 400).parent == out / "thumb_cache"
    assert thumbs.cache_file(out, src, thumbs.COMPARE_WIDTH).parent == root / "abc123" / "thumb_cache"
    assert video_mod.cache_path(out, src).parent == root / "abc123" / "video_cache"


def test_unmounted_root_is_never_recreated(tmp_path, monkeypatch):
    folder, out, root = _setup(tmp_path, monkeypatch, mounted=False)
    dest = thumbs.cache_file(out, _photo(folder), thumbs.COMPARE_WIDTH)
    try:
        thumbs.write(dest, b"x")
        raise AssertionError("write under a missing cold root should refuse")
    except cachedirs.ColdCacheUnavailable:
        pass
    assert not root.exists()


def test_image_still_served_when_cold_drive_missing(tmp_path, monkeypatch):
    folder, out, root = _setup(tmp_path, monkeypatch, mounted=False)
    src = _photo(folder)
    monkeypatch.setattr(server, "_active", ProjectContext(folder=folder, output_dir=out))
    client = TestClient(server.app, base_url="http://localhost")
    resp = client.get("/api/image", params={"path": str(src), "w": thumbs.COMPARE_WIDTH})
    assert resp.status_code == 200 and resp.content[:2] == b"\xff\xd8"
    assert not root.exists()


def test_video_probe_names_the_drive_and_marks_nothing_failed(tmp_path, monkeypatch):
    folder, out, root = _setup(tmp_path, monkeypatch, mounted=False)
    clip = folder / "a.mp4"
    clip.write_bytes(b"not really a video")
    monkeypatch.setattr(server, "_active", ProjectContext(folder=folder, output_dir=out))
    client = TestClient(server.app, base_url="http://localhost")
    resp = client.head("/api/video", params={"path": str(clip)})
    assert resp.status_code == 422
    server._transcode_bg(clip, video_mod.cache_path(out, clip))
    assert clip not in server._transcode_failed
    assert not root.exists()


def test_evict_frees_the_cold_copies_too(tmp_path, monkeypatch):
    folder, out, root = _setup(tmp_path, monkeypatch)
    src = _photo(folder)
    for w in (thumbs.GRID_MAX_WIDTH, thumbs.COMPARE_WIDTH):
        thumbs.write(thumbs.cache_file(out, src, w), b"jpeg")
    assert projects.evict_derived_caches(out) == 8
    assert not (root / "abc123" / "thumb_cache").exists()
