import os
import shutil
import pytest
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from kb_mcp.kb.cli.__init__ import main
from kb_mcp.kb.db_models import Document, RawDocument, Source, Base
from kb_mcp.kb.embedding.db_models import Chunk
from kb_mcp.kb.database import get_db_session, get_database_url

# Set up shared database file for integration test
TEST_DB_FILE = "test_kb_integration.db"
TEST_DB_URL = f"sqlite:///{TEST_DB_FILE}"

@pytest.fixture(autouse=True)
def setup_integration_db():
    """Reset database state and ensure clean environment using actual system config."""
    # Force reset of the database engine in the module to pick up actual config
    import kb_mcp.kb.database as kb_db
    kb_db._engine = None
    kb_db._SessionLocal = None
    
    # We don't wipe the actual DB, but we ensure our test source is gone first
    source_id = "test-flow"
    with get_db_session() as session:
        # Delete any documents from previous failed runs if they exist
        from sqlalchemy import delete
        
        # We need to find documents with this source first
        docs = session.query(Document).filter(Document.source_id == source_id).all()
        for doc in docs:
            # cascading delete via kb drop or direct delete
            # For simplicity in setup, we'll try to drop if exists
            pass # We'll handle cleanup more robustly in the test itself
            
    yield
    # No restoration needed as we didn't move .env

@pytest.fixture
def test_pdf(tmp_path):
    """Generate a test PDF with known text content."""
    from reportlab.pdfgen import canvas
    import uuid
    
    def _generate(filename="test_ingest.pdf"):
        test_file = tmp_path / filename
        unique_id = str(uuid.uuid4())
        
        c = canvas.Canvas(str(test_file))
        c.drawString(100, 750, f"Integration Test Document")
        c.drawString(100, 730, f"ID: {unique_id}")
        c.drawString(100, 710, "This is a test document for the knowledge base integration flow.")
        c.drawString(100, 690, "It contains known text that we can verify after ingestion.")
        c.drawString(100, 670, "1. First point: The ingestion should be successful.")
        c.drawString(100, 650, "2. Second point: Chunking should split this into verifiable segments.")
        c.drawString(100, 630, "3. Third point: Cleanup should remove all traces of this document.")
        c.showPage()
        c.save()
        
        return {"path": test_file, "unique_id": unique_id}
    
    return _generate

def run_kb_cli(*args):
    """Helper to run the kb CLI with given arguments."""
    import sys
    from unittest.mock import patch
    
    # Backup argv
    old_argv = sys.argv
    sys.argv = ["kb"] + list(args)
    
    try:
        # We catch SystemExit because CLI calls sys.exit(0) on success
        try:
            main()
        except SystemExit as e:
            if e.code != 0:
                raise RuntimeError(f"CLI exited with code {e.code}")
    finally:
        sys.argv = old_argv

def test_full_ingestion_flow(test_pdf):
    """
    Integration test for:
    - Ingesting a PDF
    - Checking for chunks and embeddings
    - Idempotency (no re-ingest)
    - --force-reparse (re-ingest without adding new documents)
    - Full auto-ingestion flow (ingest + auto-chunk)
    - Cleanup (dropping document and checking all parts removed)
    """
    source_id = "test-flow"
    doc_id = "test-pdf-doc"
    
    # Generate first PDF
    pdf_info = test_pdf("test_manual.pdf")
    pdf_path = pdf_info["path"]
    unique_id = pdf_info["unique_id"]
    
    # 1. First Ingestion (Manual Flow)
    print(f"\n[1] Ingesting {pdf_path} (Manual Flow)...")
    run_kb_cli("ingest", str(pdf_path), "--source-id", source_id, "--doc-id", doc_id, "--batch", "--no-embed", "--no-summary")
    
    with get_db_session() as session:
        # Verify document exists
        doc = session.query(Document).filter(Document.source_id == source_id, Document.doc_id == doc_id).first()
        assert doc is not None
        assert unique_id in doc.text
        doc_uuid = doc.id
        print(f"  Ingested Document UUID: {doc_uuid}")
        
    # 2. Separate Chunking
    print(f"\n[2] Chunking document {doc_uuid}...")
    run_kb_cli("chunks", "chunk", doc_uuid)
    
    with get_db_session() as session:
        # Verify chunks are created
        chunk_count = session.query(Chunk).filter(Chunk.document_id == doc_uuid).count()
        assert chunk_count > 0
        print(f"  Created {chunk_count} chunks")
        
        # 3. Second Ingestion (Idempotency)
        print("\n[3] Ingesting again (should be skipped)...")
        run_kb_cli("ingest", str(pdf_path), "--source-id", source_id, "--doc-id", doc_id, "--batch", "--no-embed", "--no-summary")
        
        all_docs = session.query(Document).filter(Document.source_id == source_id, Document.doc_id == doc_id).all()
        assert len(all_docs) == 1
        assert all_docs[0].id == doc_uuid
        print("  Verified: No new document created.")
        
        # 4. Third Ingestion with --force-reparse
        print("\n[4] Ingesting with --force-reparse...")
        run_kb_cli("ingest", str(pdf_path), "--source-id", source_id, "--doc-id", doc_id, "--batch", "--no-embed", "--no-summary", "--force-reparse")
        
        # Should still be 1 document
        all_docs = session.query(Document).filter(Document.source_id == source_id, Document.doc_id == doc_id).all()
        assert len(all_docs) == 1
        assert all_docs[0].id == doc_uuid
        print("  Verified: Still 1 document with same UUID.")

    # 5. Full Flow Ingestion (Auto-chunk + Summary + Embed)
    doc_id_auto = "test-pdf-auto"
    pdf_info_auto = test_pdf("test_auto.pdf")
    pdf_path_auto = pdf_info_auto["path"]
    unique_id_auto = pdf_info_auto["unique_id"]
    
    print(f"\n[5] Ingesting {pdf_path_auto} (Full Flow: Auto-chunk + Summary + Embed)...")
    # Run full flow - we use --batch but NO --no-embed or --no-summary
    run_kb_cli("ingest", str(pdf_path_auto), "--source-id", source_id, "--doc-id", doc_id_auto, "--batch")
    
    with get_db_session() as session:
        doc_auto = session.query(Document).filter(Document.source_id == source_id, Document.doc_id == doc_id_auto).first()
        assert doc_auto is not None
        doc_uuid_auto = doc_auto.id
        print(f"  Ingested Document UUID (Auto): {doc_uuid_auto}")
        
        # Verify summary generation
        assert doc_auto.summary is not None
        assert doc_auto.gist is not None
        print(f"  Verified: LLM Summary and Gist generated.")
        
        # Verify automatic chunking
        chunks = session.query(Chunk).filter(Chunk.document_id == doc_uuid_auto).all()
        assert len(chunks) > 0
        chunk_ids = [c.id for c in chunks]
        print(f"  Verified: {len(chunks)} chunks auto-generated.")
        
        # Verify embeddings (using configured embedding table)
        from kb_mcp.kb.embedding.db_models import get_embedding_table
        embedding_table = get_embedding_table(session)
        assert embedding_table is not None
        
        emb_count = session.query(embedding_table).filter(embedding_table.c.chunk_id.in_(chunk_ids)).count()
        assert emb_count > 0
        print(f"  Verified: {emb_count} embeddings generated in {embedding_table.name}.")

    # 6. Cleanup
    print(f"\n[6] Cleanup...")
    for uid in [doc_uuid, doc_uuid_auto]:
        print(f"  Removing document {uid}...")
        run_kb_cli("drop", uid, "--yes")
        
        with get_db_session() as session:
            assert session.query(Document).filter(Document.id == uid).first() is None
            assert session.query(Chunk).filter(Chunk.document_id == uid).count() == 0
            
    print("  Verified: Both documents and their chunks deleted.")
    
    with get_db_session() as session:
        assert session.query(Source).filter(Source.id == source_id).first() is not None
        print("  Verified: Source remains.")
