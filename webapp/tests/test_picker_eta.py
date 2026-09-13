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
    # Both lists render the same row, so both carry the tag.
    for list_id in ("recent-projects", "projects-by-date"):
        rows = page.get_by_test_id(list_id)
        hawaii = rows.locator("li", has_text="2026_07_Hawaii")
        expect(hawaii.get_by_test_id("pipeline-eta")).to_have_text("⏱ ~8 min · 731 to process")
        # Nothing to run, nothing to estimate.
        expect(rows.locator("li", has_text="2026_01_Japan").get_by_test_id("pipeline-eta")).to_have_count(0)
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
    page.get_by_role("button", name="Other folder…").click()
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


def test_trips_list_newest_trip_first(browser, webapp_server):
    page = _open_picker(
        browser, webapp_server,
        # /api/projects answers most recently used first.
        projects=[
            _project("/mnt/h0/Trips/2024/2024_05_NYC", "ready", 0, None),
            _project("/home/jason/demo_photos", "ready", 0, None),
            _project("/mnt/h0/Trips/2026_01_Panorama", "ready", 0, None),
            _project("/mnt/h0/Trips/2025/2025_12_Gothics", "ready", 0, None),
            _project("/mnt/h0/Trips/2026_01_Japan", "ready", 0, None),
            _project("/mnt/h0/Trips/2026_07_Hawaii", "stale", 731, 488),
        ],
        entries=[],
    )
    names = lambda list_id: page.get_by_test_id(list_id).locator("li p.font-medium").all_inner_texts()
    expect(page.get_by_test_id("projects-by-date").locator("li")).to_have_count(6)
    assert names("projects-by-date") == [
        "2026_07_Hawaii", "2026_01_Panorama", "2026_01_Japan", "2025_12_Gothics", "2024_05_NYC", "demo_photos",
    ]
    # Recent keeps the server's order.
    assert names("recent-projects") == [
        "2024_05_NYC", "demo_photos", "2026_01_Panorama", "2025_12_Gothics", "2026_01_Japan", "2026_07_Hawaii",
    ]
    page.context.close()


def test_browsing_waits_until_asked(browser, webapp_server):
    page = _open_picker(browser, webapp_server, projects=[], entries=[_entry("2024_03_Europe", 9522, 7140)])
    expect(page.get_by_text("Browse", exact=True)).to_have_count(0)
    expect(page.get_by_text("Selected", exact=True)).to_have_count(0)

    page.get_by_role("button", name="Other folder…").click()
    expect(page.get_by_text("2024_03_Europe", exact=True)).to_be_visible()

    page.get_by_title("Close browser").click()
    expect(page.get_by_text("2024_03_Europe", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Other folder…")).to_be_visible()
    page.context.close()
