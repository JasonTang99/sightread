"""Tests for trip export — webapp/exports.py and its two endpoints.

Export is not destructive, so the risks here are different from deletion's: it
must never overwrite an existing file, must not re-deliver what it already
delivered, and must keep going when one file fails. Since it hardlinks wherever
the filesystem allows, "delivered" also has to mean the same thing whether the
earlier run linked or copied.
"""

import os

import pytest
from fastapi.testclient import TestClient

import exports
import server
from exports import export_trip, export_dir_for, export_shots, plan_export
from projects import ProjectContext
from utils import DELETED, FAVORITE, KEPT, TO_DELETE, save_decisions


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A project folder plus an exports root. Returns (folder, output_dir, root)."""
    folder = tmp_path / "trips" / "2026_01_Japan"
    folder.mkdir(parents=True)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    root = tmp_path / "exports"
    root.mkdir()
    return folder, output_dir, root


@pytest.fixture()
def api(project, monkeypatch):
    folder, output_dir, root = project
    monkeypatch.setattr(exports, "EXPORTS_ROOT", root)
    monkeypatch.setattr(server, "EXPORTS_ROOT", root)
    monkeypatch.setattr(
        server, "_active", ProjectContext(folder=folder, output_dir=output_dir)
    )
    client = TestClient(server.app, base_url="http://localhost")
    return client, folder, output_dir, root


@pytest.fixture()
def no_links(monkeypatch):
    """Force the copy path, standing in for a cross-filesystem exports root."""

    def refuse(src, dst, **kw):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(exports.os, "link", refuse)
    monkeypatch.setattr(exports, "_link_capable", lambda folder, dest: False)


def _shot(folder, stem, *, raw=True, xmp=True, body=b"jpeg"):
    """Write a JPEG and, by default, its raw and both .xmp sidecars."""
    jpg = folder / f"{stem}.JPG"
    jpg.write_bytes(body)
    if raw:
        (folder / f"{stem}.RAF").write_bytes(b"raw" * 10)
    if xmp:
        (folder / f"{stem}.JPG.xmp").write_bytes(b"<x/>")
        if raw:
            (folder / f"{stem}.RAF.xmp").write_bytes(b"<x/>")
    return jpg


def _names(d):
    return sorted(p.name for p in d.iterdir())


# ---------------------------------------------------------------------------
# What gets exported
# ---------------------------------------------------------------------------


def test_exports_the_jpeg_alone(project):
    """The raw and the sidecars stay in the import folder."""
    folder, output_dir, root = project
    _shot(folder, "DSCF1")

    export_trip(output_dir, folder, root)

    assert _names(root / "2026_01_Japan") == ["DSCF1.JPG"]


def test_every_surviving_photo_is_exported(project):
    """Stars pick what to cut with, not what to keep — undecided photos ship too."""
    folder, output_dir, root = project
    fav = _shot(folder, "DSCF1", raw=False, xmp=False)
    kept = _shot(folder, "DSCF2", raw=False, xmp=False)
    _shot(folder, "DSCF3", raw=False, xmp=False)  # never reviewed
    save_decisions(output_dir, {str(fav): FAVORITE, str(kept): KEPT})

    export_trip(output_dir, folder, root)

    assert _names(root / "2026_01_Japan") == ["DSCF1.JPG", "DSCF2.JPG", "DSCF3.JPG"]


def test_photos_marked_for_deletion_are_not_exported(project):
    """Only an explicit delete takes a photo out of the deliverable set."""
    folder, output_dir, root = project
    keep = _shot(folder, "DSCF1", raw=False, xmp=False)
    doomed = _shot(folder, "DSCF2", raw=False, xmp=False)
    gone = _shot(folder, "DSCF3", raw=False, xmp=False)
    save_decisions(
        output_dir, {str(doomed): TO_DELETE, str(gone): DELETED, str(keep): KEPT}
    )

    export_trip(output_dir, folder, root)

    assert _names(root / "2026_01_Japan") == ["DSCF1.JPG"]


def test_only_starred_videos_are_exported(project):
    """A trip holds far more footage than an edit uses, so a star is required."""
    folder, output_dir, root = project
    starred = folder / "DSCF9.MOV"
    starred.write_bytes(b"video")
    ignored = folder / "DSCF8.MOV"
    ignored.write_bytes(b"video")
    save_decisions(output_dir, {str(starred): FAVORITE})

    export_trip(output_dir, folder, root)

    # Untagged, so it lands in the videos-only `untagged/` folder.
    assert _names(root / "2026_01_Japan" / "untagged") == ["DSCF9.MOV"]


def test_exported_clips_are_not_re_exported(project):
    """<folder>/clips/ holds cuts this app wrote, not camera footage."""
    folder, output_dir, root = project
    clips = folder / "clips"
    clips.mkdir()
    cut = clips / "DSCF9_001.MP4"
    cut.write_bytes(b"cut")
    save_decisions(output_dir, {str(cut): FAVORITE})

    assert export_shots(output_dir, folder) == []


def test_export_dir_is_named_for_the_trip(project):
    folder, _, root = project
    assert export_dir_for(folder, root) == root / "2026_01_Japan"


def test_derived_caches_are_never_exported(project):
    """Only originals: a thumb_cache copy of the same name must not be picked up."""
    folder, output_dir, root = project
    _shot(folder, "DSCF1", raw=False, xmp=False)
    cache = output_dir / "thumb_cache"
    cache.mkdir()
    (cache / "DSCF1.JPG").write_bytes(b"resized")

    export_trip(output_dir, folder, root)

    assert (root / "2026_01_Japan" / "DSCF1.JPG").read_bytes() == b"jpeg"


# ---------------------------------------------------------------------------
# Links vs copies
# ---------------------------------------------------------------------------


def test_same_filesystem_export_hardlinks(project):
    """Same drive: the delivered file is the original under another name."""
    folder, output_dir, root = project
    src = _shot(folder, "DSCF1", raw=False, xmp=False)

    report = export_trip(output_dir, folder, root)

    dest = root / "2026_01_Japan" / "DSCF1.JPG"
    assert os.path.samefile(src, dest)
    assert report.linked == ["DSCF1.JPG"] and report.copied == []
    assert report.copied_bytes == 0


def test_export_survives_the_original_being_deleted(project):
    """A hardlink is not a shortcut: the data outlives the import folder's name."""
    folder, output_dir, root = project
    src = _shot(folder, "DSCF1", raw=False, xmp=False)
    export_trip(output_dir, folder, root)

    src.unlink()

    assert (root / "2026_01_Japan" / "DSCF1.JPG").read_bytes() == b"jpeg"


def test_cross_filesystem_export_copies(project, no_links):
    """No hardlinks available: fall back to writing the bytes."""
    folder, output_dir, root = project
    src = _shot(folder, "DSCF1", raw=False, xmp=False)

    report = export_trip(output_dir, folder, root)

    dest = root / "2026_01_Japan" / "DSCF1.JPG"
    assert dest.read_bytes() == b"jpeg" and not os.path.samefile(src, dest)
    assert report.copied == ["DSCF1.JPG"] and report.linked == []
    assert report.copied_bytes == 4


def test_plan_reports_the_mode(project, monkeypatch):
    folder, output_dir, root = project
    _shot(folder, "DSCF1", raw=False, xmp=False)

    assert plan_export(output_dir, folder, root)["mode"] == "link"

    monkeypatch.setattr(exports, "_link_capable", lambda folder, dest: False)
    assert plan_export(output_dir, folder, root)["mode"] == "copy"


# ---------------------------------------------------------------------------
# Collisions and re-runs
# ---------------------------------------------------------------------------


def test_rerunning_skips_what_is_already_there(project):
    folder, output_dir, root = project
    _shot(folder, "DSCF1", raw=False, xmp=False)

    export_trip(output_dir, folder, root)
    second = export_trip(output_dir, folder, root)

    assert second.linked == [] and second.copied == []
    assert second.skipped == ["DSCF1.JPG"]
    assert len(_names(root / "2026_01_Japan")) == 1


def test_a_copied_export_is_not_relinked_on_the_next_run(project, monkeypatch):
    """The size check recognises an earlier copy as delivered, links or not."""
    folder, output_dir, root = project
    _shot(folder, "DSCF1", raw=False, xmp=False)

    def refuse(src, dst, **kw):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(exports.os, "link", refuse)
    export_trip(output_dir, folder, root)
    monkeypatch.undo()

    assert export_trip(output_dir, folder, root).skipped == ["DSCF1.JPG"]


def test_same_name_different_file_is_suffixed_not_overwritten(project):
    """Two trips can hold DSCF1.JPG. The one already delivered must survive."""
    folder, output_dir, root = project
    dest = root / "2026_01_Japan"
    dest.mkdir()
    (dest / "DSCF1.JPG").write_bytes(b"from another trip")
    _shot(folder, "DSCF1", raw=False, xmp=False)

    export_trip(output_dir, folder, root)

    assert (dest / "DSCF1.JPG").read_bytes() == b"from another trip"
    assert (dest / "DSCF1_1.JPG").read_bytes() == b"jpeg"


def test_no_partial_files_are_left_behind(project, no_links):
    folder, output_dir, root = project
    _shot(folder, "DSCF1", raw=False, xmp=False)

    export_trip(output_dir, folder, root)

    assert not any(
        n.endswith(exports.PARTIAL_SUFFIX) for n in _names(root / "2026_01_Japan")
    )


# ---------------------------------------------------------------------------
# Degrading rather than failing
# ---------------------------------------------------------------------------


def test_one_bad_file_does_not_sink_the_run(project, no_links, monkeypatch):
    folder, output_dir, root = project
    _shot(folder, "AAA", raw=False, xmp=False)
    _shot(folder, "ZZZ", raw=False, xmp=False)

    real_copy = exports.shutil.copy2

    def flaky(src, dst, *a, **kw):
        if "ZZZ" in str(src):
            raise OSError("Permission denied")
        return real_copy(src, dst, *a, **kw)

    monkeypatch.setattr(exports.shutil, "copy2", flaky)
    report = export_trip(output_dir, folder, root)

    assert report.copied == ["AAA.JPG"]
    assert len(report.failed) == 1
    assert "Permission denied" in report.failed[0]["error"]


def test_missing_favourite_is_reported_not_raised(project):
    """The export walks the disk, so a starred file that is gone must be named."""
    folder, output_dir, root = project
    save_decisions(output_dir, {str(folder / "gone.MOV"): FAVORITE})

    report = export_trip(output_dir, folder, root)

    assert report.linked == [] and report.copied == []
    assert report.failed[0]["error"] == "file not found"


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_preview_counts_files_without_delivering(project):
    folder, output_dir, root = project
    _shot(folder, "DSCF1")

    plan = plan_export(output_dir, folder, root)

    assert plan["shots"] == 1 and plan["files"] == 1
    assert plan["bytes"] == 4
    assert plan["pending"] == 1 and plan["delivered"] == 0
    assert plan["pending_bytes"] == plan["bytes"]
    assert plan["free_bytes"] > 0
    assert not (root / "2026_01_Japan").exists()


def test_preview_counts_an_earlier_run_as_delivered(project):
    folder, output_dir, root = project
    _shot(folder, "DSCF1", raw=False, xmp=False)
    export_trip(output_dir, folder, root)

    plan = plan_export(output_dir, folder, root)

    # The whole set is still described, but nothing is left to deliver — this is
    # what tells the finish panel step 2 is done after a page reload.
    assert plan["files"] == 1 and plan["bytes"] == 4
    assert plan["delivered"] == 1
    assert plan["pending"] == 0 and plan["pending_bytes"] == 0


def test_preview_splits_a_partly_delivered_export(project):
    folder, output_dir, root = project
    _shot(folder, "DSCF1", raw=False, xmp=False)
    export_trip(output_dir, folder, root)
    _shot(folder, "DSCF2", raw=False, xmp=False)

    plan = plan_export(output_dir, folder, root)

    assert plan["files"] == 2
    assert plan["delivered"] == 1 and plan["pending"] == 1
    assert plan["pending_bytes"] == 4


def test_preview_names_starred_files_that_are_gone(project):
    folder, output_dir, root = project
    save_decisions(output_dir, {str(folder / "gone.JPG"): FAVORITE})

    assert plan_export(output_dir, folder, root)["missing"] == [
        str(folder / "gone.JPG")
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_endpoint_exports_and_reports(api):
    client, folder, output_dir, root = api
    _shot(folder, "DSCF1", raw=False, xmp=False)

    data = client.post("/api/exports/trip").json()

    assert data["ok"] and data["delivered"] == 1 and data["failed"] == []
    assert data["linked"] == 1 and data["copied"] == 0
    assert data["dest"] == str(root / "2026_01_Japan")
    assert (root / "2026_01_Japan" / "DSCF1.JPG").is_file()


def test_endpoint_preview_matches_plan(api):
    client, folder, output_dir, root = api
    _shot(folder, "DSCF1", raw=False, xmp=False)

    data = client.get("/api/exports/preview").json()

    assert data["shots"] == 1 and data["files"] == 1 and data["mode"] == "link"


def test_endpoint_refuses_when_exports_root_is_missing(api, monkeypatch, tmp_path):
    client, folder, output_dir, _ = api
    monkeypatch.setattr(server, "EXPORTS_ROOT", tmp_path / "not-mounted")
    _shot(folder, "DSCF1", raw=False, xmp=False)

    res = client.post("/api/exports/trip")

    assert res.status_code == 409
    assert "Exports root unavailable" in res.json()["detail"]
    assert not (tmp_path / "not-mounted").exists()


def test_endpoint_refuses_when_the_drive_is_too_full(api, no_links, monkeypatch):
    """Only when copying — a hardlinked export writes nothing to fill the drive."""
    client, folder, output_dir, root = api
    _shot(folder, "DSCF1", raw=False, xmp=False)
    monkeypatch.setattr(exports, "_free_bytes", lambda dest: 1)

    res = client.post("/api/exports/trip")

    assert res.status_code == 409
    assert "Not enough space" in res.json()["detail"]
    assert not (root / "2026_01_Japan").exists()


def test_endpoint_exports_when_linking_even_if_the_drive_is_full(api, monkeypatch):
    client, folder, output_dir, root = api
    _shot(folder, "DSCF1", raw=False, xmp=False)
    monkeypatch.setattr(exports, "_free_bytes", lambda dest: 1)

    data = client.post("/api/exports/trip").json()

    assert data["linked"] == 1
