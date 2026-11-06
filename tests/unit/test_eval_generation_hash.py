"""Unit tests for eval generation identity.

Two generations that hash alike are merged into one, so questions from
different generators would silently land in the same question set.
"""

from kb_mcp.kb.eval.db_models import compute_generation_hash


def _hash(meta=None, name=None):
    return compute_generation_hash("synthetic", "keypoint", "mu2e-docdb", "text", "prompt", meta or {}, name)


def test_same_settings_are_one_generation():
    assert _hash({"model": "gpt-oss:120b"}) == _hash({"model": "gpt-oss:120b", "type": "qa_pairs_keypoint"})


def test_different_models_are_different_generations():
    assert _hash({"model": "gpt-oss:120b"}) != _hash({"model": "argo:gpt-5.5"})


def test_a_name_asks_for_its_own_generation():
    assert _hash({"model": "m"}, name="pilot-a") != _hash({"model": "m"}, name="pilot-b")
    assert _hash({"model": "m"}, name="pilot-a") == _hash({"model": "m"}, name="pilot-a")
