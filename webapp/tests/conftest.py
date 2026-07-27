"""Fixtures for Sightread webapp Playwright tests."""

import json
import os
import subprocess
import sys
import time
import weakref
from pathlib import Path

import pytest
import requests
from playwright.sync_api import Page

PROJECT_ROOT = Path(__file__).parent.parent.parent
PORT = 8099
BASE_URL = f"http://localhost:{PORT}"

sys.path.insert(0, str(PROJECT_ROOT / "webapp"))
from utils import TO_DELETE, load_decisions, pending_deletes, save_decisions  # noqa: E402

# Curation state is one file with one status per photo, so tests ask it these
# two questions rather than reading a delete list and a favorites list.
CURATION_FILES = ("decisions.json", "to_delete.txt", "favorites.json",
                  "to_delete.txt.migrated", "favorites.json.migrated")


def queued(output_dir) -> list[str]:
    """Paths currently marked to_delete — the delete queue."""
    return sorted(pending_deletes(output_dir))


def statuses(output_dir) -> dict:
    return load_decisions(output_dir)


def seed_queue(output_dir, *paths) -> None:
    save_decisions(output_dir, {p: TO_DELETE for p in paths})

FIXTURE_RESULTS = {
    "clusters": [
        {
            "cluster_id": 1,
            "best_image": "demo_photos/DSCF4380.JPG",
            "images": [
                {"path": "demo_photos/DSCF4380.JPG", "score": 0.72, "centrality": 0.98, "rank": 1},
                {"path": "demo_photos/DSCF4370.JPG", "score": 0.61, "centrality": 0.97, "rank": 2},
                {"path": "demo_photos/DSCF4375.JPG", "score": 0.45, "centrality": 0.96, "rank": 3},
            ],
        },
        {
            "cluster_id": 2,
            "best_image": "demo_photos/DSCF4281.JPG",
            "images": [
                {"path": "demo_photos/DSCF4281.JPG", "score": 0.68, "centrality": 0.99, "rank": 1},
                {"path": "demo_photos/DSCF4282.JPG", "score": 0.52, "centrality": 0.95, "rank": 2},
            ],
        },
        # singleton below threshold (0.30 < 0.48) → auto-delete
        {
            "cluster_id": 3,
            "best_image": "demo_photos/DSCF4283.JPG",
            "images": [
                {"path": "demo_photos/DSCF4283.JPG", "score": 0.30, "centrality": 1.0, "rank": 1},
            ],
        },
        # singleton above threshold → auto-keep
        {
            "cluster_id": 4,
            "best_image": "demo_photos/DSCF4284.JPG",
            "images": [
                {"path": "demo_photos/DSCF4284.JPG", "score": 0.65, "centrality": 1.0, "rank": 1},
            ],
        },
    ]
}


def _cluster_count() -> int:
    return sum(1 for c in FIXTURE_RESULTS["clusters"] if len(c["images"]) > 1)


def _singleton_count() -> int:
    return sum(1 for c in FIXTURE_RESULTS["clusters"] if len(c["images"]) == 1)


def _wait_for_server(timeout: int = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{BASE_URL}/api/state", timeout=2)
            if r.status_code == 200:
                return
        except requests.ConnectionError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"Webapp server did not start on port {PORT} within {timeout}s")


@pytest.fixture(scope="session")
def output_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("sightread_out")
    (out / "results.json").write_text(json.dumps(FIXTURE_RESULTS))
    return out


@pytest.fixture(scope="session")
def project_folder(tmp_path_factory):
    # Empty folder: keeps /api/videos deterministic (repo .mts/.webm files would
    # otherwise show up). Relative image paths fall back to PROJECT_ROOT.
    return tmp_path_factory.mktemp("sightread_photos")


@pytest.fixture(scope="session")
def webapp_server(output_dir, project_folder, tmp_path_factory):
    log = tmp_path_factory.mktemp("logs") / "server.log"
    env = {**os.environ, "SIGHTREAD_TEST": "1"}
    proc = subprocess.Popen(
        ["python3", "-m", "uvicorn", "webapp.server:app", f"--port={PORT}"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=open(log, "w"),
        stderr=subprocess.STDOUT,
    )
    proc._log_path = log
    try:
        _wait_for_server()
        requests.post(
            f"{BASE_URL}/api/_test_set_project",
            json={"folder": str(project_folder), "output_dir": str(output_dir)},
        )
        yield BASE_URL
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture(autouse=True)
def reset_state(output_dir, project_folder, webapp_server):
    """Restore file state and clear in-memory undo stack before each test."""
    time.sleep(0.1)  # let any in-flight requests from previous test settle
    (output_dir / "results.json").write_text(json.dumps(FIXTURE_RESULTS))
    for name in CURATION_FILES:
        (output_dir / name).unlink(missing_ok=True)
    requests.post(f"{BASE_URL}/api/_test_reset")
    requests.post(
        f"{BASE_URL}/api/_test_set_project",
        json={"folder": str(project_folder), "output_dir": str(output_dir)},
    )
    yield


_INFLIGHT: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _track_api_requests(page) -> None:
    """Record in-flight API calls so `settle()` can wait for them.

    Image requests are excluded: they are slow, numerous, and never what a test
    is waiting on.
    """
    inflight: set = set()
    _INFLIGHT[page] = inflight

    def _started(req):
        if "/api/" in req.url and "/api/image" not in req.url:
            inflight.add(req)

    def _done(req):
        inflight.discard(req)

    page.on("request", _started)
    page.on("requestfinished", _done)
    page.on("requestfailed", _done)


def settle(page, timeout_ms: int = 10_000) -> None:
    """Wait until the API calls a click just fired have completed.

    Playwright's `wait_for_load_state("networkidle")` only tracks the current
    navigation's lifecycle. These pages passed it during `goto`, so the call
    returns immediately and never waits for the XHR a button click fired — a
    test that then reads a file on disk races the server, and closing the page
    aborts the request outright.
    """
    inflight = _INFLIGHT.get(page)
    waited = 0
    # One tick first: the click's request is dispatched asynchronously, so an
    # immediate check can observe an empty set before it has even started.
    page.wait_for_timeout(50)
    while inflight and waited < timeout_ms:
        page.wait_for_timeout(50)
        waited += 50
    if inflight:
        raise AssertionError(f"API calls still in flight after {timeout_ms}ms: {inflight}")


@pytest.fixture()
def page_loaded(browser, webapp_server):
    ctx = browser.new_context()
    page = ctx.new_page()
    _track_api_requests(page)
    page.goto(webapp_server)
    page.wait_for_selector("select", timeout=10_000)
    yield page
    page.close()
    ctx.close()  # ensures all in-flight requests are flushed before reset_state sees clean slate
