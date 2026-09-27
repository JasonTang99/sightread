"""Tests for scripts/sweep_orphan_caches.py.

The script deletes from the derived caches, so the thing worth pinning down is
what it considers *reachable*. Two ways it could go wrong, and both are cheap to
get wrong silently: an absent source folder makes every key unreachable and would
hand back the whole cache as garbage, and a width that is in use but missing from
the whitelist makes a live thumbnail look dead.
"""

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "sweep_orphan_caches",
    Path(__file__).parent.parent / "scripts" / "sweep_orphan_caches.py",
)
sweep = importlib.util.module_from_spec(_SPEC)
sys.modules["sweep_orphan_caches"] = sweep
_SPEC.loader.exec_module(sweep)


def _key(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()


@pytest.fixture()
def project(tmp_path):
    """A source folder with one photo and one video, plus an empty output dir."""
    folder = tmp_path / "h0" / "trips" / "japan"
    folder.mkdir(parents=True)
    photo = folder / "DSCF0001.jpg"
    photo.write_bytes(b"jpeg")
    video = folder / "DSCF0002.mov"
    video.write_bytes(b"mov")
    out = tmp_path / "out"
    for name in ("video_cache", "poster_cache", "thumb_cache"):
        (out / name).mkdir(parents=True)
    return folder, out, photo, video


def test_current_files_are_reachable_and_survive(project):
    folder, out, photo, video = project
    keys = sweep.reachable_keys(folder, (800,))

    live_thumb = out / "thumb_cache" / f"{_key(photo, photo.stat().st_mtime_ns, 800)}.jpg"
    live_thumb.write_bytes(b"x")
    live_video = out / "video_cache" / f"{_key(video.resolve(), video.stat().st_mtime_ns)}.mp4"
    live_video.write_bytes(b"x")

    assert sweep.orphans_in(out / "thumb_cache", keys["thumb_cache"]) == ([], 0)
    assert sweep.orphans_in(out / "video_cache", keys["video_cache"]) == ([], 0)


def test_a_stale_key_is_an_orphan(project):
    """A key that no current file produces — a deleted photo, a changed mtime, or
    the width that moved from 600 to 800 in 09609f4."""
    folder, out, photo, _ = project
    keys = sweep.reachable_keys(folder, (800,))

    dead = out / "thumb_cache" / f"{_key(photo, photo.stat().st_mtime_ns, 600)}.jpg"
    dead.write_bytes(b"stale")

    found, total = sweep.orphans_in(out / "thumb_cache", keys["thumb_cache"])
    assert found == [dead]
    assert total == len(b"stale")


def test_a_width_still_in_use_is_not_an_orphan(project):
    """The whitelist is the only thing standing between a live 600px cache and
    deletion, so widening it has to make the same file reachable again."""
    folder, out, photo, _ = project
    entry = out / "thumb_cache" / f"{_key(photo, photo.stat().st_mtime_ns, 600)}.jpg"
    entry.write_bytes(b"x")

    narrow = sweep.reachable_keys(folder, (800,))
    wide = sweep.reachable_keys(folder, (600, 800))
    assert sweep.orphans_in(out / "thumb_cache", narrow["thumb_cache"])[0] == [entry]
    assert sweep.orphans_in(out / "thumb_cache", wide["thumb_cache"]) == ([], 0)


def test_an_absent_source_folder_deletes_nothing(project, monkeypatch, capsys):
    """The failure that would cost the most: an unmounted drive makes every key
    unreachable, and a sweep that trusted that would empty the cache. Skip loudly
    instead."""
    folder, out, photo, _ = project
    entry = out / "thumb_cache" / f"{_key(photo, photo.stat().st_mtime_ns, 800)}.jpg"
    entry.write_bytes(b"x")

    monkeypatch.setattr(sweep.P, "_folder_for_output_dir", lambda d: folder)
    monkeypatch.setattr(sweep, "PRIMARY_ROOT", folder.parents[1])
    for f in (photo, folder / "DSCF0002.mov"):
        f.unlink()
    folder.rmdir()

    count, freed = sweep.sweep_project(
        out, ("video_cache", "poster_cache", "thumb_cache"), (800,), apply=True
    )
    assert (count, freed) == (0, 0)
    assert entry.exists()
    assert "SKIPPED" in capsys.readouterr().out


def test_dry_run_leaves_the_orphan_on_disk(project, monkeypatch):
    folder, out, photo, _ = project
    dead = out / "thumb_cache" / f"{_key(photo, photo.stat().st_mtime_ns, 600)}.jpg"
    dead.write_bytes(b"stale")
    monkeypatch.setattr(sweep.P, "_folder_for_output_dir", lambda d: folder)

    count, freed = sweep.sweep_project(
        out, ("thumb_cache",), (800,), apply=False
    )
    assert count == 1 and freed == len(b"stale")
    assert dead.exists()

    sweep.sweep_project(out, ("thumb_cache",), (800,), apply=True)
    assert not dead.exists()
