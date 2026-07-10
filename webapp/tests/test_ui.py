"""Playwright tests for the Sightread React webapp."""

import json
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from conftest import FIXTURE_RESULTS, _cluster_count, _singleton_count, output_dir  # noqa: F401


# ---------------------------------------------------------------------------
# Page load
# ---------------------------------------------------------------------------
class TestPageLoad:
    def test_header_title(self, page_loaded: Page):
        # Header title is plain text (a span), not a button anymore
        expect(page_loaded.locator("header").get_by_text("Sightread", exact=True)).to_be_visible()

    def test_cluster_tab_visible(self, page_loaded: Page):
        expect(page_loaded.get_by_text(f"Clusters ({_cluster_count()})")).to_be_visible()

    def test_singles_tab_visible(self, page_loaded: Page):
        expect(page_loaded.get_by_text(f"Singles ({_singleton_count()})")).to_be_visible()

    def test_undo_disabled_initially(self, page_loaded: Page):
        expect(page_loaded.get_by_role("button", name="↶ Undo")).to_be_disabled()

    def test_progress_bar_visible(self, page_loaded: Page):
        expect(page_loaded.locator(".bg-blue-500").first).to_be_visible()

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
        page_loaded.wait_for_load_state("networkidle")
        deleted = (output_dir / "to_delete.txt").read_text().splitlines()
        assert len(deleted) == 2  # ranks 2 and 3 deleted by default

    def test_enter_confirm_advances_and_records_decision(self, page_loaded: Page, output_dir):
        page_loaded.keyboard.press("Enter")
        page_loaded.wait_for_load_state("networkidle")
        expect(page_loaded.locator("select")).to_have_value("1")
        decisions = json.loads((output_dir / "decisions.json").read_text())
        assert "1" in decisions
        assert len(decisions["1"]["deleted"]) == 2


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
        page_loaded.wait_for_load_state("networkidle")
        deleted = (output_dir / "to_delete.txt").read_text().splitlines()
        assert len(deleted) == 2

    def test_confirm_advances_to_next_cluster(self, page_loaded: Page):
        page_loaded.get_by_role("button", name="✓ Confirm").click()
        page_loaded.wait_for_load_state("networkidle")
        expect(page_loaded.locator("select")).to_have_value("1")

    def test_confirm_records_decision(self, page_loaded: Page, output_dir):
        page_loaded.get_by_role("button", name="✓ Confirm").click()
        page_loaded.wait_for_load_state("networkidle")
        decisions = json.loads((output_dir / "decisions.json").read_text())
        assert decisions["1"]["kept"] and len(decisions["1"]["deleted"]) == 2

    def test_skip_does_not_write_delete_list(self, page_loaded: Page, output_dir):
        page_loaded.get_by_role("button", name="Skip").click()
        content = (output_dir / "to_delete.txt").read_text().strip()
        assert content == ""


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------
class TestUndo:
    def _confirm(self, page: Page) -> None:
        page.get_by_role("button", name="✓ Confirm").click()
        page.wait_for_load_state("networkidle")

    def test_undo_enabled_after_confirm(self, page_loaded: Page):
        self._confirm(page_loaded)
        expect(page_loaded.get_by_role("button", name="↶ Undo")).to_be_enabled()

    def test_undo_clears_delete_list(self, page_loaded: Page, output_dir):
        self._confirm(page_loaded)
        page_loaded.get_by_role("button", name="↶ Undo").click()
        page_loaded.wait_for_load_state("networkidle")
        content = (output_dir / "to_delete.txt").read_text().strip()
        assert content == ""

    def test_undo_removes_decision(self, page_loaded: Page, output_dir):
        self._confirm(page_loaded)
        page_loaded.get_by_role("button", name="↶ Undo").click()
        page_loaded.wait_for_load_state("networkidle")
        decisions = json.loads((output_dir / "decisions.json").read_text())
        assert "1" not in decisions

    def test_undo_disabled_after_undo(self, page_loaded: Page):
        self._confirm(page_loaded)
        page_loaded.get_by_role("button", name="↶ Undo").click()
        page_loaded.wait_for_load_state("networkidle")
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

    def test_enter_confirms_current_item(self, page_loaded: Page, output_dir):
        self._open_singles(page_loaded)
        page_loaded.keyboard.press("Enter")
        page_loaded.wait_for_load_state("networkidle")
        deleted = (output_dir / "to_delete.txt").read_text().splitlines()
        assert len(deleted) == 1
        assert "DSCF4283" in deleted[0]
        decisions = json.loads((output_dir / "decisions.json").read_text())
        assert "3" in decisions  # singleton cluster_id 3 recorded

    def test_confirm_all_writes_delete_list(self, page_loaded: Page, output_dir):
        # No auto-keep threshold anymore: all unmarked singles default to Delete
        self._open_singles(page_loaded)
        page_loaded.get_by_role("button", name="✓ Confirm").click()
        page_loaded.wait_for_load_state("networkidle")
        deleted = (output_dir / "to_delete.txt").read_text().splitlines()
        assert len(deleted) == _singleton_count()
        assert any("DSCF4283" in d for d in deleted)
        assert any("DSCF4284" in d for d in deleted)

    def test_confirm_all_marks_confirmed(self, page_loaded: Page):
        # Tab no longer disappears after confirm-all; the current item is
        # badged "confirmed" and the tab stays visible.
        self._open_singles(page_loaded)
        page_loaded.get_by_role("button", name="✓ Confirm").click()
        page_loaded.wait_for_load_state("networkidle")
        expect(page_loaded.get_by_text("confirmed", exact=True)).to_be_visible()
        expect(page_loaded.get_by_text(f"Singles ({_singleton_count()})")).to_be_visible()


# ---------------------------------------------------------------------------
# Trash panel
# ---------------------------------------------------------------------------
class TestTrashPanel:
    def _confirm_cluster(self, page: Page) -> None:
        page.get_by_role("button", name="✓ Confirm").click()
        page.wait_for_load_state("networkidle")

    def test_trash_panel_shows_after_confirm(self, page_loaded: Page):
        self._confirm_cluster(page_loaded)
        expect(page_loaded.get_by_text("Trash —")).to_be_visible()

    def test_trash_panel_expands(self, page_loaded: Page):
        self._confirm_cluster(page_loaded)
        page_loaded.get_by_text("▼ expand").click()
        expect(page_loaded.get_by_role("button", name="🗑️ Move 2 to trash")).to_be_visible()

    def test_trash_restore_removes_from_delete_list(self, page_loaded: Page, output_dir):
        self._confirm_cluster(page_loaded)
        page_loaded.get_by_text("▼ expand").click()
        trash_filename = page_loaded.locator(".grid .rounded p.text-xs").first
        expect(trash_filename).to_be_visible(timeout=10_000)
        trash_filename.click()
        expect(page_loaded.locator(".border-blue-400").first).to_be_visible(timeout=5_000)
        page_loaded.get_by_role("button", name="Restore 1 selected").click()
        page_loaded.wait_for_load_state("networkidle")
        after = (output_dir / "to_delete.txt").read_text().strip().splitlines()
        assert len(after) == 1  # started with 2 (ranks 2,3), restored 1

    def test_apply_deletes_clears_list(self, page_loaded: Page, output_dir):
        # Nonexistent paths: apply must not touch real files during tests
        (output_dir / "to_delete.txt").write_text(
            "demo_photos/NOPE_1.JPG\ndemo_photos/NOPE_2.JPG\n"
        )
        page_loaded.reload()
        page_loaded.get_by_text("▼ expand").click()
        page_loaded.once("dialog", lambda d: d.accept())
        page_loaded.get_by_role("button", name="🗑️ Move 2 to trash").click()
        page_loaded.wait_for_load_state("networkidle")
        content = (output_dir / "to_delete.txt").read_text().strip()
        assert content == ""
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
        page_loaded.wait_for_load_state("networkidle")
        assert len((output_dir / "to_delete.txt").read_text().splitlines()) == 2
        page_loaded.keyboard.press("u")
        page_loaded.wait_for_load_state("networkidle")
        assert (output_dir / "to_delete.txt").read_text().strip() == ""

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
