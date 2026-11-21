"""/api/image serves an image with the type its bytes have.

Image records carry their parent's file type in source_type, so the route used
to fall back to image/png for every image -- the scaled JPEGs included.
"""

from types import SimpleNamespace

import pytest

from kb_mcp.server.web.routes.api import image_media_type

JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 20


def doc(binary, meta=None, source_type="application/pdf", doc_type="image"):
    return SimpleNamespace(binary=binary, meta=meta, source_type=source_type, doc_type=doc_type)


@pytest.mark.parametrize("meta, binary, expected", [
    ({"format": "jpeg"}, JPEG, "image/jpeg"),        # a scaled image
    ({"format": "JPG"}, JPEG, "image/jpeg"),
    ({"format": "png"}, PNG, "image/png"),
    (None, JPEG, "image/jpeg"),                       # no format recorded: the bytes say it
    ({}, PNG, "image/png"),
    ({}, b"GIF89a" + b"\0" * 10, "image/gif"),
    ({}, b"RIFF\0\0\0\0WEBPVP8 ", "image/webp"),
])
def test_format_then_bytes(meta, binary, expected):
    assert image_media_type(doc(binary, meta)) == expected


def test_the_parent_file_type_is_never_used():
    assert image_media_type(doc(JPEG, {}, source_type="application/pdf")) == "image/jpeg"


def test_unknown_bytes_fall_back():
    assert image_media_type(doc(b"????", {}, source_type="image/tiff")) == "image/tiff"
    assert image_media_type(doc(b"????", {})) == "image/png"
    assert image_media_type(doc(b"????", {}, doc_type="text")) == "application/octet-stream"
