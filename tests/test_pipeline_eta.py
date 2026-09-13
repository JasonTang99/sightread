"""The picker's pipeline time estimate — what it counts and what it leaves out.

The estimate is only useful if it tracks the work a run would actually do:
photos already in a project's score cache load in milliseconds, so a stale
trip with one new camera folder must be estimated for that folder alone, and a
finished project must offer no estimate at all.
"""

import json

import pytest
from fastapi.testclient import TestClient

import projects
import server
from projects import (
    ETA_FIXED_S,
    ETA_PER_MB_S,
    ETA_PER_PHOTO_S,
    estimate_pipeline,
    project_output_dir_name,
)


def _photo(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


def _cache(out_dir, paths, results=True):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scores_ensemble.paths.json").write_text(
        json.dumps([str(p.resolve()) for p in paths])
    )
    if results:
        (out_dir / "results.json").write_text('{"clusters": []}')


def _expected(sizes):
    return ETA_FIXED_S + len(sizes) * ETA_PER_PHOTO_S + sum(sizes) / 1e6 * ETA_PER_MB_S


@pytest.fixture()
def trip(tmp_path):
    folder = tmp_path / "Trips" / "2026_07_Hawaii"
    xt5 = [_photo(folder / "xt5" / f"DSCF{i}.JPG", 2_000_000) for i in range(3)]
    google = [_photo(folder / "google photos" / f"PXL_{i}.jpg", 500_000) for i in range(2)]
    _photo(folder / "xt5" / "DSCF0.RAF", 50_000_000)  # not a still the pipeline scans
    _photo(folder / "_exports" / "DSCF0.JPG", 2_000_000)  # the trip's own output
    return folder, xt5, google


def test_never_run_folder_counts_every_photo_by_size(trip, tmp_path):
    folder, xt5, google = trip

    est = estimate_pipeline(folder, tmp_path / "no-such-output")

    assert est.image_count == 5
    assert est.pending == 5
    assert est.eta_s == pytest.approx(_expected([2_000_000] * 3 + [500_000] * 2))


def test_stale_trip_is_estimated_for_the_new_folder_only(trip, tmp_path):
    folder, xt5, google = trip
    out = tmp_path / "out"
    _cache(out, xt5)

    est = estimate_pipeline(folder, out)

    assert est.image_count == 5
    assert est.pending == 2
    assert est.eta_s == pytest.approx(_expected([500_000] * 2))


def test_fully_processed_project_has_no_estimate(trip, tmp_path):
    folder, xt5, google = trip
    out = tmp_path / "out"
    _cache(out, xt5 + google)

    est = estimate_pipeline(folder, out)

    assert (est.pending, est.eta_s) == (0, None)


def test_scored_but_never_ranked_still_costs_the_fixed_part(trip, tmp_path):
    """A run that died after scoring reads never_run; rerunning is cheap, not free."""
    folder, xt5, google = trip
    out = tmp_path / "out"
    _cache(out, xt5 + google, results=False)

    assert estimate_pipeline(folder, out).eta_s == pytest.approx(ETA_FIXED_S)


def test_empty_folder_has_no_estimate(tmp_path):
    (tmp_path / "empty").mkdir()
    assert estimate_pipeline(tmp_path / "empty", None).eta_s is None


def test_fs_listing_carries_each_folder_estimate(trip, tmp_path, monkeypatch):
    folder, xt5, google = trip
    data_dir = tmp_path / "data"
    monkeypatch.setattr(projects, "DATA_DIR", data_dir)
    _cache(data_dir / project_output_dir_name(folder / "xt5"), xt5)

    client = TestClient(server.app, base_url="http://localhost")
    res = client.get("/api/fs/list", params={"path": str(folder)})

    assert res.status_code == 200
    by_name = {e["name"]: e for e in res.json()["entries"]}
    assert (by_name["xt5"]["pending_count"], by_name["xt5"]["eta_s"]) == (0, None)
    assert by_name["google photos"]["pending_count"] == 2
    assert by_name["google photos"]["eta_s"] == pytest.approx(_expected([500_000] * 2))
