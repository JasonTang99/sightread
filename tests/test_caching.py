"""Tests for the read-path caches: results.json parsing, ETag revalidation,
and the shot-time cache.

These are pure performance work, so each test pins the observable behaviour
rather than timing: that a second call doesn't re-read, and that a change on
disk still gets through.
"""

import json
import os
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ExifTags

import pipeline
import server
import utils
from projects import ProjectContext
from utils import invalidate_results_cache, load_results


@pytest.fixture()
def api(tmp_path, monkeypatch):
    folder = tmp_path / "photos"
    folder.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    monkeypatch.setattr(
        server, "_active", ProjectContext(folder=folder, output_dir=output_dir)
    )
    return TestClient(server.app, base_url="http://localhost"), folder, output_dir


@pytest.fixture(autouse=True)
def _clear_caches():
    invalidate_results_cache()
    server._shot_times_mem.clear()
    yield
    invalidate_results_cache()
    server._shot_times_mem.clear()


def _jpeg(folder, name="a.jpg", size=(64, 48)):
    path = folder / name
    Image.new("RGB", size, (120, 30, 30)).save(path, "JPEG")
    return path


def _jpeg_with_exif_dates(folder, name, *, original=None, modified=None):
    """A JPEG whose DateTimeOriginal and DateTime deliberately disagree.

    No file in the real archive has them differ — every camera and phone here
    writes the same value to both — which is exactly why reading the wrong one
    went unnoticed. The fixture has to be synthesised.
    """
    path = folder / name
    exif = Image.Exif()
    if modified is not None:
        exif[306] = modified
    if original is not None:
        exif.get_ifd(ExifTags.IFD.Exif)[36867] = original
    Image.new("RGB", (16, 12), (10, 90, 40)).save(path, "JPEG", exif=exif)
    return path


class TestResultsCache:
    def test_second_read_reuses_the_parse(self, tmp_path):
        path = tmp_path / "results.json"
        path.write_text(json.dumps({"clusters": [{"cluster_id": 1}]}))

        first = load_results(path)
        second = load_results(path)

        assert second is first  # same object: no reparse

    def test_rewrite_invalidates(self, tmp_path):
        path = tmp_path / "results.json"
        path.write_text(json.dumps({"clusters": [{"cluster_id": 1}]}))
        first = load_results(path)

        path.write_text(json.dumps({"clusters": [{"cluster_id": 2}]}))
        second = load_results(path)

        assert second is not first
        assert second["clusters"][0]["cluster_id"] == 2

    def test_same_size_rewrite_still_invalidates(self, tmp_path):
        """Size alone can't tell these apart — the mtime half of the key must."""
        path = tmp_path / "results.json"
        path.write_text(json.dumps({"clusters": [{"cluster_id": 1}]}))
        load_results(path)

        path.write_text(json.dumps({"clusters": [{"cluster_id": 9}]}))
        assert load_results(path)["clusters"][0]["cluster_id"] == 9

    def test_explicit_invalidation_forces_a_reparse(self, tmp_path):
        path = tmp_path / "results.json"
        path.write_text(json.dumps({"clusters": []}))
        first = load_results(path)

        invalidate_results_cache(path)

        assert load_results(path) is not first

    def test_missing_file_still_raises(self, tmp_path):
        with pytest.raises(OSError):
            load_results(tmp_path / "nope.json")


class TestImageETag:
    def test_response_carries_an_etag(self, api):
        client, folder, _ = api
        img = _jpeg(folder)

        resp = client.get("/api/image", params={"path": str(img), "w": 32})

        assert resp.status_code == 200
        assert resp.headers["etag"]
        assert "max-age" in resp.headers["cache-control"]

    def test_matching_etag_gets_a_304_with_no_body(self, api):
        client, folder, _ = api
        img = _jpeg(folder)
        etag = client.get("/api/image", params={"path": str(img), "w": 32}).headers["etag"]

        resp = client.get(
            "/api/image",
            params={"path": str(img), "w": 32},
            headers={"If-None-Match": etag},
        )

        assert resp.status_code == 304
        assert resp.content == b""

    def test_weak_validator_from_a_cache_still_matches(self, api):
        client, folder, _ = api
        img = _jpeg(folder)
        etag = client.get("/api/image", params={"path": str(img), "w": 32}).headers["etag"]

        resp = client.get(
            "/api/image",
            params={"path": str(img), "w": 32},
            headers={"If-None-Match": f"W/{etag}"},
        )

        assert resp.status_code == 304

    def test_different_width_is_a_different_entity(self, api):
        client, folder, _ = api
        img = _jpeg(folder)
        etag = client.get("/api/image", params={"path": str(img), "w": 32}).headers["etag"]

        resp = client.get(
            "/api/image",
            params={"path": str(img), "w": 16},
            headers={"If-None-Match": etag},
        )

        assert resp.status_code == 200

    def test_stale_etag_gets_the_bytes(self, api):
        client, folder, _ = api
        img = _jpeg(folder)

        resp = client.get(
            "/api/image",
            params={"path": str(img), "w": 32},
            headers={"If-None-Match": '"not-the-current-one"'},
        )

        assert resp.status_code == 200
        assert resp.content

    def test_edited_source_invalidates_the_client_copy(self, api):
        client, folder, _ = api
        img = _jpeg(folder)
        etag = client.get("/api/image", params={"path": str(img), "w": 32}).headers["etag"]

        # A re-export at a different size changes both mtime and size.
        Image.new("RGB", (80, 60), (10, 200, 10)).save(img, "JPEG")

        resp = client.get(
            "/api/image",
            params={"path": str(img), "w": 32},
            headers={"If-None-Match": etag},
        )

        assert resp.status_code == 200


class TestShotTimeCache:
    def test_exif_is_read_once_per_path(self, api, monkeypatch):
        client, folder, output_dir = api
        img = _jpeg(folder)
        calls = []
        monkeypatch.setattr(
            server, "_read_shot_time", lambda p: calls.append(p) or "2026-01-01T00:00:00"
        )
        ctx = server._active

        server._get_shot_times(ctx, [str(img)])
        server._get_shot_times(ctx, [str(img)])

        assert calls == [str(img)]

    def test_new_paths_extend_an_existing_cache(self, api, monkeypatch):
        client, folder, output_dir = api
        a, b = _jpeg(folder, "a.jpg"), _jpeg(folder, "b.jpg")
        monkeypatch.setattr(server, "_read_shot_time", lambda p: f"t:{p}")
        ctx = server._active

        server._get_shot_times(ctx, [str(a)])
        result = server._get_shot_times(ctx, [str(a), str(b)])

        assert result == {str(a): f"t:{a}", str(b): f"t:{b}"}
        on_disk = json.loads((output_dir / "shot_times.json").read_text())
        assert set(on_disk) == {str(a), str(b)}

    def test_cache_written_by_another_process_is_picked_up(self, api, monkeypatch):
        client, folder, output_dir = api
        img = _jpeg(folder)
        monkeypatch.setattr(
            server, "_read_shot_time", lambda p: pytest.fail("should not re-read EXIF")
        )
        (output_dir / "shot_times.json").write_text(json.dumps({str(img): "2020-05-05T05:05:05"}))
        ctx = server._active

        assert server._get_shot_times(ctx, [str(img)]) == {str(img): "2020-05-05T05:05:05"}

    def test_unparseable_cache_is_rebuilt_not_fatal(self, api, monkeypatch):
        client, folder, output_dir = api
        img = _jpeg(folder)
        (output_dir / "shot_times.json").write_text("{ truncated")
        monkeypatch.setattr(server, "_read_shot_time", lambda p: "2021-01-01T00:00:00")
        ctx = server._active

        assert server._get_shot_times(ctx, [str(img)]) == {str(img): "2021-01-01T00:00:00"}


class TestShotTimeExif:
    """DateTimeOriginal lives in the EXIF sub-IFD, DateTime (306) in IFD0.

    Reading only IFD0 silently returns the file's last-modified time as if it
    were the capture time, so a re-exported photo sorts wrong with no error.
    """

    def test_datetime_original_wins_over_datetime(self, tmp_path):
        img = _jpeg_with_exif_dates(
            tmp_path,
            "reexported.jpg",
            original="2026:09:06 13:48:52",
            modified="2026:09:11 22:03:10",
        )

        assert server._read_shot_time(str(img)) == "2026-09-06T13:48:52"

    def test_datetime_is_used_when_original_is_absent(self, tmp_path):
        img = _jpeg_with_exif_dates(tmp_path, "old.jpg", modified="2019:04:02 08:15:00")

        assert server._read_shot_time(str(img)) == "2019-04-02T08:15:00"

    def test_falls_back_to_mtime_without_exif_dates(self, tmp_path):
        img = _jpeg_with_exif_dates(tmp_path, "bare.jpg")
        os.utime(img, (1_600_000_000, 1_600_000_000))

        expected = datetime.fromtimestamp(1_600_000_000).isoformat()
        assert server._read_shot_time(str(img)) == expected

    def test_agrees_with_the_pipeline_on_the_same_file(self, tmp_path):
        """Both code paths read the same photo; they must not disagree."""
        img = _jpeg_with_exif_dates(
            tmp_path,
            "shared.jpg",
            original="2026:01:15 09:30:00",
            modified="2026:03:01 12:00:00",
        )
        with Image.open(img) as opened:
            from_pipeline = pipeline._parse_exif_timestamp(opened.getexif())

        assert from_pipeline is not None
        assert server._read_shot_time(str(img)) == datetime.fromtimestamp(from_pipeline).isoformat()
