import importlib.util
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from kb_mcp.kb.db_models import Base

# Some chunking tests need the embedding model's real token window, which is
# only knowable with a loadable sentence-transformers model: without one the
# code falls back to a 256-token window and produces a different (valid but
# different) chunking. sentence-transformers is a core dependency, so these
# normally run; the guard is for environments deliberately built without it
# (e.g. a --no-deps install).
def _embedder_unusable():
    """Why sentence-transformers cannot be used here, or None if it is fine.

    find_spec() is not enough. A transformers version that does not match
    sentence-transformers leaves the package installed but unimportable, so
    the spec is found, the skip does not fire, and these tests run against the
    256-token fallback window -- failing with a chunking difference that looks
    like a regression in the chunker rather than a broken environment.
    """
    try:
        import sentence_transformers  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - any failure makes it unusable
        return f"{type(exc).__name__}: {exc}"
    return None


_EMBEDDER_UNUSABLE = _embedder_unusable()

requires_embedder = pytest.mark.skipif(
    _EMBEDDER_UNUSABLE is not None,
    reason=f"needs a working sentence-transformers ({_EMBEDDER_UNUSABLE})",
)

# Default to SQLite file for easier testing and sharing across sessions
TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "sqlite:///test_kb.db")

@pytest.fixture(scope="session")
def engine():
    """Create a database engine for the test session."""
    try:
        engine = create_engine(TEST_DB_URL)
        # Verify connection
        engine.connect()
        return engine
    except Exception as e:
        pytest.skip(
            f"PostgreSQL test database not available at {TEST_DB_URL}.\n"
            "To run these tests, ensure PostgreSQL is running and set TEST_DATABASE_URL.\n"
            f"Error: {e}"
        )

@pytest.fixture(scope="session")
def tables(engine):
    """Create all tables in the test database."""
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)

@pytest.fixture
def db_session(engine, tables):
    """Provide a transactional database session for a test."""
    connection = engine.connect()
    transaction = connection.begin()
    
    Session = sessionmaker(bind=connection)
    session = Session()
    
    yield session
    
    session.close()
    transaction.rollback()
    connection.close()
