"""Unit tests for chronological cluster ordering and the re-shoot merges."""

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
        ts, orientation, _model = pipeline._read_exif_meta(str(p))
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
        assert pipeline._read_exif_meta(str(p)) == (None, "unknown", None)


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

    def _merge(self, *args, window_s=120.0, threshold=0.38, max_span_s=3600.0,
               models=None, cross_window_s=0.0, cross_threshold=0.22):
        embeddings, timestamps, orientations, labels = args
        return pipeline._merge_reshoot_pairs(
            embeddings, timestamps, orientations, labels,
            window_s=window_s, threshold=threshold, max_span_s=max_span_s,
            models=models, cross_window_s=cross_window_s,
            cross_threshold=cross_threshold,
        )

    def test_merges_reshoot_across_orientation(self):
        out = self._merge(*self._fixture())
        assert len(set(out.tolist())) == 1

    def test_leaves_same_orientation_alone(self):
        out = self._merge(*self._fixture(kinds=("landscape", "landscape")))
        assert len(set(out.tolist())) == 2

    def test_one_camera_shooting_twice_is_not_a_reshoot(self):
        """Same framing, same model: the tight/burst stages already had their
        say about these two, and nothing here should second-guess them."""
        out = self._merge(
            *self._fixture(kinds=("landscape", "landscape")),
            models=["X-T5"] * 4, cross_window_s=300.0,
        )
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

    def test_chained_merges_respect_the_span_cap(self):
        # Three same-subject clusters, each 100s from the next. Merging all
        # three spans 200s, which exceeds a 150s cap.
        near = [1.0, 0.0, 0.0]
        embeddings = _embeddings([near] * 3)
        timestamps = [0.0, 100.0, 200.0]
        orientations = ["landscape", "portrait", "landscape"]
        labels = np.array([0, 1, 2], dtype=np.int64)
        out = self._merge(
            embeddings, timestamps, orientations, labels,
            window_s=120.0, max_span_s=150.0,
        )
        assert len(set(out.tolist())) == 2

    def test_the_default_chain_cap_is_the_window_not_the_cluster_gap(self):
        """Re-shoots chain, and each link only has to be `window_s` from the
        last, so a cap of an hour let one walk right across it: on the Hoh
        trip that grew a 44-photo cluster of a single viewpoint into 74 over
        33 minutes."""
        # Three clusters, consecutive centroids 0.3 apart — too far for the
        # 0.22 same-scene threshold, near enough for the 0.38 re-shoot one.
        # The first and last are 1.02 apart, so they never pair directly.
        theta = np.arccos(0.7)
        a = [1.0, 0.0, 0.0]
        b = [float(np.cos(theta)), float(np.sin(theta)), 0.0]
        c = [float(np.cos(2 * theta)), float(np.sin(2 * theta)), 0.0]
        embeddings = _embeddings([a, b, c])
        timestamps = [0.0, 100.0, 200.0]
        orientations = ["landscape", "portrait", "landscape"]

        clusters = pipeline.cluster_embeddings(
            embeddings, timestamps, orientations, orient_window_s=120.0,
        )

        # Two of the three join; taking the third would span 200s.
        assert len(clusters) == 2

    def test_cluster_embeddings_skips_stage_when_orientations_absent(self):
        near = [1.0, 0.0, 0.0]
        embeddings = _embeddings([near, [0.0, 1.0, 0.0]])
        clusters = pipeline.cluster_embeddings(
            embeddings, [0.0, 5.0], None, tight=0.01, loose=0.05,
        )
        assert len(clusters) == 2


# ---------------------------------------------------------------------------
# Exposure-bracket merge (same camera, AE moved the embedding past tight)
# ---------------------------------------------------------------------------
class TestExposureBracket:
    """Canon AE hunting: same stairs 6s later, ISO 800 vs 400, dist 0.093.

    Tight is 0.08 and the 3s burst misses the gap. Widening burst with no
    embedding check chains a 20-minute Koyasan walk into one cluster.
    """

    def _pair(self, gap_s=6.0, dist=0.093):
        a = [1.0, 0.0, 0.0]
        theta = np.arccos(1.0 - dist)
        b = [float(np.cos(theta)), float(np.sin(theta)), 0.0]
        embeddings = _embeddings([a, a, b, b])
        # 1s inside each pair, `gap_s` between the last of A and first of B.
        timestamps = [0.0, 1.0, 1.0 + gap_s, 2.0 + gap_s]
        return embeddings, timestamps

    def _cluster(self, fixture, **kw):
        embeddings, timestamps = fixture
        opts = dict(tight=0.08, loose=0.22, burst_window_s=3.0, orient_window_s=0.0)
        opts.update(kw)
        return pipeline.cluster_embeddings(embeddings, timestamps, None, **opts)

    def test_joins_a_darker_reshoot_a_few_seconds_later(self):
        clusters = self._cluster(self._pair())
        assert len(clusters) == 1

    def test_does_not_join_a_different_subject_in_the_same_gap(self):
        clusters = self._cluster(self._pair(dist=0.5))
        assert len(clusters) == 2

    def test_stops_at_the_bracket_window(self):
        clusters = self._cluster(self._pair(gap_s=20.0))
        assert len(clusters) == 2

    def test_off_when_bracket_window_is_zero(self):
        clusters = self._cluster(self._pair(), bracket_window_s=0.0)
        assert len(clusters) == 2


# ---------------------------------------------------------------------------
# Cross-camera re-shoot merge
# ---------------------------------------------------------------------------
class TestCrossCameraMerge:
    """The same subject shot on a phone and on a camera.

    Framing usually does not change between the two — both held portrait — so
    the portrait/landscape rule never fires, and the sensor difference puts the
    pair past the tight threshold. On 2026_01_Japan that left the phone and the
    X-T5 versions of one subject in separate clusters every time.
    """

    def _pair(self, gap_s=200.0, kinds=("portrait", "portrait"),
              models=("iPhone 14", "X-T5"), dist=0.1):
        a = [1.0, 0.0, 0.0]
        theta = np.arccos(1.0 - dist)
        b = [float(np.cos(theta)), float(np.sin(theta)), 0.0]
        embeddings = _embeddings([a, a, b, b])
        timestamps = [0.0, 1.0, gap_s, gap_s + 1.0]
        orientations = [kinds[0], kinds[0], kinds[1], kinds[1]]
        labels = np.array([0, 0, 1, 1], dtype=np.int64)
        model_list = [models[0], models[0], models[1], models[1]]
        return embeddings, timestamps, orientations, labels, model_list

    def _merge(self, fixture, **kw):
        embeddings, timestamps, orientations, labels, models = fixture
        opts = dict(window_s=120.0, threshold=0.38, max_span_s=3600.0,
                    models=models, cross_window_s=300.0, cross_threshold=0.22)
        opts.update(kw)
        return pipeline._merge_reshoot_pairs(
            embeddings, timestamps, orientations, labels, **opts)

    def test_merges_the_same_subject_off_two_cameras(self):
        out = self._merge(self._pair())
        assert len(set(out.tolist())) == 1

    def test_needs_the_wider_cross_camera_window(self):
        """200s apart is past the framing window and inside the camera one."""
        out = self._merge(self._pair(), cross_window_s=120.0)
        assert len(set(out.tolist())) == 2

    def test_stops_at_the_cross_camera_window(self):
        out = self._merge(self._pair(gap_s=400.0))
        assert len(set(out.tolist())) == 2

    def test_same_framing_uses_the_tighter_ceiling(self):
        """0.3 apart clears the 0.38 rotation ceiling but not the 0.22 one.

        Letting same-framing pairs in at 0.38 pulled neighbouring compositions
        into one group on the Hoh trip — twelve photos of three subjects.
        """
        out = self._merge(self._pair(dist=0.3))
        assert len(set(out.tolist())) == 2
        loose = self._merge(self._pair(dist=0.3), cross_threshold=0.35)
        assert len(set(loose.tolist())) == 1

    def test_rotation_across_cameras_keeps_the_looser_ceiling(self):
        out = self._merge(self._pair(kinds=("portrait", "landscape"), dist=0.3))
        assert len(set(out.tolist())) == 1

    def test_an_unknown_model_is_not_assumed_to_be_a_second_camera(self):
        fixture = list(self._pair())
        fixture[4] = ["iPhone 14", "iPhone 14", None, None]
        out = self._merge(tuple(fixture))
        assert len(set(out.tolist())) == 2

    def test_cluster_embeddings_passes_models_through(self):
        embeddings, timestamps, orientations, _labels, models = self._pair()
        without = pipeline.cluster_embeddings(
            embeddings, timestamps, orientations, orient_window_s=120.0,
        )
        withal = pipeline.cluster_embeddings(
            embeddings, timestamps, orientations, orient_window_s=120.0,
            models=models, cross_window_s=300.0, cross_threshold=0.22,
        )
        assert len(without) == 2
        assert len(withal) == 1

    def test_chain_span_cap_covers_the_wider_window(self):
        """Three cameras in a row must still not chain past one re-shoot."""
        near = [1.0, 0.0, 0.0]
        embeddings = _embeddings([near] * 3)
        timestamps = [0.0, 250.0, 500.0]
        orientations = ["portrait"] * 3
        models = ["iPhone 14", "X-T5", "iPhone 14"]
        out = pipeline._merge_reshoot_pairs(
            embeddings, timestamps, orientations,
            np.array([0, 1, 2], dtype=np.int64),
            window_s=120.0, threshold=0.38, max_span_s=300.0,
            models=models, cross_window_s=300.0, cross_threshold=0.22,
        )
        assert len(set(out.tolist())) == 2


# ---------------------------------------------------------------------------
# Own folders vs other people's cameras
# ---------------------------------------------------------------------------
class TestOwnVsGuestMerge:
    """Other people's cameras may join each other. Jason's stay out.

    Folder, not EXIF model: `google photos/` holds six cameras, and a handful
    of his own iPhone frames live in there too.
    """

    def _pair(self, models=("Galaxy Z Fold6", "Xiaomi 17 Pro Max"),
              kinds=("portrait", "portrait"), dist=0.1, gap_s=200.0):
        a = [1.0, 0.0, 0.0]
        theta = np.arccos(1.0 - dist)
        b = [float(np.cos(theta)), float(np.sin(theta)), 0.0]
        embeddings = _embeddings([a, a, b, b])
        timestamps = [0.0, 1.0, gap_s, gap_s + 1.0]
        orientations = [kinds[0], kinds[0], kinds[1], kinds[1]]
        labels = np.array([0, 0, 1, 1], dtype=np.int64)
        model_list = [models[0], models[0], models[1], models[1]]
        return embeddings, timestamps, orientations, labels, model_list

    def _merge(self, fixture, devices, **kw):
        embeddings, timestamps, orientations, labels, models = fixture
        opts = dict(window_s=120.0, threshold=0.38, max_span_s=3600.0,
                    models=models, cross_window_s=300.0, cross_threshold=0.22,
                    own=pipeline.own_flags(devices, models))
        opts.update(kw)
        return pipeline._merge_reshoot_pairs(
            embeddings, timestamps, orientations, labels, **opts)

    def test_other_people_cameras_still_merge(self):
        out = self._merge(self._pair(), devices=["google photos"] * 4)
        assert len(set(out.tolist())) == 1

    def test_own_camera_does_not_join_other_people(self):
        out = self._merge(
            self._pair(models=("X-T5", "Galaxy Z Fold6")),
            devices=["xt5", "xt5", "google photos", "google photos"],
        )
        assert len(set(out.tolist())) == 2

    def test_own_phone_and_camera_still_merge(self):
        out = self._merge(
            self._pair(models=("iPhone 14", "X-T5")),
            devices=["iphone", "iphone", "xt5", "xt5"],
        )
        assert len(set(out.tolist())) == 1

    def test_shared_folder_counts_as_other_people(self):
        out = self._merge(
            self._pair(models=("X-T5", "iPhone 17 Pro")),
            devices=["xt5", "xt5", "shared", "shared"],
        )
        assert len(set(out.tolist())) == 2

    def test_unidentified_folder_is_treated_as_own(self):
        out = self._merge(
            self._pair(models=("X-T5", "Galaxy Z Fold6")),
            devices=["", "", "google photos", "google photos"],
        )
        assert len(set(out.tolist())) == 2

    def test_rotation_merge_also_refuses_own_into_guest(self):
        out = self._merge(
            self._pair(models=("X-T5", "Galaxy Z Fold6"),
                       kinds=("portrait", "landscape"), dist=0.3),
            devices=["xt5", "xt5", "google photos", "google photos"],
        )
        assert len(set(out.tolist())) == 2

    def test_cluster_embeddings_keeps_own_out_of_guest_group(self):
        embeddings, timestamps, orientations, _labels, models = self._pair(
            models=("X-T5", "Galaxy Z Fold6"),
        )
        devices = ["xt5", "xt5", "google photos", "google photos"]
        clusters = pipeline.cluster_embeddings(
            embeddings, timestamps, orientations, orient_window_s=120.0,
            models=models, cross_window_s=300.0, cross_threshold=0.22,
            own=pipeline.own_flags(devices, models),
        )
        assert len(clusters) == 2

    def test_a_near_duplicate_of_a_guest_frame_is_split_off(self):
        """Two people photographing one view land inside the tight threshold.

        Stage 4 never merged these — the stages above it did, as near-duplicates
        — so refusing the merge is not enough to keep the two owners apart.
        """
        same = [1.0, 0.0, 0.0]
        embeddings = _embeddings([same] * 4)
        timestamps = [0.0, 1.0, 2.0, 3.0]
        orientations = ["portrait"] * 4
        models = ["X-T5", "X-T5", "iPhone 17 Pro", "iPhone 17 Pro"]
        devices = ["xt5", "xt5", "google photos", "google photos"]
        clusters = pipeline.cluster_embeddings(
            embeddings, timestamps, orientations, orient_window_s=120.0,
            models=models, cross_window_s=300.0, cross_threshold=0.22,
            own=pipeline.own_flags(devices, models),
        )
        assert len(clusters) == 2
        assert sorted(sorted(v) for v in clusters.values()) == [[0, 1], [2, 3]]

    def test_no_device_folders_leaves_clusters_alone(self):
        """A project opened on one camera folder has no line to split on."""
        same = [1.0, 0.0, 0.0]
        clusters = pipeline.cluster_embeddings(
            _embeddings([same] * 4), [0.0, 1.0, 2.0, 3.0], ["portrait"] * 4,
            orient_window_s=120.0,
        )
        assert len(clusters) == 1


class TestOwnFlags:
    """Which photos are his. Folder decides; model only un-misfiles."""

    def test_guest_folders_are_other_peoples(self):
        assert pipeline.own_flags(
            ["iphone", "xt5", "canon", "google photos", "shared"],
            ["iPhone 14", "X-T5", "EOS R6", "Galaxy Z Fold6", "iPhone 17 Pro"],
        ) == [True, True, True, False, False]

    def test_a_file_in_the_trip_root_counts_as_his(self):
        assert pipeline.own_flags(["", "google photos"], ["X-T5", "X-T5"])[0] is True

    def test_his_frame_filed_in_the_shared_album_is_still_his(self):
        """`google photos/` backfills numbering gaps in `iphone/`.

        7 frames on Hawaii, 15 on Vegas — his own camera, filed with everyone
        else's. Splitting those off his burst would be the new wrong.
        """
        assert pipeline.own_flags(
            ["iphone", "google photos", "google photos"],
            ["iPhone 14", "iPhone 14", "Galaxy Z Fold6"],
        ) == [True, True, False]

    def test_only_a_model_his_own_folders_carry_is_rescued(self):
        """No own folder names that model, so nothing in the album is his."""
        assert pipeline.own_flags(
            ["xt5", "google photos"], ["X-T5", "iPhone 14"],
        ) == [True, False]

    def test_a_guest_frame_without_a_model_stays_a_guest(self):
        assert pipeline.own_flags(["iphone", "google photos"], ["X-T5", None]) == [True, False]

    def test_models_are_optional(self):
        assert pipeline.own_flags(["xt5", "shared"]) == [True, False]


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
