"""Core graph operations."""

import logging
from typing import List, Optional, Tuple, Dict, Any

from sqlalchemy import and_, or_, func

from ..database import get_db_session
from .db_models import GraphNodeType, GraphVerb, GraphNode, GraphRelation, GraphRelationEvidence, GraphNodeMap

logger = logging.getLogger(__name__)


# Default node types and verbs
DEFAULT_TYPES = [
    ("Concept", "Theoretical ideas (e.g., 'Standard Model', 'Muon', 'Antimatter')"),
    ("Document", "Papers, memos, logbooks"),
    ("Location", "Physical locations (e.g., 'CERN', 'Building 4')"),
    ("Measurement", "Specific results (e.g., '125 GeV', 'Efficiency 98%')"),
    ("Hardware", "Detectors, cables, chips"),
    ("Experiment", "Experiments or runs (e.g., 'Run-2', 'Test Beam 2024')"),
    ("Organization", "Institutions (e.g., 'University of Zurich', 'DOE')"),
    ("Person", "Researchers, authors"),
    #("Value", "Numerical values without or with units (e.g., '5 Tesla', '3 cm', '0.3 efficiency', '0.824 ± 0.031(stat) ± 0.032(syst)')"),
]

DEFAULT_VERBS = [
    ("references", "General reference link (Doc -> Concept / Doc -> Doc)"),
    ("cites", "Formal citation (Doc -> Doc)"),
    ("measures", "Measurement relationship (Hardware -> Measurement / Concept)"),
    ("located_in", "Location relationship (Hardware -> Location)"),
    ("supersedes", "Replacement relationship (Doc A -> Doc B, Concept -> Concept)"),
    ("part_of", "Component relationship (Hardware -> Hardware)"),
    ("influences", "Influence relationship (Concept -> Concept, Hardware -> Hardware)"),
    ("produced", "Production relationship (Experiment -> Measurement)"),
    ("authored_by", "Authorship (Doc -> Person)"),
    #("has_value_of", "Value relationship (Concept -> Values)"),
]


def seed_graph_defaults(session=None) -> None:
    """
    Seed default node types and verbs if tables are empty.

    This function is called automatically during init_db() to populate
    common entity types and relationship types.

    Args:
        session: Optional database session. If None, creates a new one.
    """
    with get_db_session(session) as session:
        # Check if graph_node_types table is empty
        type_count = session.query(func.count(GraphNodeType.id)).scalar()

        if type_count == 0:
            logger.info("Seeding default graph node types...")
            for label, description in DEFAULT_TYPES:
                node_type = GraphNodeType(label=label, description=description)
                session.add(node_type)

        # Check if graph_verbs table is empty
        verb_count = session.query(func.count(GraphVerb.id)).scalar()

        if verb_count == 0:
            logger.info("Seeding default graph verbs...")
            for name, description in DEFAULT_VERBS:
                verb = GraphVerb(name=name, description=description)
                session.add(verb)

        # Commit if we added anything
        if type_count == 0 or verb_count == 0:
            session.commit()
            logger.info(
                f"Graph defaults seeded: {len(DEFAULT_TYPES)} types, {len(DEFAULT_VERBS)} verbs"
            )


def get_node_types(session=None) -> dict:
    """
    Get all node types, ordered by label.

    Args:
        session: Optional database session.

    Returns:
        List of GraphNodeType instances.
    """
    with get_db_session(session) as session:
        types = session.query(GraphNodeType).order_by(GraphNodeType.label).all()
        return {t.label: t.description for t in types}


def get_verbs(session=None) -> List[GraphVerb]:
    """
    Get all verbs, ordered by name.

    Args:
        session: Optional database session.

    Returns:
        List of GraphVerb instances.
    """
    with get_db_session(session) as session:
        verbs =  session.query(GraphVerb).order_by(GraphVerb.name).all()
        return {v.name: v.description for v in verbs}






def get_or_create_node(
    node_type: str,
    name: str,
    aliases: Optional[List[str]] = None,
    meta: Optional[Dict[str, Any]] = None,
    similarity_threshold: Optional[float] = None,
    session=None,
) -> GraphNode:
    """
    Get existing node or create new one using multi-strategy matching.

    Matching strategies (in order):
    1. Exact canonical name match within type
    2. Alias match within type
    3. Vector similarity search within type (if embeddings available)

    If node found, returns existing node.
    If not found, creates new node with auto-generated embedding.

    Args:
        node_type: Type label (e.g., "Person", "Organization").
        name: Node name to search for or create.
        aliases: Additional aliases to add if creating new node.
        meta: Metadata dict to store with node.
        similarity_threshold: Minimum similarity for vector match (defaults to config).
        session: Optional database session.

    Returns:
        GraphNode instance (existing or newly created).

    Raises:
        ValueError: If name is empty or node_type doesn't exist.
    """
    from ...config import get_graph_config
    from ..embedding.utils import get_embedder
    from .matching import _normalize_name, find_node

    # Validate input
    if not name or not name.strip():
        raise ValueError("Node name cannot be empty")

    canonical = _normalize_name(name)

    with get_db_session(session) as session:
        # Try to find existing node (this also validates node_type)
        existing_node = find_node(
            name=name,
            node_type=node_type,
            similarity_threshold=similarity_threshold,
            session=session
        )

        if existing_node:
            if existing_node.canonical_name != canonical:
                # Add canonical name to aliases if not present
                if existing_node.aliases is None:
                    existing_node.aliases = []
                if canonical not in existing_node.aliases:
                    existing_node.aliases.append(canonical)
                    # Mark the aliases attribute as modified so SQLAlchemy detects the change
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(existing_node, 'aliases')
                    existing_node.updated_time = func.now()
                    logger.debug(f"Added '{canonical}' (canonical form of '{name}') to aliases of node '{existing_node.name}'")

            return existing_node

        # No match found - create new node
        logger.info(f"Creating new node: '{name}' (type: {node_type})")

        # Get configuration and normalize name for node creation
        graph_config = get_graph_config()
        embedding_config = graph_config['embedding']
        

        # Get node type (already validated by find_node, but we need the object)
        node_type_obj = session.query(GraphNodeType).filter(
            GraphNodeType.label == node_type
        ).first()

        # Generate embedding for new node
        node_embedding = None
        node_embedding_name = None
        try:
            embedder = get_embedder(provider=embedding_config['provider'],
                                    model=embedding_config['model'],
                                    session=session)
            node_embedding = embedder([canonical])[0]
            # Get the embedding name from the embedder
            # The embedder should have a short_name property based on provider/model
            if hasattr(embedder, 'short_name'):
                node_embedding_name = embedder.short_name
        except Exception as e:
            logger.warning(f"Could not generate embedding for new node '{name}': {e}")

        # Prepare aliases list (store canonical forms for matching)
        node_aliases = []
        if aliases:
            # Normalize all provided aliases
            for alias in aliases:
                alias_canonical = _normalize_name(alias)
                if alias_canonical and alias_canonical != canonical and alias_canonical not in node_aliases:
                    node_aliases.append(alias_canonical)

        # Create new node
        new_node = GraphNode(
            type_id=node_type_obj.id,
            name=name,
            canonical_name=canonical,
            embedding=node_embedding,
            embedding_name=node_embedding_name,
            meta=meta or {},
            aliases=node_aliases if node_aliases else None,
        )
        session.add(new_node)
        session.flush()  # Get the ID

        logger.info(f"Created new node: {new_node.id} - '{name}'")
        return new_node


def update_node_map(
    node_id: str,
    document_id: str,
    session,
) -> None:
    """
    Update or create graph_node_map entry, incrementing count if exists.

    Args:
        node_id: Node ID.
        document_id: Document ID.
        session: Database session.
    """
    # Check if mapping already exists
    existing_map = session.query(GraphNodeMap).filter(
        and_(
            GraphNodeMap.node_id == node_id,
            GraphNodeMap.document_id == document_id
        )
    ).first()

    if existing_map:
        # Increment count
        existing_map.count += 1
        existing_map.updated_time = func.now()
        logger.debug(f"Updated node_map: node={node_id}, doc={document_id}, count={existing_map.count}")
    else:
        # Create new mapping
        new_map = GraphNodeMap(
            node_id=node_id,
            document_id=document_id,
            count=1
        )
        session.add(new_map)
        logger.debug(f"Created node_map: node={node_id}, doc={document_id}")


def add_relation(
    source_type: str,
    source_name: str,
    verb: str,
    target_type: str,
    target_name: str,
    evidence_document_id: Optional[str] = None,
    evidence_text: Optional[str] = None,
    confidence: Optional[float] = None,
    extraction_model: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    session=None,
) -> tuple[GraphRelation, bool]:
    """
    Add or update a relation between two nodes with evidence.

    Uses get_or_create_node for both source and target.
    Creates or updates graph_node_map entries.
    Adds evidence record.

    Args:
        source_type: Source node type.
        source_name: Source node name.
        verb: Verb name (e.g., "authored", "cites").
        target_type: Target node type.
        target_name: Target node name.
        evidence_document_id: Document ID where relation was found.
        evidence_text: Optional text snippet justifying relation.
        confidence: Optional confidence score (0.0-1.0).
        extraction_model: Optional model that extracted this relation.
        meta: Optional additional metadata.
        session: Optional database session.

    Returns:
        Tuple of (GraphRelation object, is_new: bool).
        is_new is True if the relation was newly created, False if it already existed.
    """
    with get_db_session(session) as session:
        # Get or create source node
        source_node = get_or_create_node(
            node_type=source_type,
            name=source_name,
            session=session
        )

        # Get or create target node
        target_node = get_or_create_node(
            node_type=target_type,
            name=target_name,
            session=session
        )

        # Get verb id
        verb_obj = session.query(GraphVerb).filter(GraphVerb.name == verb).first()
        if not verb_obj:
            raise ValueError(f"Verb '{verb}' does not exist.")

        # Use INSERT ... ON CONFLICT for PostgreSQL (upsert), fallback for SQLite
        dialect = session.bind.dialect.name

        if dialect == 'postgresql':
            # PostgreSQL: Use INSERT ... ON CONFLICT with RETURNING
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from sqlalchemy import literal_column

            # Insert with ON CONFLICT DO UPDATE (dummy update to trigger RETURNING on conflict)
            stmt = pg_insert(GraphRelation).values(
                source_id=source_node.id,
                verb_id=verb_obj.id,
                target_id=target_node.id,
                meta=meta or {}
            ).on_conflict_do_update(
                index_elements=['source_id', 'verb_id', 'target_id'],
                set_={'updated_time': func.now()}  # Dummy update to trigger RETURNING
            ).returning(
                GraphRelation.source_id,
                GraphRelation.verb_id,
                GraphRelation.target_id,
                GraphRelation.meta,
                GraphRelation.created_time,
                GraphRelation.updated_time,
                # Use xmax to detect if this was an insert (0) or update (>0)
                # xmax is a PostgreSQL system column that's 0 for new rows
                literal_column('(xmax = 0)').label('inserted')
            )

            result = session.execute(stmt)
            returned_row = result.fetchone()

            # Check if this was a new insert or existing row
            is_new = bool(returned_row.inserted)

            # Query the relation from the database (it already exists from the upsert)
            relation = session.query(GraphRelation).filter(
                and_(
                    GraphRelation.source_id == source_node.id,
                    GraphRelation.verb_id == verb_obj.id,
                    GraphRelation.target_id == target_node.id
                )
            ).first()

            if is_new:
                logger.info(
                    f"Created relation: {source_node.name} --[{verb}]--> {target_node.name}"
                )
            else:
                logger.debug(
                    f"Relation already exists: {source_node.name} --[{verb}]--> {target_node.name}"
                )
        else:
            # SQLite: Use try/except with IntegrityError
            from sqlalchemy.exc import IntegrityError

            relation = GraphRelation(
                source_id=source_node.id,
                verb_id=verb_obj.id,
                target_id=target_node.id,
                meta=meta or {}
            )
            session.add(relation)

            try:
                session.flush()
                is_new = True
                logger.info(
                    f"Created relation: {source_node.name} --[{verb}]--> {target_node.name}"
                )
            except IntegrityError:
                # Relation already exists, rollback and query for it
                session.rollback()
                is_new = False
                relation = session.query(GraphRelation).filter(
                    and_(
                        GraphRelation.source_id == source_node.id,
                        GraphRelation.verb_id == verb_obj.id,
                        GraphRelation.target_id == target_node.id
                    )
                ).first()
                logger.debug(
                    f"Relation already exists: {source_node.name} --[{verb}]--> {target_node.name}"
                )

        # Add evidence record
        evidence = GraphRelationEvidence(
            relation_id=relation.id,
            document_id=evidence_document_id,
            evidence_text=evidence_text,
            confidence=confidence,
            extraction_model=extraction_model,
            meta=meta or {}
        )
        session.add(evidence)
        logger.debug(f"Added evidence for relation {relation.id} from document {evidence_document_id}")

        # Update graph_node_map for source node if we have a document ID
        if evidence_document_id:
            update_node_map(
                node_id=source_node.id,
                document_id=evidence_document_id,
            session=session
            )
            logger.debug(f"Updated node_map for source node {source_node.id} and document {evidence_document_id}")

            update_node_map(
                node_id=target_node.id,
                document_id=evidence_document_id,
                session=session
            )
            logger.debug(f"Updated node_map for target node {target_node.id} and document {evidence_document_id}")
        return relation, is_new
