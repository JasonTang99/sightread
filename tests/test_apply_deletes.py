"""Tests for POST /api/apply-deletes.

Applying deletes permanently unlinks files from the primary drive, so the
mirror-verification guard is the only thing standing between a curation session
and unrecoverable data loss. These tests pin that guard down.
"""

import pytest
from fastapi.testclient import TestClient

import server
from projects import ProjectContext


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
    (output_dir / "to_delete.txt").write_text("".join(f"{p}\n" for p in paths))


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
    assert (output_dir / "to_delete.txt").read_text().strip() == ""


def test_keeps_file_whose_mirror_is_missing(api):
    client, folder, output_dir, mirror_folder = api
    src = _photo(folder, mirror_folder, "a.jpg", mirror_body=None)
    _pending(output_dir, src)

    data = client.post("/api/apply-deletes").json()

    assert data["deleted"] == 0
    assert data["unmirrored"] == [str(src)]
    assert src.exists()
    # Stays pending so a later run can retry once the mirror is in place.
    assert (output_dir / "to_delete.txt").read_text().strip() == str(src)


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
    assert (output_dir / "to_delete.txt").read_text().strip() == str(bad)


def test_favorites_are_never_deleted(api):
    client, folder, output_dir, mirror_folder = api
    src = _photo(folder, mirror_folder, "a.jpg")
    _pending(output_dir, src)
    (output_dir / "favorites.json").write_text(f'["{src}"]')

    data = client.post("/api/apply-deletes").json()

    assert data["deleted"] == 0
    # Starred skips are reported rather than silently dropped, so the count in the
    # UI accounts for every entry that left the pending list.
    assert data["starred"] == 1
    assert src.exists()
    assert _manifest_lines(mirror_folder) == []


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
    assert (output_dir / "to_delete.txt").read_text().strip() == ""


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
    (output_dir / "to_delete.txt").write_text(f"{src}\n")

    client = TestClient(server.app, base_url="http://localhost")
    data = client.post("/api/apply-deletes").json()

    assert data["deleted"] == 0
    assert data["unmirrored"] == [str(src)]
    assert src.exists()
