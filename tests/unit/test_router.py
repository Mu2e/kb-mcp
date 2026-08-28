"""Unit tests for the rule-based query router (classification + route payloads)."""

from kb_mcp.kb.search.router import QueryRouter, QueryType


ROUTER = QueryRouter()


def test_table_query_classification_and_boost():
    query = "what value is listed in Table 2 for crystal 512?"
    assert ROUTER.classify(query) == QueryType.TABLE
    route = ROUTER.route(query)
    assert route.doc_type_boost == {"table": 1.7}
    assert route.rerank is True


def test_figure_query_classification_and_boost():
    query = "show me the calorimeter diagram"
    assert ROUTER.classify(query) == QueryType.FIGURE
    route = ROUTER.route(query)
    assert route.doc_type_boost == {"image": 1.5}
    assert route.rerank is False


def test_identifier_query_no_boost():
    query = "MU2E-STM-01"
    assert ROUTER.classify(query) == QueryType.IDENTIFIER
    route = ROUTER.route(query)
    assert route.doc_type_boost is None


def test_synthesis_query_boosts_sections():
    query = "compare all subsystems of the detector"
    assert ROUTER.classify(query) == QueryType.SYNTHESIS
    route = ROUTER.route(query)
    assert route.chunk_strategy_boost is not None
    assert route.chunk_strategy_boost.get("section", 0) > 1.0


def test_factual_default_route():
    query = "what is the momentum resolution requirement"
    route = ROUTER.route(query)
    # Factual default: config-driven rerank, no boost.
    assert route.query_type in (QueryType.FACTUAL, QueryType.LOOKUP)
    assert route.doc_type_boost is None


def test_route_always_returns_positive_max_results():
    for query in (
        "what value is in table 3",
        "show the plot of momentum",
        "MU2E-CAL-99",
        "overview of all backgrounds",
        "what is CRV?",
        "how do I run the reco job",
        "random ordinary question",
    ):
        route = ROUTER.route(query)
        assert route.max_results > 0, query
        assert route.search_type == "hybrid", query
