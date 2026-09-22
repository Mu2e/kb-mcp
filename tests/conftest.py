import importlib.util
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from kb_mcp.kb.db_models import Base

# Some chunking tests need the embedding model's real token window, which is
# only knowable with a loadable sentence-transformers model: without one the
# code falls back to a 256-token window and produces a different (valid but
# different) chunking. CI installs no torch on purpose -- the whole point of
# the serve-only core -- so those tests skip there and run wherever the
# [local-embed] extra is present.
requires_embedder = pytest.mark.skipif(
    importlib.util.find_spec("sentence_transformers") is None,
    reason="needs sentence-transformers (install the kb-mcp[local-embed] extra)",
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
