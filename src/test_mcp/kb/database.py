"""Database setup and session management."""

import logging
import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .core import Base

# Import embedding models to ensure they're registered with Base.metadata
# This ensures database tables are created when init_db() is called
try:
    from .embedding.core import Chunk, EmbeddingConfig  # noqa: F401
except ImportError:
    # Embedding module may not be available if dependencies aren't installed
    pass

# Import search models to ensure they're registered with Base.metadata
try:
    from .search.core import SearchLog  # noqa: F401
except ImportError:
    # Search module may not be available if dependencies aren't installed
    pass

# Import eval models to ensure they're registered with Base.metadata
try:
    from .eval.core import (  # noqa: F401
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
    - PostgreSQL: DATABASE_URL or individual components
    - SQLite: SQLITE_DB_PATH (defaults to 'data/kb.db' for dev)

    Returns:
        Database URL string
    """
    # Check for explicit DATABASE_URL first
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    # Check for PostgreSQL components
    db_host = os.getenv("DB_HOST")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "test_mcp")
    db_user = os.getenv("DB_USER")
    db_password = os.getenv("DB_PASSWORD")

    if db_host and db_user and db_password:
        return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

    # Default to SQLite for development
    sqlite_path = os.getenv("SQLITE_DB_PATH", "data/kb.db")
    logger.info(f"Using SQLite database: {sqlite_path}")
    return f"sqlite:///{sqlite_path}"


def create_engine_with_config() -> Engine:
    """Create SQLAlchemy engine with appropriate configuration."""
    database_url = get_database_url()

    # SQLite-specific configuration
    if database_url.startswith("sqlite"):
        # Enable foreign keys for SQLite
        engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            echo=os.getenv("DB_ECHO", "false").lower() == "true",
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
        database_url,
        echo=os.getenv("DB_ECHO", "false").lower() == "true",
        pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
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
def get_db_session() -> Generator[Session, None, None]:
    """Get a database session (context manager).

    Usage:
        with get_db_session() as session:
            # use session
    """
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(create_tables: bool = True) -> None:
    """Initialize the database.

    Args:
        create_tables: If True, create all tables. If False, only verify connection.
    """
    # Ensure eval models are imported before creating tables
    # This ensures they're registered with Base.metadata
    try:
        from .eval.core import (  # noqa: F401
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

    if create_tables:
        # Create all tables (including SearchLog from search module and eval models)
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created/verified")

    # Test connection
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        result.fetchone()
        logger.info("Database connection successful")

