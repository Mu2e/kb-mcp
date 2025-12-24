"""Database models for chunks and embeddings."""

import re
import uuid
from typing import List, Optional, Union, Dict, Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    func,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from ..db_models import Base, Document, JSONB
from .types import Vector


def sanitize_table_name(name: str) -> str:
    """
    Sanitize a name for use as a SQL table name.
    
    Replaces invalid characters with hyphens and ensures it starts with a letter.
    Table names will be like: embeddings_openai-small
    
    Args:
        name: Model ID (e.g., "openai-small")
    
    Returns:
        Sanitized table name component
    """
    # Replace any non-alphanumeric characters (except hyphens) with underscores
    sanitized = re.sub(r'[^a-zA-Z0-9-]', '_', name)
    # Ensure it starts with a letter
    if sanitized and not sanitized[0].isalpha():
        sanitized = 'a' + sanitized
    return sanitized.lower()


def get_embedding_table_name(short_name: str) -> str:
    """
    Get the table name for an embedding model.
    
    Args:
        short_name: Short name from EmbeddingConfig (e.g., "openai-small")
    
    Returns:
        Table name (e.g., "embeddings_openai-small")
    """
    sanitized = sanitize_table_name(short_name)
    return f"embeddings_{sanitized}"


def create_embedding_table(short_name: str, dimension: int, metadata=None) -> Table:
    """
    Create a SQLAlchemy Table for embeddings of a specific model.
    
    Each embedding model gets its own table with a fixed dimension.
    Table name format: embeddings_{short_name}
    
    Args:
        short_name: Short name from EmbeddingConfig (e.g., "openai-small")
        dimension: Embedding vector dimension
        metadata: Optional SQLAlchemy MetaData (defaults to Base.metadata)
    
    Returns:
        SQLAlchemy Table object

    Attributes:
        id (str): Primary key (UUID stored as string).
        chunk_id (str): Foreign key to the chunks table.
        embedding (vector): Embedding vector.
        created_time (datetime): Timestamp when the embedding was created.
    """
    if metadata is None:
        metadata = Base.metadata
    
    table_name = get_embedding_table_name(short_name)
    
    # Check if table already exists
    if table_name in metadata.tables:
        return metadata.tables[table_name]
    
    # Create vector type with fixed dimension for pgvector
    vector_type = Vector(dimension=dimension)
    
    table = Table(
        table_name,
        metadata,
        Column("id", String(36), primary_key=True, default=lambda: str(uuid.uuid4())),
        Column("chunk_id", String(36), ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False),
        Column("embedding", vector_type, nullable=False),
        Column("created_time", DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now(), index=True),
        Index(f"idx_{table_name}_chunk_id", "chunk_id"),
        Index(f"uq_{table_name}_chunk_id", "chunk_id", unique=True),
    )
    
    return table


def get_embedding_table(
    session,
    embedding_name: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[Table]:
    """Get embedding table if the config exists, otherwise return None.

    Args:
        session: Database session (required)
        embedding_name: Short name of embedding config (e.g., "openai-small")
        provider: Provider name (e.g., "openai") - used if embedding_name not provided
        model: Model name - used if embedding_name not provided

    Returns:
        Table object if config exists, None otherwise
    """
    # Determine which embedding config we're looking for
    if embedding_name is None:
        # Need to get embedder to generate the config name
        from kb_mcp.kb.embedding.utils import get_embedder
        embedder = get_embedder(provider=provider, model=model, session=session)
        config_name = embedder._generate_short_name()
    else:
        config_name = embedding_name

    # Try to get existing embedding config
    embedding_config = session.query(EmbeddingConfig).filter(
        EmbeddingConfig.short_name == config_name
    ).first()

    if embedding_config:
        return create_embedding_table(embedding_config.short_name, embedding_config.dimension)
    else:
        return None


class EmbeddingConfig(Base):
    """Embedding configuration table 'embedding_configs' that is used to map (provider, model) -> short_name + dimension + meta.

    Attributes:
        short_name (str): Short internal name (e.g., "openai-small", "st-minilm").
        provider (str): Provider identifier (e.g., "openai", "sentence-transformers").
        model (str): Full model name (e.g., "text-embedding-3-small", "all-MiniLM-L6-v2").
        dimension (int): Embedding dimension.
        meta (dict): Metadata - flexible JSON field for API parameters, version, etc.
        created_time (datetime): Timestamp when the embedding configuration was created.
    """

    __tablename__ = "embedding_configs"

    # Primary key - short internal name (e.g., "openai-small", "st-minilm")
    short_name = Column(String(64), primary_key=True)

    # Provider identifier (e.g., "openai", "sentence-transformers")
    provider = Column(String(64), nullable=False, index=True)

    # Full model name (e.g., "text-embedding-3-small", "all-MiniLM-L6-v2")
    model = Column(String(128), nullable=False, index=True)

    # Embedding dimension
    dimension = Column(Integer, nullable=False)

    # Metadata - flexible JSON field for API parameters, version, etc.
    meta = Column(JSONB, nullable=True, default=dict)  # JSONB for PostgreSQL, JSON for SQLite

    # Timestamp
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )

    # Unique constraint on provider+model combination
    __table_args__ = (
        UniqueConstraint("provider", "model", name="uq_embedding_config_provider_model"),
        Index("idx_embedding_config_provider_model", "provider", "model"),
    )

    def __repr__(self) -> str:
        return (
            f"<EmbeddingConfig(short_name={self.short_name}, provider={self.provider}, "
            f"model={self.model}, dimension={self.dimension})>"
        )

    def get_table_name(self) -> str:
        """Get the table name for embeddings of this config."""
        return get_embedding_table_name(self.short_name)

    def get_table(self) -> Table:
        """Get or create the SQLAlchemy Table for embeddings of this config."""
        return create_embedding_table(self.short_name, self.dimension)

    @classmethod
    def from_dict(cls, data: dict) -> "EmbeddingConfig":
        """Create an EmbeddingConfig instance from a dictionary.

        Args:
            data: Dictionary with config fields. Required fields:
                - short_name, provider, model, dimension

        Returns:
            EmbeddingConfig instance (not yet saved to database)
        """
        if "short_name" not in data:
            raise ValueError("short_name is required")
        if "provider" not in data:
            raise ValueError("provider is required")
        if "model" not in data:
            raise ValueError("model is required")
        if "dimension" not in data:
            raise ValueError("dimension is required")

        return cls(
            short_name=data["short_name"],
            provider=data["provider"],
            model=data["model"],
            dimension=data["dimension"],
            meta=data.get("meta", {}),
        )

    def to_dict(self) -> dict:
        """Convert EmbeddingConfig instance to dictionary.

        Returns:
            Dictionary representation of the config
        """
        return {
            "short_name": self.short_name,
            "provider": self.provider,
            "model": self.model,
            "dimension": self.dimension,
            "meta": self.meta if self.meta else {},
            "created_time": self.created_time.isoformat() if self.created_time else None,
            "table_name": self.get_table_name(),
        }


class ChunkStrategy(Base):
    """Table to store information about different chunk strategies.
    
    Attributes:
        strategy (str): Strategy identifier (e.g., "tokens_1000_200", "paragraphs_10").
        meta (dict): Metadata - flexible JSON field for chunking parameters. For example, the number of tokens to chunk into or the chunking overlap.
        created_time (datetime): Timestamp when the chunk strategy was created.
    """

    __tablename__ = "chunk_strategies"

    # Primary key - strategy identifier (e.g., "tokens_1000_200")
    strategy = Column(String(128), primary_key=True)

    # Metadata - flexible JSON field for chunking parameters
    meta = Column(JSONB, nullable=True, default=dict)  # JSONB for PostgreSQL, JSON for SQLite

    # Timestamp
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )

    def __repr__(self) -> str:
        return f"<ChunkStrategy(strategy={self.strategy}, meta={self.meta})>"

    @classmethod
    def from_dict(cls, data: dict) -> "ChunkStrategy":
        """Create a ChunkStrategy instance from a dictionary.

        Args:
            data: Dictionary with strategy fields. Required fields:
                - strategy

        Returns:
            ChunkStrategy instance (not yet saved to database)
        """
        if "strategy" not in data:
            raise ValueError("strategy is required")

        return cls(
            strategy=data["strategy"],
            meta=data.get("meta", {}),
        )

    def to_dict(self) -> dict:
        """Convert ChunkStrategy instance to dictionary.

        Returns:
            Dictionary representation of the strategy
        """
        return {
            "strategy": self.strategy,
            "meta": self.meta if self.meta else {},
            "created_time": self.created_time.isoformat() if self.created_time else None,
        }


class ParsingLog(Base):
    """Log table 'logs_parsing' for tracking document parsing/text extraction operations.

    Attributes:
        id (str): Primary key (UUID stored as string).
        document_id (str): Foreign key to the documents table.
        text_extraction_time_seconds (float): Time for text extraction only.
        image_description_time_seconds (float): Time for image description generation (if enabled).
        total_time_seconds (float): Total parsing time.
        num_documents (int): Number of documents extracted.
        text_length (int): Total text length extracted.
        hostname (str): Hostname of the machine where operation was performed.
        meta (dict): Metadata - flexible JSON field for additional metadata.
        insertion_time (datetime): Timestamp when the operation completed.
    """

    __tablename__ = "logs_parsing"

    # Primary key - UUID stored as string
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )

    # Foreign key to document table (nullable since document might not exist yet during parsing)
    # No CASCADE - logs should persist even if document is deleted
    document_id = Column(
        String(36),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Timing information
    text_extraction_time_seconds = Column(Float, nullable=False)  # Time for text extraction only
    image_description_time_seconds = Column(Float, nullable=True)  # Time for image description generation (if enabled)
    total_time_seconds = Column(Float, nullable=False)  # Total parsing time

    # Counts
    num_documents = Column(Integer, nullable=False, default=1)  # Number of documents extracted
    text_length = Column(Integer, nullable=True)  # Total text length extracted

    # Hostname of the machine where operation was performed
    hostname = Column(String(256), nullable=True, index=True)

    # Timestamp when operation completed
    insertion_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )

    # Additional metadata (JSON)
    meta = Column(JSONB, nullable=True, default=dict)  # JSONB for PostgreSQL, JSON for SQLite

    # Relationships
    document = relationship("Document", backref="parsing_logs")

    # Indexes
    __table_args__ = (
        Index("idx_logs_parsing_document_id", "document_id"),
        Index("idx_logs_parsing_insertion_time", "insertion_time"),
        Index("idx_logs_parsing_hostname", "hostname"),
    )

    def __repr__(self) -> str:
        return (
            f"<ParsingLog(id={self.id}, document_id={self.document_id}, "
            f"total_time={self.total_time_seconds}s, "
            f"text_extraction={self.text_extraction_time_seconds}s, "
            f"image_description={self.image_description_time_seconds}s, "
            f"num_documents={self.num_documents}, hostname={self.hostname})>"
        )


class ChunkEmbeddingLog(Base):
    """Log table 'logs_chunk_embedding' for tracking chunking and embedding operations.
    
    Attributes:
        id (str): Primary key (UUID stored as string).
        document_id (str): Foreign key to the documents table.
        chunking_time_seconds (float): Time for chunking.
        embedding_time_seconds (float): Time for embedding.
        total_time_seconds (float): Total time.
        num_chunks (int): Number of chunks.
        num_embeddings (int): Number of embeddings.
        chunk_strategy (str): Foreign key to the chunk_strategies table.
        embedding_name (str): Foreign key to the embedding_configs table.
        hostname (str): Hostname of the machine where operation was performed.
        insertion_time (datetime): Timestamp when the operation completed.
        meta (dict): Metadata - flexible JSON field for additional metadata.

    """

    __tablename__ = "logs_chunk_embedding"

    # Primary key - UUID stored as string
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )

    # Foreign key to document table
    document_id = Column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Timing information
    chunking_time_seconds = Column(Float, nullable=False)
    embedding_time_seconds = Column(Float, nullable=False)
    total_time_seconds = Column(Float, nullable=False)

    # Counts
    num_chunks = Column(Integer, nullable=False, default=0)
    num_embeddings = Column(Integer, nullable=False, default=0)

    # Configuration used
    chunk_strategy = Column(String(128), nullable=True, index=True)
    embedding_name = Column(String(64), nullable=True, index=True)

    # Hostname of the machine where operation was performed
    hostname = Column(String(256), nullable=True, index=True)

    # Timestamp when operation completed
    insertion_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )

    # Additional metadata (JSON)
    meta = Column(JSONB, nullable=True, default=dict)  # JSONB for PostgreSQL, JSON for SQLite

    # Relationships
    document = relationship("Document", backref="chunk_embedding_logs")

    # Indexes
    __table_args__ = (
        Index("idx_logs_chunk_embedding_document_id", "document_id"),
        Index("idx_logs_chunk_embedding_insertion_time", "insertion_time"),
        Index("idx_logs_chunk_embedding_strategy", "chunk_strategy"),
        Index("idx_logs_chunk_embedding_embedding_name", "embedding_name"),
        Index("idx_logs_chunk_embedding_hostname", "hostname"),
    )

    def __repr__(self) -> str:
        return (
            f"<ChunkEmbeddingLog(id={self.id}, document_id={self.document_id}, "
            f"total_time={self.total_time_seconds}s, chunks={self.num_chunks}, "
            f"embeddings={self.num_embeddings}, hostname={self.hostname})>"
        )


class SummaryLog(Base):
    """Log table for tracking summary and gist generation operations."""

    __tablename__ = "logs_summary"

    # Primary key - UUID stored as string
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )

    # Foreign key to document table (nullable - logs persist if document deleted)
    document_id = Column(
        String(36),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Model used for generation
    model = Column(String(128), nullable=False, index=True)

    # Timing information
    time_summary = Column(Float, nullable=False)  # Time in seconds

    # Hostname of the machine where operation was performed
    hostname = Column(String(256), nullable=True, index=True)

    # Timestamp when operation completed
    insertion_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )

    # Additional metadata (JSON) - can store token counts, prompt info, etc.
    meta = Column(JSONB, nullable=True, default=dict)  # JSONB for PostgreSQL, JSON for SQLite

    # Relationships
    document = relationship("Document", backref="summary_logs")

    # Indexes
    __table_args__ = (
        Index("idx_logs_summary_document_id", "document_id"),
        Index("idx_logs_summary_insertion_time", "insertion_time"),
        Index("idx_logs_summary_model", "model"),
        Index("idx_logs_summary_hostname", "hostname"),
    )

    def __repr__(self) -> str:
        return (
            f"<SummaryLog(id={self.id}, document_id={self.document_id}, "
            f"model={self.model}, time={self.time_summary}s, "
            f"hostname={self.hostname})>"
        )


class Chunk(Base):
    """Table storing text chunks from documents.
    
    Attributes:
        id (str): Primary key (UUID stored as string).
        document_id (str): Foreign key to the documents table.
        text (str): Text content of the chunk. 
        chunk_index (int): Position in document (0-based) per chunk strategy.
        char_start_index (int): Character position where chunk starts. Optional.
        char_end_index (int): Character position where chunk ends. Optional.
        token_length (int): Number of tokens in chunk.
        section_path (str): Section path for hierarchical context (e.g., "Chapter 1 > Section 1.2"). Can be used in embedding context. Optional.
        chunk_strategy (str): Foreign key to the chunk_strategies table.
        meta (dict): Metadata - flexible JSON field for additional metadata.
        created_time (datetime): Timestamp when the chunk was created.
    """
    __tablename__ = "chunks"

    # Primary key - UUID stored as string
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )

    # Foreign key to document table
    document_id = Column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Chunk text content
    text = Column(Text, nullable=False)

    # Position information
    chunk_index = Column(Integer, nullable=False, index=True)  # Position in document (0-based)
    char_start_index = Column(Integer, nullable=True, index=True)  # Character position where chunk starts
    char_end_index = Column(Integer, nullable=True)  # Character position where chunk ends

    # Token information
    token_length = Column(Integer, nullable=True)  # Number of tokens in chunk

    # Section path for hierarchical context (e.g., "Chapter 1 > Section 1.2")
    section_path = Column(Text, nullable=True)

    # Chunk strategy - foreign key to chunk_strategies table
    chunk_strategy = Column(
        String(128),
        ForeignKey("chunk_strategies.strategy", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Additional metadata (JSON)
    meta = Column(JSONB, nullable=True, default=dict)  # JSONB for PostgreSQL, JSON for SQLite

    # Timestamp
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )

    # Relationships
    document = relationship("Document", backref="chunks")
    strategy_config = relationship("ChunkStrategy", backref="chunks")

    # Indexes
    __table_args__ = (
        Index("idx_chunks_document_id_index", "document_id", "chunk_index"),
        Index("idx_chunks_document_id_start", "document_id", "char_start_index"),
    )

    def __repr__(self) -> str:
        return (
            f"<Chunk(id={self.id}, document_id={self.document_id}, "
            f"chunk_index={self.chunk_index}, token_length={self.token_length})>"
        )

    @classmethod
    def from_dict(cls, data: dict) -> "Chunk":
        """Create a Chunk instance from a dictionary.

        Args:
            data: Dictionary with chunk fields. Required fields:
                - document_id, text, chunk_index

        Returns:
            Chunk instance (not yet saved to database)
        """
        if "document_id" not in data:
            raise ValueError("document_id is required")
        if "text" not in data:
            raise ValueError("text is required")
        if "chunk_index" not in data:
            raise ValueError("chunk_index is required")

        return cls(
            id=data.get("id"),  # Allow explicit ID, otherwise will be generated
            document_id=data["document_id"],
            text=data["text"],
            chunk_index=data["chunk_index"],
            char_start_index=data.get("char_start_index"),
            char_end_index=data.get("char_end_index"),
            token_length=data.get("token_length"),
            section_path=data.get("section_path"),
            chunk_strategy=data.get("chunk_strategy"),
            meta=data.get("meta", {}),
        )

    def to_dict(self) -> dict:
        """Convert Chunk instance to dictionary.

        Returns:
            Dictionary representation of the chunk
        """
        # Get meta from strategy_config relationship if available
        meta = {}
        if self.strategy_config and self.strategy_config.meta:
            meta = self.strategy_config.meta

        return {
            "id": self.id,
            "document_id": self.document_id,
            "text": self.text,
            "chunk_index": self.chunk_index,
            "char_start_index": self.char_start_index,
            "char_end_index": self.char_end_index,
            "token_length": self.token_length,
            "section_path": self.section_path,
            "chunk_strategy": self.chunk_strategy,
            "meta": meta,
            "created_time": self.created_time.isoformat() if self.created_time else None,
        }

    def embed(
        self,
        embedding_name: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        session=None,
        **kwargs,
    ) -> List[float]:
        """Embed this chunk and store it in the database.

        This is a convenience method that calls embed_chunk() from the embedding module.
        Similar to doc.chunk() but for embeddings.
        Accepts either embedding_name OR (provider, model). If neither is provided, uses env vars.

        Args:
            embedding_name: Optional short name for the embedding config (e.g., "openai-small")
                           This should match the short_name in EmbeddingConfig and corresponds
                           to the embeddings_XXX table name.
                           If None, uses provider/model or env vars.
            provider: Optional provider name (used if embedding_name is not provided)
            model: Optional model name (used if embedding_name is not provided)
            session: Optional database session. If None, creates a new session.
            **kwargs: Additional parameters passed to embedder

        Returns:
            The embedding vector as a list of floats

        Example:
            ```python
            chunk = get_chunks(document_id="abc-123")[0]
            # By embedding name
            embedding = chunk.embed(embedding_name="openai-small")
            # Or by provider/model
            embedding = chunk.embed(provider="openai", model="text-embedding-3-small")
            # Or use defaults from env vars
            embedding = chunk.embed()
            ```
        """
        from .embedding import embed_chunk

# Use object's session if attached, otherwise None
        from sqlalchemy.inspection import inspect as sqlalchemy_inspect
        obj_state = sqlalchemy_inspect(self)
        if session is None:
            session = obj_state.session if obj_state.session is not None else None

        return embed_chunk(
            self,
            embedding_name=embedding_name,
            provider=provider,
            model=model,
            session=session,
            **kwargs
        )

    def get_embeddings(
        self,
        embedding_name: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Get embedding(s) for this chunk with metadata.

        This is a convenience method that calls get_embeddings() from the embedding module.

        Args:
            embedding_name: Optional short name for the embedding config (e.g., "openai-small")
                           If provided, returns dict with only that embedding.
                           If None, returns dict with all embeddings for this chunk.

        Returns:
            Dict mapping embedding_name to dict with:
            - id: str - The embedding record ID
            - embedding: List[float] - The embedding vector
            Empty dict if no embeddings found

        Example:
            ```python
            chunk = get_chunks(document_id="abc-123")[0]
            # Get specific embedding
            result = chunk.get_embeddings(embedding_name="openai-small")
            # Returns: {"openai-small": {"id": "...", "embedding": [0.1, 0.2, ...]}}
            # Get all embeddings
            all_embeddings = chunk.get_embeddings()
            # Returns: {"openai-small": {"id": "...", "embedding": [...]}, "st-minilm": {...}}
            ```
        """
        if not self.id:
            return {}

        from .embedding import get_embeddings

# get_embeddings creates its own session, so we just call it directly
        return get_embeddings(self.id, embedding_name=embedding_name)

    def get_embedding_vector(
        self,
        embedding_name: str,
    ) -> Optional[List[float]]:
        """Get a single embedding vector for this chunk.

        This is a convenience method that calls get_embedding_vector() from the embedding module.

        Args:
            embedding_name: Short name for the embedding config (e.g., "openai-small").
                           Required.

        Returns:
            The embedding vector (List[float]) or None if not found

        Example:
            ```python
            chunk = get_chunks(document_id="abc-123")[0]
            embedding = chunk.get_embedding_vector(embedding_name="openai-small")
            # Returns: [0.1, 0.2, ...] or None
            ```
        """
        if not self.id:
            return None

        from .embedding import get_embedding_vector

# get_embedding_vector creates its own session, so we just call it directly
        return get_embedding_vector(self.id, embedding_name=embedding_name)


