import pytest
from sqlalchemy import text
from kb_mcp.kb.db_models import Source, Document

def test_db_ping(db_session):
    """Verify that we can perform a simple query."""
    result = db_session.execute(text("SELECT 1")).scalar()
    assert result == 1

def test_source_lifecycle(db_session):
    """Test full CRUD lifecycle for a Source."""
    # Create
    source = Source(id="test-lifecycle", name="Lifecycle Test")
    db_session.add(source)
    db_session.commit()
    
    # Read
    retrieved = db_session.query(Source).filter_by(id="test-lifecycle").first()
    assert retrieved is not None
    assert retrieved.name == "Lifecycle Test"
    
    # Update
    retrieved.name = "Updated Name"
    db_session.commit()
    updated = db_session.query(Source).filter_by(id="test-lifecycle").first()
    assert updated.name == "Updated Name"
    
    # Delete (manual check)
    db_session.delete(updated)
    db_session.commit()
    assert db_session.query(Source).filter_by(id="test-lifecycle").first() is None
