import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from kb_mcp.kb.db_models import Base

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
