"""Unit tests for the single-file, single-status curation state.

The prior design split state across decisions.json, to_delete.txt and
favorites.json with nothing keeping them in agreement, and they drifted: photos
marked kept sat in the delete queue. These tests pin the merged model and the
rules that fold the old files into it.
"""

import json
from pathlib import Path

import pytest

from utils import (
    DELETED,
    FAVORITE,
    KEPT,
    SCHEMA_VERSION,
    TO_DELETE,
    load_decisions,
    merge_legacy_state,
    migrate_project_state,
    paths_with_status,
    pending_deletes,
    save_decisions,
)


def write_legacy(output_dir, decisions=None, queued=(), favorites=()):
    if decisions is not None:
        (output_dir / "decisions.json").write_text(json.dumps(decisions))
    if queued:
        (output_dir / "to_delete.txt").write_text("\n".join(queued) + "\n")
    if favorites:
        (output_dir / "favorites.json").write_text(json.dumps(list(favorites)))


class TestLoadAndSave:
    def test_missing_state(self, tmp_path):
        assert load_decisions(tmp_path) == {}

    def test_round_trip(self, tmp_path):
        save_decisions(tmp_path, {"a.jpg": KEPT, "b.jpg": TO_DELETE})
        assert load_decisions(tmp_path) == {"a.jpg": KEPT, "b.jpg": TO_DELETE}

    def test_writes_versioned_envelope(self, tmp_path):
        save_decisions(tmp_path, {"a.jpg": KEPT})
        raw = json.loads((tmp_path / "decisions.json").read_text())
        assert raw["schema_version"] == SCHEMA_VERSION
        assert raw["photos"] == {"a.jpg": KEPT}

    def test_none_clears_a_status(self, tmp_path):
        save_decisions(tmp_path, {"a.jpg": KEPT, "b.jpg": TO_DELETE})
        save_decisions(tmp_path, {"a.jpg": None})
        assert load_decisions(tmp_path) == {"b.jpg": TO_DELETE}

    def test_unknown_status_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            save_decisions(tmp_path, {"a.jpg": "maybe"})

    def test_unknown_status_on_disk_ignored(self, tmp_path):
        (tmp_path / "decisions.json").write_text(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "photos": {"a.jpg": KEPT, "b.jpg": "maybe"},
        }))
        assert load_decisions(tmp_path) == {"a.jpg": KEPT}

    def test_corrupt_file_is_backed_up(self, tmp_path):
        (tmp_path / "decisions.json").write_text("{not json")
        assert load_decisions(tmp_path) == {}
        assert (tmp_path / "decisions.json.corrupt").exists()

    def test_write_is_atomic(self, tmp_path):
        # A leftover temp file must never be mistaken for the real thing.
        save_decisions(tmp_path, {"a.jpg": KEPT})
        assert not list(tmp_path.glob("decisions.json.tmp"))

    def test_pending_deletes_is_derived(self, tmp_path):
        save_decisions(tmp_path, {
            "a.jpg": KEPT, "b.jpg": TO_DELETE, "c.jpg": FAVORITE, "d.jpg": DELETED,
        })
        assert pending_deletes(tmp_path) == ["b.jpg"]


class TestMergeRules:
    """Conflicts resolve away from deletion, never toward it."""

    def _merge(self, decisions, queued=(), favorites=(), on_disk=lambda p: True):
        return merge_legacy_state(decisions, set(queued), set(favorites), on_disk)

    def test_keep_beats_a_stale_queue_entry(self):
        # The live failure: 44 photos marked kept were still queued, and the
        # sweep reads the queue.
        merged = self._merge({"1": {"kept": ["a.jpg"], "deleted": []}}, queued=["a.jpg"])
        assert merged == {"a.jpg": KEPT}

    def test_star_beats_a_queue_entry(self):
        merged = self._merge({}, queued=["a.jpg"], favorites=["a.jpg"])
        assert merged == {"a.jpg": FAVORITE}

    def test_star_beats_a_delete_decision(self):
        merged = self._merge(
            {"1": {"kept": [], "deleted": ["a.jpg"]}}, favorites=["a.jpg"]
        )
        assert merged == {"a.jpg": FAVORITE}

    def test_pending_delete_stays_pending(self):
        merged = self._merge({"1": {"kept": [], "deleted": ["a.jpg"]}}, queued=["a.jpg"])
        assert merged == {"a.jpg": TO_DELETE}

    def test_applied_delete_is_recorded_as_gone(self):
        # Legacy "deleted" meant either queued or already applied; file
        # existence is what tells them apart.
        merged = self._merge(
            {"1": {"kept": [], "deleted": ["a.jpg"]}}, on_disk=lambda p: False
        )
        assert merged == {"a.jpg": DELETED}

    def test_queued_but_already_gone_is_recorded_as_gone(self):
        merged = self._merge({}, queued=["a.jpg"], on_disk=lambda p: False)
        assert merged == {"a.jpg": DELETED}

    def test_plain_keep_survives(self):
        merged = self._merge({"1": {"kept": ["a.jpg"], "deleted": []}})
        assert merged == {"a.jpg": KEPT}

    def test_every_source_path_appears_exactly_once(self):
        merged = self._merge(
            {"1": {"kept": ["a.jpg"], "deleted": ["b.jpg"]}},
            queued=["b.jpg", "c.jpg"],
            favorites=["d.jpg"],
        )
        assert set(merged) == {"a.jpg", "b.jpg", "c.jpg", "d.jpg"}


class TestMigration:
    def test_folds_three_files_into_one(self, tmp_path):
        # Absolute paths: whether a file is still on disk is what separates a
        # pending delete from an applied one.
        a, b, c, d = (str(tmp_path / n) for n in ("a.jpg", "b.jpg", "c.jpg", "d.jpg"))
        for p in (a, b, c, d):
            Path(p).write_bytes(b"px")
        write_legacy(
            tmp_path,
            decisions={"1": {"kept": [a], "deleted": [b]}},
            queued=[b, c],
            favorites=[d],
        )

        assert migrate_project_state(tmp_path) is True
        assert load_decisions(tmp_path) == {
            a: KEPT, b: TO_DELETE, c: TO_DELETE, d: FAVORITE,
        }

    def test_supersededfiles_are_kept_as_backups(self, tmp_path):
        write_legacy(tmp_path, decisions={}, queued=["b.jpg"], favorites=["d.jpg"])
        migrate_project_state(tmp_path)
        assert not (tmp_path / "to_delete.txt").exists()
        assert not (tmp_path / "favorites.json").exists()
        assert (tmp_path / "to_delete.txt.migrated").exists()
        assert (tmp_path / "favorites.json.migrated").exists()

    def test_is_idempotent(self, tmp_path):
        write_legacy(tmp_path, decisions={"1": {"kept": ["a.jpg"], "deleted": []}})
        assert migrate_project_state(tmp_path) is True
        before = load_decisions(tmp_path)
        assert migrate_project_state(tmp_path) is False
        assert load_decisions(tmp_path) == before

    def test_nothing_to_migrate(self, tmp_path):
        assert migrate_project_state(tmp_path) is False

    def test_reading_legacy_state_does_not_rewrite_it(self, tmp_path):
        write_legacy(tmp_path, decisions={"1": {"kept": ["a.jpg"], "deleted": []}})
        original = (tmp_path / "decisions.json").read_text()
        assert load_decisions(tmp_path) == {"a.jpg": KEPT}
        assert (tmp_path / "decisions.json").read_text() == original

    def test_legacy_queue_without_decisions_file(self, tmp_path):
        b = str(tmp_path / "b.jpg")
        Path(b).write_bytes(b"px")
        write_legacy(tmp_path, queued=[b])
        assert load_decisions(tmp_path) == {b: TO_DELETE}

    def test_survives_a_recluster(self, tmp_path):
        # Statuses key off photo paths, so renumbering clusters changes nothing.
        write_legacy(tmp_path, decisions={"1": {"kept": ["a.jpg"], "deleted": ["b.jpg"]}})
        migrate_project_state(tmp_path)
        before = load_decisions(tmp_path)
        assert load_decisions(tmp_path) == before


class TestPathsWithStatus:
    def test_filters_by_status(self):
        decisions = {"a.jpg": KEPT, "b.jpg": TO_DELETE, "c.jpg": TO_DELETE}
        assert sorted(paths_with_status(decisions, TO_DELETE)) == ["b.jpg", "c.jpg"]

    def test_empty_when_none_match(self):
        assert paths_with_status({"a.jpg": KEPT}, DELETED) == []
