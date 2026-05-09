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
        expect(page_loaded.get_by_role("heading", name="Sightread")).to_be_visible()

    def test_cluster_tab_visible(self, page_loaded: Page):
        expect(page_loaded.get_by_text(f"Clusters ({_cluster_count()})")).to_be_visible()

    def test_singles_tab_visible(self, page_loaded: Page):
        expect(page_loaded.get_by_text(f"Singles ({_singleton_count()})")).to_be_visible()

    def test_undo_disabled_initially(self, page_loaded: Page):
        expect(page_loaded.get_by_role("button", name="↶ Undo")).to_be_disabled()

    def test_progress_bar_visible(self, page_loaded: Page):
        expect(page_loaded.locator(".bg-blue-500")).to_be_visible()

    def test_cluster_select_shows_first(self, page_loaded: Page):
        # Select dropdown should show "1/N" for the first cluster
        select = page_loaded.locator("select")
        expect(select).to_have_value("0")  # 0-indexed value


# ---------------------------------------------------------------------------
# Navigation (buttons)
# ---------------------------------------------------------------------------
class TestNavigation:
    def test_prev_disabled_on_first(self, page_loaded: Page):
        prev = page_loaded.get_by_role("button", name="←")
        expect(prev).to_be_disabled()

    def test_next_advances_cluster(self, page_loaded: Page):
        page_loaded.get_by_role("button", name="→").click()
        expect(page_loaded.locator("select")).to_have_value("1")

    def test_prev_returns_to_first(self, page_loaded: Page):
        page_loaded.get_by_role("button", name="→").click()
        page_loaded.get_by_role("button", name="←").click()
        expect(page_loaded.get_by_role("button", name="←")).to_be_disabled()

    def test_next_disabled_on_last(self, page_loaded: Page):
        for _ in range(_cluster_count() - 1):
            page_loaded.get_by_role("button", name="→").click()
        expect(page_loaded.get_by_role("button", name="→")).to_be_disabled()

    def test_skip_advances_cluster(self, page_loaded: Page):
        page_loaded.get_by_role("button", name="Skip").click()
        expect(page_loaded.locator("select")).to_have_value("1")


# ---------------------------------------------------------------------------
# Keyboard navigation
# ---------------------------------------------------------------------------
class TestKeyboardNav:
    def test_l_advances_cluster(self, page_loaded: Page):
        page_loaded.keyboard.press("l")
        expect(page_loaded.locator("select")).to_have_value("1")

    def test_h_goes_back(self, page_loaded: Page):
        page_loaded.keyboard.press("l")
        page_loaded.keyboard.press("h")
        expect(page_loaded.locator("select")).to_have_value("0")

    def test_h_noop_on_first(self, page_loaded: Page):
        page_loaded.keyboard.press("h")
        expect(page_loaded.locator("select")).to_have_value("0")

    def test_j_moves_image_focus(self, page_loaded: Page):
        # First image card should be focused (border-blue-400)
        expect(page_loaded.locator(".border-blue-400").first).to_be_visible()
        page_loaded.keyboard.press("j")
        # Focus moved — second card now has blue border; count still 1
        cards = page_loaded.locator(".border-blue-400")
        expect(cards).to_have_count(1)

    def test_k_moves_focus_back(self, page_loaded: Page):
        page_loaded.keyboard.press("j")
        page_loaded.keyboard.press("k")
        # Back to index 0 — first card focused
        expect(page_loaded.locator(".border-blue-400").first).to_be_visible()

    def test_space_toggles_focused_image(self, page_loaded: Page):
        # Rank-1 image starts as Keep (green). Space should flip to Delete.
        expect(page_loaded.locator("button.bg-green-50").first).to_be_visible()
        page_loaded.keyboard.press("Space")
        # First card (focused) now red
        expect(page_loaded.locator("button.bg-red-50").first).to_be_visible()

    def test_space_toggles_back(self, page_loaded: Page):
        page_loaded.keyboard.press("Space")  # → delete
        page_loaded.keyboard.press("Space")  # → keep again
        expect(page_loaded.locator("button.bg-green-50").first).to_be_visible()

    def test_enter_confirms(self, page_loaded: Page, output_dir):
        page_loaded.keyboard.press("Enter")
        page_loaded.wait_for_load_state("networkidle")
        deleted = (output_dir / "to_delete.txt").read_text().splitlines()
        assert len(deleted) == 2  # ranks 2 and 3 deleted by default

    def test_enter_confirm_reduces_cluster_count(self, page_loaded: Page):
        page_loaded.keyboard.press("Enter")
        page_loaded.wait_for_load_state("networkidle")
        expect(page_loaded.get_by_text(f"Clusters ({_cluster_count() - 1})")).to_be_visible()


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
        expect(page_loaded.locator("button.bg-red-50").first).to_be_visible()

    def test_toggle_button_flips(self, page_loaded: Page):
        page_loaded.locator("button.bg-green-50").first.click()
        expect(page_loaded.locator("button.bg-red-50").first).to_be_visible()


# ---------------------------------------------------------------------------
# Keep Best
# ---------------------------------------------------------------------------
class TestKeepBest:
    def test_keep_best_sets_rank1_green(self, page_loaded: Page):
        page_loaded.locator("button.bg-green-50").first.click()
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
        expect(page_loaded.locator("select")).to_have_value("0")
        expect(page_loaded.get_by_text(f"Clusters ({_cluster_count() - 1})")).to_be_visible()

    def test_confirm_removes_from_results(self, page_loaded: Page, output_dir):
        page_loaded.get_by_role("button", name="✓ Confirm").click()
        page_loaded.wait_for_load_state("networkidle")
        results = json.loads((output_dir / "results.json").read_text())
        multi = [c for c in results["clusters"] if len(c["images"]) > 1]
        assert len(multi) == _cluster_count() - 1

    def test_skip_does_not_write_delete_list(self, page_loaded: Page, output_dir):
        page_loaded.get_by_role("button", name="Skip").click()
        content = (output_dir / "to_delete.txt").read_text().strip()
        assert content == ""


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------
class TestUndo:
    def test_undo_enabled_after_confirm(self, page_loaded: Page):
        page_loaded.get_by_role("button", name="✓ Confirm").click()
        page_loaded.wait_for_load_state("networkidle")
        expect(page_loaded.get_by_role("button", name="↶ Undo")).to_be_enabled()

    def test_undo_clears_delete_list(self, page_loaded: Page, output_dir):
        page_loaded.get_by_role("button", name="✓ Confirm").click()
        page_loaded.wait_for_load_state("networkidle")
        page_loaded.get_by_role("button", name="↶ Undo").click()
        page_loaded.wait_for_load_state("networkidle")
        content = (output_dir / "to_delete.txt").read_text().strip()
        assert content == ""

    def test_undo_restores_cluster(self, page_loaded: Page, output_dir):
        page_loaded.get_by_role("button", name="✓ Confirm").click()
        page_loaded.wait_for_load_state("networkidle")
        page_loaded.get_by_role("button", name="↶ Undo").click()
        page_loaded.wait_for_load_state("networkidle")
        results = json.loads((output_dir / "results.json").read_text())
        multi = [c for c in results["clusters"] if len(c["images"]) > 1]
        assert len(multi) == _cluster_count()

    def test_undo_disabled_after_undo(self, page_loaded: Page):
        page_loaded.get_by_role("button", name="✓ Confirm").click()
        page_loaded.wait_for_load_state("networkidle")
        page_loaded.get_by_role("button", name="↶ Undo").click()
        page_loaded.wait_for_load_state("networkidle")
        expect(page_loaded.get_by_role("button", name="↶ Undo")).to_be_disabled()


# ---------------------------------------------------------------------------
# Singles tab
# ---------------------------------------------------------------------------
class TestSingles:
    def _open_singles(self, page: Page) -> None:
        page.get_by_text(f"Singles ({_singleton_count()})").click()
        page.wait_for_selector("button:has-text('✓ Confirm Singles')", timeout=8_000)

    def test_singles_tab_renders(self, page_loaded: Page):
        self._open_singles(page_loaded)
        expect(page_loaded.get_by_role("button", name="✓ Confirm Singles")).to_be_visible()

    def test_bad_singleton_starts_delete(self, page_loaded: Page):
        self._open_singles(page_loaded)
        expect(page_loaded.locator("button.bg-red-50").first).to_be_visible()

    def test_keep_all_removes_delete_buttons(self, page_loaded: Page):
        self._open_singles(page_loaded)
        page_loaded.get_by_role("button", name="✅ Keep All").click()
        expect(page_loaded.locator("button.bg-red-50")).to_have_count(0)

    def test_select_all_bad_restores_deletes(self, page_loaded: Page):
        self._open_singles(page_loaded)
        page_loaded.get_by_role("button", name="✅ Keep All").click()
        page_loaded.get_by_role("button", name="🗑️ Select All Bad").click()
        expect(page_loaded.locator("button.bg-red-50").first).to_be_visible()

    def test_threshold_slider_visible(self, page_loaded: Page):
        self._open_singles(page_loaded)
        slider = page_loaded.locator("input[type='range']")
        expect(slider).to_be_visible()
        expect(slider).to_have_attribute("min", "0")
        expect(slider).to_have_attribute("max", "1")

    def test_threshold_slider_high_marks_all_delete(self, page_loaded: Page):
        self._open_singles(page_loaded)
        # Set threshold above 0.65 (highest singleton score) → both become delete
        slider = page_loaded.locator("input[type='range']")
        slider.fill("0.99")
        slider.dispatch_event("input")
        page_loaded.wait_for_timeout(200)
        expect(page_loaded.locator("button.bg-red-50")).to_have_count(2)

    def test_threshold_slider_zero_marks_all_keep(self, page_loaded: Page):
        self._open_singles(page_loaded)
        # Set threshold to 0 → all become keep
        slider = page_loaded.locator("input[type='range']")
        slider.fill("0")
        slider.dispatch_event("input")
        page_loaded.wait_for_timeout(200)
        expect(page_loaded.locator("button.bg-red-50")).to_have_count(0)

    def test_singles_confirm_writes_delete_list(self, page_loaded: Page, output_dir):
        self._open_singles(page_loaded)
        page_loaded.get_by_role("button", name="✓ Confirm Singles").click()
        page_loaded.wait_for_load_state("networkidle")
        deleted = (output_dir / "to_delete.txt").read_text().splitlines()
        assert len(deleted) == 1
        assert "DSCF4283" in deleted[0]


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
        expect(page_loaded.get_by_text("delete_marked.py")).to_be_visible()

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


# ---------------------------------------------------------------------------
# Completion screen
# ---------------------------------------------------------------------------
class TestCompletion:
    def _confirm_all(self, page: Page) -> None:
        for _ in range(_cluster_count()):
            page.wait_for_selector("button:has-text('✓ Confirm')", timeout=10_000)
            page.get_by_role("button", name="✓ Confirm").click()
            page.wait_for_load_state("networkidle")
        page.wait_for_selector("button:has-text('✓ Confirm Singles')", timeout=10_000)
        page.get_by_role("button", name="✓ Confirm Singles").click()
        page.wait_for_load_state("networkidle")

    def test_completion_message_shown(self, page_loaded: Page):
        self._confirm_all(page_loaded)
        expect(page_loaded.get_by_text("All done!")).to_be_visible(timeout=10_000)

    def test_completion_shows_pending_count(self, page_loaded: Page):
        self._confirm_all(page_loaded)
        expect(page_loaded.get_by_text("images pending deletion", exact=False)).to_be_visible(timeout=10_000)

    def test_completion_hides_tabs(self, page_loaded: Page):
        self._confirm_all(page_loaded)
        expect(page_loaded.get_by_text(f"Clusters ({_cluster_count()})")).not_to_be_visible(timeout=10_000)


# ---------------------------------------------------------------------------
# New keyboard bindings — ClusterView
# ---------------------------------------------------------------------------
class TestKeyboardNewBindings:
    def test_b_skips_cluster(self, page_loaded: Page):
        page_loaded.keyboard.press("b")
        expect(page_loaded.locator("select")).to_have_value("1")

    def test_s_skips_cluster(self, page_loaded: Page):
        page_loaded.keyboard.press("s")
        expect(page_loaded.locator("select")).to_have_value("1")

    def test_shift_k_keep_best(self, page_loaded: Page):
        # Cluster 1 has 3 images; rank-1 keep, rank-2 keep, rank-3 delete by default.
        # After K: only rank-1 green.
        page_loaded.keyboard.press("K")
        expect(page_loaded.locator("button.bg-green-50")).to_have_count(1)
        expect(page_loaded.locator("button.bg-red-50")).to_have_count(2)

    def test_digit_1_toggles_rank1(self, page_loaded: Page):
        # rank-1 starts Keep (green). Press 1 to toggle to Delete.
        expect(page_loaded.locator("button.bg-green-50").first).to_be_visible()
        page_loaded.keyboard.press("1")
        # rank-1 flipped → all 3 images now delete
        expect(page_loaded.locator("button.bg-red-50")).to_have_count(3)

    def test_digit_2_toggles_rank2(self, page_loaded: Page):
        # rank-2 starts Delete. Press 2 to toggle to Keep.
        page_loaded.keyboard.press("2")
        expect(page_loaded.locator("button.bg-green-50")).to_have_count(2)

    def test_u_undo_after_confirm(self, page_loaded: Page):
        page_loaded.keyboard.press("Enter")
        page_loaded.wait_for_load_state("networkidle")
        expect(page_loaded.get_by_text(f"Clusters ({_cluster_count() - 1})")).to_be_visible()
        page_loaded.keyboard.press("u")
        page_loaded.wait_for_load_state("networkidle")
        expect(page_loaded.get_by_text(f"Clusters ({_cluster_count()})")).to_be_visible()

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
# New keyboard bindings — SingletonsView
# ---------------------------------------------------------------------------
class TestSinglesKeyboard:
    def _open_singles(self, page: Page) -> None:
        page.get_by_text(f"Singles ({_singleton_count()})").click()
        page.wait_for_selector("button:has-text('✓ Confirm Singles')", timeout=8_000)

    def test_singles_j_moves_focus(self, page_loaded: Page):
        self._open_singles(page_loaded)
        page_loaded.keyboard.press("j")
        expect(page_loaded.locator(".border-blue-400")).to_have_count(1)

    def test_singles_k_moves_focus_back(self, page_loaded: Page):
        self._open_singles(page_loaded)
        page_loaded.keyboard.press("j")
        page_loaded.keyboard.press("k")
        # back to index 0
        expect(page_loaded.locator(".border-blue-400")).to_have_count(1)

    def test_singles_space_toggles_focused(self, page_loaded: Page):
        self._open_singles(page_loaded)
        # index 0 = lowest score (0.30) = delete (red) by default
        # space should flip it to keep
        page_loaded.keyboard.press("Space")
        # both now green
        expect(page_loaded.locator("button.bg-red-50")).to_have_count(0)

    def test_singles_a_keep_all(self, page_loaded: Page):
        self._open_singles(page_loaded)
        page_loaded.keyboard.press("a")
        expect(page_loaded.locator("button.bg-red-50")).to_have_count(0)

    def test_singles_d_select_bad(self, page_loaded: Page):
        self._open_singles(page_loaded)
        page_loaded.keyboard.press("a")  # keep all first
        page_loaded.keyboard.press("d")  # reset to threshold
        expect(page_loaded.locator("button.bg-red-50").first).to_be_visible()

    def test_singles_enter_confirms(self, page_loaded: Page, output_dir):
        self._open_singles(page_loaded)
        page_loaded.keyboard.press("Enter")
        page_loaded.wait_for_load_state("networkidle")
        deleted = (output_dir / "to_delete.txt").read_text().splitlines()
        assert len(deleted) == 1
        assert "DSCF4283" in deleted[0]
