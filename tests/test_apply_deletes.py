"""Tests for POST /api/apply-deletes.

Applying deletes permanently unlinks files from the primary drive, so the
mirror-verification guard is the only thing standing between a curation session
and unrecoverable data loss. These tests pin that guard down.
"""

import pytest
from fastapi.testclient import TestClient

import server
from projects import ProjectContext
from utils import FAVORITE, KEPT, TO_DELETE, load_decisions, pending_deletes, save_decisions


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """TestClient with a project on a fake primary drive and a fake mirror.

    Returns (client, folder, output_dir, mirror_folder).
    """
    primary_root = tmp_path / "h0"
    mirror_root = tmp_path / "h1" / "h0"
    folder = primary_root / "trips" / "japan"
    folder.mkdir(parents=True)
    mirror_folder = mirror_root / "trips" / "japan"
    mirror_folder.mkdir(parents=True)
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    monkeypatch.setattr(server, "PRIMARY_ROOT", primary_root)
    monkeypatch.setattr(server, "MIRROR_ROOT", mirror_root)
    monkeypatch.setattr(
        server, "_active", ProjectContext(folder=folder, output_dir=output_dir)
    )
    client = TestClient(server.app, base_url="http://localhost")
    return client, folder, output_dir, mirror_folder


def _photo(folder, mirror_folder, name, body=b"pixels", mirror_body=b"pixels"):
    """Create a photo on the primary and, unless mirror_body is None, its mirror."""
    src = folder / name
    src.write_bytes(body)
    if mirror_body is not None:
        (mirror_folder / name).write_bytes(mirror_body)
    return src


def _pending(output_dir, *paths):
    save_decisions(output_dir, {str(p): TO_DELETE for p in paths})


def _queue(output_dir):
    return sorted(pending_deletes(output_dir))


def _manifest_lines(mirror_folder):
    manifest = mirror_folder / server.MIRROR_MANIFEST_NAME
    if not manifest.exists():
        return []
    return manifest.read_text().split()


def test_deletes_file_with_matching_mirror(api):
    client, folder, output_dir, mirror_folder = api
    src = _photo(folder, mirror_folder, "a.jpg")
    _pending(output_dir, src)

    data = client.post("/api/apply-deletes").json()

    assert data["deleted"] == 1
    assert data["freed_bytes"] == len(b"pixels")
    assert not src.exists()
    assert (mirror_folder / "a.jpg").exists()  # mirror untouched
    assert _manifest_lines(mirror_folder) == ["a.jpg"]
    assert _queue(output_dir) == []


def test_keeps_file_whose_mirror_is_missing(api):
    client, folder, output_dir, mirror_folder = api
    src = _photo(folder, mirror_folder, "a.jpg", mirror_body=None)
    _pending(output_dir, src)

    data = client.post("/api/apply-deletes").json()

    assert data["deleted"] == 0
    assert data["unmirrored"] == [str(src)]
    assert src.exists()
    # Stays pending so a later run can retry once the mirror is in place.
    assert _queue(output_dir) == [str(src)]


def test_keeps_file_whose_mirror_size_differs(api):
    client, folder, output_dir, mirror_folder = api
    src = _photo(folder, mirror_folder, "a.jpg", body=b"full", mirror_body=b"trunc8ed")
    _pending(output_dir, src)

    data = client.post("/api/apply-deletes").json()

    assert data["deleted"] == 0
    assert data["unmirrored"] == [str(src)]
    assert src.exists()


def test_mixed_batch_deletes_only_verified_files(api):
    client, folder, output_dir, mirror_folder = api
    good = _photo(folder, mirror_folder, "good.jpg")
    bad = _photo(folder, mirror_folder, "bad.jpg", mirror_body=None)
    _pending(output_dir, good, bad)

    data = client.post("/api/apply-deletes").json()

    assert data["deleted"] == 1
    assert not good.exists()
    assert bad.exists()
    assert _manifest_lines(mirror_folder) == ["good.jpg"]
    assert _queue(output_dir) == [str(bad)]


def test_starred_photo_cannot_reach_the_queue(api):
    """Protection is structural: `favorite` and `to_delete` are one field, so
    there is no starred-but-queued state left for the sweep to guard against."""
    client, folder, output_dir, mirror_folder = api
    src = _photo(folder, mirror_folder, "a.jpg")
    save_decisions(output_dir, {str(src): FAVORITE})

    assert _queue(output_dir) == []
    data = client.post("/api/apply-deletes").json()

    assert data["deleted"] == 0
    assert src.exists()
    assert _manifest_lines(mirror_folder) == []
    assert load_decisions(output_dir)[str(src)] == FAVORITE


def test_starring_a_queued_photo_cancels_the_delete(api):
    client, folder, output_dir, mirror_folder = api
    src = _photo(folder, mirror_folder, "a.jpg")
    _pending(output_dir, src)

    client.post("/api/favorite", json={"path": str(src)})

    assert _queue(output_dir) == []
    assert load_decisions(output_dir)[str(src)] == FAVORITE
    client.post("/api/apply-deletes")
    assert src.exists()


def test_unmounted_drive_refuses_to_sweep(api, tmp_path):
    """An unmounted primary makes every file look gone; settling the queue on
    that basis would record the whole project as deleted."""
    client, folder, output_dir, mirror_folder = api
    src = _photo(folder, mirror_folder, "a.jpg")
    _pending(output_dir, src)
    # Simulate the drive going away: the project folder is no longer there.
    server._active = ProjectContext(folder=tmp_path / "unmounted", output_dir=output_dir)

    resp = client.post("/api/apply-deletes")

    assert resp.status_code == 409
    assert _queue(output_dir) == [str(src)]  # queue untouched
    assert load_decisions(output_dir)[str(src)] == TO_DELETE


def test_kept_photo_is_never_swept(api):
    """The live drift: a photo marked kept must not be deleted, ever."""
    client, folder, output_dir, mirror_folder = api
    src = _photo(folder, mirror_folder, "a.jpg")
    save_decisions(output_dir, {str(src): KEPT})

    data = client.post("/api/apply-deletes").json()

    assert data["deleted"] == 0
    assert src.exists()


def test_applied_delete_is_recorded_as_gone(api):
    client, folder, output_dir, mirror_folder = api
    src = _photo(folder, mirror_folder, "a.jpg")
    _pending(output_dir, src)

    client.post("/api/apply-deletes")

    assert load_decisions(output_dir)[str(src)] == "deleted"
    # A settled delete must not be re-swept or re-queued on the next run.
    assert _queue(output_dir) == []
    assert client.post("/api/apply-deletes").json()["deleted"] == 0


def test_manifest_appends_across_runs(api):
    client, folder, output_dir, mirror_folder = api
    first = _photo(folder, mirror_folder, "a.jpg")
    _pending(output_dir, first)
    client.post("/api/apply-deletes")

    second = _photo(folder, mirror_folder, "b.jpg")
    _pending(output_dir, second)
    client.post("/api/apply-deletes")

    assert _manifest_lines(mirror_folder) == ["a.jpg", "b.jpg"]


def test_missing_source_is_skipped_not_reported_as_unmirrored(api):
    client, folder, output_dir, mirror_folder = api
    ghost = folder / "gone.jpg"
    _pending(output_dir, ghost)

    data = client.post("/api/apply-deletes").json()

    assert data["skipped"] == 1
    assert data["deleted"] == 0
    assert data["unmirrored"] == []
    assert _queue(output_dir) == []


def test_file_outside_primary_root_is_never_deleted(tmp_path, monkeypatch):
    """A project that doesn't live on the primary drive has no mirror to verify."""
    folder = tmp_path / "elsewhere" / "photos"
    folder.mkdir(parents=True)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    monkeypatch.setattr(server, "PRIMARY_ROOT", tmp_path / "h0")
    monkeypatch.setattr(server, "MIRROR_ROOT", tmp_path / "h1" / "h0")
    monkeypatch.setattr(
        server, "_active", ProjectContext(folder=folder, output_dir=output_dir)
    )
    src = folder / "a.jpg"
    src.write_bytes(b"pixels")
    save_decisions(output_dir, {str(src): TO_DELETE})

    client = TestClient(server.app, base_url="http://localhost")
    data = client.post("/api/apply-deletes").json()

    assert data["deleted"] == 0
    assert data["unmirrored"] == [str(src)]
    assert src.exists()


# ---------------------------------------------------------------------------
# Raw + sidecar sweep
#
# The decision queue holds JPEGs, but the camera wrote a raw beside each one and
# editors leave .xmp files next to both. Before this, applying deletes reclaimed
# the JPEG alone — a tenth of the space on a Fujifilm trip — and orphaned the
# raw. These pin down that the whole shot goes, and that it goes all-or-nothing.
# ---------------------------------------------------------------------------


def test_deletes_raw_and_xmp_alongside_the_jpeg(api):
    client, folder, output_dir, mirror_folder = api
    jpg = _photo(folder, mirror_folder, "DSCF1.JPG")
    raw = _photo(folder, mirror_folder, "DSCF1.RAF", body=b"raw" * 100, mirror_body=b"raw" * 100)
    jpg_xmp = _photo(folder, mirror_folder, "DSCF1.JPG.xmp", body=b"<x/>", mirror_body=b"<x/>")
    raw_xmp = _photo(folder, mirror_folder, "DSCF1.RAF.xmp", body=b"<x/>", mirror_body=b"<x/>")
    _pending(output_dir, jpg)

    data = client.post("/api/apply-deletes").json()

    assert data["deleted"] == 1
    assert data["companions"] == 3
    assert not jpg.exists() and not raw.exists()
    assert not jpg_xmp.exists() and not raw_xmp.exists()
    assert data["freed_bytes"] == len(b"pixels") + 300 + 4 + 4
    assert _queue(output_dir) == []


def test_manifest_names_every_file_removed(api):
    client, folder, output_dir, mirror_folder = api
    jpg = _photo(folder, mirror_folder, "DSCF2.JPG")
    _photo(folder, mirror_folder, "DSCF2.RAF", body=b"raw", mirror_body=b"raw")
    _pending(output_dir, jpg)

    client.post("/api/apply-deletes")

    assert sorted(_manifest_lines(mirror_folder)) == ["DSCF2.JPG", "DSCF2.RAF"]


def test_unmirrored_raw_defers_the_whole_shot(api):
    """A shot half on each drive is worse than one left queued for a later run."""
    client, folder, output_dir, mirror_folder = api
    jpg = _photo(folder, mirror_folder, "DSCF3.JPG")
    raw = _photo(folder, mirror_folder, "DSCF3.RAF", body=b"raw", mirror_body=None)
    _pending(output_dir, jpg)

    data = client.post("/api/apply-deletes").json()

    assert data["deleted"] == 0
    assert data["unmirrored"] == [str(jpg)]
    assert jpg.exists() and raw.exists()
    assert _queue(output_dir) == [str(jpg)]


def test_raw_of_a_kept_photo_is_untouched(api):
    """Only the queued shot's own sidecars go — a neighbour's raw is not a companion."""
    client, folder, output_dir, mirror_folder = api
    doomed = _photo(folder, mirror_folder, "DSCF4.JPG")
    _photo(folder, mirror_folder, "DSCF4.RAF", body=b"raw", mirror_body=b"raw")
    keeper = _photo(folder, mirror_folder, "DSCF5.JPG")
    keeper_raw = _photo(folder, mirror_folder, "DSCF5.RAF", body=b"raw", mirror_body=b"raw")
    save_decisions(output_dir, {str(doomed): TO_DELETE, str(keeper): KEPT})

    client.post("/api/apply-deletes")

    assert keeper.exists() and keeper_raw.exists()


def test_replaced_spelling_xmp_is_swept(api):
    """Some editors write DSCF6.xmp rather than DSCF6.JPG.xmp."""
    client, folder, output_dir, mirror_folder = api
    jpg = _photo(folder, mirror_folder, "DSCF6.JPG")
    xmp = _photo(folder, mirror_folder, "DSCF6.xmp", body=b"<x/>", mirror_body=b"<x/>")
    _pending(output_dir, jpg)

    data = client.post("/api/apply-deletes").json()

    assert data["companions"] == 1
    assert not xmp.exists()


def test_jpeg_with_no_sidecars_still_works(api):
    client, folder, output_dir, mirror_folder = api
    src = _photo(folder, mirror_folder, "lonely.jpg")
    _pending(output_dir, src)

    data = client.post("/api/apply-deletes").json()

    assert data["deleted"] == 1 and data["companions"] == 0
    assert not src.exists()


def test_favorite_raw_survives(api):
    """A starred shot cannot be queued, so neither it nor its raw is reachable."""
    client, folder, output_dir, mirror_folder = api
    star = _photo(folder, mirror_folder, "DSCF7.JPG")
    star_raw = _photo(folder, mirror_folder, "DSCF7.RAF", body=b"raw", mirror_body=b"raw")
    save_decisions(output_dir, {str(star): FAVORITE})

    client.post("/api/apply-deletes")

    assert star.exists() and star_raw.exists()
    assert load_decisions(output_dir)[str(star)] == FAVORITE
