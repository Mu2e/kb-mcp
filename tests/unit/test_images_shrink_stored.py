"""`kb tools images shrink`: stored originals replaced by the copy new images get."""

import io
import os

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from kb_mcp.kb import images  # noqa: E402
from kb_mcp.parser import boilerplate  # noqa: E402


def _png(size, noise=True):
    img = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)) if noise else Image.new("RGB", size, (30, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def storage_limits(monkeypatch):
    monkeypatch.setenv("PARSE_IMAGE_DESCRIPTION_MAX_DIM", "1280")
    monkeypatch.setenv("PARSE_IMAGE_DESCRIPTION_MAX_BYTES", "800000")
    monkeypatch.delenv("PARSE_IMAGE_STORE_ORIGINAL", raising=False)


def test_a_large_original_is_replaced_and_keeps_its_hash():
    data = _png((2400, 1800))
    out, meta = images.shrunk_image(data, {"description": "a plot", "format": "png"})
    assert len(out) < 0.9 * len(data) and len(out) <= 800_000
    img = Image.open(io.BytesIO(out)); img.load()
    assert img.format == "JPEG" and max(img.size) <= 1280
    assert meta["image_hash"] == boilerplate.image_hash(data)      # the original's: registry key
    assert meta["format"] == "jpeg" and meta["original_bytes"] == len(data)
    assert meta["description"] == "a plot"


def test_an_existing_hash_is_kept():
    _, meta = images.shrunk_image(_png((2400, 1800)), {"image_hash": "abc"})
    assert meta["image_hash"] == "abc"


def test_small_images_are_left_alone():
    assert images.shrunk_image(_png((300, 200)), {}) is None
    assert images.shrunk_image(b"not an image", {}) is None


def test_store_original_setting_disables_it(monkeypatch):
    monkeypatch.setenv("PARSE_IMAGE_STORE_ORIGINAL", "true")
    assert images.shrunk_image(_png((2400, 1800)), {}) is None


def test_the_command_is_registered_as_a_dry_run_by_default():
    import argparse
    from kb_mcp.kb.cli import tools_commands

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    tools_commands.setup_commands(sub)
    args = parser.parse_args(["tools", "images", "shrink", "--max-minutes", "30"])
    assert args.func is tools_commands.cmd_images_shrink
    assert args.apply is False and args.min_bytes == 800_000 and args.max_minutes == 30
