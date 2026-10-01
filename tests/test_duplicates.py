"""Album copies of his own frames are left out of review and queued.

Both near misses in the module docstring for DUPLICATE_MAX_DIST come from the
archive: the same second on two different frames (Vegas), and a guest's
camera whose file numbers collide with his (Hawaii).
"""

import numpy as np
from PIL import ExifTags, Image

import pipeline
from utils import FAVORITE, TO_DELETE, load_decisions, save_decisions


def _shot(path, when="2026:05:15 22:07:16", sub="008", model="iPhone 14"):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (32, 24), "gray")
    exif = Image.Exif()
    exif[pipeline._EXIF_MODEL_TAG] = model
    ifd = exif.get_ifd(ExifTags.IFD.Exif)
    ifd[pipeline._EXIF_DATETIME_TAG] = when
    if sub is not None:
        ifd[pipeline._EXIF_SUBSEC_TAG] = sub
    img.save(path, "JPEG", exif=exif)
    return str(path)


def _find(root, paths, embeddings=None):
    meta = [pipeline._read_exif_meta(p) for p in paths]
    devices = [pipeline.media.device_of(p, root) for p in paths]
    if embeddings is None:
        embeddings = np.ones((len(paths), 4), dtype=np.float32)
    return pipeline.find_duplicates(
        paths, [m.timestamp for m in meta], [m.model for m in meta], devices, embeddings,
    )


class TestFindDuplicates:
    def test_album_copy_of_his_frame_is_found(self, tmp_path):
        own = _shot(tmp_path / "iphone" / "IMG_9108.JPG")
        copy = _shot(tmp_path / "google photos" / "IMG_9108.JPG")
        assert _find(tmp_path, [own, copy]) == {1: 0}

    def test_same_second_different_subsecond_is_two_frames(self, tmp_path):
        """Vegas IMG_9107 / IMG_9108."""
        own = _shot(tmp_path / "iphone" / "IMG_9108.JPG", sub="926")
        other = _shot(tmp_path / "google photos" / "IMG_9107.JPG", sub="008")
        assert _find(tmp_path, [own, other]) == {}

    def test_no_subsecond_is_not_enough(self, tmp_path):
        own = _shot(tmp_path / "xt5" / "DSCF1.JPG", sub=None, model="X-T5")
        copy = _shot(tmp_path / "google photos" / "DSCF1.JPG", sub=None, model="X-T5")
        assert _find(tmp_path, [own, copy]) == {}

    def test_another_camera_is_never_a_copy(self, tmp_path):
        """Hawaii: a guest's iPhone 17 Pro shares his IMG_ numbering."""
        own = _shot(tmp_path / "iphone" / "IMG_9464.JPG")
        guest = _shot(tmp_path / "google photos" / "IMG_9464.HEIC.jpg", model="iPhone 17 Pro")
        assert _find(tmp_path, [own, guest]) == {}

    def test_a_different_picture_is_not_a_copy(self, tmp_path):
        own = _shot(tmp_path / "iphone" / "a.JPG")
        copy = _shot(tmp_path / "google photos" / "a.JPG")
        far = np.array([[1, 0, 0, 0], [0.9, 0.44, 0, 0]], dtype=np.float32)
        assert _find(tmp_path, [own, copy], far) == {}

    def test_his_own_folders_are_never_the_copy(self, tmp_path):
        """Both cameras of his, same instant: neither is deleted."""
        a = _shot(tmp_path / "iphone" / "a.JPG")
        b = _shot(tmp_path / "xt5" / "a.JPG")
        assert _find(tmp_path, [a, b]) == {}


class TestQueueDuplicates:
    def test_copies_are_queued_for_deletion(self, tmp_path):
        assert pipeline.queue_duplicates(tmp_path, ["/x/google photos/a.jpg"]) == 1
        assert load_decisions(tmp_path) == {"/x/google photos/a.jpg": TO_DELETE}

    def test_an_existing_decision_wins(self, tmp_path):
        save_decisions(tmp_path, {"/x/google photos/a.jpg": FAVORITE})
        assert pipeline.queue_duplicates(tmp_path, ["/x/google photos/a.jpg"]) == 0
        assert load_decisions(tmp_path) == {"/x/google photos/a.jpg": FAVORITE}
