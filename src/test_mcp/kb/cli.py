#!/usr/bin/env python3
"""CLI tool for knowledge base operations."""

import argparse
import sys
from pathlib import Path

from . import add, add_source, get, get_stats, list_sources
from .core import Source
from .database import get_db_session
from ..parser import parse


def cmd_add(args):
    """Add a document from a file."""
    file_path = Path(args.file)
    
    if not file_path.exists():
        print(f"Error: File not found: {file_path}")
        sys.exit(1)

    # Determine source_id if not provided
    source_id = args.source_id
    if not source_id:
        # Default to file extension or "local"
        source_id = "local"
        print(f"Warning: No source_id provided, using '{source_id}'")
        print("  Use --source-id to specify a source")

    # Check if source exists, create if needed
    with get_db_session() as session:
        source = session.query(Source).filter(Source.id == source_id).first()
        if not source:
            print(f"Source '{source_id}' does not exist. Creating it...")
            add_source(source_id=source_id, name=f"Local files ({source_id})")

    print(f"Processing file: {file_path}")

    # Parse document (MIME type will be auto-detected)
    # Note: parse() returns List[dict] - we use the first (main) document
    try:
        doc_dicts = parse(file_path)
        if not doc_dicts:
            raise ValueError("No documents extracted from file")
        
        main_doc = doc_dicts[0]
        text_content = main_doc.get('text', '')
        mime_type = main_doc.get('source_type', '')
        
        print(f"  Detected MIME type: {mime_type}")
        if not text_content:
            print(f"  Warning: No text extracted from {file_path}")
        
        if len(doc_dicts) > 1:
            print(f"  Note: {len(doc_dicts) - 1} image document(s) also extracted")
    except NotImplementedError as e:
        print(f"Error: Unsupported file type: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error parsing document: {e}")
        print("  Adding document with empty text content")
        text_content = ""
        # Try to detect MIME type anyway for source_type
        try:
            from ..parser import detect_mime_type
            mime_type = detect_mime_type(file_path)
        except Exception:
            mime_type = "application/octet-stream"

    # Determine doc_id
    doc_id = args.doc_id
    if not doc_id:
        # Use filename without extension as doc_id
        doc_id = file_path.stem

    # Prepare document data
    doc_data = {
        "source_id": source_id,
        "doc_id": doc_id,
        "uri": f"file://{file_path.absolute()}",
        "source_type": mime_type,
        "text": text_content,
        "meta": {
            "filename": file_path.name,
            "filepath": str(file_path.absolute()),
            "filesize": file_path.stat().st_size,
        },
    }

    # Add document
    try:
        doc = add(doc_data)
        print(f"✓ Added document: {doc.id}")
        print(f"  Source: {doc.source_id}")
        print(f"  Doc ID: {doc.doc_id}")
        print(f"  Type: {doc.source_type}")
        if doc.text:
            print(f"  Text length: {len(doc.text)} characters")
    except Exception as e:
        print(f"Error adding document: {e}")
        sys.exit(1)


def cmd_get(args):
    """Get a document."""
    try:
        result = get(
            identifier=args.identifier,
            uuid=args.uuid,
            source_id=args.source_id,
            doc_id=args.doc_id,
        )

        if result is None:
            print("No document found")
            sys.exit(1)
        elif isinstance(result, list):
            print(f"Found {len(result)} document(s):")
            for doc in result:
                print(f"  - {doc.id} | {doc.source_id}/{doc.doc_id} | {doc.source_type}")
        else:
            doc = result
            print(f"Document: {doc.id}")
            print(f"  Source: {doc.source_id}")
            print(f"  Doc ID: {doc.doc_id}")
            print(f"  URI: {doc.uri}")
            print(f"  Type: {doc.source_type}")
            if doc.text:
                print(f"  Text: {len(doc.text)} characters")
                if args.show_text:
                    print(f"\n  Content:\n  {doc.text[:500]}...")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_stats(args):
    """Show knowledge base statistics."""
    import json
    
    stats = get_stats()
    
    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        print("Knowledge Base Statistics")
        print("=" * 40)
        print(f"Total documents: {stats['total_documents']}")
        print(f"Total sources: {stats['total_sources']}")
        print()
        if stats['documents_by_source']:
            print("Documents by source:")
            for item in stats['documents_by_source']:
                print(f"  {item['source_id']}: {item['count']}")


def cmd_source_list(args):
    """List all sources."""
    import json
    
    sources = list_sources()
    
    if args.json:
        print(json.dumps(sources, indent=2))
    else:
        if not sources:
            print("No sources found")
            return
        
        print("Sources")
        print("=" * 40)
        for source in sources:
            print(f"ID: {source['id']}")
            if source['name']:
                print(f"  Name: {source['name']}")
            if source['description']:
                print(f"  Description: {source['description']}")
            if source['base_uri']:
                print(f"  Base URI: {source['base_uri']}")
            if source['created_at']:
                print(f"  Created: {source['created_at']}")
            print()


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(description="Knowledge base CLI")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Add command
    add_parser = subparsers.add_parser("add", help="Add a document from a file")
    add_parser.add_argument("file", help="Path to document file")
    add_parser.add_argument("--source-id", help="Source identifier (default: 'local')")
    add_parser.add_argument("--doc-id", help="Document ID (default: filename without extension)")

    # Get command
    get_parser = subparsers.add_parser("get", help="Get a document")
    get_parser.add_argument("identifier", nargs="?", help="Document identifier (UUID or parsed format)")
    get_parser.add_argument("--uuid", help="Document UUID")
    get_parser.add_argument("--source-id", help="Source identifier")
    get_parser.add_argument("--doc-id", help="Document ID")
    get_parser.add_argument("--show-text", action="store_true", help="Show document text content")

    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show knowledge base statistics")
    stats_parser.add_argument(
        "--json",
        action="store_true",
        help="Output statistics as JSON"
    )

    # Source command
    source_parser = subparsers.add_parser("source", help="Manage sources")
    source_subparsers = source_parser.add_subparsers(dest="source_command", help="Source commands")
    
    source_add_parser = source_subparsers.add_parser("add", help="Add or update a source")
    source_add_parser.add_argument("source_id", help="Source identifier")
    source_add_parser.add_argument("--name", help="Source name")
    source_add_parser.add_argument("--description", help="Source description")
    source_add_parser.add_argument("--base-uri", help="Base URI for the source")
    
    source_list_parser = source_subparsers.add_parser("list", help="List all sources")
    source_list_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "add":
        cmd_add(args)
    elif args.command == "get":
        cmd_get(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "source":
        if args.source_command == "add":
            try:
                source = add_source(
                    source_id=args.source_id,
                    name=args.name,
                    description=args.description,
                    base_uri=args.base_uri,
                )
                print(f"✓ Added/updated source: {source.id}")
                if source.name:
                    print(f"  Name: {source.name}")
            except Exception as e:
                print(f"Error: {e}")
                sys.exit(1)
        elif args.source_command == "list":
            cmd_source_list(args)
        else:
            source_parser.print_help()
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

