"""Tests for scripts/delete_marked.py.

The script permanently unlinks files from the primary drive, so the
mirror-verification guard is what keeps a stale queue from destroying the only
copy of a photo. These tests pin that guard down, and match the coverage of the
equivalent server endpoint in tests/test_apply_deletes.py.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from utils import FAVORITE, KEPT, TO_DELETE, load_decisions, pending_deletes, save_decisions

_SPEC = importlib.util.spec_from_file_location(
    "delete_marked", Path(__file__).parent.parent / "scripts" / "delete_marked.py"
)
delete_marked = importlib.util.module_from_spec(_SPEC)
sys.modules["delete_marked"] = delete_marked
_SPEC.loader.exec_module(delete_marked)


@pytest.fixture()
def drives(tmp_path, monkeypatch):
    """Fake primary and mirror drives. Returns (folder, mirror_folder, output_dir)."""
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
    return folder, mirror_folder, out


def _photo(folder, mirror_folder, name, body=b"pixels", mirror_body=b"pixels"):
    src = folder / name
    src.write_bytes(body)
    if mirror_body is not None:
        (mirror_folder / name).write_bytes(mirror_body)
    return src


def _pending(out, *paths):
    save_decisions(out, {str(p): TO_DELETE for p in paths})


def _queue(out):
    return sorted(pending_deletes(out))


def _manifest_lines(mirror_folder):
    manifest = mirror_folder / delete_marked.MIRROR_MANIFEST_NAME
    return manifest.read_text().split() if manifest.exists() else []


def _run(out, **kw):
    delete_marked.delete_marked(str(out), **kw)


def test_deletes_file_with_matching_mirror(drives):
    folder, mirror_folder, out = drives
    src = _photo(folder, mirror_folder, "a.jpg")
    _pending(out, src)

    _run(out)

    assert not src.exists()
    assert (mirror_folder / "a.jpg").exists()  # mirror untouched
    assert _manifest_lines(mirror_folder) == ["a.jpg"]
    assert _queue(out) == []
    # The record settles as gone rather than vanishing entirely.
    assert load_decisions(out)[str(src)] == "deleted"


def test_dry_run_deletes_nothing(drives):
    folder, mirror_folder, out = drives
    src = _photo(folder, mirror_folder, "a.jpg")
    _pending(out, src)

    _run(out, dry_run=True)

    assert src.exists()
    assert _manifest_lines(mirror_folder) == []
    assert _queue(out) == [str(src)]


def test_keeps_file_whose_mirror_is_missing(drives):
    folder, mirror_folder, out = drives
    src = _photo(folder, mirror_folder, "a.jpg", mirror_body=None)
    _pending(out, src)

    _run(out)

    assert src.exists()
    # Stays pending so a later run can retry once the mirror is in place.
    assert _queue(out) == [str(src)]


def test_keeps_file_whose_mirror_size_differs(drives):
    folder, mirror_folder, out = drives
    src = _photo(folder, mirror_folder, "a.jpg", body=b"full", mirror_body=b"trunc8ed")
    _pending(out, src)

    _run(out)

    assert src.exists()
    assert _manifest_lines(mirror_folder) == []


def test_mixed_batch_deletes_only_verified_files(drives):
    folder, mirror_folder, out = drives
    good = _photo(folder, mirror_folder, "good.jpg")
    bad = _photo(folder, mirror_folder, "bad.jpg", mirror_body=None)
    _pending(out, good, bad)

    _run(out)

    assert not good.exists()
    assert bad.exists()
    assert _manifest_lines(mirror_folder) == ["good.jpg"]
    assert _queue(out) == [str(bad)]


def test_starred_photo_cannot_reach_the_queue(drives):
    """Protection is structural: `favorite` and `to_delete` are one field."""
    folder, mirror_folder, out = drives
    src = _photo(folder, mirror_folder, "a.jpg")
    save_decisions(out, {str(src): FAVORITE})

    assert _queue(out) == []
    _run(out)

    assert src.exists()
    assert _manifest_lines(mirror_folder) == []
    assert load_decisions(out)[str(src)] == FAVORITE


def test_kept_photo_is_never_swept(drives):
    """The live drift: a photo marked kept must not be deleted, ever."""
    folder, mirror_folder, out = drives
    src = _photo(folder, mirror_folder, "a.jpg")
    save_decisions(out, {str(src): KEPT})

    _run(out)

    assert src.exists()
    assert _manifest_lines(mirror_folder) == []


def test_file_outside_primary_root_is_never_deleted(drives, tmp_path):
    _folder, mirror_folder, out = drives
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    src = outside / "a.jpg"
    src.write_bytes(b"pixels")
    _pending(out, src)

    _run(out)

    assert src.exists()
    assert _queue(out) == [str(src)]


def test_root_guard_skips_paths_outside_project(drives, tmp_path):
    folder, mirror_folder, out = drives
    src = _photo(folder, mirror_folder, "a.jpg")
    _pending(out, src)

    _run(out, root=str(tmp_path / "somewhere_else"))

    assert src.exists()
    assert _manifest_lines(mirror_folder) == []


def test_missing_source_is_skipped(drives):
    folder, mirror_folder, out = drives
    _pending(out, folder / "gone.jpg")

    _run(out)

    # Settled as gone rather than re-queued forever.
    assert _queue(out) == []
    assert _manifest_lines(mirror_folder) == []


def test_manifest_appends_across_runs(drives):
    folder, mirror_folder, out = drives
    first = _photo(folder, mirror_folder, "a.jpg")
    _pending(out, first)
    _run(out)

    second = _photo(folder, mirror_folder, "b.jpg")
    _pending(out, second)
    _run(out)

    assert _manifest_lines(mirror_folder) == ["a.jpg", "b.jpg"]


def test_subdirectories_get_their_own_manifest(drives):
    folder, mirror_folder, out = drives
    (folder / "day1").mkdir()
    (mirror_folder / "day1").mkdir()
    nested = _photo(folder / "day1", mirror_folder / "day1", "a.jpg")
    top = _photo(folder, mirror_folder, "b.jpg")
    _pending(out, nested, top)

    _run(out)

    assert _manifest_lines(mirror_folder / "day1") == ["a.jpg"]
    assert _manifest_lines(mirror_folder) == ["b.jpg"]


def test_unmounted_drive_refuses_to_run(drives, tmp_path, monkeypatch):
    """An unmounted primary makes every file look gone; settling the queue on
    that basis would record intact photos as destroyed."""
    folder, mirror_folder, out = drives
    src = _photo(folder, mirror_folder, "a.jpg")
    _pending(out, src)
    monkeypatch.setattr(delete_marked, "PRIMARY_ROOT", tmp_path / "unmounted")

    with pytest.raises(SystemExit):
        _run(out)

    assert src.exists()
    assert _queue(out) == [str(src)]  # queue untouched


def test_legacy_queue_is_migrated_before_sweeping(drives):
    """A stale to_delete.txt must go through the merge rules, not straight to unlink."""
    folder, mirror_folder, out = drives
    kept = _photo(folder, mirror_folder, "keep.jpg")
    doomed = _photo(folder, mirror_folder, "doomed.jpg")
    # Legacy state: both queued, but one was explicitly decided "kept".
    (out / "to_delete.txt").write_text(f"{kept}\n{doomed}\n")
    (out / "decisions.json").write_text(
        '{"1": {"kept": ["%s"], "deleted": ["%s"]}}' % (kept, doomed)
    )

    _run(out)

    assert kept.exists()  # keep outranks the stale queue entry
    assert not doomed.exists()
    assert _manifest_lines(mirror_folder) == ["doomed.jpg"]
