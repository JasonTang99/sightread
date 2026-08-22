"""Shared thumbnail rendering and cache addressing.

Both the webapp and the offline pipeline write into the same on-disk thumbnail
cache, so the key scheme and the resize/quality decisions live here rather than
being restated in each. Anything that diverges silently stops being a cache hit
and quietly costs a ~1.3s render per photo, which is exactly the failure that
would not show up in a test of either side on its own.
"""
import hashlib
import io
import os
import threading
from pathlib import Path

from PIL import Image, ImageOps

# Grid tiles are ~380px on screen and there are hundreds of them, so they trade
# a little quality for bytes; the 2400px renders behind the compare views are
# where a soft JPEG would actually change which photo you pick.
GRID_MAX_WIDTH = 800
COMPARE_WIDTH = 2400
GRID_QUALITY = 88
DETAIL_QUALITY = 95


def cache_file(output_dir: Path, abs_path: Path, w: int, st: os.stat_result | None = None) -> Path:
    """Where the w-wide thumbnail of abs_path lives. Keyed by path/mtime/width."""
    if st is None:
        st = abs_path.stat()
    key = hashlib.sha1(f"{abs_path}|{st.st_mtime_ns}|{w}".encode()).hexdigest()
    return Path(output_dir) / "thumb_cache" / f"{key}.jpg"


def encode(img: Image.Image, w: int) -> bytes:
    """Resize an already-decoded image down to w and encode it as JPEG.

    Split out from render() so the pipeline, which has just decoded the full
    image to score it, can produce thumbnails from those pixels instead of
    reading and decoding the original a second time.
    """
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    # thumbnail() mutates, and the caller may still need the image it passed in.
    img = img.copy()
    img.thumbnail((w, w * 3), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=GRID_QUALITY if w <= GRID_MAX_WIDTH else DETAIL_QUALITY)
    return buf.getvalue()


def render(abs_path: Path, w: int) -> bytes:
    img = Image.open(abs_path)
    # Decode straight to a DCT-scaled size (1/2, 1/4, 1/8) instead of unpacking
    # 40 megapixels and throwing most of them away: ~3x faster per thumbnail on
    # this footage, which is most of what a cold project's prewarm costs.
    # draft() never picks a scale below the requested size, so LANCZOS still
    # does the final, quality-carrying step.
    img.draft("RGB", (w, w))
    return encode(img, w)


def write(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Thread id as well as pid: the prewarm pool has several threads writing
    # thumbnails at once, and a shared temp name would let them clobber.
    tmp = dest.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
