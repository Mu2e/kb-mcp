import pytest
from kb_mcp.parser import parse
from kb_mcp.kb.db_models import Document

def test_parse_simple_text():
    """Test parsing simple text data."""
    data = {
        "source_id": "test-parsing",
        "doc_id": "test-doc-1",
        "binary": b"This is a test document content.",
        "meta": {"filename": "test.txt"}
    }
    
    # parse() returns a list of dictionaries
    results = parse(data=data)
    
    assert len(results) == 1
    doc_dict = results[0]
    assert doc_dict["source_id"] == "test-parsing"
    assert doc_dict["doc_id"] == "test-doc-1"
    assert "test document content" in doc_dict["text"]
    assert doc_dict["source_type"] == "text/plain"

def test_parse_and_insert(db_session):
    """Test parsing a document and inserting it into the DB."""
    data = {
        "source_id": "test-parsing-db",
        "doc_id": "test-doc-db",
        "binary": b"Database insertion test.",
        "meta": {"filename": "test_db.txt"}
    }
    
    # 1. Parse
    results = parse(data=data)
    doc_dict = results[0]
    
    # 2. Convert to Document
    doc = Document.from_dict(doc_dict)
    
    # 3. Insert
    db_session.add(doc)
    db_session.commit()
    
    # 4. Verify
    retrieved = db_session.query(Document).filter_by(doc_id="test-doc-db").first()
    assert retrieved is not None
    assert "Database insertion test" in retrieved.text
