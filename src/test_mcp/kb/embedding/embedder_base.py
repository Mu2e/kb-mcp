"""Base embedder class for embedding generation."""

import logging
import uuid
from abc import ABC, abstractmethod
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .core import Chunk

logger = logging.getLogger(__name__)


class BaseEmbedder(ABC):
    """
    Base class for embedding generators.
    
    Subclasses must:
    - Set self.provider (str) - e.g., "openai", "sentence-transformers"
    - Set self.model (str) - e.g., "text-embedding-3-small"
    - Implement embedding_dimension property
    - Implement generate_embeddings() method
    """

    # Subclasses should set these attributes
    provider: str
    model: str

    @property
    @abstractmethod
    def embedding_dimension(self) -> int:
        """Get embedding dimension."""
        pass

    @property
    @abstractmethod
    def max_tokens(self) -> int:
        """Get maximum input tokens for the model."""
        pass

    @abstractmethod
    def generate_embeddings(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        **kwargs,
    ) -> List[List[float]]:
        """
        Generate embeddings for text chunks.

        Args:
            texts: List of text chunks to embed
            batch_size: Optional batch size
            **kwargs: Additional parameters

        Returns:
            List of embedding vectors (each is a list of floats)
        """
        pass

    def embed_chunks(
        self,
        chunks: List["Chunk"],
        short_name: Optional[str] = None,
        batch_size: Optional[int] = None,
        session=None,
        **kwargs,
    ) -> List[List[float]]:
        """
        Generate embeddings for chunks and store them in the database.
        
        Args:
            chunks: List of Chunk objects to embed
            short_name: Optional short name for EmbeddingConfig (if None, generates from provider-model)
            batch_size: Optional batch size for embedding generation
            session: Optional database session. If None, creates a new session.
            **kwargs: Additional parameters passed to generate_embeddings
            
        Returns:
            List of embedding vectors (each is a list of floats)
            
        Raises:
            ValueError: If number of embeddings doesn't match number of chunks
        """
        from ..database import get_db_session
        from .core import EmbeddingConfig, create_embedding_table
        
        if not chunks:
            return []
        
        # Validate chunks have IDs (must be saved to DB first)
        chunks_without_id = [c for c in chunks if not c.id]
        if chunks_without_id:
            raise ValueError(
                f"{len(chunks_without_id)} chunk(s) do not have an ID. "
                "Chunks must be saved to the database before embedding. "
                "Use session.add(chunk) and session.commit() first."
            )
        
        # Extract texts from chunks
        texts = [chunk.text for chunk in chunks]
        
        # Generate embeddings using the abstract method
        try:
            embeddings = self.generate_embeddings(texts, batch_size=batch_size, **kwargs)
        except Exception as e:
            logger.error(f"Error generating embeddings: {e}")
            raise
        
        # Validate that we got the right number of embeddings
        if len(embeddings) != len(chunks):
            raise ValueError(
                f"Number of embeddings ({len(embeddings)}) doesn't match "
                f"number of chunks ({len(chunks)})"
            )
        
        # Get or create EmbeddingConfig
        if short_name is None:
            # Generate default short name from provider-model
            short_name = self._generate_short_name()
        
        # Use provided session or create a new one
        own_session = session is None
        if own_session:
            session = get_db_session().__enter__()
        
        try:
            # Ensure base tables exist (embedding_configs, chunks)
            from ..core import Base
            Base.metadata.create_all(bind=session.bind, checkfirst=True)
            
            # Get or create EmbeddingConfig (each config corresponds to a table)
            config = session.query(EmbeddingConfig).filter(
                EmbeddingConfig.short_name == short_name
            ).first()
            
            if not config:
                # Create new EmbeddingConfig
                config = EmbeddingConfig(
                    short_name=short_name,
                    provider=self.provider,
                    model=self.model,
                    dimension=self.embedding_dimension,
                )
                session.add(config)
                session.flush()
                logger.info(f"Created embedding config: {short_name} ({self.provider}/{self.model}, dim={self.embedding_dimension})")
            # If config exists, use it as-is (don't update)
            
            # Get or create embedding table (each EmbeddingConfig corresponds to a table)
            embedding_table = create_embedding_table(config.short_name, config.dimension)
            
            # Ensure table exists in database (create if it doesn't exist)
            # checkfirst=True efficiently checks existence before creating
            embedding_table.create(bind=session.bind, checkfirst=True)
            
            # Get dialect name for later use
            dialect_name = session.bind.dialect.name
            
            # For existing SQLite tables, ensure the unique index exists
            # SQLite requires a unique index (not just constraint) for ON CONFLICT to work
            if dialect_name == 'sqlite':
                from sqlalchemy import inspect, text
                inspector = inspect(session.bind)
                indexes = inspector.get_indexes(embedding_table.name)
                index_names = [idx['name'] for idx in indexes if idx.get('unique') and 'chunk_id' in idx.get('column_names', [])]
                if not index_names:
                    # Unique index doesn't exist, add it
                    constraint_name = f"uq_{embedding_table.name}_chunk_id"
                    try:
                        session.execute(text(f'CREATE UNIQUE INDEX IF NOT EXISTS {constraint_name} ON "{embedding_table.name}" (chunk_id)'))
                        session.flush()
                    except Exception as e:
                        logger.warning(f"Could not create unique index on {embedding_table.name}: {e}")
            
            # Insert or update embeddings (upsert)
            from sqlalchemy import insert, func
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert
            
            embedding_rows = []
            for chunk, embedding in zip(chunks, embeddings):
                embedding_rows.append({
                    "id": str(uuid.uuid4()),
                    "chunk_id": chunk.id,
                    "embedding": embedding,
                    # created_time has a default, so we don't need to set it
                })
            
            if embedding_rows:
                # Use upsert: insert or update on conflict with chunk_id
                # Create base insert statement based on dialect
                if dialect_name == 'postgresql':
                    stmt = pg_insert(embedding_table).values(embedding_rows)
                elif dialect_name == 'sqlite':
                    stmt = sqlite_insert(embedding_table).values(embedding_rows)
                else:
                    # Fall back to regular insert (will fail on duplicate due to unique constraint)
                    stmt = insert(embedding_table).values(embedding_rows)
                
                # Apply upsert logic for dialects that support it
                if dialect_name == 'postgresql':
                    stmt = stmt.on_conflict_do_update(
                        index_elements=['chunk_id'],
                        set_={
                            'embedding': stmt.excluded.embedding,
                            'created_time': func.now(),
                        }
                    )
                elif dialect_name == 'sqlite':
                    # For SQLite, use index_elements (works with unique index)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=['chunk_id'],
                        set_={
                            'embedding': stmt.excluded.embedding,
                            'created_time': func.now(),
                        }
                    )
                
                session.execute(stmt)
                if own_session:
                    session.commit()
                else:
                    session.flush()
            
            logger.info(f"Stored {len(embedding_rows)} embeddings in {config.get_table_name()}")
            
            return embeddings
        except Exception as e:
            if own_session:
                session.rollback()
            logger.error(f"Error storing embeddings: {e}")
            raise
        finally:
            if own_session:
                session.close()

    def _generate_short_name(self) -> str:
        """Generate default short name from provider and model."""
        # Default implementation - subclasses can override
        return f"{self.provider}-{self.model.split('-')[-1]}"

    def get_table_name(self, short_name: Optional[str] = None) -> str:
        """
        Get table name for embeddings.
        
        Args:
            short_name: Optional short name (if None, generates from provider-model)
        
        Returns:
            Table name (e.g., "embeddings_openai-small")
        """
        from .core import sanitize_table_name
        
        if short_name is None:
            short_name = self._generate_short_name()
        
        sanitized = sanitize_table_name(short_name)
        return f"embeddings_{sanitized}"

    def __call__(self, texts: List[str], batch_size: Optional[int] = None, **kwargs) -> List[List[float]]:
        """Make embedder callable: embedder(texts)."""
        return self.generate_embeddings(texts, batch_size=batch_size, **kwargs)

