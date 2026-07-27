"""Unit tests for chronological cluster ordering and portrait/landscape merging."""

import json

import numpy as np
import pytest
from PIL import Image

import pipeline
from utils import cluster_shot_at, sort_clusters_chronologically


def _unit(vec: list[float]) -> np.ndarray:
    arr = np.array(vec, dtype=np.float32)
    return arr / np.linalg.norm(arr)


def _embeddings(vectors: list[list[float]]) -> np.ndarray:
    return np.stack([_unit(v) for v in vectors])


# ---------------------------------------------------------------------------
# EXIF orientation
# ---------------------------------------------------------------------------
def _write_jpeg(path, size, orientation=None):
    img = Image.new("RGB", size, "gray")
    exif = img.getexif()
    exif[pipeline._EXIF_DATETIME_TAG] = "2026:07:26 10:00:00"
    if orientation is not None:
        exif[pipeline._EXIF_ORIENTATION_TAG] = orientation
    img.save(path, exif=exif)


class TestReadExifMeta:
    def test_landscape_from_pixel_dimensions(self, tmp_path):
        p = tmp_path / "wide.jpg"
        _write_jpeg(p, (200, 100))
        ts, orientation = pipeline._read_exif_meta(str(p))
        assert orientation == "landscape"
        assert ts is not None

    def test_portrait_from_pixel_dimensions(self, tmp_path):
        p = tmp_path / "tall.jpg"
        _write_jpeg(p, (100, 200))
        assert pipeline._read_exif_meta(str(p))[1] == "portrait"

    def test_rotation_tag_swaps_axes(self, tmp_path):
        # Stored landscape, but Orientation=6 displays it rotated 90°.
        p = tmp_path / "rotated.jpg"
        _write_jpeg(p, (200, 100), orientation=6)
        assert pipeline._read_exif_meta(str(p))[1] == "portrait"

    def test_square(self, tmp_path):
        p = tmp_path / "square.jpg"
        _write_jpeg(p, (150, 150))
        assert pipeline._read_exif_meta(str(p))[1] == "square"

    def test_unreadable_file_is_unknown(self, tmp_path):
        p = tmp_path / "broken.jpg"
        p.write_bytes(b"not an image")
        assert pipeline._read_exif_meta(str(p)) == (None, "unknown")


# ---------------------------------------------------------------------------
# Portrait/landscape merge
# ---------------------------------------------------------------------------
class TestOrientationMerge:
    # Two clusters of the same subject: near-identical embeddings, 10s apart,
    # one framed landscape and one portrait.
    def _fixture(self, gap_s=10.0, kinds=("landscape", "portrait"), similar=True):
        near = [1.0, 0.0, 0.0]
        far = [1.0, 0.0, 0.0] if similar else [0.0, 1.0, 0.0]
        embeddings = _embeddings([near, near, far, far])
        timestamps = [0.0, 1.0, gap_s, gap_s + 1.0]
        orientations = [kinds[0], kinds[0], kinds[1], kinds[1]]
        labels = np.array([0, 0, 1, 1], dtype=np.int64)
        return embeddings, timestamps, orientations, labels

    def _merge(self, *args, window_s=120.0, threshold=0.38, max_gap_s=3600.0):
        embeddings, timestamps, orientations, labels = args
        return pipeline._merge_orientation_pairs(
            embeddings, timestamps, orientations, labels,
            window_s=window_s, threshold=threshold, max_gap_s=max_gap_s,
        )

    def test_merges_reshoot_across_orientation(self):
        out = self._merge(*self._fixture())
        assert len(set(out.tolist())) == 1

    def test_leaves_same_orientation_alone(self):
        out = self._merge(*self._fixture(kinds=("landscape", "landscape")))
        assert len(set(out.tolist())) == 2

    def test_leaves_dissimilar_subjects_alone(self):
        out = self._merge(*self._fixture(similar=False))
        assert len(set(out.tolist())) == 2

    def test_respects_the_time_window(self):
        out = self._merge(*self._fixture(gap_s=600.0), window_s=120.0)
        assert len(set(out.tolist())) == 2

    def test_threshold_gates_the_merge(self):
        # Centroids sit at cosine distance 0.3 from each other.
        a = [1.0, 0.0, 0.0]
        b = [0.7, np.sqrt(1 - 0.7**2), 0.0]
        embeddings = _embeddings([a, a, b, b])
        timestamps = [0.0, 1.0, 10.0, 11.0]
        orientations = ["landscape", "landscape", "portrait", "portrait"]

        loose = self._merge(embeddings, timestamps, orientations,
                            np.array([0, 0, 1, 1], dtype=np.int64), threshold=0.35)
        assert len(set(loose.tolist())) == 1

        strict = self._merge(embeddings, timestamps, orientations,
                             np.array([0, 0, 1, 1], dtype=np.int64), threshold=0.25)
        assert len(set(strict.tolist())) == 2

    def test_images_without_timestamps_are_never_merged(self):
        embeddings, _, orientations, labels = self._fixture()
        out = self._merge(embeddings, [None] * 4, orientations, labels)
        assert len(set(out.tolist())) == 2

    def test_chained_merges_respect_max_gap(self):
        # Three same-subject clusters, each 100s from the next. Merging all
        # three spans 200s, which exceeds a 150s max_gap_s.
        near = [1.0, 0.0, 0.0]
        embeddings = _embeddings([near] * 3)
        timestamps = [0.0, 100.0, 200.0]
        orientations = ["landscape", "portrait", "landscape"]
        labels = np.array([0, 1, 2], dtype=np.int64)
        out = self._merge(
            embeddings, timestamps, orientations, labels,
            window_s=120.0, max_gap_s=150.0,
        )
        assert len(set(out.tolist())) == 2

    def test_cluster_embeddings_skips_stage_when_orientations_absent(self):
        near = [1.0, 0.0, 0.0]
        embeddings = _embeddings([near, [0.0, 1.0, 0.0]])
        clusters = pipeline.cluster_embeddings(
            embeddings, [0.0, 5.0], None, tight=0.01, loose=0.05,
        )
        assert len(clusters) == 2


# ---------------------------------------------------------------------------
# Chronological ordering
# ---------------------------------------------------------------------------
class TestChronologicalOrdering:
    def test_rank_and_save_orders_clusters_by_first_shot(self, tmp_path):
        paths = ["late_a.jpg", "late_b.jpg", "early_a.jpg", "early_b.jpg"]
        clusters = {0: [0, 1], 1: [2, 3]}
        # The later cluster scores higher — time must still win.
        scores = [0.9, 0.8, 0.3, 0.2]
        embeddings = _embeddings([[1.0, 0.0]] * 4)
        timestamps = [500.0, 501.0, 100.0, 101.0]

        results = pipeline.rank_and_save(
            paths, clusters, scores, embeddings, tmp_path, timestamps=timestamps,
        )
        assert [c["cluster_id"] for c in results["clusters"]] == [1, 0]
        assert results["clusters"][0]["cluster_timestamp"] == 100.0

        on_disk = json.loads((tmp_path / "results.json").read_text())
        assert [c["cluster_id"] for c in on_disk["clusters"]] == [1, 0]

    def test_clusters_without_exif_trail_the_timeline(self, tmp_path):
        paths = ["no_exif.jpg", "dated.jpg"]
        clusters = {0: [0], 1: [1]}
        scores = [0.99, 0.1]
        embeddings = _embeddings([[1.0, 0.0]] * 2)
        timestamps = [None, 900.0]

        results = pipeline.rank_and_save(
            paths, clusters, scores, embeddings, tmp_path, timestamps=timestamps,
        )
        assert [c["cluster_id"] for c in results["clusters"]] == [1, 0]
        assert "cluster_timestamp" not in results["clusters"][1]


class TestSortClustersChronologically:
    def test_uses_cluster_timestamp_when_present(self):
        clusters = [
            {"cluster_id": 1, "cluster_score": 0.9, "cluster_timestamp": 300.0, "images": []},
            {"cluster_id": 2, "cluster_score": 0.1, "cluster_timestamp": 100.0, "images": []},
        ]
        assert [c["cluster_id"] for c in sort_clusters_chronologically(clusters)] == [2, 1]

    def test_derives_time_from_images_for_legacy_results(self):
        # results.json written before cluster_timestamp existed.
        clusters = [
            {"cluster_id": 1, "cluster_score": 0.9,
             "images": [{"path": "b.jpg", "exif_timestamp": 300.0}]},
            {"cluster_id": 2, "cluster_score": 0.1,
             "images": [{"path": "a.jpg", "exif_timestamp": 100.0},
                        {"path": "c.jpg", "exif_timestamp": 400.0}]},
        ]
        assert [c["cluster_id"] for c in sort_clusters_chronologically(clusters)] == [2, 1]

    def test_undated_clusters_trail_and_fall_back_to_score(self):
        clusters = [
            {"cluster_id": 1, "cluster_score": 0.2, "images": []},
            {"cluster_id": 2, "cluster_score": 0.8, "images": []},
            {"cluster_id": 3, "cluster_score": 0.5, "cluster_timestamp": 100.0, "images": []},
        ]
        assert [c["cluster_id"] for c in sort_clusters_chronologically(clusters)] == [3, 2, 1]

    def test_cluster_shot_at_returns_none_without_exif(self):
        assert cluster_shot_at({"images": [{"path": "a.jpg"}]}) is None
