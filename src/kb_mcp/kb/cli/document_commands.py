"""Document management commands (add, get)."""

import os
import sys
from pathlib import Path

from .. import add_source, get
from ..tools import ingest
from ..db_models import Document, Source
from ..database import get_db_session


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


def cmd_ingest(args):
    """Ingest a document from a file."""
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
            add_source(source_id=source_id, name=f"Local files ({source_id})", session=session)

    # Determine doc_id
    doc_id = args.doc_id
    if not doc_id:
        doc_id = file_path.stem

    print(f"Processing file: {file_path}")

    # Determine image LLM description setting
    # If --parse-images is set, enable LLM descriptions by default unless --no-parse-images-llm is set
    describe_images = args.parse_images and not args.no_parse_images_llm

    # Prepare chunk_config if chunk_size or chunk_overlap are provided
    chunk_config = None
    if args.chunk_size or args.chunk_overlap:
        chunk_config = {}
        if args.chunk_size:
            chunk_config["chunk_size"] = args.chunk_size
        if args.chunk_overlap:
            chunk_config["chunk_overlap"] = args.chunk_overlap

    # Use ingest() function for the full workflow
    try:
        result = ingest(
            file_path,
            source_id=source_id,
            doc_id=doc_id,
            extract_images=args.parse_images,
            describe_images=describe_images,
            dedup_level=args.dedup_level,
            force_reparse=args.force_reparse,
            copy_to_kb=not args.no_copy,
            uri=None,
            meta=None,
            generate_summary=not args.no_summary,
            chunk_and_embed=not args.no_embed,
            create_summary_chunks=not args.no_summary_chunks and not args.no_summary,
            chunk_strategy=args.strategy,
            chunk_config=chunk_config,
            embedding_name=args.embedding_name,
            embedding_provider=args.provider,
            embedding_model=args.model,
        )

        # Display results
        if result.get('skipped', False):
            print(f"\n  File already processed, skipping ingestion.")
            print(f"  Use --force-reparse to re-process the file.")
            if result.get('raw_document_id'):
                print(f"  RawDocument ID: {result['raw_document_id']}")
        else:
            # Show copied path if file was copied
            if result.get('copied', False) and result.get('copied_path'):
                print(f"\n  Copied file to: {result['copied_path']}")
            
            print(f"\n  Ingested {result['num_documents']} document(s):")
            if result['document_ids']:
                # Get documents for display
                from .. import get as get_doc
                documents = get_doc(uid=result['document_ids'])
                if not isinstance(documents, list):
                    documents = [documents] if documents else []
                
                for doc in documents:
                    print(f"  • {doc.id}")
                    print(f"    Source: {doc.source_id}")
                    print(f"    Doc ID: {doc.doc_id}")
                    print(f"    Type: {doc.source_type}")
                    if doc.uri:
                        print(f"    URI: {doc.uri}")
                    if doc.text:
                        print(f"    Text length: {len(doc.text)} characters")

            # Show summary of processing
            if result.get('num_summaries', 0) > 0:
                print(f"\n  Generated {result['num_summaries']} summary(ies)")
            if result.get('num_chunks', 0) > 0:
                print(f"  Created {result['num_chunks']} chunk(s)")
                if result.get('num_text_chunks', 0) > 0:
                    print(f"    - {result['num_text_chunks']} text chunk(s)")
                if result.get('num_summary_chunks', 0) > 0:
                    print(f"    - {result['num_summary_chunks']} summary chunk(s)")
                if result.get('num_image_chunks', 0) > 0:
                    print(f"    - {result['num_image_chunks']} image chunk(s)")

    except Exception as e:
        print(f"Error ingesting document: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_get(args):
    """Get a document."""
    try:
        result = get(
            identifier=args.identifier,
            uid=args.uuid,
            source_id=args.source_id,
            doc_id=args.doc_id,
            limit=args.limit,
            offset=args.offset,
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


def setup_commands(subparsers):
    """Set up document management commands."""
    # Ingest command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest a document from a file")
    ingest_parser.add_argument("file", help="Path to document file")
    ingest_parser.add_argument("--source-id", help="Source identifier (default: 'local')")
    ingest_parser.add_argument("--doc-id", help="Document ID (default: filename without extension)")
    ingest_parser.add_argument(
        "--dedup-level",
        type=int,
        choices=[0, 1, 2, 3, 4],
        help="Deduplication level: 0=insert, 1=warn, 2=overwrite hash, 3=overwrite hash+warn (default), 4=overwrite all"
    )
    ingest_parser.add_argument(
        "--batch",
        action="store_true",
        help="Batch mode: disable interactive prompts, use default deduplication level"
    )
    ingest_parser.add_argument(
        "--parse-images",
        action="store_true",
        help="Extract images as separate documents (implies --parse-images-llm unless --no-parse-images-llm is set)"
    )
    ingest_parser.add_argument(
        "--no-parse-images-llm",
        action="store_true",
        help="Don't generate LLM descriptions for images (only applies if --parse-images is set)"
    )
    ingest_parser.add_argument(
        "--no-copy",
        action="store_true",
        help="Don't copy file to DATA_DIR/sources/{source_id}/ (default: files are copied)"
    )
    ingest_parser.add_argument(
        "--force-reparse",
        action="store_true",
        help="Re-parse file even if it was already processed (by content hash)"
    )
    ingest_parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Skip automatic summary generation (title, gist, summary)"
    )
    ingest_parser.add_argument(
        "--no-embed",
        action="store_true",
        help="Skip automatic chunking and embedding after ingesting document"
    )
    ingest_parser.add_argument(
        "--no-summary-chunks",
        action="store_true",
        help="Skip creating summary chunks (only applies if summary is generated)"
    )
    ingest_parser.add_argument(
        "--no-image-chunks",
        action="store_true",
        help="Skip creating image chunks for image documents"
    )
    ingest_parser.add_argument(
        "--strategy",
        help="Chunking strategy for text documents (default: from CHUNK_STRATEGY env var or 'tokens')"
    )
    ingest_parser.add_argument(
        "--chunk-size",
        type=int,
        help="Chunk size in tokens (default: 1000)"
    )
    ingest_parser.add_argument(
        "--chunk-overlap",
        type=int,
        help="Chunk overlap in tokens (default: 200)"
    )
    ingest_parser.add_argument(
        "--embedding-name",
        help="Embedding name (e.g., 'openai-small')"
    )
    ingest_parser.add_argument(
        "--provider",
        help="Embedding provider (e.g., 'openai')"
    )
    ingest_parser.add_argument(
        "--model",
        help="Model name (e.g., 'text-embedding-3-small')"
    )
    ingest_parser.add_argument(
        "--batch-size",
        type=int,
        help="Batch size for embedding generation"
    )
    ingest_parser.set_defaults(func=cmd_ingest)

    # Get command
    get_parser = subparsers.add_parser("get", help="Get a document")
    get_parser.add_argument("identifier", nargs="?", help="Document identifier (UUID or parsed format)")
    get_parser.add_argument("--uuid", help="Document UUID")
    get_parser.add_argument("--source-id", help="Source identifier")
    get_parser.add_argument("--doc-id", help="Document ID")
    get_parser.add_argument("--limit", type=int, help="Maximum number of documents to return")
    get_parser.add_argument("--offset", type=int, help="Number of documents to skip (for pagination)")
    get_parser.add_argument("--show-text", action="store_true", help="Show document text content")
    get_parser.set_defaults(func=cmd_get)
