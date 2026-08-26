"""The image-description preflight must prove vision works, not list models.

`/v1/models` is the wrong signal: the ALCF endpoint serves chat completions
while answering `/v1/models` with a 404, and a model appearing in a listing
still says nothing about whether it can read an image. The preflight therefore
does the same red-PNG round-trip as `kb-import --check-connections`.
"""

import types

import pytest

from kb_mcp.health import RED_PNG_B64
from kb_mcp.parser.image_descriptions import _preflight_model


class _FakeClient:
    """Minimal stand-in for the OpenAI client, recording the call it got."""

    def __init__(self, content=None, raises=None):
        self._content = content
        self._raises = raises
        self.calls = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        message = types.SimpleNamespace(content=self._content)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    # A models.list() that would blow up if anything still called it.
    @property
    def models(self):  # pragma: no cover - only reached on regression
        raise AssertionError("preflight must not call /v1/models")


def test_vision_model_passes():
    client = _FakeClient(content="Red")
    _preflight_model(client, "some-vision-model")

    assert len(client.calls) == 1
    # The probe must actually send the image, not just a text prompt.
    content = client.calls[0]["messages"][0]["content"]
    assert any(RED_PNG_B64 in part.get("image_url", {}).get("url", "") for part in content)


@pytest.mark.parametrize("answer", ["Blue", "I cannot see images.", "", None])
def test_non_vision_model_raises(answer):
    """A model that answers but gets the image wrong stops the run loudly."""
    client = _FakeClient(content=answer)

    with pytest.raises(ValueError) as exc:
        _preflight_model(client, "text-only-model")

    # The message must say what to change, not just that something is wrong.
    assert "PARSE_IMAGE_DESCRIPTION_MODEL" in str(exc.value)


def test_verbose_answer_still_passes():
    """Don't be brittle about phrasing — only the colour matters."""
    _preflight_model(_FakeClient(content="The image is a solid red square."), "m")


def test_transport_failure_is_not_fatal():
    """A dead endpoint is left to the per-image calls, which report it better."""
    client = _FakeClient(raises=RuntimeError("connection refused"))

    _preflight_model(client, "some-model")  # must not raise
