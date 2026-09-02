"""Tests for favourites export — webapp/exports.py and its two endpoints.

Export is not destructive, so the risks here are different from deletion's: it
must never overwrite an existing file, must not re-copy what it already
delivered, and must keep going when one file fails.
"""

import pytest
from fastapi.testclient import TestClient

import exports
import server
from exports import export_favorites, export_dir_for, plan_export
from projects import ProjectContext
from utils import FAVORITE, KEPT, TO_DELETE, save_decisions


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


def test_exports_favourite_shots_with_raw_and_sidecars(project):
    folder, output_dir, root = project
    fav = _shot(folder, "DSCF1")
    save_decisions(output_dir, {str(fav): FAVORITE})

    report = export_favorites(output_dir, folder, root)

    dest = root / "2026_01_Japan"
    assert _names(dest) == ["DSCF1.JPG", "DSCF1.JPG.xmp", "DSCF1.RAF", "DSCF1.RAF.xmp"]
    assert report.copied_bytes == 4 + 4 + 30 + 4


def test_only_favourites_are_exported(project):
    """kept and to_delete are not deliverables — only a star is."""
    folder, output_dir, root = project
    fav = _shot(folder, "DSCF1")
    kept = _shot(folder, "DSCF2")
    doomed = _shot(folder, "DSCF3")
    save_decisions(
        output_dir,
        {str(fav): FAVORITE, str(kept): KEPT, str(doomed): TO_DELETE},
    )

    export_favorites(output_dir, folder, root)

    assert _names(root / "2026_01_Japan") == [
        "DSCF1.JPG", "DSCF1.JPG.xmp", "DSCF1.RAF", "DSCF1.RAF.xmp",
    ]


def test_video_favourite_exports_as_a_single_file(project):
    """A video has no raw, so the shot grouping delivers just the original.

    Untagged, so it lands in the videos-only `untagged/` folder.
    """
    folder, output_dir, root = project
    mov = folder / "DSCF9.MOV"
    mov.write_bytes(b"video")
    save_decisions(output_dir, {str(mov): FAVORITE})

    export_favorites(output_dir, folder, root)

    assert _names(root / "2026_01_Japan" / "untagged") == ["DSCF9.MOV"]


def test_export_dir_is_named_for_the_trip(project):
    folder, _, root = project
    assert export_dir_for(folder, root) == root / "2026_01_Japan"


def test_derived_caches_are_never_exported(project):
    """Only originals: a thumb_cache copy of the same name must not be picked up."""
    folder, output_dir, root = project
    fav = _shot(folder, "DSCF1", raw=False, xmp=False)
    cache = output_dir / "thumb_cache"
    cache.mkdir()
    (cache / "DSCF1.JPG").write_bytes(b"resized")
    save_decisions(output_dir, {str(fav): FAVORITE})

    export_favorites(output_dir, folder, root)

    assert (root / "2026_01_Japan" / "DSCF1.JPG").read_bytes() == b"jpeg"


# ---------------------------------------------------------------------------
# Collisions and re-runs
# ---------------------------------------------------------------------------


def test_rerunning_skips_what_is_already_there(project):
    folder, output_dir, root = project
    fav = _shot(folder, "DSCF1")
    save_decisions(output_dir, {str(fav): FAVORITE})

    export_favorites(output_dir, folder, root)
    second = export_favorites(output_dir, folder, root)

    assert second.copied == [] and len(second.skipped) == 4
    assert second.copied_bytes == 0
    assert len(_names(root / "2026_01_Japan")) == 4


def test_same_name_different_file_is_suffixed_not_overwritten(project):
    """Two trips can hold DSCF1.JPG. The one already delivered must survive."""
    folder, output_dir, root = project
    dest = root / "2026_01_Japan"
    dest.mkdir()
    (dest / "DSCF1.JPG").write_bytes(b"from another trip")
    fav = _shot(folder, "DSCF1", raw=False, xmp=False)
    save_decisions(output_dir, {str(fav): FAVORITE})

    export_favorites(output_dir, folder, root)

    assert (dest / "DSCF1.JPG").read_bytes() == b"from another trip"
    assert (dest / "DSCF1_1.JPG").read_bytes() == b"jpeg"


def test_no_partial_files_are_left_behind(project):
    folder, output_dir, root = project
    fav = _shot(folder, "DSCF1")
    save_decisions(output_dir, {str(fav): FAVORITE})

    export_favorites(output_dir, folder, root)

    assert not any(
        n.endswith(exports.PARTIAL_SUFFIX) for n in _names(root / "2026_01_Japan")
    )


# ---------------------------------------------------------------------------
# Degrading rather than failing
# ---------------------------------------------------------------------------


def test_one_bad_file_does_not_sink_the_run(project, monkeypatch):
    folder, output_dir, root = project
    good = _shot(folder, "AAA", raw=False, xmp=False)
    bad = _shot(folder, "ZZZ", raw=False, xmp=False)
    save_decisions(output_dir, {str(good): FAVORITE, str(bad): FAVORITE})

    real_copy = exports.shutil.copy2

    def flaky(src, dst, *a, **kw):
        if "ZZZ" in str(src):
            raise OSError("Permission denied")
        return real_copy(src, dst, *a, **kw)

    monkeypatch.setattr(exports.shutil, "copy2", flaky)
    report = export_favorites(output_dir, folder, root)

    assert report.copied == ["AAA.JPG"]
    assert len(report.failed) == 1
    assert "Permission denied" in report.failed[0]["error"]


def test_missing_favourite_is_reported_not_raised(project):
    folder, output_dir, root = project
    save_decisions(output_dir, {str(folder / "gone.JPG"): FAVORITE})

    report = export_favorites(output_dir, folder, root)

    assert report.copied == []
    assert report.failed[0]["error"] == "file not found"


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_preview_counts_shots_and_files_without_copying(project):
    folder, output_dir, root = project
    fav = _shot(folder, "DSCF1")
    save_decisions(output_dir, {str(fav): FAVORITE})

    plan = plan_export(output_dir, folder, root)

    assert plan["shots"] == 1 and plan["files"] == 4
    assert plan["bytes"] == 4 + 4 + 30 + 4
    assert plan["pending"] == 4 and plan["delivered"] == 0
    assert plan["pending_bytes"] == plan["bytes"]
    assert plan["free_bytes"] > 0
    assert not (root / "2026_01_Japan").exists()


def test_preview_counts_an_earlier_run_as_delivered(project):
    folder, output_dir, root = project
    fav = _shot(folder, "DSCF1")
    save_decisions(output_dir, {str(fav): FAVORITE})
    export_favorites(output_dir, folder, root)

    plan = plan_export(output_dir, folder, root)

    # The whole set is still described, but nothing is left to copy — this is
    # what tells the finish panel step 2 is done after a page reload.
    assert plan["files"] == 4 and plan["bytes"] == 4 + 4 + 30 + 4
    assert plan["delivered"] == 4
    assert plan["pending"] == 0 and plan["pending_bytes"] == 0


def test_preview_splits_a_partly_delivered_export(project):
    folder, output_dir, root = project
    fav = _shot(folder, "DSCF1")
    save_decisions(output_dir, {str(fav): FAVORITE})
    export_favorites(output_dir, folder, root)
    _shot(folder, "DSCF2")
    save_decisions(
        output_dir,
        {str(fav): FAVORITE, str(folder / "DSCF2.JPG"): FAVORITE},
    )

    plan = plan_export(output_dir, folder, root)

    assert plan["files"] == 8
    assert plan["delivered"] == 4 and plan["pending"] == 4
    assert plan["pending_bytes"] == 4 + 4 + 30 + 4


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_endpoint_exports_and_reports(api):
    client, folder, output_dir, root = api
    fav = _shot(folder, "DSCF1", raw=False, xmp=False)
    save_decisions(output_dir, {str(fav): FAVORITE})

    data = client.post("/api/exports/favorites").json()

    assert data["ok"] and data["copied"] == 1 and data["failed"] == []
    assert data["dest"] == str(root / "2026_01_Japan")
    assert (root / "2026_01_Japan" / "DSCF1.JPG").is_file()


def test_endpoint_preview_matches_plan(api):
    client, folder, output_dir, root = api
    fav = _shot(folder, "DSCF1", raw=False, xmp=False)
    save_decisions(output_dir, {str(fav): FAVORITE})

    data = client.get("/api/exports/preview").json()

    assert data["shots"] == 1 and data["files"] == 1


def test_endpoint_refuses_when_exports_root_is_missing(api, monkeypatch, tmp_path):
    client, folder, output_dir, _ = api
    monkeypatch.setattr(server, "EXPORTS_ROOT", tmp_path / "not-mounted")
    fav = _shot(folder, "DSCF1", raw=False, xmp=False)
    save_decisions(output_dir, {str(fav): FAVORITE})

    res = client.post("/api/exports/favorites")

    assert res.status_code == 409
    assert "Exports root unavailable" in res.json()["detail"]
    assert not (tmp_path / "not-mounted").exists()


def test_endpoint_refuses_when_the_drive_is_too_full(api, monkeypatch):
    client, folder, output_dir, root = api
    fav = _shot(folder, "DSCF1", raw=False, xmp=False)
    save_decisions(output_dir, {str(fav): FAVORITE})
    monkeypatch.setattr(exports, "_free_bytes", lambda dest: 1)

    res = client.post("/api/exports/favorites")

    assert res.status_code == 409
    assert "Not enough space" in res.json()["detail"]
    assert not (root / "2026_01_Japan").exists()
