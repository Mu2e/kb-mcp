"""Database setup and session management."""

import logging
import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .db_models import Base

# Import embedding models to ensure they're registered with Base.metadata
# This ensures database tables are created when init_db() is called
try:
    from .embedding.db_models import Chunk, EmbeddingConfig  # noqa: F401
except ImportError:
    # Embedding module may not be available if dependencies aren't installed
    pass

# Import search models to ensure they're registered with Base.metadata
try:
    from .search.db_models import SearchLog  # noqa: F401
except ImportError:
    # Search module may not be available if dependencies aren't installed
    pass

# Import eval models to ensure they're registered with Base.metadata
try:
    from .eval.db_models import (  # noqa: F401
        EvalGeneration,
        EvalDataset,
        EvalAudit,
        EvalRun,
        EvalResult,
        EvalRetrievedDocument,
    )
except ImportError:
    # Eval module may not be available
    pass

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
    db_config = get_database_config()

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
    return create_engine(
        database_url
    )


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
def get_db_session(session=None, auto_commit: bool = True) -> Generator[Session, None, None]:
    """
    Get or create a database session (context manager).

    This function handles session management automatically:
    - Creates a new session if none provided
    - Auto-commits on success (for new sessions only)
    - Auto-refreshes all objects before expunging (for new sessions)
    - Auto-expunges all objects (for new sessions, so they can be used after session closes)
    - Handles rollback on errors
    - Closes session when done

    Args:
        session: Existing session to use, or None to create new one
        auto_commit: If True, commit on success (only applies to new sessions)

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

        # Auto-refresh and expunge if local session
        if is_local:
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

    except Exception:
        if is_local:
            session.rollback()
        raise
    finally:
        if is_local:
            session.close()


def init_db(create_tables: bool = True) -> None:
    """Initialize the database.

    Args:
        create_tables: If True, create all tables. If False, only verify connection.
    """
    # Ensure eval models are imported before creating tables
    # This ensures they're registered with Base.metadata
    try:
        from .eval.db_models import (  # noqa: F401
            EvalGeneration,
            EvalDataset,
            EvalAudit,
            EvalRun,
            EvalResult,
            EvalRetrievedDocument,
        )
    except ImportError:
        # Eval module may not be available
        pass
    
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

    if create_tables:
        # Create all tables (including SearchLog from search module and eval models)
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created/verified")

    # Test connection
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        result.fetchone()
        logger.info("Database connection successful")

