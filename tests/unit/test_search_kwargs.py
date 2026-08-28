"""Regression tests for kwargs forwarding in the search chain.

search() -> search_hybrid() -> search_semantic()/search_fulltext() -> log_search()
all forward **kwargs. log_search() takes `search_type` as an explicit
parameter, and each search function also passes its own literal
search_type=..., so a `search_type` travelling in **kwargs used to produce:

    TypeError: log_search() got multiple values for keyword argument 'search_type'

which search_hybrid caught and turned into "Semantic search failed", silently
dropping the semantic half of a hybrid search. These tests pin the contract
without needing a database or an embedding model.
"""

import importlib
import inspect

# NB: `import kb_mcp.kb.search.search` does not work - the package __init__
# does `from .search import search`, which rebinds the name `search` in the
# package namespace from the submodule to the function. importlib fetches the
# actual module objects.
search_mod = importlib.import_module("kb_mcp.kb.search.search")
fulltext_mod = importlib.import_module("kb_mcp.kb.search.search_fulltext")
hybrid_mod = importlib.import_module("kb_mcp.kb.search.search_hybrid")


def _source(fn):
    return inspect.getsource(fn)


def test_log_search_still_takes_search_type_explicitly():
    """The precondition that makes the collision possible."""
    params = inspect.signature(search_mod.log_search).parameters
    assert "search_type" in params


def test_search_semantic_does_not_forward_search_type_to_log_search():
    src = _source(search_mod.search_semantic)
    assert "**log_kwargs" in src, "search_semantic must filter kwargs before log_search"
    assert 'k != "search_type"' in src


def test_search_fulltext_does_not_forward_search_type_to_log_search():
    src = _source(fulltext_mod.search_fulltext)
    assert "**log_kwargs" in src
    assert 'k != "search_type"' in src


def test_search_hybrid_filters_kwargs_for_sub_searches_and_logging():
    src = _source(hybrid_mod.search_hybrid)
    # sub-searches must not receive search_type ...
    assert "**sub_kwargs" in src
    # ... and neither must log_search
    assert "**log_kwargs" in src
    assert 'k != "search_type"' in src


def test_log_search_accepts_a_stray_search_type_free_kwargs_dict():
    """Calling the real signature with the filtered kwargs must bind cleanly."""
    sig = inspect.signature(search_mod.log_search)
    kwargs = {
        "search_type": "hybrid",
        "query": "q",
        "final_results": [],
        "session": None,
        "should_close": False,
        "max_results": 5,
    }
    # Should not raise; this is the call shape the search functions use.
    sig.bind(**kwargs)
