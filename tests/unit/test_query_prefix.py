"""Query-side instruction handling for asymmetric retrieval models.

Some models (BAAI's bge-*-v1.5) are trained with a short instruction on
the query and none on the passage. Getting this wrong degrades retrieval
quietly rather than failing — both sides still land in the same vector
space — so the contract is pinned here rather than left to inspection.

No model weights are loaded: `query_prefix` is a pure function of the
configured model name.
"""

from kb_mcp.kb.embedding.embedders import SentenceTransformersEmbedder

BGE_INSTRUCTION = "Represent this sentence for searching relevant passages: "


def _embedder(model_name):
    """A SentenceTransformersEmbedder without loading any weights."""
    e = SentenceTransformersEmbedder.__new__(SentenceTransformersEmbedder)
    e.provider = "sentence-transformers"
    e.model = model_name
    return e


def test_bge_models_carry_the_query_instruction():
    for name in ("bge-small-en-v1.5", "bge-base-en-v1.5", "bge-large-en-v1.5"):
        assert _embedder(name).query_prefix == BGE_INSTRUCTION


def test_org_qualified_model_name_resolves():
    """EMBEDDING_MODEL must be org-qualified for BGE ("BAAI/...") or the
    weights don't resolve on the Hub, so the lookup can't be exact-match."""
    assert _embedder("BAAI/bge-small-en-v1.5").query_prefix == BGE_INSTRUCTION


def test_symmetric_models_get_no_prefix():
    """MiniLM and mpnet embed query and passage identically. A prefix here
    would corrupt queries against an already-indexed table."""
    for name in ("all-MiniLM-L6-v2", "all-mpnet-base-v2", "some-unknown-model"):
        assert _embedder(name).query_prefix == ""


def test_embed_query_applies_the_prefix_and_only_to_the_query():
    """The instruction goes on the query only — prefixing the passages too
    would cancel the benefit and require re-embedding the whole corpus."""
    seen = []

    e = _embedder("BAAI/bge-small-en-v1.5")
    e.generate_embeddings = lambda texts, **kw: seen.append(texts) or [[0.0]]

    e.embed_query("what is the tracker resolution")
    assert seen == [[BGE_INSTRUCTION + "what is the tracker resolution"]]

    # Indexing path is untouched: no prefix.
    seen.clear()
    e(["a stored passage"])
    assert seen == [["a stored passage"]]


def test_embed_query_is_a_no_op_for_symmetric_models():
    seen = []
    e = _embedder("all-MiniLM-L6-v2")
    e.generate_embeddings = lambda texts, **kw: seen.append(texts) or [[0.0]]

    e.embed_query("what is the tracker resolution")
    assert seen == [["what is the tracker resolution"]]
