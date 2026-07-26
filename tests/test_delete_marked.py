"""Tests for scripts/delete_marked.py.

The script permanently unlinks files from the primary drive, so the
mirror-verification guard is what keeps a stale delete list from destroying the
only copy of a photo. These tests pin that guard down, and match the coverage of
the equivalent server endpoint in tests/test_apply_deletes.py.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "delete_marked", Path(__file__).parent.parent / "scripts" / "delete_marked.py"
)
delete_marked = importlib.util.module_from_spec(_SPEC)
sys.modules["delete_marked"] = delete_marked
_SPEC.loader.exec_module(delete_marked)


@pytest.fixture()
def drives(tmp_path, monkeypatch):
    """Fake primary and mirror drives. Returns (folder, mirror_folder, delete_file)."""
    primary_root = tmp_path / "h0"
    mirror_root = tmp_path / "h1" / "h0"
    folder = primary_root / "trips" / "japan"
    folder.mkdir(parents=True)
    mirror_folder = mirror_root / "trips" / "japan"
    mirror_folder.mkdir(parents=True)
    out = tmp_path / "out"
    out.mkdir()

    monkeypatch.setattr(delete_marked, "PRIMARY_ROOT", primary_root)
    monkeypatch.setattr(delete_marked, "MIRROR_ROOT", mirror_root)
    return folder, mirror_folder, out / "to_delete.txt"


def _photo(folder, mirror_folder, name, body=b"pixels", mirror_body=b"pixels"):
    src = folder / name
    src.write_bytes(body)
    if mirror_body is not None:
        (mirror_folder / name).write_bytes(mirror_body)
    return src


def _pending(delete_file, *paths):
    delete_file.write_text("".join(f"{p}\n" for p in paths))


def _manifest_lines(mirror_folder):
    manifest = mirror_folder / delete_marked.MIRROR_MANIFEST_NAME
    return manifest.read_text().split() if manifest.exists() else []


def _run(delete_file, **kw):
    delete_marked.delete_marked(str(delete_file), **kw)


def test_deletes_file_with_matching_mirror(drives):
    folder, mirror_folder, delete_file = drives
    src = _photo(folder, mirror_folder, "a.jpg")
    _pending(delete_file, src)

    _run(delete_file)

    assert not src.exists()
    assert (mirror_folder / "a.jpg").exists()  # mirror untouched
    assert _manifest_lines(mirror_folder) == ["a.jpg"]
    assert delete_file.read_text().strip() == ""


def test_dry_run_deletes_nothing(drives):
    folder, mirror_folder, delete_file = drives
    src = _photo(folder, mirror_folder, "a.jpg")
    _pending(delete_file, src)

    _run(delete_file, dry_run=True)

    assert src.exists()
    assert _manifest_lines(mirror_folder) == []
    assert delete_file.read_text().strip() == str(src)


def test_keeps_file_whose_mirror_is_missing(drives):
    folder, mirror_folder, delete_file = drives
    src = _photo(folder, mirror_folder, "a.jpg", mirror_body=None)
    _pending(delete_file, src)

    _run(delete_file)

    assert src.exists()
    # Stays pending so a later run can retry once the mirror is in place.
    assert delete_file.read_text().strip() == str(src)


def test_keeps_file_whose_mirror_size_differs(drives):
    folder, mirror_folder, delete_file = drives
    src = _photo(folder, mirror_folder, "a.jpg", body=b"full", mirror_body=b"trunc8ed")
    _pending(delete_file, src)

    _run(delete_file)

    assert src.exists()
    assert _manifest_lines(mirror_folder) == []


def test_mixed_batch_deletes_only_verified_files(drives):
    folder, mirror_folder, delete_file = drives
    good = _photo(folder, mirror_folder, "good.jpg")
    bad = _photo(folder, mirror_folder, "bad.jpg", mirror_body=None)
    _pending(delete_file, good, bad)

    _run(delete_file)

    assert not good.exists()
    assert bad.exists()
    assert _manifest_lines(mirror_folder) == ["good.jpg"]
    assert delete_file.read_text().strip() == str(bad)


def test_favorites_are_never_deleted(drives):
    folder, mirror_folder, delete_file = drives
    src = _photo(folder, mirror_folder, "a.jpg")
    _pending(delete_file, src)
    (delete_file.parent / "favorites.json").write_text(f'["{src}"]')

    _run(delete_file)

    assert src.exists()
    assert _manifest_lines(mirror_folder) == []


def test_file_outside_primary_root_is_never_deleted(drives, tmp_path):
    _folder, mirror_folder, delete_file = drives
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    src = outside / "a.jpg"
    src.write_bytes(b"pixels")
    _pending(delete_file, src)

    _run(delete_file)

    assert src.exists()
    assert delete_file.read_text().strip() == str(src)


def test_root_guard_skips_paths_outside_project(drives, tmp_path):
    folder, mirror_folder, delete_file = drives
    src = _photo(folder, mirror_folder, "a.jpg")
    _pending(delete_file, src)

    _run(delete_file, root=str(tmp_path / "somewhere_else"))

    assert src.exists()
    assert _manifest_lines(mirror_folder) == []


def test_missing_source_is_skipped(drives):
    folder, mirror_folder, delete_file = drives
    _pending(delete_file, folder / "gone.jpg")

    _run(delete_file)

    assert delete_file.read_text().strip() == ""
    assert _manifest_lines(mirror_folder) == []


def test_manifest_appends_across_runs(drives):
    folder, mirror_folder, delete_file = drives
    first = _photo(folder, mirror_folder, "a.jpg")
    _pending(delete_file, first)
    _run(delete_file)

    second = _photo(folder, mirror_folder, "b.jpg")
    _pending(delete_file, second)
    _run(delete_file)

    assert _manifest_lines(mirror_folder) == ["a.jpg", "b.jpg"]


def test_subdirectories_get_their_own_manifest(drives):
    folder, mirror_folder, delete_file = drives
    (folder / "day1").mkdir()
    (mirror_folder / "day1").mkdir()
    nested = _photo(folder / "day1", mirror_folder / "day1", "a.jpg")
    top = _photo(folder, mirror_folder, "b.jpg")
    _pending(delete_file, nested, top)

    _run(delete_file)

    assert _manifest_lines(mirror_folder / "day1") == ["a.jpg"]
    assert _manifest_lines(mirror_folder) == ["b.jpg"]
