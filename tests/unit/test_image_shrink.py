"""Images sent to the vision endpoint must be small enough for it to accept.

ALCF answers HTTP 500/413 for 8-20 MB figures, so `shrink_for_vision` cuts the
copy that goes over the wire while leaving small images and the stored bytes
alone.
"""

import io
import os

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from kb_mcp.config import get_parser_config  # noqa: E402
from kb_mcp.parser.image_utils import shrink_for_vision  # noqa: E402


def _png(size, mode="RGB", noise=False):
    if noise:
        img = Image.frombytes(mode, size, os.urandom(size[0] * size[1] * len(mode)))
    else:
        img = Image.new(mode, size, (30, 120, 200, 255)[: len(mode)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _decode(data):
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def test_small_image_is_returned_untouched():
    data = _png((200, 100))
    assert shrink_for_vision(data, max_dim=1280, max_bytes=800_000) == data


def test_large_noisy_image_is_scaled_and_fits_the_byte_limit():
    data = _png((3000, 2400), noise=True)
    assert len(data) > 5_000_000
    out = shrink_for_vision(data, max_dim=1280, max_bytes=800_000)
    img = _decode(out)
    assert max(img.size) <= 1280
    assert len(out) <= 800_000
    assert img.format == "JPEG"


def test_image_within_dimension_but_over_byte_limit_is_recompressed():
    data = _png((1000, 1000), noise=True)
    assert len(data) > 800_000
    out = shrink_for_vision(data, max_dim=1280, max_bytes=800_000)
    assert len(out) <= 800_000
    assert len(out) < len(data)


def test_transparent_png_is_flattened_onto_white():
    data = _png((3000, 2000), mode="RGBA", noise=True)
    out = shrink_for_vision(data, max_dim=1000, max_bytes=800_000)
    img = _decode(out)
    assert img.mode == "RGB"
    assert max(img.size) <= 1000


def test_undecodable_bytes_come_back_unchanged():
    junk = b"not an image at all" * 1000
    assert shrink_for_vision(junk, max_dim=100, max_bytes=10) == junk


def test_empty_input_is_returned_as_is():
    assert shrink_for_vision(b"") == b""


def test_zero_limits_disable_shrinking():
    data = _png((3000, 2400), noise=True)
    assert shrink_for_vision(data, max_dim=0, max_bytes=0) is data


def test_config_defaults_and_env_override(monkeypatch):
    monkeypatch.delenv("PARSE_IMAGE_DESCRIPTION_MAX_DIM", raising=False)
    monkeypatch.delenv("PARSE_IMAGE_DESCRIPTION_MAX_BYTES", raising=False)
    cfg = get_parser_config()
    assert cfg["image_description_max_dim"] == 1280
    assert cfg["image_description_max_bytes"] == 800000
    monkeypatch.setenv("PARSE_IMAGE_DESCRIPTION_MAX_DIM", "1024")
    monkeypatch.setenv("PARSE_IMAGE_DESCRIPTION_MAX_BYTES", "500000")
    cfg = get_parser_config()
    assert cfg["image_description_max_dim"] == 1024
    assert cfg["image_description_max_bytes"] == 500000


def _noise_png(w, h):
    import io
    from PIL import Image
    import random
    random.seed(0)
    img = Image.new("RGB", (w, h))
    img.putdata([(random.randrange(256), random.randrange(256), random.randrange(256)) for _ in range(w * h)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_storage_keeps_the_scaled_copy_the_vision_call_gets(monkeypatch):
    from PIL import Image
    import io
    from kb_mcp.parser.image_utils import scale_for_storage
    monkeypatch.delenv("PARSE_IMAGE_STORE_ORIGINAL", raising=False)
    monkeypatch.setenv("PARSE_IMAGE_DESCRIPTION_MAX_DIM", "400")
    monkeypatch.setenv("PARSE_IMAGE_DESCRIPTION_MAX_BYTES", "800000")
    big = _noise_png(1200, 600)
    out, fmt = scale_for_storage(big)
    assert fmt == "jpeg" and len(out) < len(big)
    assert max(Image.open(io.BytesIO(out)).size) == 400


def test_storage_leaves_small_images_and_honours_store_original(monkeypatch):
    from kb_mcp.parser.image_utils import scale_for_storage
    monkeypatch.setenv("PARSE_IMAGE_DESCRIPTION_MAX_DIM", "400")
    small = _noise_png(100, 50)
    assert scale_for_storage(small) == (small, None)
    big = _noise_png(1200, 600)
    monkeypatch.setenv("PARSE_IMAGE_STORE_ORIGINAL", "true")
    out, fmt = scale_for_storage(big)
    assert out is big and fmt is None
