"""Node matching utilities for graph knowledge base."""

import json
import logging
import unicodedata
from typing import List, Optional, Tuple

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from ..database import get_db_session
from .db_models import GraphNode, GraphNodeType

logger = logging.getLogger(__name__)


def _normalize_name(name: str) -> str:
    """
    Normalize a name for matching.

    Applies the following transformations:
    - Convert to lowercase
    - Unicode NFD normalization (decompose accents)
    - Remove combining characters (accents)
    - Strip leading/trailing whitespace
    - Collapse multiple spaces to single space

    Args:
        name: Input name string.

    Returns:
        Normalized name string.

    Examples:
        >>> _normalize_name("Alice Smith")
        'alice smith'
        >>> _normalize_name("  René O'Brien  ")
        'rene o\\'brien'
        >>> _normalize_name("JOHN   DOE")
        'john doe'
    """
    if not name:
        return ""

    # Convert to lowercase
    normalized = name.lower()

    # Unicode NFD normalization (decompose characters with accents)
    normalized = unicodedata.normalize('NFD', normalized)

    # Remove combining characters (accents)
    normalized = ''.join(
        c for c in normalized
        if unicodedata.category(c) != 'Mn'
    )

    # Strip whitespace
    normalized = normalized.strip()

    # Collapse multiple spaces
    normalized = ' '.join(normalized.split())

    return normalized


def find_similar_nodes_pgvector(
    query_embedding: List[float],
    node_type_id: Optional[str] = None,
    limit: int = 10,
    session: Optional[Session] = None,
) -> List[Tuple[GraphNode, float]]:
    """
    Find similar nodes using pgvector cosine distance (PostgreSQL only).

    Args:
        query_embedding: Query vector embedding.
        node_type_id: Optional filter by node type ID. If None, searches across all types.
        limit: Maximum number of results.
        session: Optional database session.

    Returns:
        List of (GraphNode, similarity_score) tuples, ordered by similarity descending.
    """
    with get_db_session(session) as session:
        # Convert query embedding to PostgreSQL vector format
        query_vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        # Use raw SQL to access pgvector's cosine distance operator (<=>)
        # This is necessary because TypeDecorator hides the pgvector methods
        if node_type_id:
            sql_query = text("""
                SELECT
                    graph_nodes.id,
                    (1 - (graph_nodes.embedding <=> CAST(:query_embedding AS vector))) AS similarity
                FROM graph_nodes
                WHERE graph_nodes.type_id = :node_type_id
                    AND graph_nodes.embedding IS NOT NULL
                ORDER BY similarity DESC
                LIMIT :limit
            """)
            params = {
                "query_embedding": query_vec_str,
                "node_type_id": node_type_id,
                "limit": limit
            }
        else:
            sql_query = text("""
                SELECT
                    graph_nodes.id,
                    (1 - (graph_nodes.embedding <=> CAST(:query_embedding AS vector))) AS similarity
                FROM graph_nodes
                WHERE graph_nodes.embedding IS NOT NULL
                ORDER BY similarity DESC
                LIMIT :limit
            """)
            params = {
                "query_embedding": query_vec_str,
                "limit": limit
            }

        result = session.execute(sql_query, params)

        # Collect node IDs and similarities
        node_data = []
        for row in result:
            node_data.append((row.id, float(row.similarity)))

        if not node_data:
            return []

        # Fetch actual GraphNode objects from the session
        # This ensures they are properly tracked by SQLAlchemy
        node_ids = [node_id for node_id, _ in node_data]
        nodes = session.query(GraphNode).filter(GraphNode.id.in_(node_ids)).all()

        # Create a mapping of node_id -> node
        node_map = {node.id: node for node in nodes}

        # Build results in the same order as the similarity query
        results = []
        for node_id, similarity in node_data:
            node = node_map.get(node_id)
            if node:
                results.append((node, similarity))

        return results


def find_similar_nodes_fallback(
    query_embedding: List[float],
    node_type_id: Optional[str] = None,
    limit: int = 10,
    session: Optional[Session] = None,
) -> List[Tuple[GraphNode, float]]:
    """
    Find similar nodes using in-memory cosine similarity (SQLite fallback).

    Args:
        query_embedding: Query vector embedding.
        node_type_id: Optional filter by node type ID. If None, searches across all types.
        limit: Maximum number of results.
        session: Optional database session.

    Returns:
        List of (GraphNode, similarity_score) tuples, ordered by similarity descending.
    """
    import numpy as np

    with get_db_session(session) as session:
        # Fetch all nodes of this type with embeddings
        query = session.query(GraphNode).filter(GraphNode.embedding.isnot(None))
        if node_type_id:
            query = query.filter(GraphNode.type_id == node_type_id)
        nodes = query.all()

        if not nodes:
            return []

        # Convert query to numpy array
        query_vec = np.array(query_embedding)
        query_norm = np.linalg.norm(query_vec)

        # Calculate cosine similarity for each node
        results = []
        for node in nodes:
            # Node embedding is a list (from JSON storage)
            node_vec = np.array(node.embedding)
            node_norm = np.linalg.norm(node_vec)

            # Cosine similarity = dot product / (norm1 * norm2)
            if query_norm > 0 and node_norm > 0:
                similarity = np.dot(query_vec, node_vec) / (query_norm * node_norm)
                results.append((node, float(similarity)))

        # Sort by similarity descending
        results.sort(key=lambda x: x[1], reverse=True)

        return results[:limit]


def find_similar_nodes(
    query_embedding: List[float],
    node_type_id: Optional[str] = None,
    limit: int = 10,
    session: Optional[Session] = None,
) -> List[Tuple[GraphNode, float]]:
    """
    Find similar nodes using vector similarity search.

    Automatically selects the appropriate backend (pgvector for PostgreSQL,
    in-memory for SQLite).

    Args:
        query_embedding: Query vector embedding.
        node_type_id: Optional filter by node type ID. If None, searches across all types.
        limit: Maximum number of results.
        session: Optional database session.

    Returns:
        List of (GraphNode, similarity_score) tuples, ordered by similarity descending.
    """
    with get_db_session(session) as session:
        # Detect database dialect
        dialect = session.bind.dialect.name

        if dialect == 'postgresql':
            return find_similar_nodes_pgvector(query_embedding, node_type_id, limit, session)
        else:
            return find_similar_nodes_fallback(query_embedding, node_type_id, limit, session)


def _disambiguate_nodes_with_llm(
    query_name: str,
    candidates: List[Tuple[GraphNode, float]],
    session: Optional[Session] = None,
) -> Optional[GraphNode]:
    """
    Use LLM to disambiguate between multiple similar node candidates.

    When multiple nodes have similar similarity scores (within 10% of each other),
    this function asks an LLM to choose the best match based on names and aliases.

    Args:
        query_name: The name being searched for.
        candidates: List of (GraphNode, similarity_score) tuples.
        session: Optional database session.

    Returns:
        Selected GraphNode, or None if LLM suggests creating a new node.
    """
    from ...llm import get_openai_client

    try:
        # Build prompt
        candidates_text = []
        for i, (node, similarity) in enumerate(candidates, 1):
            aliases_str = ", ".join(node.aliases) if node.aliases else "None"
            candidates_text.append(
                f'{i}. Name: "{node.name}", Aliases: [{aliases_str}], Similarity: {similarity:.3f}'
            )

        prompt = f"""Which of these existing entities best matches "{query_name}"?

Candidates:
{chr(10).join(candidates_text)}

Return JSON: {{"choice": 1 or 2 or 3 etc. or null, "reasoning": "..."}}
If no good match exists, return null for choice."""

        client = get_openai_client()
        response = client.chat.completions.create(
            model=None,  # Will use default from config
            messages=[
                {
                    "role": "system",
                    "content": "You are a knowledge graph entity matching assistant. Always return valid JSON."
                },
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )

        # Parse response
        content = response.choices[0].message.content.strip()
        result = json.loads(content)

        choice = result.get("choice")
        reasoning = result.get("reasoning", "")

        if choice is None:
            logger.info(f"LLM suggested creating new node for '{query_name}': {reasoning}")
            return None

        # Validate choice is in range
        if not isinstance(choice, int) or choice < 1 or choice > len(candidates):
            logger.warning(f"Invalid LLM choice {choice} for {len(candidates)} candidates")
            # Fall back to highest similarity
            return candidates[0][0]

        # Return selected node (1-indexed to 0-indexed)
        selected_node = candidates[choice - 1][0]
        logger.info(f"LLM selected node '{selected_node.name}' for query '{query_name}': {reasoning}")
        return selected_node

    except Exception as e:
        logger.warning(f"LLM disambiguation failed: {e}, falling back to highest similarity")
        # On error, return node with highest similarity (first in list)
        return candidates[0][0] if candidates else None


def find_node(
    name: str,
    node_type: Optional[str] = None,
    similarity_threshold: Optional[float] = None,
    session=None,
) -> Optional[GraphNode]:
    """
    Find a graph node by name and optional type.

    Args:
        name: Node name to search for.
        node_type: Optional node type label to filter by. If None, searches across all types.
        similarity_threshold: Minimum similarity for vector match (defaults to config).
        session: Optional database session.
    Returns:
        GraphNode instance if found, else None.
    """
    from ...config import get_graph_config
    from ..embedding.utils import get_embedder
    from sqlalchemy import func

    # Get configuration
    graph_config = get_graph_config()
    embedding_config = graph_config['embedding']
    if similarity_threshold is None:
        similarity_threshold = graph_config['node_similarity_threshold']

    canonical = _normalize_name(name)
    with get_db_session(session) as session:
        node_type_obj = None
        if node_type:
            node_type_obj = session.query(GraphNodeType).filter(
                GraphNodeType.label == node_type
            ).first()

            if not node_type_obj:
                raise ValueError(f"Node type '{node_type}' does not exist. Please create it first or use seed_graph_defaults().")

        # Strategy 1: Exact canonical name match
        query = session.query(GraphNode).filter(GraphNode.canonical_name == canonical)
        if node_type_obj:
            query = query.filter(GraphNode.type_id == node_type_obj.id)
        existing_node = query.first()

        if existing_node:
            logger.debug(f"Found exact match for '{name}' (canonical: '{canonical}')")
            return existing_node
        else:
            logger.debug(f"No exact match for '{name}' (canonical: '{canonical}'), trying aliases and vector search")

        # Strategy 2: Alias match
        dialect = session.bind.dialect.name

        if dialect == 'postgresql':
            # PostgreSQL: check if canonical is in the array using raw SQL with parameterization
            from sqlalchemy import text as sql_text
            query = session.query(GraphNode).filter(
                sql_text(":canonical = ANY(graph_nodes.aliases)").bindparams(canonical=canonical)
            )
            if node_type_obj:
                query = query.filter(GraphNode.type_id == node_type_obj.id)
            alias_match = query.first()

            if alias_match:
                logger.debug(f"Found alias match for '{name}' in node '{alias_match.name}'")
                return alias_match
            else:
                logger.debug(f"No alias match for '{name}'")
        else:
            # SQLite: fetch all nodes of this type and check aliases in Python
            query = session.query(GraphNode).filter(GraphNode.aliases.isnot(None))
            if node_type_obj:
                query = query.filter(GraphNode.type_id == node_type_obj.id)
            nodes_with_aliases = query.all()

            for node in nodes_with_aliases:
                if node.aliases and canonical in node.aliases:
                    logger.debug(f"Found alias match for '{name}' in node '{node.name}'")
                    return node

        # Strategy 3: Vector similarity search
        try:
            # Generate embedding for query name
            embedder = get_embedder(provider=embedding_config['provider'],
                                    model=embedding_config['model'],
                                    session=session)
            query_embedding = embedder([canonical])[0]

            # Find similar nodes
            similar_nodes = find_similar_nodes(
                query_embedding=query_embedding,
                node_type_id=node_type_obj.id if node_type_obj else None,
                limit=10,
                session=session
            )

            # Filter by threshold
            logger.debug(f"Vector similarity search found {len(similar_nodes)} candidates for '{name}', best similarity: {similar_nodes[0][1] if similar_nodes else 'N/A'}")
            candidates = [(node, score) for node, score in similar_nodes if score >= similarity_threshold]

            if candidates:
                logger.debug(f"Found {len(candidates)} candidates above threshold {similarity_threshold}")
                # Take first candidate
                matched_node = candidates[0][0]
                logger.debug(f"Found vector match for '{name}': '{matched_node.name}' (similarity: {candidates[0][1]:.3f})")
                # Add canonical name to aliases
                if matched_node.aliases is None:
                    matched_node.aliases = []
                if canonical not in matched_node.aliases and canonical != matched_node.canonical_name:
                    matched_node.aliases.append(canonical)
                    # Mark the aliases attribute as modified so SQLAlchemy detects the change
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(matched_node, 'aliases')
                    matched_node.updated_time = func.now()
                    logger.debug(f"Added '{canonical}' (canonical form of '{name}') to aliases of node '{matched_node.name}'")
                return matched_node

        except Exception as e:
            logger.warning(f"Vector similarity search failed for '{name}': {e}")
            # Continue to return None

        # No match found
        return None
