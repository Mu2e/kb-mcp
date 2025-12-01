#!/usr/bin/env python3
"""CLI tool for knowledge base operations."""

import argparse
import sys
from pathlib import Path

from . import add, add_from_path, add_source, deduplicate, get, get_stats, list_sources
from .utils import find_all_duplicates
from .core import Document, Source
from .database import get_db_session
from ..parser import parse


def _interactive_dedup_choice(existing_by_id, existing_by_hash, new_doc):
    """Interactive prompt for handling duplicates."""
    print("\n    Duplicates found:")
    
    if existing_by_id:
        print(f"  • Existing document with same source_id+doc_id:")
        print(f"    ID: {existing_by_id.id}")
        print(f"    Source: {existing_by_id.source_id}/{existing_by_id.doc_id}")
        print(f"    Inserted: {existing_by_id.insert_time}")
    
    if existing_by_hash and (not existing_by_id or existing_by_hash.id != existing_by_id.id):
        print(f"  • Existing document with same content_hash:")
        print(f"    ID: {existing_by_hash.id}")
        print(f"    Source: {existing_by_hash.source_id}/{existing_by_hash.doc_id}")
        print(f"    Inserted: {existing_by_hash.insert_time}")
    
    print(f"\n  New document:")
    print(f"    Source: {new_doc.source_id}/{new_doc.doc_id}")
    
    print("\nOptions:")
    print("  0 - Insert anyway (create duplicate)")
    print("  1 - Insert with warning (create duplicate)")
    print("  2 - Update existing by hash (if exists)")
    print("  3 - Update existing by hash with warnings")
    print("  4 - Update existing by source_id+doc_id (if exists), else by hash")
    
    while True:
        try:
            choice = input("\nChoose action [0-4] (default: 2): ").strip()
            if not choice:
                return 3
            level = int(choice)
            if 0 <= level <= 4:
                return level
            print("Invalid choice. Please enter 0-4.")
        except ValueError:
            print("Invalid input. Please enter a number 0-4.")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            sys.exit(1)


def cmd_add(args):
    """Add a document from a file."""
    file_path = Path(args.file)
    
    if not file_path.exists():
        print(f"Error: File not found: {file_path}")
        sys.exit(1)

    # Determine source_id if not provided
    source_id = args.source_id
    if not source_id:
        source_id = "local"
        print(f"Warning: No source_id provided, using '{source_id}'")
        print("  Use --source-id to specify a source")

    # Check if source exists, create if needed
    with get_db_session() as session:
        source = session.query(Source).filter(Source.id == source_id).first()
        if not source:
            print(f"Source '{source_id}' does not exist. Creating it...")
            add_source(source_id=source_id, name=f"Local files ({source_id})")

    # Determine doc_id
    doc_id = args.doc_id
    if not doc_id:
        doc_id = file_path.stem

    # Prepare data dict
    data = {"source_id": source_id}
    if doc_id:
        data["doc_id"] = doc_id

    # Determine deduplication level
    dedup_level = args.dedup_level
    
    # If no dedup level specified and not in batch mode, use interactive mode
    interactive = not args.batch
    
    # If no dedup level specified and interactive mode, check for duplicates first
    if dedup_level is None and interactive:
        # Parse the file first to check for duplicates
        try:
            from .base import _find_duplicates
            from .core import Document
            import hashlib
            
            doc_dicts = parse(file_path, data=data)
            if not doc_dicts:
                raise ValueError("No documents extracted from file")
            
            main_doc_dict = doc_dicts[0]
            temp_doc = Document.from_dict(main_doc_dict)
            
            # Compute hash manually
            if temp_doc.text:
                content = temp_doc.text.encode("utf-8")
            elif temp_doc.binary:
                content = temp_doc.binary
            else:
                content = b""
            if content:
                temp_doc.content_hash = hashlib.sha256(content).hexdigest()
            
            with get_db_session() as session:
                existing_by_id, existing_by_hash = _find_duplicates(temp_doc, session)
                
                if existing_by_id or existing_by_hash:
                    dedup_level = _interactive_dedup_choice(
                        existing_by_id, existing_by_hash, temp_doc
                    )
        except Exception as e:
            print(f"Warning: Could not check for duplicates: {e}")
            print("  Proceeding with default deduplication level")

    print(f"Processing file: {file_path}")
    
    # Add document using add_from_path
    try:
        documents = add_from_path(
            file_path,
            data=data,
            parse_image_additional_doc=args.parse_images,
            parse_image_llm_description=args.parse_images_llm,
            dedup_level=dedup_level,
        )
        
        print(f"✓ Added {len(documents)} document(s):")
        for doc in documents:
            print(f"  • {doc.id}")
            print(f"    Source: {doc.source_id}")
            print(f"    Doc ID: {doc.doc_id}")
            print(f"    Type: {doc.source_type}")
            if doc.text:
                print(f"    Text length: {len(doc.text)} characters")
    except Exception as e:
        print(f"Error adding document: {e}")
        import traceback
        traceback.print_exc()
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


def cmd_deduplicate(args):
    """Deduplicate the entire database."""
    print("Scanning database for duplicates...")
    
    duplicates = find_all_duplicates()
    
    if not duplicates:
        print("✓ No duplicates found.")
        return
    
    print(f"\nFound {len(duplicates)} duplicate group(s):")
    
    # Fetch document details for display
    from . import get
    
    total_by_id = sum(len(by_id_dups) for _, by_id_dups, _ in duplicates)
    total_by_hash = sum(len(by_hash_dups) for _, _, by_hash_dups in duplicates)
    
    if total_by_id > 0:
        print(f"\n  {total_by_id} duplicate(s) by source_id+doc_id:")
        count = 0
        for keep_id, by_id_dups, _ in duplicates:
            if by_id_dups:
                keep_doc = get(uuid=keep_id)
                for dup_id in by_id_dups[:10 - count]:  # Show up to 10 total
                    dup_doc = get(uuid=dup_id)
                    if keep_doc and dup_doc:
                        print(f"    • Keep: {keep_id[:8]}... ({keep_doc.source_id}/{keep_doc.doc_id})")
                        print(f"      Duplicate: {dup_id[:8]}... ({dup_doc.source_id}/{dup_doc.doc_id})")
                    count += 1
                    if count >= 10:
                        break
                if count >= 10:
                    break
        if total_by_id > 10:
            print(f"    ... and {total_by_id - 10} more")
    
    if total_by_hash > 0:
        print(f"\n  {total_by_hash} duplicate(s) by content_hash:")
        count = 0
        for keep_id, _, by_hash_dups in duplicates:
            if by_hash_dups:
                keep_doc = get(uuid=keep_id)
                for dup_id in by_hash_dups[:10 - count]:  # Show up to 10 total
                    dup_doc = get(uuid=dup_id)
                    if keep_doc and dup_doc:
                        hash_preview = keep_doc.content_hash[:16] if keep_doc.content_hash else 'N/A'
                        print(f"    • Keep: {keep_id[:8]}... (hash: {hash_preview}...)")
                        print(f"      Duplicate: {dup_id[:8]}...")
                    count += 1
                    if count >= 10:
                        break
                if count >= 10:
                    break
        if total_by_hash > 10:
            print(f"    ... and {total_by_hash - 10} more")
    
    if args.dry_run:
        print("\n[DRY RUN] No changes made. Use --apply to perform deduplication.")
        return
    
    if not args.apply:
        print("\nUse --apply to perform deduplication, or --dry-run to see what would happen.")
        return
    
    # Apply deduplication
    print(f"\nApplying deduplication...")
    print("  (Keeping oldest document in each duplicate group, deleting others)")
    
    result = deduplicate()
    
    print(f"✓ Deduplication complete:")
    print(f"  Deleted: {result['deleted']} duplicate document(s)")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(description="Knowledge base CLI")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Add command
    add_parser = subparsers.add_parser("add", help="Add a document from a file")
    add_parser.add_argument("file", help="Path to document file")
    add_parser.add_argument("--source-id", help="Source identifier (default: 'local')")
    add_parser.add_argument("--doc-id", help="Document ID (default: filename without extension)")
    add_parser.add_argument(
        "--dedup-level",
        type=int,
        choices=[0, 1, 2, 3, 4],
        help="Deduplication level: 0=insert, 1=warn, 2=overwrite hash, 3=overwrite hash+warn (default), 4=overwrite all"
    )
    add_parser.add_argument(
        "--batch",
        action="store_true",
        help="Batch mode: disable interactive prompts, use default deduplication level"
    )
    add_parser.add_argument(
        "--parse-images",
        action="store_true",
        help="Extract images as separate documents"
    )
    add_parser.add_argument(
        "--parse-images-llm",
        action="store_true",
        help="Generate LLM descriptions for images (requires OPENAI_API_KEY)"
    )

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

    # DB Admin command
    db_admin_parser = subparsers.add_parser("db-admin", help="Database administration commands")
    db_admin_subparsers = db_admin_parser.add_subparsers(dest="db_command", help="DB admin commands")
    
    dedup_parser = db_admin_subparsers.add_parser("deduplicate", help="Find and remove duplicate documents")
    dedup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show duplicates without making changes"
    )
    dedup_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply deduplication (required to make changes)"
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
    elif args.command == "db-admin":
        if args.db_command == "deduplicate":
            cmd_deduplicate(args)
        else:
            db_admin_parser.print_help()
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

