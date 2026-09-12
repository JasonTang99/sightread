"""Playwright tests for the Sightread React webapp."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import requests
from playwright.sync_api import Page, expect

from utils import save_decisions

from conftest import (  # noqa: F401
    BASE_URL,
    FIXTURE_RESULTS,
    _cluster_count,
    _singleton_count,
    output_dir,
    project_folder,
    queued,
    seed_queue,
    settle,
    statuses,
)


# ---------------------------------------------------------------------------
# Page load
# ---------------------------------------------------------------------------
class TestPageLoad:
    def test_header_leads_with_the_project_not_the_product(self, page_loaded: Page):
        """The wordmark said nothing a user needed twice; the project does."""
        expect(page_loaded.locator("header").get_by_text("Sightread", exact=True)).to_have_count(0)
        first = page_loaded.locator("header > *").first
        expect(first).to_have_text("← Projects")

    def test_header_names_the_open_project(self, page_loaded: Page, project_folder):
        """Which trip is on screen. Every other project control is a verb, so
        with several trips half reviewed nothing said where you were."""
        name = page_loaded.get_by_test_id("project-name")
        expect(name).to_have_text(project_folder.name)
        # Trips on different drives share names, so the path is the tooltip.
        expect(name).to_have_attribute("title", str(project_folder))

    def test_the_project_name_survives_a_tab_change(self, page_loaded: Page, project_folder):
        page_loaded.get_by_role("button", name="Timeline").click()
        settle(page_loaded)
        expect(page_loaded.get_by_test_id("project-name")).to_have_text(project_folder.name)

    def test_cluster_tab_visible(self, page_loaded: Page):
        expect(page_loaded.get_by_text(f"Clusters ({_cluster_count()})")).to_be_visible()

    def test_singles_tab_visible(self, page_loaded: Page):
        expect(page_loaded.get_by_text(f"Singles ({_singleton_count()})")).to_be_visible()

    def test_undo_disabled_initially(self, page_loaded: Page):
        expect(page_loaded.get_by_role("button", name="↶ Undo")).to_be_disabled()

    def test_progress_bar_visible(self, page_loaded: Page):
        # By test id, not by colour class: the header's session meter uses the
        # same blue and would otherwise win `.first` purely by DOM order.
        expect(page_loaded.get_by_test_id("cluster-progress")).to_be_visible()

    def test_session_progress_visible(self, page_loaded: Page):
        expect(page_loaded.get_by_test_id("session-progress")).to_contain_text("reviewed")

    def test_cluster_select_shows_first(self, page_loaded: Page):
        # Select dropdown should show "1/N" for the first cluster
        select = page_loaded.locator("select")
        expect(select).to_have_value("0")  # 0-indexed value


# ---------------------------------------------------------------------------
# Navigation (buttons)
# ---------------------------------------------------------------------------
class TestNavigation:
    def test_prev_disabled_on_first(self, page_loaded: Page):
        # exact=True: header now also has a "← Projects" button
        prev = page_loaded.get_by_role("button", name="←", exact=True)
        expect(prev).to_be_disabled()

    def test_next_advances_cluster(self, page_loaded: Page):
        page_loaded.get_by_role("button", name="→", exact=True).click()
        expect(page_loaded.locator("select")).to_have_value("1")

    def test_prev_returns_to_first(self, page_loaded: Page):
        page_loaded.get_by_role("button", name="→", exact=True).click()
        page_loaded.get_by_role("button", name="←", exact=True).click()
        expect(page_loaded.get_by_role("button", name="←", exact=True)).to_be_disabled()

    def test_next_disabled_on_last(self, page_loaded: Page):
        for _ in range(_cluster_count() - 1):
            page_loaded.get_by_role("button", name="→", exact=True).click()
        expect(page_loaded.get_by_role("button", name="→", exact=True)).to_be_disabled()

    def test_skip_advances_cluster(self, page_loaded: Page):
        page_loaded.get_by_role("button", name="Skip").click()
        expect(page_loaded.locator("select")).to_have_value("1")


# ---------------------------------------------------------------------------
# Keyboard navigation — ClusterView
# ---------------------------------------------------------------------------
class TestKeyboardNav:
    def test_arrow_right_advances_cluster(self, page_loaded: Page):
        page_loaded.keyboard.press("ArrowRight")
        expect(page_loaded.locator("select")).to_have_value("1")

    def test_arrow_left_goes_back(self, page_loaded: Page):
        page_loaded.keyboard.press("ArrowRight")
        page_loaded.keyboard.press("ArrowLeft")
        expect(page_loaded.locator("select")).to_have_value("0")

    def test_arrow_left_noop_on_first(self, page_loaded: Page):
        page_loaded.keyboard.press("ArrowLeft")
        expect(page_loaded.locator("select")).to_have_value("0")

    def test_l_moves_image_focus(self, page_loaded: Page):
        # First image card focused (border-blue-400); l moves focus right
        expect(page_loaded.locator(".border-blue-400").first).to_be_visible()
        page_loaded.keyboard.press("l")
        cards = page_loaded.locator(".border-blue-400")
        expect(cards).to_have_count(1)

    def test_j_moves_image_focus(self, page_loaded: Page):
        expect(page_loaded.locator(".border-blue-400").first).to_be_visible()
        page_loaded.keyboard.press("j")
        cards = page_loaded.locator(".border-blue-400")
        expect(cards).to_have_count(1)

    def test_k_moves_focus_back(self, page_loaded: Page):
        page_loaded.keyboard.press("j")
        page_loaded.keyboard.press("k")
        expect(page_loaded.locator(".border-blue-400").first).to_be_visible()

    def test_space_toggles_focused_image(self, page_loaded: Page):
        # Rank-1 image starts as Keep (green). Space should flip to Delete.
        expect(page_loaded.locator("button.bg-green-50").first).to_be_visible()
        page_loaded.keyboard.press("Space")
        # All three images of cluster 1 now red
        expect(page_loaded.locator("button.bg-red-50")).to_have_count(3)

    def test_space_toggles_back(self, page_loaded: Page):
        page_loaded.keyboard.press("Space")  # → delete
        page_loaded.keyboard.press("Space")  # → keep again
        expect(page_loaded.locator("button.bg-green-50").first).to_be_visible()

    def test_enter_confirms(self, page_loaded: Page, output_dir):
        page_loaded.keyboard.press("Enter")
        settle(page_loaded)
        assert len(queued(output_dir)) == 2  # ranks 2 and 3 deleted by default

    def test_enter_confirm_advances_and_records_decision(self, page_loaded: Page, output_dir):
        page_loaded.keyboard.press("Enter")
        settle(page_loaded)
        expect(page_loaded.locator("select")).to_have_value("1")
        decisions = statuses(output_dir)
        assert decisions["demo_photos/DSCF4380.JPG"] == "kept"  # rank 1
        assert sorted(p for p, v in decisions.items() if v == "to_delete") == [
            "demo_photos/DSCF4370.JPG",
            "demo_photos/DSCF4375.JPG",
        ]


# ---------------------------------------------------------------------------
# Keep / Delete toggle
# ---------------------------------------------------------------------------
class TestToggle:
    def test_rank1_starts_keep(self, page_loaded: Page):
        expect(page_loaded.locator("button.bg-green-50").first).to_be_visible()

    def test_rank2_starts_delete(self, page_loaded: Page):
        expect(page_loaded.locator("button.bg-red-50").first).to_be_visible()

    def test_click_image_toggles(self, page_loaded: Page):
        page_loaded.locator("button.bg-green-50").first.click()
        expect(page_loaded.locator("button.bg-red-50")).to_have_count(3)


# ---------------------------------------------------------------------------
# Keep Best
# ---------------------------------------------------------------------------
class TestKeepBest:
    def test_keep_best_sets_rank1_green(self, page_loaded: Page):
        page_loaded.locator("button.bg-green-50").first.click()  # rank-1 → delete
        page_loaded.get_by_role("button", name="🏆 Best").click()
        expect(page_loaded.locator("button.bg-green-50")).to_have_count(1)

    def test_keep_best_marks_others_red(self, page_loaded: Page):
        page_loaded.get_by_role("button", name="🏆 Best").click()
        expect(page_loaded.locator("button.bg-red-50")).to_have_count(2)


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------
class TestConfirm:
    def test_confirm_writes_delete_list(self, page_loaded: Page, output_dir):
        page_loaded.get_by_role("button", name="✓ Confirm").click()
        settle(page_loaded)
        assert len(queued(output_dir)) == 2

    def test_confirm_advances_to_next_cluster(self, page_loaded: Page):
        page_loaded.get_by_role("button", name="✓ Confirm").click()
        settle(page_loaded)
        expect(page_loaded.locator("select")).to_have_value("1")

    def test_confirm_records_decision(self, page_loaded: Page, output_dir):
        page_loaded.get_by_role("button", name="✓ Confirm").click()
        settle(page_loaded)
        decisions = statuses(output_dir)
        assert decisions["demo_photos/DSCF4380.JPG"] == "kept"
        assert sum(1 for v in decisions.values() if v == "to_delete") == 2

    def test_skip_does_not_write_delete_list(self, page_loaded: Page, output_dir):
        page_loaded.get_by_role("button", name="Skip").click()
        assert queued(output_dir) == []


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------
class TestUndo:
    def _confirm(self, page: Page) -> None:
        page.get_by_role("button", name="✓ Confirm").click()
        settle(page)

    def test_undo_enabled_after_confirm(self, page_loaded: Page):
        self._confirm(page_loaded)
        expect(page_loaded.get_by_role("button", name="↶ Undo")).to_be_enabled()

    def test_undo_clears_delete_list(self, page_loaded: Page, output_dir):
        self._confirm(page_loaded)
        page_loaded.get_by_role("button", name="↶ Undo").click()
        settle(page_loaded)
        assert queued(output_dir) == []

    def test_undo_removes_decision(self, page_loaded: Page, output_dir):
        self._confirm(page_loaded)
        page_loaded.get_by_role("button", name="↶ Undo").click()
        settle(page_loaded)
        decisions = statuses(output_dir)
        assert decisions == {}  # every photo the cluster decided on is cleared

    def test_undo_disabled_after_undo(self, page_loaded: Page):
        self._confirm(page_loaded)
        page_loaded.get_by_role("button", name="↶ Undo").click()
        settle(page_loaded)
        expect(page_loaded.get_by_role("button", name="↶ Undo")).to_be_disabled()


# ---------------------------------------------------------------------------
# Singles tab (single-image review flow)
# ---------------------------------------------------------------------------
class TestSingles:
    def _open_singles(self, page: Page) -> None:
        page.get_by_text(f"Singles ({_singleton_count()})").click()
        expect(page.get_by_text(f"1 / {_singleton_count()}")).to_be_visible(timeout=8_000)

    def test_singles_tab_renders_counter(self, page_loaded: Page):
        self._open_singles(page_loaded)
        expect(page_loaded.get_by_text(f"1 / {_singleton_count()}")).to_be_visible()

    def test_lowest_score_first_marked_delete(self, page_loaded: Page):
        # Items sorted ascending by score; every single defaults to Delete
        self._open_singles(page_loaded)
        expect(page_loaded.get_by_text("Delete", exact=True)).to_be_visible()

    def test_space_toggles_to_keep(self, page_loaded: Page):
        self._open_singles(page_loaded)
        page_loaded.keyboard.press("Space")
        expect(page_loaded.get_by_text("Keep", exact=True)).to_be_visible()

    def test_j_advances_k_returns(self, page_loaded: Page):
        self._open_singles(page_loaded)
        page_loaded.keyboard.press("j")
        expect(page_loaded.get_by_text(f"2 / {_singleton_count()}")).to_be_visible()
        page_loaded.keyboard.press("k")
        expect(page_loaded.get_by_text(f"1 / {_singleton_count()}")).to_be_visible()

    def test_clicking_a_tag_stars_and_keeps_the_single(self, page_loaded: Page, output_dir):
        """Photos share the video tag vocabulary; a tag is a star and a keep."""
        self._open_singles(page_loaded)
        expect(page_loaded.get_by_text("Delete", exact=True)).to_be_visible()
        page_loaded.get_by_role("button", name=re.compile(r"vibes")).first.click()
        settle(page_loaded)
        expect(page_loaded.get_by_text("Keep", exact=True)).to_be_visible()
        expect(page_loaded.get_by_text("★ Favorites (1)")).to_be_visible()
        photo = "demo_photos/DSCF4283.JPG"
        assert json.loads((output_dir / "video_tags.json").read_text())["videos"][photo] == "vibes"
        assert photo in requests.get(f"{BASE_URL}/api/state").json()["favorites"]

    def test_enter_confirms_current_item(self, page_loaded: Page, output_dir):
        self._open_singles(page_loaded)
        expect(page_loaded.get_by_test_id("session-progress")).to_contain_text("0/4 reviewed")
        page_loaded.keyboard.press("Enter")
        settle(page_loaded)
        deleted = queued(output_dir)
        assert len(deleted) == 1
        assert "DSCF4283" in deleted[0]
        decisions = statuses(output_dir)
        assert decisions["demo_photos/DSCF4283.JPG"] == "to_delete"
        expect(page_loaded.get_by_test_id("session-progress")).to_contain_text("1/4 reviewed")

    def test_confirm_all_writes_delete_list(self, page_loaded: Page, output_dir):
        # No auto-keep threshold anymore: all unmarked singles default to Delete
        self._open_singles(page_loaded)
        page_loaded.get_by_role("button", name="✓ Confirm").click()
        settle(page_loaded)
        deleted = queued(output_dir)
        assert len(deleted) == _singleton_count()
        assert any("DSCF4283" in d for d in deleted)
        assert any("DSCF4284" in d for d in deleted)

    def test_confirm_all_marks_confirmed(self, page_loaded: Page):
        # Tab no longer disappears after confirm-all; the current item is
        # badged "confirmed" and the tab stays visible.
        self._open_singles(page_loaded)
        page_loaded.get_by_role("button", name="✓ Confirm").click()
        settle(page_loaded)
        expect(page_loaded.get_by_text("confirmed", exact=True)).to_be_visible()
        expect(page_loaded.get_by_text(f"Singles ({_singleton_count()})")).to_be_visible()


# ---------------------------------------------------------------------------
# Trash panel
# ---------------------------------------------------------------------------
class TestTrashPanel:
    def _open_finish(self, page: Page) -> None:
        page.get_by_test_id("finish-tab").click()
        settle(page)

    def _confirm_cluster(self, page: Page) -> None:
        page.get_by_role("button", name="✓ Confirm").click()
        settle(page)

    def test_trash_panel_shows_after_confirm(self, page_loaded: Page):
        self._confirm_cluster(page_loaded)
        self._open_finish(page_loaded)
        expect(page_loaded.get_by_text("Trash —")).to_be_visible()

    def test_trash_panel_expands(self, page_loaded: Page):
        self._confirm_cluster(page_loaded)
        self._open_finish(page_loaded)
        page_loaded.get_by_text("▼ expand").click()
        expect(page_loaded.get_by_role("button", name="🗑️ Delete 2 from primary drive")).to_be_visible()

    def test_trash_restore_removes_from_delete_list(self, page_loaded: Page, output_dir):
        self._confirm_cluster(page_loaded)
        self._open_finish(page_loaded)
        page_loaded.get_by_text("▼ expand").click()
        trash_filename = page_loaded.locator(".grid .rounded p.text-xs").first
        expect(trash_filename).to_be_visible(timeout=10_000)
        trash_filename.click()
        expect(page_loaded.locator(".border-blue-400").first).to_be_visible(timeout=5_000)
        page_loaded.get_by_role("button", name="Restore 1 selected").click()
        settle(page_loaded)
        assert len(queued(output_dir)) == 1  # started with 2 (ranks 2,3), restored 1

    def test_apply_deletes_clears_list(self, page_loaded: Page, output_dir):
        # Nonexistent paths: apply must not touch real files during tests
        seed_queue(output_dir, "demo_photos/NOPE_1.JPG", "demo_photos/NOPE_2.JPG")
        page_loaded.reload()
        self._open_finish(page_loaded)
        page_loaded.get_by_text("▼ expand").click()
        page_loaded.once("dialog", lambda d: d.accept())
        page_loaded.get_by_role("button", name="🗑️ Delete 2 from primary drive").click()
        settle(page_loaded)
        assert queued(output_dir) == []
        expect(page_loaded.get_by_text("Trash —")).not_to_be_visible()


# ---------------------------------------------------------------------------
# Completion screen
# ---------------------------------------------------------------------------
class TestCompletion:
    def test_completion_message_shown(self, page_loaded: Page, output_dir):
        (output_dir / "results.json").write_text(json.dumps({"clusters": []}))
        page_loaded.reload()
        expect(page_loaded.get_by_text("All done!")).to_be_visible(timeout=10_000)


# ---------------------------------------------------------------------------
# Other keyboard bindings — ClusterView
# ---------------------------------------------------------------------------
class TestKeyboardNewBindings:
    def test_b_skips_cluster(self, page_loaded: Page):
        page_loaded.keyboard.press("b")
        expect(page_loaded.locator("select")).to_have_value("1")

    def test_s_stars_focused_image(self, page_loaded: Page):
        # "s" no longer skips — it toggles favorite on the focused image,
        # which makes the ★ Favorites tab appear in the header.
        page_loaded.keyboard.press("s")
        expect(page_loaded.get_by_text("★ Favorites (1)")).to_be_visible()

    def test_starring_a_delete_marked_photo_keeps_it(self, page_loaded: Page):
        """A star is a keep: rank-2 starts delete, starring it must flip Keep."""
        page_loaded.keyboard.press("l")
        expect(page_loaded.locator("button.bg-red-50")).to_have_count(2)
        page_loaded.keyboard.press("s")
        settle(page_loaded)
        expect(page_loaded.locator("button.bg-green-50")).to_have_count(2)
        expect(page_loaded.get_by_text("★ Favorites (1)")).to_be_visible()

    def test_shift_k_keep_best(self, page_loaded: Page):
        page_loaded.keyboard.press("2")  # rank-2 → keep (2 green)
        expect(page_loaded.locator("button.bg-green-50")).to_have_count(2)
        page_loaded.keyboard.press("K")  # reset to rank-1 only
        expect(page_loaded.locator("button.bg-green-50")).to_have_count(1)
        expect(page_loaded.locator("button.bg-red-50")).to_have_count(2)

    def test_digit_1_toggles_rank1(self, page_loaded: Page):
        expect(page_loaded.locator("button.bg-green-50").first).to_be_visible()
        page_loaded.keyboard.press("1")
        expect(page_loaded.locator("button.bg-red-50")).to_have_count(3)

    def test_digit_2_toggles_rank2(self, page_loaded: Page):
        page_loaded.keyboard.press("2")
        expect(page_loaded.locator("button.bg-green-50")).to_have_count(2)

    def test_u_undo_after_confirm(self, page_loaded: Page, output_dir):
        page_loaded.keyboard.press("Enter")
        settle(page_loaded)
        assert len(queued(output_dir)) == 2
        page_loaded.keyboard.press("u")
        settle(page_loaded)
        assert queued(output_dir) == []

    def test_question_mark_shows_help(self, page_loaded: Page):
        page_loaded.keyboard.press("?")
        expect(page_loaded.get_by_text("Keyboard shortcuts")).to_be_visible()

    def test_question_mark_toggles_help_off(self, page_loaded: Page):
        page_loaded.keyboard.press("?")
        page_loaded.keyboard.press("?")
        expect(page_loaded.get_by_text("Keyboard shortcuts")).not_to_be_visible()

    def test_escape_closes_help(self, page_loaded: Page):
        page_loaded.keyboard.press("?")
        page_loaded.keyboard.press("Escape")
        expect(page_loaded.get_by_text("Keyboard shortcuts")).not_to_be_visible()


# ---------------------------------------------------------------------------
# Timeline video tiles
# ---------------------------------------------------------------------------
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestTimelineVideos:
    """Videos are curated like photos, so the timeline has to show their status.

    Its tiles are still frames rather than <video> elements: media elements load
    eagerly, and at six connections per origin they starve the lazy photo
    thumbnails below them — with 89 videos in a real project, 150 photos never
    got requested at all.
    """

    @pytest.fixture()
    def video_project(self, tmp_path, webapp_server):
        """Point the server at a throwaway folder holding one real video."""
        folder = tmp_path / "clips"
        folder.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        (out / "results.json").write_text(json.dumps({"clusters": []}))
        video = folder / "a.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi",
             "-i", "testsrc=size=160x120:rate=10:duration=2",
             "-pix_fmt", "yuv420p", str(video)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        requests.post(
            f"{BASE_URL}/api/_test_set_project",
            json={"folder": str(folder), "output_dir": str(out)},
        )
        return out, str(video.resolve())

    def _open_timeline(self, page: Page) -> None:
        page.goto(BASE_URL)
        page.get_by_role("button", name="Timeline").click()
        settle(page)

    def test_tile_uses_a_poster_not_a_video_element(self, page_loaded: Page, video_project):
        self._open_timeline(page_loaded)
        expect(page_loaded.locator("img[src*='/api/video-poster']")).to_have_count(1)
        expect(page_loaded.locator("video")).to_have_count(0)

    def test_undecided_video_is_grey(self, page_loaded: Page, video_project):
        self._open_timeline(page_loaded)
        tile = page_loaded.locator("img[src*='/api/video-poster']").locator("..")
        expect(tile).to_have_class(re.compile(r"border-gray-300"))

    def test_marked_video_is_red(self, page_loaded: Page, video_project):
        out, video = video_project
        seed_queue(out, video)
        self._open_timeline(page_loaded)
        tile = page_loaded.locator("img[src*='/api/video-poster']").locator("..")
        expect(tile).to_have_class(re.compile(r"border-red-400"))

    def test_confirm_day_records_a_video_keep(self, page_loaded: Page, video_project):
        out, video = video_project
        self._open_timeline(page_loaded)
        page_loaded.get_by_role("button", name="✓ Confirm Day").click()
        settle(page_loaded)
        assert statuses(out).get(video) == "kept"

    def test_clicking_a_tile_then_confirming_queues_the_video(self, page_loaded: Page, video_project):
        out, video = video_project
        self._open_timeline(page_loaded)
        page_loaded.locator("img[src*='/api/video-poster']").click()
        page_loaded.get_by_role("button", name="✓ Confirm Day").click()
        settle(page_loaded)
        assert queued(out) == [video]

    def test_space_records_a_delete_immediately(self, page_loaded: Page, video_project):
        """Marks used to live only in component state until a bulk confirm."""
        out, video = video_project
        page_loaded.goto(BASE_URL)
        page_loaded.get_by_role("button", name="Videos (1)").click()
        expect(page_loaded.get_by_test_id("session-progress")).to_contain_text("0/1 reviewed")
        page_loaded.keyboard.press(" ")
        settle(page_loaded)
        assert queued(out) == [video]
        expect(page_loaded.get_by_test_id("session-progress")).to_contain_text("1/1 reviewed")

    def test_enter_records_a_keep_immediately(self, page_loaded: Page, video_project):
        out, video = video_project
        page_loaded.goto(BASE_URL)
        page_loaded.get_by_role("button", name="Videos (1)").click()
        expect(page_loaded.get_by_test_id("session-progress")).to_contain_text("0/1 reviewed")
        page_loaded.keyboard.press("Enter")
        settle(page_loaded)
        assert statuses(out).get(video) == "kept"
        expect(page_loaded.get_by_test_id("session-progress")).to_contain_text("1/1 reviewed")

    def test_starring_a_delete_marked_video_keeps_it(self, page_loaded: Page, video_project):
        out, video = video_project
        page_loaded.goto(BASE_URL)
        page_loaded.get_by_role("button", name="Videos (1)").click()
        page_loaded.keyboard.press(" ")
        settle(page_loaded)
        expect(page_loaded.get_by_text("Delete", exact=True)).to_be_visible()
        page_loaded.keyboard.press("s")
        settle(page_loaded)
        expect(page_loaded.get_by_text("Keep", exact=True)).to_be_visible()
        assert video in requests.get(f"{BASE_URL}/api/state").json()["favorites"]
        expect(page_loaded.get_by_test_id("session-progress")).to_contain_text("1/1 reviewed")

    def test_a_video_kept_in_the_reviewer_shows_green_in_the_timeline(self, page_loaded: Page, video_project):
        out, video = video_project
        page_loaded.goto(BASE_URL)
        page_loaded.get_by_role("button", name="Videos (1)").click()
        page_loaded.keyboard.press("Enter")
        settle(page_loaded)
        page_loaded.get_by_role("button", name="Timeline").click()
        settle(page_loaded)
        tile = page_loaded.locator("img[src*='/api/video-poster']").locator("..")
        expect(tile).to_have_class(re.compile(r"border-green-400"))


# ---------------------------------------------------------------------------
# Video tag feedback
# ---------------------------------------------------------------------------
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestVideoTagFeedback:
    """Pressing a tag has to say so on screen.

    The pills scroll horizontally once a project has a few tags, so "which pill
    is lit" is not a reliable answer to "what is this clip tagged" — the lit one
    can be off-screen. A badge in the toolbar states it outright, and confirms
    the write only after the server has acknowledged it.
    """

    @pytest.fixture()
    def video_project(self, tmp_path, webapp_server):
        folder = tmp_path / "clips"
        folder.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        (out / "results.json").write_text(json.dumps({"clusters": []}))
        video = folder / "a.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi",
             "-i", "testsrc=size=160x120:rate=10:duration=2",
             "-pix_fmt", "yuv420p", str(video)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        requests.post(
            f"{BASE_URL}/api/_test_set_project",
            json={"folder": str(folder), "output_dir": str(out)},
        )
        return out, str(video.resolve())

    def _open_videos(self, page: Page) -> None:
        page.goto(BASE_URL)
        page.get_by_role("button", name="Videos").click()
        settle(page)

    def test_the_tags_are_on_screen_before_anything_is_starred(
        self, page_loaded: Page, video_project
    ):
        """The row used to be hidden until a clip was favourited, which hid the
        whole feature: nothing to discover, and 1-9 silently did nothing."""
        self._open_videos(page_loaded)
        expect(page_loaded.get_by_role("button", name=re.compile(r"vibes"))).to_have_count(1)
        # Nothing is tagged yet, so nothing in the row is selected and the
        # export badge -- which only means something for a favourite -- is absent.
        expect(page_loaded.locator("text=🏷")).to_have_count(0)

    def test_clicking_a_tag_stars_the_clip_and_says_so(self, page_loaded: Page, video_project):
        """Tagging means 'deliver this', and exports.py only delivers favourites,
        so the tag is taken as the stronger statement and stars the clip too."""
        out, video = video_project
        self._open_videos(page_loaded)

        page_loaded.get_by_role("button", name=re.compile(r"vibes")).first.click()
        settle(page_loaded)

        expect(page_loaded.locator("span[title='Exports to …/vibes/']")).to_have_count(1)
        assert json.loads((out / "video_tags.json").read_text())["videos"][video] == "vibes"
        # The star is the half that would silently not happen: the tag write
        # succeeds either way, and only a favourite is ever exported.
        assert video in requests.get(f"{BASE_URL}/api/state").json()["favorites"]

    def test_clearing_a_tag_does_not_star_anything(self, page_loaded: Page, video_project):
        """'No tag' says nothing about wanting the clip, so it must not favourite
        it -- only a real tag carries that meaning."""
        self._open_videos(page_loaded)
        page_loaded.get_by_role("button", name=re.compile(r"vibes")).first.click()
        settle(page_loaded)
        page_loaded.get_by_title("No tag → …/untagged/").click()
        settle(page_loaded)
        # Still favourited from the tag click, now with no tag.
        expect(page_loaded.locator("span[title='Exports to …/untagged/']")).to_have_count(1)

    def test_the_tag_survives_stepping_away_and_back(self, page_loaded: Page, video_project):
        """A badge driven by local click state rather than by server state would
        pass the test above and still lose the tag on reload."""
        out, video = video_project
        self._open_videos(page_loaded)
        page_loaded.get_by_role("button", name=re.compile(r"vibes")).first.click()
        settle(page_loaded)

        assert json.loads((out / "video_tags.json").read_text())["videos"][video] == "vibes"

        self._open_videos(page_loaded)
        expect(page_loaded.locator("span[title='Exports to …/vibes/']")).to_have_count(1)


# ---------------------------------------------------------------------------
# Live Photos
# ---------------------------------------------------------------------------
class TestLivePhotoHover:
    """A Live Photo plays when the pointer rests on its LIVE badge.

    Playback hangs off the badge, not the tile, so crossing a grid on the way
    to the keep button does not start dozens of clips, and the <video> is only
    mounted while hovered — one media element per tile loads eagerly and, at
    six connections per origin, starves the lazy thumbnails around it.
    """

    @pytest.fixture()
    def live_project(self, tmp_path, webapp_server):
        """A cluster of two stills where only the first has a motion file."""
        folder = tmp_path / "live"
        folder.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        stills = []
        for name in ("IMG_0001.JPG", "IMG_0002.JPG"):
            dst = folder / name
            shutil.copy(Path(__file__).parent.parent.parent / "demo_photos" / "DSCF4380.JPG", dst)
            stills.append(str(dst.resolve()))
        motion = folder / "IMG_0001.MOV"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi",
             "-i", "testsrc=size=160x120:rate=10:duration=2",
             "-pix_fmt", "yuv420p", str(motion)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        (out / "results.json").write_text(json.dumps({"clusters": [{
            "cluster_id": 1,
            "best_image": stills[0],
            "images": [
                {"path": stills[0], "score": 0.71, "centrality": 0.98, "rank": 1,
                 "motion": str(motion.resolve())},
                {"path": stills[1], "score": 0.52, "centrality": 0.95, "rank": 2},
            ],
        }]}))
        requests.post(
            f"{BASE_URL}/api/_test_set_project",
            json={"folder": str(folder), "output_dir": str(out)},
        )
        return out, stills, str(motion.resolve())

    def _open_clusters(self, page: Page) -> None:
        page.goto(BASE_URL)
        page.wait_for_selector("select", timeout=10_000)
        settle(page)

    def test_only_the_still_with_a_motion_file_is_badged(self, page_loaded: Page, live_project):
        self._open_clusters(page_loaded)
        expect(page_loaded.get_by_test_id("live-badge")).to_have_count(1)

    def test_nothing_plays_until_the_badge_is_hovered(self, page_loaded: Page, live_project):
        self._open_clusters(page_loaded)
        expect(page_loaded.locator("video")).to_have_count(0)

    def test_hovering_the_badge_plays_the_motion_file(self, page_loaded: Page, live_project):
        out, _stills, motion = live_project
        self._open_clusters(page_loaded)
        page_loaded.get_by_test_id("live-badge").hover()
        video = page_loaded.locator("video")
        expect(video).to_have_count(1)
        assert motion in requests.utils.unquote(video.get_attribute("src"))

    def test_leaving_the_badge_unmounts_the_video(self, page_loaded: Page, live_project):
        self._open_clusters(page_loaded)
        page_loaded.get_by_test_id("live-badge").hover()
        expect(page_loaded.locator("video")).to_have_count(1)
        page_loaded.mouse.move(0, 0)
        expect(page_loaded.locator("video")).to_have_count(0)

    def test_hovering_one_badge_does_not_start_the_neighbours(self, page_loaded: Page, live_project):
        """Both stills are Live Photos here, so a tile-level hover would play two."""
        out, stills, motion = live_project
        results = json.loads((out / "results.json").read_text())
        results["clusters"][0]["images"][1]["motion"] = motion
        (out / "results.json").write_text(json.dumps(results))
        self._open_clusters(page_loaded)
        expect(page_loaded.get_by_test_id("live-badge")).to_have_count(2)
        page_loaded.get_by_test_id("live-badge").first.hover()
        expect(page_loaded.locator("video")).to_have_count(1)

    def test_the_badge_is_not_a_keep_or_delete_vote(self, page_loaded: Page, live_project):
        """The tile toggles on click; the badge sits on top of it."""
        self._open_clusters(page_loaded)
        before = page_loaded.get_by_role("button", name=re.compile(r"Keep|Delete")).first.inner_text()
        page_loaded.get_by_test_id("live-badge").click()
        settle(page_loaded)
        after = page_loaded.get_by_role("button", name=re.compile(r"Keep|Delete")).first.inner_text()
        assert before == after

    def test_the_timeline_badges_it_too(self, page_loaded: Page, live_project):
        self._open_clusters(page_loaded)
        page_loaded.get_by_role("button", name="Timeline").click()
        settle(page_loaded)
        expect(page_loaded.get_by_test_id("live-badge")).to_have_count(1)


# ---------------------------------------------------------------------------
# Skipping what has already been reviewed
# ---------------------------------------------------------------------------
class TestSkipReviewed:
    """Opening a trip whose camera folder was reviewed on its own adopts that
    review, so the queue is full of clusters, singles and clips that already
    have a decision. The header toggle takes them out of it."""

    @pytest.fixture()
    def video_project(self, tmp_path, webapp_server):
        """A throwaway project holding one real clip and no photos."""
        folder = tmp_path / "clips"
        folder.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        (out / "results.json").write_text(json.dumps({"clusters": []}))
        video = folder / "a.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi",
             "-i", "testsrc=size=160x120:rate=10:duration=2",
             "-pix_fmt", "yuv420p", str(video)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        requests.post(
            f"{BASE_URL}/api/_test_set_project",
            json={"folder": str(folder), "output_dir": str(out)},
        )
        return out, str(video.resolve())

    def _decide(self, output_dir, cluster_index: int, status: str = "kept") -> None:
        images = FIXTURE_RESULTS["clusters"][cluster_index]["images"]
        save_decisions(output_dir, {img["path"]: status for img in images})

    def test_absent_until_there_is_something_to_skip(self, page_loaded: Page, output_dir):
        """A project with nothing decided has nothing to hide, and a control
        that would do nothing is noise in a header this busy."""
        expect(page_loaded.get_by_test_id("hide-reviewed")).to_have_count(0)

        self._decide(output_dir, 0)
        page_loaded.reload()
        settle(page_loaded)

        expect(page_loaded.get_by_test_id("hide-reviewed")).to_have_text(
            re.compile(r"^Skip reviewed")
        )
        expect(page_loaded.get_by_text(f"Clusters ({_cluster_count()})")).to_be_visible()

    def test_the_button_counts_what_it_would_hide(self, page_loaded: Page, output_dir):
        self._decide(output_dir, 0)
        page_loaded.reload()
        settle(page_loaded)
        expect(page_loaded.get_by_test_id("hide-reviewed")).to_have_text("Skip reviewed (1)")

    def test_a_decided_cluster_leaves_the_queue(self, page_loaded: Page, output_dir):
        self._decide(output_dir, 0)
        page_loaded.reload()
        settle(page_loaded)
        page_loaded.get_by_test_id("hide-reviewed").click()
        expect(page_loaded.get_by_text(f"Clusters ({_cluster_count() - 1})")).to_be_visible()

    def test_a_decided_single_leaves_the_queue(self, page_loaded: Page, output_dir):
        # Cluster index 3 is a singleton in the fixture.
        self._decide(output_dir, 3, "favorite")
        page_loaded.reload()
        settle(page_loaded)
        page_loaded.get_by_test_id("hide-reviewed").click()
        expect(page_loaded.get_by_text(f"Singles ({_singleton_count() - 1})")).to_be_visible()

    def test_turning_it_off_brings_them_back(self, page_loaded: Page, output_dir):
        self._decide(output_dir, 0)
        page_loaded.reload()
        settle(page_loaded)
        toggle = page_loaded.get_by_test_id("hide-reviewed")
        toggle.click()
        expect(page_loaded.get_by_text(f"Clusters ({_cluster_count() - 1})")).to_be_visible()
        toggle.click()
        expect(page_loaded.get_by_text(f"Clusters ({_cluster_count()})")).to_be_visible()

    def test_the_choice_survives_a_reload(self, page_loaded: Page, output_dir):
        """It is a way of working, not a per-visit decision."""
        self._decide(output_dir, 0)
        page_loaded.reload()
        settle(page_loaded)
        page_loaded.get_by_test_id("hide-reviewed").click()
        page_loaded.reload()
        settle(page_loaded)
        expect(page_loaded.get_by_test_id("hide-reviewed")).to_have_text(
            re.compile(r"^Unreviewed only")
        )
        expect(page_loaded.get_by_text(f"Clusters ({_cluster_count() - 1})")).to_be_visible()

    def test_progress_still_counts_everything(self, page_loaded: Page, output_dir):
        """Hiding reviewed work must not make the project look smaller."""
        self._decide(output_dir, 0)
        page_loaded.reload()
        settle(page_loaded)
        page_loaded.get_by_test_id("hide-reviewed").click()
        total = _cluster_count() + _singleton_count()
        expect(page_loaded.get_by_test_id("session-progress")).to_contain_text(f"1/{total} reviewed")

    def test_a_reviewed_clip_leaves_the_videos_tab(self, page_loaded: Page, video_project):
        out, video = video_project
        page_loaded.goto(BASE_URL)
        settle(page_loaded)
        expect(page_loaded.get_by_role("button", name="Videos (1)")).to_be_visible()
        save_decisions(out, {video: "kept"})
        page_loaded.reload()
        settle(page_loaded)
        page_loaded.get_by_test_id("hide-reviewed").click()
        expect(page_loaded.get_by_role("button", name=re.compile(r"^Videos"))).to_have_count(0)
