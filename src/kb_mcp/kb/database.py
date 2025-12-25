"""Database setup and session management."""

import logging
import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .db_models import Base

logger = logging.getLogger(__name__)


def get_database_url() -> str:
    """Get database URL from environment variables.

    Supports:
    - PostgreSQL: DB_URL or individual components
    - SQLite: SQLITE_DB_PATH (defaults to 'data/kb.db' for dev)

    Returns:
        Database URL string
    """
    from ..config import get_database_config

    # Get configuration from config module
    db_config = get_database_config()


    # Check for PostgreSQL components
    if db_config['user'] and db_config['name']:
        if db_config['password']:
            return f"postgresql://{db_config['user']}:{db_config['password']}@{db_config['host']}:{db_config['port']}/{db_config['name']}"
        else:
            return f"postgresql://{db_config['user']}@{db_config['host']}:{db_config['port']}/{db_config['name']}"


    # Default to SQLite for development
    sqlite_path = db_config['sqlite_path']
    logger.info(f"Using SQLite database: {sqlite_path}")
    return f"sqlite:///{sqlite_path}"


def create_engine_with_config() -> Engine:
    """Create SQLAlchemy engine with appropriate configuration."""
    from ..config import get_database_config

    database_url = get_database_url()

    # SQLite-specific configuration
    if database_url.startswith("sqlite"):
        # Enable foreign keys for SQLite
        engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False}
        )

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            """Enable foreign key constraints in SQLite."""
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine

    # PostgreSQL configuration
    db_config = get_database_config() # for schema setting
    engine = create_engine(database_url)
    
    @event.listens_for(engine, "connect")
    def set_search_path(dbapi_conn, connection_record):
        """Set schema search_path for PostgreSQL."""
        cursor = dbapi_conn.cursor()
        cursor.execute(f"SET search_path TO {db_config['schema']}")
        cursor.close()
    
    return engine


# Create engine and session factory
_engine = None
_SessionLocal = None


def get_engine() -> Engine:
    """Get or create the database engine."""
    global _engine
    if _engine is None:
        _engine = create_engine_with_config()
    return _engine


def get_session_factory() -> sessionmaker:
    """Get or create the session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=get_engine(),
        )
    return _SessionLocal


@contextmanager
def get_db_session(session=None, auto_commit: bool = True, auto_expunge: bool = True) -> Generator[Session, None, None]:
    """
    Get or create a database session (context manager).

    This function handles session management automatically:
    - Creates a new session if none provided
    - Auto-commits on success (for new sessions only)
    - Auto-refreshes all objects before expunging (for new sessions, if auto_expunge=True)
    - Auto-expunges all objects (for new sessions, if auto_expunge=True, so they can be used after session closes)
    - Handles rollback on errors
    - Closes session when done

    Args:
        session: Existing session to use, or None to create new one
        auto_commit: If True, commit on success (only applies to new sessions)
        auto_expunge: If True, refresh and expunge all objects (only applies to new sessions).
                     Set to False for better performance when objects aren't needed after session closes.

    Yields:
        Database session with .is_local attribute indicating if it was created here

    Usage:
        ```python
        # Create new session (auto-commits, auto-expunges):
        with get_db_session() as session:
            doc = session.query(Document).first()
            return doc  # Works! Objects are refreshed and expunged automatically

        # Use existing session (no commit, no expunge):
        with get_db_session(existing_session) as session:
            doc = session.query(Document).first()
            return doc  # Stays attached to existing_session

        # Ephemeral session (no expunge, faster for read-only queries):
        with get_db_session_ephemeral() as session:
            docs = session.query(Document).all()
            # Objects are not available after session closes, but query is faster
        ```
    """
    is_local = session is None

    if is_local:
        SessionLocal = get_session_factory()
        session = SessionLocal()

    # Mark whether this is a local session
    session.is_local = is_local

    try:
        yield session

        # Auto-refresh and expunge if local session and auto_expunge is enabled
        if is_local and auto_expunge:
            from sqlalchemy.orm import make_transient
            
            # Refresh all objects to load their data before expunging
            # This ensures objects can be used after the session closes
            for obj in list(session.identity_map.values()):
                try:
                    # Refresh to ensure we have the latest data and load all attributes
                    session.refresh(obj)
                    # Make transient: this fully detaches the object from the session
                    # refresh() should have loaded all attributes into __dict__, so they'll
                    # be available after make_transient()
                    make_transient(obj)
                except Exception:
                    # Skip invalid/deleted objects
                    pass

            if auto_commit:
                session.commit()

            # Expunge all objects (make_transient already detached them, but this cleans up)
            session.expunge_all()
        elif is_local and auto_commit:
            # Still commit even if not expunging
            session.commit()

    except Exception:
        if is_local:
            session.rollback()
        raise
    finally:
        if is_local:
            session.close()


@contextmanager
def get_db_session_ephemeral(session=None, auto_commit: bool = True) -> Generator[Session, None, None]:
    """
    Get an ephemeral database session that doesn't refresh/expunge objects.

    This is faster than get_db_session() when you don't need to use objects
    after the session closes. Use this for read-only queries where you only
    need to extract data (e.g., building HTML, generating reports).

    Args:
        session: Existing session to use, or None to create new one
        auto_commit: If True, commit on success (only applies to new sessions)

    Yields:
        Database session

    Usage:
        ```python
        # Fast read-only query (objects not available after session closes):
        with get_db_session_ephemeral() as session:
            docs = session.query(Document).all()
            for doc in docs:
                print(doc.title)  # OK: access during session
            # doc objects are NOT available after this block
        ```
    """
    with get_db_session(session=session, auto_commit=auto_commit, auto_expunge=False) as s:
        yield s


def _setup_fulltext_search_trigger(engine: Engine) -> None:
    """Set up PostgreSQL trigger for auto-updating text_search_vector on chunks.

    This creates a trigger function and trigger that automatically updates the
    text_search_vector column whenever a chunk is inserted or updated.

    The text_search_vector combines:
    - document.title or document.title_gen (weight 'A' - highest)
    - chunk.text (weight 'B')
    - document.summary (weight 'D' - lower)

    Args:
        engine: SQLAlchemy engine connected to PostgreSQL
    """
    # Check if function exists
    check_function_sql = text("""
        SELECT EXISTS (
            SELECT 1 FROM pg_proc
            WHERE proname = 'update_chunk_text_search_vector'
        );
    """)

    # Check if trigger exists
    check_trigger_sql = text("""
        SELECT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'chunks_text_search_vector_update'
        );
    """)

    with engine.connect() as conn:
        function_exists = conn.execute(check_function_sql).scalar()
        trigger_exists = conn.execute(check_trigger_sql).scalar()

        # Only create function if it doesn't exist
        if not function_exists:
            create_function_sql = text("""
                CREATE FUNCTION update_chunk_text_search_vector()
                RETURNS TRIGGER AS $$
                DECLARE
                    doc_title TEXT;
                    doc_summary TEXT;
                BEGIN
                    -- Get document title (prefer title, fallback to title_gen) and summary
                    SELECT
                        COALESCE(title, title_gen, ''),
                        COALESCE(summary, '')
                    INTO doc_title, doc_summary
                    FROM documents
                    WHERE id = NEW.document_id;

                    -- Build the weighted tsvector
                    NEW.text_search_vector :=
                        setweight(to_tsvector('english', doc_title), 'A') ||
                        setweight(to_tsvector('english', COALESCE(NEW.text, '')), 'B') ||
                        setweight(to_tsvector('english', doc_summary), 'D');

                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
            """)
            conn.execute(create_function_sql)
            logger.info("Created full-text search trigger function")

        # Only create trigger if it doesn't exist
        if not trigger_exists:
            create_trigger_sql = text("""
                CREATE TRIGGER chunks_text_search_vector_update
                BEFORE INSERT OR UPDATE ON chunks
                FOR EACH ROW
                EXECUTE FUNCTION update_chunk_text_search_vector();
            """)
            conn.execute(create_trigger_sql)
            logger.info("Created full-text search trigger")

        conn.commit()


def init_db(create_tables: bool = True) -> None:
    """Initialize the database.

    Args:
        create_tables: If True, create all tables. If False, only verify connection.
    """
    # Ensure all models are imported before creating tables
    # This ensures they're registered with Base.metadata
    from .embedding.db_models import Chunk, EmbeddingConfig  # noqa: F401
    from .search.db_models import SearchLog  # noqa: F401
    from .eval.db_models import (  # noqa: F401
        EvalGeneration,
        EvalDataset,
        EvalAudit,
        EvalRun,
        EvalResult,
        EvalRetrievedDocument,
    )

    engine = get_engine()
    database_url = get_database_url()

    logger.info(f"Initializing database: {database_url.split('@')[-1] if '@' in database_url else database_url}")

    # Enable pgvector extension for PostgreSQL (required for vector columns)
    if database_url.startswith('postgresql'):
        try:
            with engine.connect() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                conn.commit()
            logger.info("PostgreSQL vector extension enabled")
        except Exception as e:
            logger.warning(f"Could not enable vector extension (may not have permissions): {e}")

        # Set up full-text search trigger
        try:
            _setup_fulltext_search_trigger(engine)
            logger.info("Full-text search trigger created/verified")
        except Exception as e:
            logger.warning(f"Could not create full-text search trigger: {e}")

    if create_tables:
        # Create all tables (including SearchLog from search module and eval models)
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created/verified")

    # Test connection
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        result.fetchone()
        logger.info("Database connection successful")

