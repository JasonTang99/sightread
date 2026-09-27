"""Tests for user-editable clips: PUT /api/clips, POST /api/clips/export,
user_clips in /api/videos, and exclusion of exported cuts from video scans."""

import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

import clips as clips_mod
import server
from projects import ProjectContext


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """TestClient with an active project in tmp_path and transcoding disabled.

    Returns (client, folder, output_dir). Mirrors tests/test_server_highlights.py.
    """
    folder = tmp_path / "photos"
    folder.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    monkeypatch.setattr(
        server, "_active", ProjectContext(folder=folder, output_dir=output_dir)
    )
    monkeypatch.setattr(
        server._transcode_executor, "submit", lambda *a, **k: None
    )
    # Cuts land in <trip>/_exports/clips; pin the layout regardless of the env.
    monkeypatch.setattr(server, "EXPORTS_ROOT", None)
    return TestClient(server.app, base_url="http://localhost"), folder, output_dir


def _make_video(folder, name="a.mp4"):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake video bytes")
    return str(path.resolve())


def _write_highlights(output_dir, videos):
    (output_dir / "video_highlights.json").write_text(json.dumps({
        "schema_version": 1,
        "videos": videos,
    }))


def _read_user_clips(output_dir):
    return json.loads((output_dir / "user_clips.json").read_text())


# ---------------------------------------------------------------------------
# PUT /api/clips — validation
# ---------------------------------------------------------------------------

class TestPutClipsValidation:
    def test_path_outside_project_rejected(self, api, tmp_path):
        client, folder, _ = api
        outside = tmp_path / "elsewhere" / "a.mp4"
        outside.parent.mkdir()
        outside.write_bytes(b"x")

        resp = client.put("/api/clips", json={
            "path": str(outside), "clips": [{"start": 0.0, "end": 1.0}],
        })

        assert resp.status_code == 400
        assert "outside project" in resp.json()["detail"]

    def test_missing_file_rejected(self, api):
        client, folder, _ = api
        resp = client.put("/api/clips", json={
            "path": str(folder / "ghost.mp4"),
            "clips": [{"start": 0.0, "end": 1.0}],
        })
        assert resp.status_code == 400

    def test_non_video_file_rejected(self, api):
        client, folder, _ = api
        txt = folder / "notes.txt"
        txt.write_text("hi")
        resp = client.put("/api/clips", json={
            "path": str(txt), "clips": [{"start": 0.0, "end": 1.0}],
        })
        assert resp.status_code == 400

    def test_start_not_below_end_rejected(self, api):
        client, folder, _ = api
        video = _make_video(folder)
        for bad in ({"start": 5.0, "end": 5.0}, {"start": 6.0, "end": 2.0}):
            resp = client.put("/api/clips", json={"path": video, "clips": [bad]})
            assert resp.status_code == 400
            assert "start < end" in resp.json()["detail"]

    def test_negative_start_rejected(self, api):
        client, folder, _ = api
        video = _make_video(folder)
        resp = client.put("/api/clips", json={
            "path": video, "clips": [{"start": -1.0, "end": 2.0}],
        })
        assert resp.status_code == 400

    def test_non_numeric_rejected(self, api):
        client, folder, _ = api
        video = _make_video(folder)
        for bad in (
            {"start": "zero", "end": 2.0},
            {"start": 0.0, "end": None},
            {"start": True, "end": 2.0},   # bools are not clip times
            {"end": 2.0},                   # missing start
        ):
            resp = client.put("/api/clips", json={"path": video, "clips": [bad]})
            assert resp.status_code == 400
            assert "must be a number" in resp.json()["detail"]

    def test_clip_not_an_object_rejected(self, api):
        client, folder, _ = api
        video = _make_video(folder)
        resp = client.put("/api/clips", json={"path": video, "clips": [[0.0, 1.0]]})
        assert resp.status_code == 400

    def test_valid_clip_not_persisted_after_earlier_invalid(self, api):
        client, folder, out = api
        video = _make_video(folder)
        resp = client.put("/api/clips", json={
            "path": video,
            "clips": [{"start": 0.0, "end": 1.0}, {"start": 3.0, "end": 2.0}],
        })
        assert resp.status_code == 400
        assert not (out / "user_clips.json").exists()


# ---------------------------------------------------------------------------
# PUT /api/clips — roundtrip and persistence
# ---------------------------------------------------------------------------

class TestPutClipsRoundtrip:
    def test_put_returns_clips_sorted_by_start(self, api):
        client, folder, _ = api
        video = _make_video(folder)

        resp = client.put("/api/clips", json={
            "path": video,
            "clips": [{"start": 7.5, "end": 9.0}, {"start": 1.2, "end": 5.6}],
        })

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "clips": [
            {"start": 1.2, "end": 5.6}, {"start": 7.5, "end": 9.0},
        ]}

    def test_put_writes_schema_v1_file(self, api):
        client, folder, out = api
        video = _make_video(folder)

        client.put("/api/clips", json={
            "path": video, "clips": [{"start": 1.2, "end": 5.6}],
        })

        data = _read_user_clips(out)
        assert data["schema_version"] == 1
        assert data["videos"] == {video: {"clips": [{"start": 1.2, "end": 5.6}]}}

    def test_put_overwrites_existing_entry(self, api):
        client, folder, out = api
        video = _make_video(folder)
        client.put("/api/clips", json={
            "path": video, "clips": [{"start": 0.0, "end": 3.0}],
        })

        client.put("/api/clips", json={
            "path": video, "clips": [{"start": 4.0, "end": 8.0}],
        })

        data = _read_user_clips(out)
        assert data["videos"][video]["clips"] == [{"start": 4.0, "end": 8.0}]

    def test_put_preserves_other_videos_entries(self, api):
        client, folder, out = api
        a = _make_video(folder, "a.mp4")
        b = _make_video(folder, "b.mp4")
        client.put("/api/clips", json={"path": a, "clips": [{"start": 0.0, "end": 1.0}]})

        client.put("/api/clips", json={"path": b, "clips": [{"start": 2.0, "end": 3.0}]})

        data = _read_user_clips(out)
        assert set(data["videos"]) == {a, b}

    def test_user_clips_in_api_videos(self, api):
        client, folder, _ = api
        video = _make_video(folder)
        client.put("/api/clips", json={
            "path": video, "clips": [{"start": 1.2, "end": 5.6}],
        })

        body = client.get("/api/videos").json()

        assert body["user_clips"] == {video: {"clips": [{"start": 1.2, "end": 5.6}]}}

    def test_empty_list_persisted_and_returned(self, api):
        client, folder, out = api
        video = _make_video(folder)

        resp = client.put("/api/clips", json={"path": video, "clips": []})

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "clips": []}
        assert _read_user_clips(out)["videos"][video] == {"clips": []}
        # Presence with an empty list survives into /api/videos (UI override).
        body = client.get("/api/videos").json()
        assert body["user_clips"] == {video: {"clips": []}}

    def test_missing_user_clips_file_degrades(self, api):
        client, folder, _ = api
        _make_video(folder)
        assert client.get("/api/videos").json()["user_clips"] == {}

    def test_corrupt_user_clips_file_degrades(self, api):
        client, folder, out = api
        _make_video(folder)
        (out / "user_clips.json").write_text("{not json")

        resp = client.get("/api/videos")

        assert resp.status_code == 200
        assert resp.json()["user_clips"] == {}

    def test_user_clips_filtered_to_listed_paths(self, api):
        client, folder, out = api
        video = _make_video(folder)
        (out / "user_clips.json").write_text(json.dumps({
            "schema_version": 1,
            "videos": {
                video: {"clips": [{"start": 0.0, "end": 1.0}]},
                "/videos/gone.mp4": {"clips": [{"start": 2.0, "end": 3.0}]},
            },
        }))

        body = client.get("/api/videos").json()

        assert set(body["user_clips"]) == {video}


# ---------------------------------------------------------------------------
# POST /api/clips/export — mocked ffmpeg
# ---------------------------------------------------------------------------

class _FakeRun:
    """Records ffmpeg invocations; returns success unless returncode is set."""

    def __init__(self, returncode=0, stderr=""):
        self.calls = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, self.returncode, stdout="", stderr=self.stderr
        )


@pytest.fixture()
def fake_ffmpeg(monkeypatch):
    fake = _FakeRun()
    monkeypatch.setattr(clips_mod.subprocess, "run", fake)
    monkeypatch.setattr(clips_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    return fake


class TestExportClips:
    def test_export_user_clips_reencode(self, api, fake_ffmpeg):
        client, folder, _ = api
        video = _make_video(folder, "beach.mp4")
        client.put("/api/clips", json={
            "path": video,
            "clips": [{"start": 1.2, "end": 5.6}, {"start": 7.0, "end": 9.5}],
        })

        resp = client.post("/api/clips/export", json={"path": video})

        assert resp.status_code == 200
        body = resp.json()
        clips_dir = folder.parent / "_exports" / "clips"
        assert clips_dir.is_dir()
        assert body == {"ok": True, "files": [
            str((clips_dir / "beach_c01_1p2s-5p6s.mp4").resolve()),
            str((clips_dir / "beach_c02_7p0s-9p5s.mp4").resolve()),
        ]}
        assert len(fake_ffmpeg.calls) == 2

        cmd = fake_ffmpeg.calls[0]
        # Fast seek: -ss precedes -i, then -t duration.
        assert cmd.index("-ss") < cmd.index("-i")
        assert cmd[cmd.index("-ss") + 1] == "1.200"
        assert cmd[cmd.index("-i") + 1] == video  # original file, not cache
        assert cmd[cmd.index("-t") + 1] == "4.400"
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert cmd[cmd.index("-crf") + 1] == "18"
        assert cmd[cmd.index("-preset") + 1] == "fast"
        assert cmd[cmd.index("-c:a") + 1] == "aac"
        assert cmd[cmd.index("-movflags") + 1] == "+faststart"
        assert "-y" in cmd
        assert cmd[-1].endswith("beach_c01_1p2s-5p6s.mp4")

    def test_export_copy_mode(self, api, fake_ffmpeg):
        client, folder, _ = api
        video = _make_video(folder)
        client.put("/api/clips", json={
            "path": video, "clips": [{"start": 0.0, "end": 3.0}],
        })

        resp = client.post("/api/clips/export", json={"path": video, "mode": "copy"})

        assert resp.status_code == 200
        cmd = fake_ffmpeg.calls[0]
        assert cmd[cmd.index("-c") + 1] == "copy"
        assert "-c:v" not in cmd and "libx264" not in cmd

    def test_export_bad_mode_rejected(self, api, fake_ffmpeg):
        client, folder, _ = api
        video = _make_video(folder)
        resp = client.post("/api/clips/export", json={"path": video, "mode": "wat"})
        assert resp.status_code == 400
        assert fake_ffmpeg.calls == []

    def test_export_falls_back_to_suggested_highlights(self, api, fake_ffmpeg):
        client, folder, out = api
        video = _make_video(folder, "surf.mp4")
        _write_highlights(out, {
            video: {"fingerprint": "1:1", "duration": 30.0, "clips": [
                {"start": 3.0, "end": 8.0, "score": 0.9, "scores": {}},
            ]},
        })

        resp = client.post("/api/clips/export", json={"path": video})

        assert resp.status_code == 200
        assert resp.json()["files"] == [
            str((folder.parent / "_exports" / "clips" / "surf_c01_3p0s-8p0s.mp4").resolve()),
        ]

    def test_user_clips_override_suggestions(self, api, fake_ffmpeg):
        client, folder, out = api
        video = _make_video(folder, "v.mp4")
        _write_highlights(out, {
            video: {"duration": 30.0, "clips": [
                {"start": 3.0, "end": 8.0, "score": 0.9, "scores": {}},
            ]},
        })
        client.put("/api/clips", json={
            "path": video, "clips": [{"start": 10.0, "end": 12.0}],
        })

        resp = client.post("/api/clips/export", json={"path": video})

        assert resp.json()["files"] == [
            str((folder.parent / "_exports" / "clips" / "v_c01_10p0s-12p0s.mp4").resolve()),
        ]

    def test_export_400_when_no_clips_anywhere(self, api, fake_ffmpeg):
        client, folder, _ = api
        video = _make_video(folder)

        resp = client.post("/api/clips/export", json={"path": video})

        assert resp.status_code == 400
        assert fake_ffmpeg.calls == []

    def test_export_400_when_user_explicitly_wants_none(self, api, fake_ffmpeg):
        client, folder, out = api
        video = _make_video(folder)
        # Suggestions exist, but the user's empty list overrides them.
        _write_highlights(out, {
            video: {"duration": 30.0, "clips": [
                {"start": 3.0, "end": 8.0, "score": 0.9, "scores": {}},
            ]},
        })
        client.put("/api/clips", json={"path": video, "clips": []})

        resp = client.post("/api/clips/export", json={"path": video})

        assert resp.status_code == 400
        assert fake_ffmpeg.calls == []

    def test_export_path_outside_project_rejected(self, api, fake_ffmpeg, tmp_path):
        client, _, _ = api
        outside = tmp_path / "elsewhere.mp4"
        outside.write_bytes(b"x")
        resp = client.post("/api/clips/export", json={"path": str(outside)})
        assert resp.status_code == 400

    def test_export_ffmpeg_failure_returns_500_with_stderr_tail(
        self, api, monkeypatch
    ):
        client, folder, _ = api
        video = _make_video(folder)
        client.put("/api/clips", json={
            "path": video, "clips": [{"start": 0.0, "end": 1.0}],
        })
        fake = _FakeRun(returncode=1, stderr="boom: invalid data found")
        monkeypatch.setattr(clips_mod.subprocess, "run", fake)
        monkeypatch.setattr(clips_mod.shutil, "which", lambda n: f"/usr/bin/{n}")

        resp = client.post("/api/clips/export", json={"path": video})

        assert resp.status_code == 500
        assert "boom: invalid data found" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Exported cuts excluded from video scans
# ---------------------------------------------------------------------------

class TestScanExclusion:
    def test_api_videos_skips_clips_dir(self, api):
        client, folder, _ = api
        video = _make_video(folder, "a.mp4")
        _make_video(folder, "clips/a_c01_1p2s-5p6s.mp4")

        body = client.get("/api/videos").json()

        assert body["paths"] == [video]

    def test_api_videos_keeps_nested_clips_dir(self, api):
        """Only <folder>/clips/ is the export dir; sub/clips/ is real footage."""
        client, folder, _ = api
        video = _make_video(folder, "a.mp4")
        nested = _make_video(folder, "sub/clips/real.mp4")

        body = client.get("/api/videos").json()

        assert body["paths"] == sorted([video, nested])

    def test_pipeline_scan_skips_clips_dir(self, tmp_path):
        import pipeline

        folder = tmp_path / "photos"
        keep = _make_video(folder, "a.mp4")
        nested = _make_video(folder, "sub/b.mov")
        _make_video(folder, "clips/a_c01_0p0s-1p0s.mp4")

        assert pipeline._scan_video_paths(str(folder)) == sorted([keep, nested])


# ---------------------------------------------------------------------------
# Real ffmpeg integration (one test)
# ---------------------------------------------------------------------------

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_export_real_ffmpeg_roundtrip(api):
    client, folder, _ = api
    src = folder / "test.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=128x96:rate=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src),
        ],
        check=True, capture_output=True, timeout=120,
    )
    video = str(src.resolve())
    resp = client.put("/api/clips", json={
        "path": video, "clips": [{"start": 0.5, "end": 1.5}],
    })
    assert resp.status_code == 200

    resp = client.post("/api/clips/export", json={"path": video})

    assert resp.status_code == 200, resp.json()
    files = resp.json()["files"]
    assert len(files) == 1
    out = files[0]
    assert out.endswith("test_c01_0p5s-1p5s.mp4")
    assert (folder.parent / "_exports" / "clips" / "test_c01_0p5s-1p5s.mp4").exists()
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", out,
        ],
        check=True, capture_output=True, text=True, timeout=60,
    )
    assert abs(float(probe.stdout.strip()) - 1.0) < 0.25
