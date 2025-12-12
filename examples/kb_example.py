#!/usr/bin/env python3
"""Example usage of the knowledge base."""

import os
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from kb_mcp.kb import add, add_source, get


def main():
    """Example usage of knowledge base."""
    # Create sources first (database auto-initializes on first use)
    print("1. Creating sources...")
    add_source(
        source_id="mu2e-docdb",
        name="Mu2e DocDB",
        description="Mu2e Document Database",
        base_uri="https://mu2e-docdb.fnal.gov/docdb",
        meta={"type": "docdb", "project": "mu2e"},
    )
    add_source(
        source_id="mu2e-wiki",
        name="Mu2e Wiki",
        description="Mu2e Wiki Pages",
        base_uri="https://mu2e.fnal.gov/wiki",
        meta={"type": "wiki"},
    )

    # Example 1: Add document from dict with doc_id (recommended)
    print("\n2. Adding document from dict with doc_id...")
    doc1 = add({
        "source_id": "mu2e-docdb",
        "doc_id": "1234-doc1",  # Human-readable identifier
        "uri": "https://mu2e-docdb.fnal.gov/docdb/1234",
        "source_type": "application/pdf",
        "text": "This is a test document about Mu2e experiment. It contains important information about detector design.",
        "meta": {
            "author": "John Doe",
            "title": "Mu2e Detector Design",
            "category": "detector",
        },
        "creating_time": datetime(2024, 1, 15, 10, 30, 0),
    })
    print(f"   Added document: {doc1.id} (doc_id: {doc1.doc_id})")

    # Example 2: Add document using Document.from_dict()
    print("\n3. Adding document using Document.from_dict()...")
    from kb_mcp.kb.core import Document
    doc_obj = Document.from_dict({
        "source_id": "mu2e-wiki",
        "uri": "https://mu2e.fnal.gov/wiki/DetectorOverview",
        "source_type": "text/html",
        "text": "Overview of the Mu2e detector system.",
        "meta": {"page_title": "Detector Overview"},
    })
    doc2 = add(doc_obj)
    print(f"   Added document: {doc2.id}")

    # Example 3: Add another document from dict
    print("\n4. Adding another document from dict...")
    doc3 = add({
        "source_id": "mu2e-docdb",
        "uri": "https://mu2e-docdb.fnal.gov/docdb/5678",
        "source_type": "application/pdf",
        "text": "Another test document.",
    })
    print(f"   Added document: {doc3.id}")

    # Example 4: Add document without URI (optional)
    print("\n5. Adding document without URI...")
    doc4 = add({
        "source_id": "mu2e-docdb",
        "source_type": "text/plain",
        "text": "Document without URI.",
    })
    print(f"   Added document: {doc4.id}")

    # Example 5: Get document by parsed identifier (split on "_")
    print("\n6. Getting document by parsed identifier...")
    doc = get("mu2e-docdb_1234-doc1")  # Parses to source_id="mu2e-docdb", doc_id="1234-doc1"
    if doc:
        print(f"   Found: {doc.id}")
        print(f"   Source: {doc.source_id}, Doc ID: {doc.doc_id}")
        print(f"   URI: {doc.uri}")
        print(f"   Type: {doc.source_type}")
        print(f"   Text preview: {doc.text[:50] if doc.text else 'N/A'}...")

    # Example 6: Get document by UUID (auto-detected)
    print("\n7. Getting document by UUID...")
    doc = get(doc1.id)
    if doc:
        print(f"   Found: {doc.source_id}-{doc.doc_id}")

    # Example 6b: Get document by explicit UUID parameter
    print("\n7b. Getting document by explicit UUID...")
    doc = get(uuid=doc1.id)
    if doc:
        print(f"   Found: {doc.source_id}-{doc.doc_id}")

    # Example 6c: Get document by doc_id only
    print("\n7c. Getting document by doc_id...")
    doc = get("1234-doc1")  # Parsed as doc_id
    if doc:
        print(f"   Found: {doc.id}")

    # Example 6d: Get documents by explicit parameters
    print("\n7d. Getting documents by explicit parameters...")
    docs = get(source_id="mu2e-docdb", doc_id="1234-doc1")
    if docs:
        print(f"   Found: {docs.id if isinstance(docs, type(doc1)) else len(docs)} document(s)")

    # Example 7: Get another document
    print("\n8. Getting another document...")
    doc = get(doc2.id)
    if doc:
        print(f"   Found: {doc.uri}")

    print("\nDone!")


if __name__ == "__main__":
    main()
