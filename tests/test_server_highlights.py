"""Unit tests for webapp/server.py _load_highlights_for and /api/videos."""

import json

import pytest
from fastapi.testclient import TestClient

import server
from projects import ProjectContext
from utils import KEPT, TO_DELETE, save_decisions


def _write_highlights(output_dir, videos):
    (output_dir / "video_highlights.json").write_text(json.dumps({
        "schema_version": 1,
        "videos": videos,
    }))


CLIPS = [
    {"start": 0.0, "end": 4.0, "score": 0.9,
     "scores": {"motion": 0.8, "scene_change": 0.7, "novelty": 0.6}},
]


class TestLoadHighlightsFor:
    def test_missing_file_returns_empty(self, tmp_path):
        assert server._load_highlights_for(tmp_path, ["/videos/a.mp4"]) == {}

    def test_corrupt_file_returns_empty(self, tmp_path):
        (tmp_path / "video_highlights.json").write_text("{not json")
        assert server._load_highlights_for(tmp_path, ["/videos/a.mp4"]) == {}

    def test_videos_not_dict_returns_empty(self, tmp_path):
        (tmp_path / "video_highlights.json").write_text(json.dumps({"videos": [1, 2]}))
        assert server._load_highlights_for(tmp_path, ["/videos/a.mp4"]) == {}

    def test_merges_only_listed_paths(self, tmp_path):
        _write_highlights(tmp_path, {
            "/videos/a.mp4": {"fingerprint": "1:1", "duration": 34.5, "clips": CLIPS},
            "/videos/deleted.mp4": {"fingerprint": "2:2", "duration": 10.0, "clips": []},
        })

        result = server._load_highlights_for(tmp_path, ["/videos/a.mp4", "/videos/b.mp4"])

        assert result == {"/videos/a.mp4": {"duration": 34.5, "clips": CLIPS}}

    def test_fingerprint_not_exposed(self, tmp_path):
        _write_highlights(tmp_path, {
            "/videos/a.mp4": {"fingerprint": "1:1", "duration": 34.5, "clips": CLIPS},
        })
        entry = server._load_highlights_for(tmp_path, ["/videos/a.mp4"])["/videos/a.mp4"]
        assert set(entry.keys()) == {"duration", "clips"}

    def test_malformed_entry_skipped(self, tmp_path):
        _write_highlights(tmp_path, {
            "/videos/a.mp4": {"duration": 5.0, "clips": "nope"},
            "/videos/b.mp4": "garbage",
            "/videos/c.mp4": {"duration": 8.0, "clips": CLIPS},
        })

        result = server._load_highlights_for(
            tmp_path, ["/videos/a.mp4", "/videos/b.mp4", "/videos/c.mp4"]
        )

        assert result == {"/videos/c.mp4": {"duration": 8.0, "clips": CLIPS}}

    def test_low_score_clips_filtered(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SIGHTREAD_MIN_CLIP_SCORE", "0.5")
        keep = {"start": 0.0, "end": 4.0, "score": 0.7, "scores": {}}
        drop = {"start": 5.0, "end": 9.0, "score": 0.3, "scores": {}}
        _write_highlights(tmp_path, {
            "/videos/a.mp4": {"duration": 20.0, "clips": [keep, drop]},
            "/videos/b.mp4": {"duration": 10.0, "clips": [drop]},
        })

        result = server._load_highlights_for(
            tmp_path, ["/videos/a.mp4", "/videos/b.mp4"]
        )

        # Below-threshold clips are dropped; videos with none left are omitted.
        assert result == {"/videos/a.mp4": {"duration": 20.0, "clips": [keep]}}

    def test_empty_clips_video_omitted(self, tmp_path):
        _write_highlights(tmp_path, {
            "/videos/a.mp4": {"duration": 8.0, "clips": []},
        })

        assert server._load_highlights_for(tmp_path, ["/videos/a.mp4"]) == {}

    def test_invalid_threshold_env_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SIGHTREAD_MIN_CLIP_SCORE", "not-a-number")
        _write_highlights(tmp_path, {
            "/videos/a.mp4": {"duration": 8.0, "clips": CLIPS},
        })

        result = server._load_highlights_for(tmp_path, ["/videos/a.mp4"])

        assert result == {"/videos/a.mp4": {"duration": 8.0, "clips": CLIPS}}


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """TestClient with an active project in tmp_path and transcoding disabled.

    Returns (client, folder, output_dir).
    """
    folder = tmp_path / "photos"
    folder.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    monkeypatch.setattr(
        server, "_active", ProjectContext(folder=folder, output_dir=output_dir)
    )
    # /api/videos submits background ffmpeg transcodes for uncached videos —
    # our fixture "videos" are garbage bytes, so stub the executor out.
    monkeypatch.setattr(
        server._transcode_executor, "submit", lambda *a, **k: None
    )
    # base_url: the server's middleware 403s any non-loopback Host header,
    # and TestClient's default is "testserver".
    return TestClient(server.app, base_url="http://localhost"), folder, output_dir


def _make_video(folder, name="a.mp4"):
    path = folder / name
    path.write_bytes(b"fake video bytes")
    return str(path.resolve())


class TestApiVideosEndpoint:
    def test_response_shape_with_highlights(self, api):
        client, folder, out = api
        video = _make_video(folder)
        _write_highlights(out, {
            video: {"fingerprint": "1:1", "duration": 34.5, "clips": CLIPS},
        })

        resp = client.get("/api/videos")

        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {
            "paths", "folder", "statuses", "shot_times", "highlights", "user_clips", "video_tags",
        }
        assert body["paths"] == [video]
        assert body["statuses"] == {video: "undecided"}
        assert video in body["shot_times"]
        assert body["highlights"] == {video: {"duration": 34.5, "clips": CLIPS}}

    def test_pending_delete_videos_reported_not_dropped(self, api):
        """The timeline colours a marked video red, so it has to be listed.

        Views that only review undecided footage filter on `statuses` instead.
        """
        client, folder, out = api
        keep = _make_video(folder, "keep.mp4")
        gone = _make_video(folder, "gone.mp4")
        save_decisions(out, {gone: TO_DELETE, keep: KEPT})
        _write_highlights(out, {
            keep: {"fingerprint": "1:1", "duration": 34.5, "clips": CLIPS},
            gone: {"fingerprint": "2:2", "duration": 10.0, "clips": []},
        })

        body = client.get("/api/videos").json()

        assert body["paths"] == [gone, keep]
        assert body["statuses"] == {gone: "delete", keep: "keep"}
        assert body["highlights"][keep] == {"duration": 34.5, "clips": CLIPS}

    def test_missing_highlights_file_degrades(self, api):
        client, folder, _ = api
        video = _make_video(folder)

        body = client.get("/api/videos").json()

        assert body["paths"] == [video]
        assert body["highlights"] == {}

    def test_corrupt_highlights_file_degrades(self, api):
        client, folder, out = api
        video = _make_video(folder)
        (out / "video_highlights.json").write_text("{not json")

        resp = client.get("/api/videos")

        assert resp.status_code == 200
        body = resp.json()
        assert body["paths"] == [video]
        assert body["highlights"] == {}

    def test_non_video_files_ignored(self, api):
        client, folder, _ = api
        video = _make_video(folder, "a.mp4")
        (folder / "photo.jpg").write_bytes(b"jpg")
        (folder / "code.ts").write_bytes(b"typescript, not MPEG-TS")

        body = client.get("/api/videos").json()

        assert body["paths"] == [video]
