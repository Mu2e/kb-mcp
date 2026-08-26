"""`_generate_short_name()` becomes an `embedding_configs` key and a table name.

It strips a leading "all-" so `all-MiniLM-L6-v2` reads as `st_MiniLML6v2`. That
strip must be anchored: unanchored it also fires *inside* "bge-small-en-v1.5"
(sm-ALL-en), naming that model's table `embeddings_st_bgesmenv1_5`. Once a
corpus is indexed under a mangled name it is stuck with it, so this is pinned.
"""

import pytest

from kb_mcp.kb.embedding.db_models import get_embedding_table_name
from kb_mcp.kb.embedding.embedders import SentenceTransformersEmbedder


def _short_name(model: str) -> str:
    # Avoid loading weights: the method only reads self.model.
    e = SentenceTransformersEmbedder.__new__(SentenceTransformersEmbedder)
    e.model = model
    return SentenceTransformersEmbedder._generate_short_name(e)


@pytest.mark.parametrize("model,expected", [
    # The bug: "small-en" contains "all-".
    ("BAAI/bge-small-en-v1.5", "st_bgesmallenv1_5"),
    ("BAAI/bge-base-en-v1.5", "st_bgebaseenv1_5"),
    ("BAAI/bge-large-en-v1.5", "st_bgelargeenv1_5"),
])
def test_bge_names_survive_intact(model, expected):
    assert _short_name(model) == expected


@pytest.mark.parametrize("model,expected", [
    # Already-indexed models must keep resolving to their existing keys, or
    # they'd silently point at a table that doesn't exist.
    ("all-MiniLM-L6-v2", "st_MiniLML6v2"),
    ("all-mpnet-base-v2", "st_mpnetbasev2"),
])
def test_existing_names_unchanged(model, expected):
    assert _short_name(model) == expected


def test_name_is_usable_as_an_identifier():
    """The dotted version must not leak into the table name."""
    table = get_embedding_table_name(_short_name("BAAI/bge-small-en-v1.5"))
    assert table == "embeddings_st_bgesmallenv1_5"
    assert "." not in table and "-" not in table
