"""Query router for dispatching questions to optimal retrieval strategies.

Classifies incoming queries by type (factual, procedural, synthesis, figure,
table, identifier) and returns search configuration tuned for that category —
including doc_type boosts so the search backend can lean toward
`doc_type="table"` records for table-shaped questions and toward
`doc_type="image"` records for figure-shaped ones.

Usage:
    from kb_mcp.kb.search.router import QueryRouter

    router = QueryRouter()
    route = router.route("What are all the subsystems of Mu2e?")
    # route.query_type == "synthesis"
    # route.max_results == 10
    # route.rerank == True
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


class QueryType(str, Enum):
    """Classification of query intent."""
    FACTUAL = "factual"          # Single-doc factual answers (materials, dimensions, parameters)
    PROCEDURAL = "procedural"    # How-to questions (setup, running jobs, configuration)
    SYNTHESIS = "synthesis"      # Cross-document reasoning (all subsystems, comparisons, overviews)
    FIGURE = "figure"            # Visual/diagram/plot questions
    TABLE = "table"              # Tabular value lookup (cell values, rows, columns, table N)
    IDENTIFIER = "identifier"    # Exact acronyms, detector IDs, code symbols (BM25-heavy)
    LOOKUP = "lookup"            # Simple entity lookup (what is X?)


@dataclass
class SearchRoute:
    """Search configuration returned by the router."""
    query_type: QueryType
    max_results: int = 5
    rerank: Optional[bool] = None
    search_type: str = "hybrid"
    confidence: float = 1.0
    reasoning: str = ""
    # doc_type → rrf_score multiplier applied after fusion. e.g. {"table": 1.5}
    # tells search_hybrid to boost chunks of table records by 50 %. None means
    # no per-doc_type bias.
    doc_type_boost: Optional[Dict[str, float]] = None


# --- Keyword patterns for rule-based classification ---

_SYNTHESIS_PATTERNS = [
    # "all" / "every" / "each" + plural noun patterns
    r"\ball\b.*\b(subsystem|component|detector|background|source|process)",
    r"\bevery\b.*\b(subsystem|component|detector)",
    r"\beach\b.*\b(subsystem|component)",
    # Comparison / overview patterns
    r"\bcompare\b",
    r"\bcomparison\b",
    r"\bdifference\s+between\b",
    r"\boverview\b",
    r"\boverall\s+design\b",
    r"\bsummar(y|ize)\b",
    r"\blist\b.*\b(all|every|main|major)\b",
    r"\bhow\s+many\s+(subsystem|component|detector)",
    r"\bwhat\s+are\s+the\s+(main|major|key|different)\b",
    r"\bentire\s+(experiment|detector|system)\b",
    r"\bfull\s+(experiment|detector|system|design)\b",
    r"\brelat(e|ionship)\b.*\bbetween\b",
    r"\bwork\s+together\b",
    r"\binteract\b.*\b(with|between)\b",
]

_PROCEDURAL_PATTERNS = [
    r"\bhow\s+(do|can|to|should)\s+(i|you|we|one)\b",
    r"\bhow\s+to\b",
    r"\bstep[s-]by[- ]step\b",
    r"\bsetup\b",
    r"\bset\s+up\b",
    r"\binstall(ation)?\b",
    r"\bconfigur(e|ation|ing)\b",
    r"\bbuild(ing)?\s+(the|mu2e|offline)\b",
    r"\brun(ning)?\s+(a|the|mu2e|job|grid)\b",
    r"\bsubmit(ting)?\s+(a|the|job)\b",
    r"\blog\s*(in|on|onto)\b",
    r"\bssh\b",
    r"\btutorial\b",
    r"\bworkflow\b",
    r"\bprocedure\b",
    r"\binstructions?\b",
]

_FIGURE_PATTERNS = [
    r"\bfigure\b",
    r"\bplot\b",
    r"\bdiagram\b",
    r"\bgraph\b(?!\s*(node|relation|extract))",  # "graph" but not "graph node"
    r"\bchart\b",
    r"\bimage\b",
    r"\bphoto\b",
    r"\bpicture\b",
    r"\bdrawing\b",
    r"\bschematic\b",
    r"\blayout\b",
    r"\bcross[- ]section\b",
    r"\bshow\s+me\b",
    r"\bwhat\s+does\s+.*\blook\s+like\b",
    r"\bvisual\b",
]

_LOOKUP_PATTERNS = [
    r"^what\s+is\s+(a|an|the)?\s*\w+[\s\w]{0,30}\??\s*$",
    r"^define\b",
    r"^what\s+does\s+\w+\s+stand\s+for\b",
    r"^what\s+is\s+\w+\??\s*$",
]

_TABLE_PATTERNS = [
    r"\btable\b(?!\s+of\s+contents)",          # "table" but not "table of contents"
    r"\brow\b",
    r"\bcolumn\b",
    r"\bcell\b\s+(value|content)",
    r"\bvalue\s+(of|for|in)\b",
    r"\bvalues?\s+listed\b",
    r"\bspreadsheet\b",
    r"\bmilestone\s+(table|list)\b",
    r"\bbudget\b",
    r"\bschedule\b\s+(table|by\s+month)",
]

# Identifier / acronym / code-symbol patterns. Detect either:
#  - tokens that look like detector IDs (e.g. MU2E-STM-01, MDC2025-002)
#  - 2+ all-caps tokens of length ≥ 3 (e.g. CRV, DAQ, FHiCL — likely acronyms)
#  - "what does <ACRONYM> stand for" / "<ACRONYM> meaning"
_IDENTIFIER_HYPHEN_RE = re.compile(r"\b[A-Z][A-Z0-9]+-[A-Z0-9-]+\b")
_IDENTIFIER_ACRONYM_RE = re.compile(r"\b[A-Z]{3,}[0-9]*\b")
_IDENTIFIER_PATTERNS = [
    r"\bstand\s+for\b",
    r"\bwhat\s+does\s+[A-Z]{2,}\b",
    r"\bacronym\b",
]


def _match_patterns(query: str, patterns: List[str]) -> bool:
    """Check if query matches any of the regex patterns."""
    query_lower = query.lower().strip()
    for pattern in patterns:
        if re.search(pattern, query_lower):
            return True
    return False


class QueryRouter:
    """Classifies queries and returns optimal search configurations.

    Uses rule-based keyword matching for fast, deterministic classification.
    Falls back to FACTUAL for unmatched queries (the most common type and
    safest default).
    """

    def classify(self, query: str) -> QueryType:
        """Classify a query into a QueryType.

        Classification priority (first match wins):
        1. Table — tabular value-lookup keywords ("table 2", "row", "column")
        2. Synthesis — compare / overview / "all subsystems" — multi-doc
           reasoning wins over identifier even if the query also contains
           acronyms (e.g. "compare CRV and DS designs").
        3. Identifier — explicit acronym phrasing, hyphenated detector IDs,
           or short queries with all-caps acronym tokens.
        4. Figure — visual/diagram keywords.
        5. Procedural — how-to / setup / workflow keywords.
        6. Lookup — simple "what is X?" patterns.
        7. Factual — default fallback.

        Args:
            query: The search query string.

        Returns:
            QueryType enum value.
        """
        if _match_patterns(query, _TABLE_PATTERNS):
            return QueryType.TABLE

        # Synthesis comes before identifier so "compare CRV and DS designs"
        # resolves to SYNTHESIS (multi-doc reasoning) rather than IDENTIFIER
        # (BM25-heavy on the acronym tokens).
        if _match_patterns(query, _SYNTHESIS_PATTERNS):
            return QueryType.SYNTHESIS

        # Identifier signals: explicit phrasing, OR a hyphenated detector-ID
        # token, OR multiple all-caps acronym tokens. The acronym heuristic
        # uses the original-case query (the rest of classification is
        # case-insensitive).
        if _match_patterns(query, _IDENTIFIER_PATTERNS):
            return QueryType.IDENTIFIER
        if _IDENTIFIER_HYPHEN_RE.search(query):
            return QueryType.IDENTIFIER
        acronym_hits = _IDENTIFIER_ACRONYM_RE.findall(query)
        if acronym_hits and len(acronym_hits) >= 1 and len(query.split()) <= 12:
            # Short query containing at least one all-caps token — likely an
            # acronym lookup or detector-ID question.
            return QueryType.IDENTIFIER

        if _match_patterns(query, _FIGURE_PATTERNS):
            return QueryType.FIGURE

        if _match_patterns(query, _PROCEDURAL_PATTERNS):
            return QueryType.PROCEDURAL

        if _match_patterns(query, _LOOKUP_PATTERNS):
            return QueryType.LOOKUP

        return QueryType.FACTUAL

    def route(self, query: str) -> SearchRoute:
        """Classify a query and return the optimal search configuration.

        Args:
            query: The search query string.

        Returns:
            SearchRoute with tuned parameters for the query type.
        """
        query_type = self.classify(query)

        if query_type == QueryType.SYNTHESIS:
            return SearchRoute(
                query_type=query_type,
                max_results=10,       # More results for cross-doc reasoning
                rerank=True,          # Rerank to surface best synthesis chunks
                reasoning="Cross-document question — boosting section + summary context",
                # Sections aggregate paragraph-level text under one heading, which
                # is exactly what synthesis queries (compare / overview / list-all)
                # want. A modest boost lifts them above raw chunks without
                # drowning them.
                doc_type_boost={"section": 1.3},
            )

        if query_type == QueryType.PROCEDURAL:
            return SearchRoute(
                query_type=query_type,
                max_results=5,
                rerank=True,          # Rerank helps find the specific how-to
                reasoning="Procedural question — reranking for precision",
            )

        if query_type == QueryType.FIGURE:
            return SearchRoute(
                query_type=query_type,
                max_results=5,
                rerank=False,         # Reranker doesn't help with visual content
                reasoning="Figure/visual question — boosting image records",
                doc_type_boost={"image": 1.5},
            )

        if query_type == QueryType.TABLE:
            return SearchRoute(
                query_type=query_type,
                max_results=5,
                rerank=True,
                reasoning="Table-shaped question — boosting table records",
                doc_type_boost={"table": 1.7},
            )

        if query_type == QueryType.IDENTIFIER:
            return SearchRoute(
                query_type=query_type,
                max_results=5,
                rerank=False,
                reasoning="Acronym / identifier lookup — keyword-heavy retrieval",
                # No doc_type boost; the search backend's BM25 trigger already
                # weights doc.title, chunk.text, and chunk.section_path so
                # exact tokens surface naturally. A future bm25_weight knob
                # could lean further into fulltext for this query type.
            )

        if query_type == QueryType.LOOKUP:
            return SearchRoute(
                query_type=query_type,
                max_results=3,        # Simple lookups need fewer results
                rerank=False,         # Fast path for simple queries
                reasoning="Simple lookup — minimal results, no reranking",
            )

        # FACTUAL (default)
        return SearchRoute(
            query_type=query_type,
            max_results=5,
            rerank=None,              # Use server config default
            reasoning="Factual question — default search configuration",
        )


# Module-level singleton
_router = QueryRouter()


def get_router() -> QueryRouter:
    """Get the module-level QueryRouter instance."""
    return _router
