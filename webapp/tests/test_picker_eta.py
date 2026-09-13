"""The picker tags each folder with how long its pipeline run would take.

The picker's data is mocked at the network layer: the shared server fixture
always has a project open, and the tag is a rendering of two fields, so real
folders on disk would add nothing but setup.
"""

import json

from playwright.sync_api import Page, expect


def _project(folder, status, pending, eta_s):
    return {
        "folder": folder,
        "display_name": folder.rsplit("/", 1)[-1],
        "last_opened": None,
        "last_pipeline_run": None,
        "image_count": 1173,
        "status": status,
        "done_at": None,
        "pending_count": pending,
        "eta_s": eta_s,
    }


def _entry(name, pending, eta_s):
    return {
        "name": name,
        "path": f"/trips/{name}",
        "is_dir": True,
        "image_count": pending or 40,
        "pending_count": pending,
        "eta_s": eta_s,
    }


def _open_picker(browser, webapp_server, projects, entries) -> Page:
    ctx = browser.new_context()
    page = ctx.new_page()

    def fulfill(body):
        return lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)
        )

    page.route("**/api/state", fulfill({"no_project": True}))
    page.route("**/api/projects", fulfill(projects))
    page.route("**/api/fs/list*", fulfill({"path": "/trips", "parent": None, "entries": entries}))
    page.goto(webapp_server)
    expect(page.get_by_text("Recent", exact=True)).to_be_visible()
    return page


def test_stale_project_shows_time_for_its_new_photos(browser, webapp_server):
    page = _open_picker(
        browser, webapp_server,
        projects=[
            _project("/trips/2026_07_Hawaii", "stale", 731, 488),
            _project("/trips/2026_01_Japan", "ready", 0, None),
        ],
        entries=[],
    )
    hawaii = page.locator("li", has_text="2026_07_Hawaii")
    expect(hawaii.get_by_test_id("pipeline-eta")).to_have_text("⏱ ~8 min · 731 to process")
    # Nothing to run, nothing to estimate.
    expect(page.locator("li", has_text="2026_01_Japan").get_by_test_id("pipeline-eta")).to_have_count(0)
    page.context.close()


def test_browse_rows_carry_the_tag_under_the_name(browser, webapp_server):
    page = _open_picker(
        browser, webapp_server,
        projects=[],
        entries=[
            _entry("2024_03_Europe", 9522, 7140),
            _entry("2026_07_Rattlesnake", 1, 31),
            _entry("2026_05_Vegas", 0, None),
        ],
    )
    europe = page.locator("li", has_text="2024_03_Europe").get_by_test_id("pipeline-eta")
    expect(europe).to_have_text("⏱ ~2.0 h · 9522 to process")
    expect(page.locator("li", has_text="2026_07_Rattlesnake").get_by_test_id("pipeline-eta")).to_have_text(
        "⏱ <1 min · 1 to process"
    )
    expect(page.locator("li", has_text="2026_05_Vegas").get_by_test_id("pipeline-eta")).to_have_count(0)

    # Under the folder name, not beside it.
    name_box = page.get_by_text("2024_03_Europe", exact=True).bounding_box()
    tag_box = europe.bounding_box()
    assert tag_box["y"] >= name_box["y"] + name_box["height"] - 1
    page.context.close()
