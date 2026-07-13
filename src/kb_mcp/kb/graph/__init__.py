"""Graph knowledge base module for kb-mcp."""

from .db_models import (
    GraphNodeType,
    GraphVerb,
    GraphNode,
    GraphRelation,
    GraphRelationEvidence,
    GraphNodeMap,
    GraphExtractionLog,
)

from .graph import (
    get_or_create_node,
    add_relation,
    get_verbs,
    get_node_types,
    seed_graph_defaults,
    update_node_map,
)

from .extraction import (
    extract_relations,
    process_relations,
    extract_and_process_document,
)

from .queries import (
    get_nodes_for_document,
    get_document_for_node,
    get_node,
    find_paths,
    get_relation_evidence,
)

from .tools import (
    extract_all,
)

__all__ = [
    # Models
    "GraphNodeType",
    "GraphVerb",
    "GraphNode",
    "GraphRelation",
    "GraphRelationEvidence",
    "GraphNodeMap",
    "GraphExtractionLog",
    # Operations
    "get_or_create_node",
    "add_relation",
    "get_verbs",
    "get_node_types",
    "seed_graph_defaults",
    "update_node_map",
    # Extraction
    "extract_relations",
    "process_relations",
    "extract_and_process_document",
    # Queries
    "get_nodes_for_document",
    "get_document_for_node",
    "get_node",
    "find_paths",
    "get_relation_evidence",
    # Tools
    "extract_all",
]
