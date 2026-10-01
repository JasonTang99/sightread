"""Stepping through Singles must leave one photo in the frame.

A single that has a motion file mounts LiveMotion beside the still. Those
two used the same React key, so production (no duplicate-key warning) kept
every previous <img> in the frame. ArrowRight across a run that includes
one Live Photo must still show exactly one photo.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import requests
from playwright.sync_api import Page, expect

from conftest import BASE_URL, settle

# Review order is score ascending. The Live Photo sits in the middle so
# ArrowRight both enters and leaves it.
_SCORES = (0.20, 0.40, 0.55, 0.70)
_LIVE_INDEX = 1


@pytest.fixture()
def singles_with_motion(tmp_path, webapp_server):
    """Several one-photo clusters; the second still has a motion file."""
    folder = tmp_path / "singles"
    folder.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    src = Path(__file__).parent.parent.parent / "demo_photos" / "DSCF4380.JPG"
    stills = []
    for i in range(len(_SCORES)):
        dst = folder / f"IMG_{i:04d}.JPG"
        shutil.copy(src, dst)
        stills.append(str(dst.resolve()))
    cluster_paths = []
    for name in ("CLU_A.JPG", "CLU_B.JPG"):
        dst = folder / name
        shutil.copy(src, dst)
        cluster_paths.append(str(dst.resolve()))
    motion = folder / "IMG_0001.MOV"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=160x120:rate=10:duration=2",
         "-pix_fmt", "yuv420p", str(motion)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    clusters = [
        {
            "cluster_id": 0,
            "best_image": cluster_paths[0],
            "images": [
                {"path": cluster_paths[0], "score": 0.9, "centrality": 0.9, "rank": 1},
                {"path": cluster_paths[1], "score": 0.5, "centrality": 0.8, "rank": 2},
            ],
        }
    ]
    for i, (path, score) in enumerate(zip(stills, _SCORES), start=1):
        image = {"path": path, "score": score, "centrality": 1.0, "rank": 1}
        if i - 1 == _LIVE_INDEX:
            image["motion"] = str(motion.resolve())
        clusters.append({
            "cluster_id": i,
            "best_image": path,
            "images": [image],
        })
    (out / "results.json").write_text(json.dumps({"clusters": clusters}))
    requests.post(
        f"{BASE_URL}/api/_test_set_project",
        json={"folder": str(folder), "output_dir": str(out)},
    )
    return len(_SCORES)


def _open_singles(page: Page, n: int) -> None:
    page.goto(BASE_URL)
    page.wait_for_selector("[data-testid=cluster-view]", timeout=10_000)
    settle(page)
    page.get_by_role("button", name=f"Singles (0/{n})", exact=True).click()
    expect(page.get_by_test_id("singles-view")).to_have_attribute("data-index", "0")


def _photo_imgs(page: Page):
    """The stills in the singles frame, not a badge or a motion clip."""
    container = page.get_by_test_id("singles-view").locator("> div")
    return container.locator("img[src*='/api/image']")


def test_arrow_right_leaves_one_photo(page_loaded: Page, singles_with_motion: int):
    n = singles_with_motion
    _open_singles(page_loaded, n)
    expect(_photo_imgs(page_loaded)).to_have_count(1)

    page_loaded.keyboard.press("ArrowRight")
    expect(page_loaded.get_by_test_id("singles-view")).to_have_attribute(
        "data-index", str(_LIVE_INDEX)
    )
    expect(page_loaded.get_by_test_id("live-badge")).to_have_count(1)

    for index in range(_LIVE_INDEX + 1, n):
        page_loaded.keyboard.press("ArrowRight")
        expect(page_loaded.get_by_test_id("singles-view")).to_have_attribute(
            "data-index", str(index)
        )

    expect(_photo_imgs(page_loaded)).to_have_count(1)
