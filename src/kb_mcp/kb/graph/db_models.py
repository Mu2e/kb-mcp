"""Database models for graph knowledge base."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from ..db_models import Base, JSONB
from ..embedding.types import Vector
from .types import ArrayOfStrings


class GraphNodeType(Base):
    """Table 'graph_node_types' for tracking entity types.

    Entity types categorize nodes in the graph (e.g., Person, Organization, Concept).

    Attributes:
        id (str): Primary key (UUID stored as string).
        label (str): Type name (unique, e.g., "Person", "Organization").
        description (str): Optional description of the type.
        meta (dict): Additional metadata (JSON).
        created_time (datetime): Timestamp when the type was created.
    """
    __tablename__ = "graph_node_types"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    label = Column(String(128), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    meta = Column(JSONB, nullable=True, default=dict)
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )

    # Relationships
    nodes = relationship("GraphNode", back_populates="node_type")

    def __repr__(self) -> str:
        return f"<GraphNodeType(id={self.id}, label={self.label})>"


class GraphVerb(Base):
    """Table 'graph_verbs' for tracking relationship types.

    Verbs define the types of relationships between nodes (e.g., "authored", "cites").

    Attributes:
        id (str): Primary key (UUID stored as string).
        name (str): Verb name (unique, e.g., "authored", "cites").
        description (str): Optional description of the verb.
        inverse_verb_id (str): Optional FK to another verb (for bidirectional relationships).
        meta (dict): Additional metadata (e.g., transitivity, symmetry).
        created_time (datetime): Timestamp when the verb was created.
    """
    __tablename__ = "graph_verbs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    name = Column(String(128), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    inverse_verb_id = Column(
        String(36),
        ForeignKey("graph_verbs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    meta = Column(JSONB, nullable=True, default=dict)
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )

    # Relationships
    relations = relationship("GraphRelation", back_populates="verb")
    inverse_verb = relationship(
        "GraphVerb",
        remote_side=[id],
        foreign_keys=[inverse_verb_id],
        backref="reverse_verbs",
    )

    def __repr__(self) -> str:
        return f"<GraphVerb(id={self.id}, name={self.name})>"


class GraphNode(Base):
    """Table 'graph_nodes' for entity instances in the graph.

    Nodes represent entities (people, organizations, concepts, etc.) in the knowledge graph.

    Attributes:
        id (str): Primary key (UUID stored as string).
        type_id (str): Foreign key to graph_node_types.
        name (str): Display name of the entity.
        canonical_name (str): Normalized name for matching (lowercase, stripped).
        embedding (list): Optional vector embedding for similarity matching.
        embedding_name (str): Optional FK to embedding_configs (which model was used).
        meta (dict): Arbitrary properties (JSON).
        aliases (list): Alternative names (PostgreSQL array, SQLite JSON).
        created_time (datetime): Timestamp when the node was created.
        updated_time (datetime): Timestamp when the node was last updated.
    """
    __tablename__ = "graph_nodes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    type_id = Column(
        String(36),
        ForeignKey("graph_node_types.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name = Column(String(512), nullable=False, index=True)
    canonical_name = Column(String(512), nullable=False, index=True)
    embedding = Column(Vector(dimension=None), nullable=True)  # Variable dimension
    embedding_name = Column(
        String(64),
        ForeignKey("embedding_configs.short_name", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    meta = Column(JSONB, nullable=True, default=dict)
    aliases = Column(ArrayOfStrings, nullable=True)
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )
    updated_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
    )

    # Relationships
    node_type = relationship("GraphNodeType", back_populates="nodes")
    embedding_config = relationship("EmbeddingConfig")
    outgoing_relations = relationship(
        "GraphRelation",
        foreign_keys="GraphRelation.source_id",
        back_populates="source_node",
    )
    incoming_relations = relationship(
        "GraphRelation",
        foreign_keys="GraphRelation.target_id",
        back_populates="target_node",
    )
    document_mappings = relationship("GraphNodeMap", back_populates="node")

    # Indexes
    __table_args__ = (
        Index("idx_graph_nodes_type_name", "type_id", "canonical_name"),
        # GIN index for aliases array (PostgreSQL only)
        Index(
            "idx_graph_nodes_aliases",
            "aliases",
            postgresql_using="gin",
        ),
    )

    def __repr__(self) -> str:
        return f"<GraphNode(id={self.id}, name={self.name}, type={self.type_id})>"


class GraphRelation(Base):
    """Table 'graph_relations' for relationships between nodes.

    Relations represent directed edges in the knowledge graph.

    Attributes:
        id (str): Primary key (UUID stored as string).
        source_id (str): FK to graph_nodes (source).
        verb_id (str): FK to graph_verbs.
        target_id (str): FK to graph_nodes (target).
        meta (dict): Additional metadata (JSON).
        created_time (datetime): Timestamp when the relation was created.
        updated_time (datetime): Timestamp when the relation was last updated.
    """
    __tablename__ = "graph_relations"

    # UUID primary key
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)

    # Unique composite constraint on (source_id, verb_id, target_id)
    source_id = Column(
        String(36),
        ForeignKey("graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    verb_id = Column(
        String(36),
        ForeignKey("graph_verbs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    target_id = Column(
        String(36),
        ForeignKey("graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    meta = Column(JSONB, nullable=True, default=dict)
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )
    updated_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
    )

    # Relationships
    source_node = relationship(
        "GraphNode",
        foreign_keys=[source_id],
        back_populates="outgoing_relations",
    )
    target_node = relationship(
        "GraphNode",
        foreign_keys=[target_id],
        back_populates="incoming_relations",
    )
    verb = relationship("GraphVerb", back_populates="relations")
    evidence_records = relationship(
        "GraphRelationEvidence",
        back_populates="relation",
        cascade="all, delete-orphan",
    )

    # Additional indexes and constraints
    __table_args__ = (
        Index("idx_graph_relations_source_verb", "source_id", "verb_id"),
        # Unique constraint on composite key (source, verb, target)
        Index("idx_graph_relations_unique", "source_id", "verb_id", "target_id", unique=True),
    )

    def __repr__(self) -> str:
        return f"<GraphRelation(source={self.source_id}, verb={self.verb_id}, target={self.target_id})>"


class GraphRelationEvidence(Base):
    """Table 'graph_relations_evidence' for relation provenance.

    Evidence records track where and how relations were discovered, allowing
    multiple independent justifications per relation.

    Attributes:
        id (str): Primary key (UUID stored as string).
        relation_id (str): FK to graph_relations.
        document_id (str): Optional FK to documents (source document).
        evidence_text (str): Optional text snippet justifying the relation.
        confidence (float): Confidence score (0.0-1.0).
        extraction_model (str): Optional LLM model that extracted this.
        meta (dict): Additional metadata (JSON).
        created_time (datetime): Timestamp when the evidence was created.
    """
    __tablename__ = "graph_relations_evidence"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    relation_id = Column(
        String(36),
        ForeignKey("graph_relations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id = Column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    evidence_text = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True, default=1.0)
    extraction_model = Column(String(128), nullable=True)
    meta = Column(JSONB, nullable=True, default=dict)
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )

    # Relationships
    document = relationship("Document")
    relation = relationship("GraphRelation", back_populates="evidence_records")

    def __repr__(self) -> str:
        return f"<GraphRelationEvidence(id={self.id}, document={self.document_id})>"


class GraphNodeMap(Base):
    """Table 'graph_node_map' for node-document associations.

    Tracks which documents mention which nodes, enabling efficient queries
    like "find all documents that mention this entity".

    Attributes:
        node_id (str): Primary key part 1 - FK to graph_nodes.
        document_id (str): Primary key part 2 - FK to documents.
        count (int): Number of times the node is mentioned in this document.
        meta (dict): Additional metadata (JSON).
        created_time (datetime): Timestamp when the mapping was created.
        updated_time (datetime): Timestamp when the mapping was last updated.
    """
    __tablename__ = "graph_node_map"

    # Composite primary key
    node_id = Column(
        String(36),
        ForeignKey("graph_nodes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id = Column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    count = Column(Integer, nullable=False, default=1)
    meta = Column(JSONB, nullable=True, default=dict)
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    updated_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
    )

    # Relationships
    node = relationship("GraphNode", back_populates="document_mappings")
    document = relationship("Document")

    def __repr__(self) -> str:
        return f"<GraphNodeMap(node={self.node_id}, document={self.document_id}, count={self.count})>"


class GraphExtractionLog(Base):
    """Table 'graph_extraction_logs' for tracking extraction runs.

    Logs extraction attempts with timing, model info, and statistics.

    Attributes:
        id (str): Primary key (UUID stored as string).
        document_id (str): FK to documents.
        hostname (str): Host where extraction ran.
        extraction_model (str): LLM model used for extraction.
        time_extraction (float): Extraction time in seconds.
        time_processing (float): Processing time in seconds.
        relations_extracted (int): Number of relations extracted by LLM.
        relations_processed (int): Number of relations successfully processed.
        relations_created (int): Number of new relations created.
        relations_updated (int): Number of existing relations updated.
        relations_errors (int): Number of relations that failed processing.
        error_details (dict): JSON array of error details.
        meta (dict): Additional metadata (JSON).
        created_time (datetime): Timestamp when the extraction ran.
    """
    __tablename__ = "graph_extraction_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    document_id = Column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hostname = Column(String(256), nullable=True)
    extraction_model = Column(String(128), nullable=False)
    time_extraction = Column(Float, nullable=True)  # Seconds
    time_processing = Column(Float, nullable=True)  # Seconds
    relations_extracted = Column(Integer, nullable=False, default=0)
    relations_processed = Column(Integer, nullable=False, default=0)
    relations_created = Column(Integer, nullable=False, default=0)
    relations_updated = Column(Integer, nullable=False, default=0)
    relations_errors = Column(Integer, nullable=False, default=0)
    error_details = Column(JSONB, nullable=True)  # Array of error dicts
    meta = Column(JSONB, nullable=True, default=dict)
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )

    # Relationships
    document = relationship("Document")

    # Indexes
    __table_args__ = (
        Index("idx_graph_extraction_logs_document", "document_id"),
        Index("idx_graph_extraction_logs_created", "created_time"),
    )

    def __repr__(self) -> str:
        return f"<GraphExtractionLog(id={self.id}, document={self.document_id}, model={self.extraction_model})>"
