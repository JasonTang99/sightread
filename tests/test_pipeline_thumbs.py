"""The pipeline and the webapp must agree on the thumbnail cache.

Scoring already decodes every photo off the NAS — 717ms each, measured on this
footage — so emitting the webapp's two derivatives there costs 147ms more per
photo. Rendering them later, from the webapp, costs ~1310ms per photo and
spends it while someone is waiting to look at them. That trade only pays if
the file the pipeline writes is the exact file the server goes looking for,
which is what these pin.
"""
import io

import pytest
from PIL import Image

import pipeline
import server
import thumbs
from projects import ProjectContext


@pytest.fixture()
def project(tmp_path, monkeypatch):
    folder = tmp_path / "trip"
    folder.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(server, "_active", ProjectContext(folder=folder, output_dir=out))
    return folder, out


def test_pipeline_thumbnails_are_cache_hits_for_the_server(project):
    folder, out = project
    src = folder / "a.jpg"
    Image.new("RGB", (4000, 3000), "red").save(src)

    pipeline._emit_thumbs(out, str(src), Image.open(src))

    for w in (thumbs.GRID_MAX_WIDTH, thumbs.COMPARE_WIDTH):
        expected = server._thumb_cache_file(server._active, src, w)
        assert expected.exists(), f"server would re-render at w={w}"

    def _boom(*a, **k):
        raise AssertionError("re-rendered a thumbnail the pipeline already wrote")

    from fastapi.testclient import TestClient

    client = TestClient(server.app, base_url="http://localhost")
    for w in (thumbs.GRID_MAX_WIDTH, thumbs.COMPARE_WIDTH):
        server._render_thumb, real = _boom, server._render_thumb
        try:
            r = client.get(f"/api/image?path={src}&w={w}")
        finally:
            server._render_thumb = real
        assert r.status_code == 200
        assert Image.open(io.BytesIO(r.content)).width == w


def test_pipeline_thumbnails_respect_exif_orientation(project, tmp_path):
    """Scoring skips exif_transpose because it does not care which way is up.

    A thumbnail does: writing the untransposed pixels would cache every
    portrait frame on its side, and the cache key gives no hint that the
    contents are wrong.
    """
    folder, out = project
    src = folder / "portrait.jpg"
    img = Image.new("RGB", (400, 200), "blue")
    exif = img.getexif()
    exif[274] = 6  # rotate 90° CW on display
    img.save(src, exif=exif)

    pipeline._emit_thumbs(out, str(src), Image.open(src))

    dest = thumbs.cache_file(out, src, thumbs.GRID_MAX_WIDTH)
    got = Image.open(dest)
    assert got.height > got.width, f"orientation ignored: {got.size}"


def test_a_failed_thumbnail_does_not_stop_scoring(project):
    folder, out = project
    with pytest.warns(UserWarning, match="Thumbnail failed"):
        pipeline._emit_thumbs(out, str(folder / "gone.jpg"), Image.new("RGB", (10, 10)))
    assert pipeline._emit_thumbs(out, str(folder / "x.jpg"), None) is None
