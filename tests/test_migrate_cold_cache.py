import os

import migrate_cold_cache as mig


def test_hard_links_stay_one_copy(tmp_path, monkeypatch):
    root = tmp_path / "cold"
    root.mkdir()
    monkeypatch.setenv("SIGHTREAD_COLD_CACHE_ROOT", str(root))
    a = tmp_path / "trip" / "thumb_cache" / "k.jpg"
    b = tmp_path / "city" / "thumb_cache" / "k.jpg"
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    a.write_bytes(b"jpeg")
    os.link(a, b)
    seen = {}
    da, db = root / "trip" / "thumb_cache" / "k.jpg", root / "city" / "thumb_cache" / "k.jpg"
    assert mig.move(a, da, seen) == 4
    assert mig.move(b, db, seen) == 0
    assert not a.exists() and not b.exists()
    assert da.stat().st_ino == db.stat().st_ino and da.read_bytes() == b"jpeg"
