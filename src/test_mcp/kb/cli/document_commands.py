"""Document management commands (add, get)."""

import os
import shutil
import sys
from pathlib import Path

from ...parser import parse
from .. import add, add_from_path, add_source, get
from ..db_models import Document, Source
from ..database import get_db_session

# Import embedding functions (may not be available if dependencies not installed)
try:
    from ..embedding import chunk_and_embed
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False


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

    # Handle file copying if --copy is specified
    uri = None
    if args.copy:
        # Get DATA_DIR and create local directory
        from ...config import get_data_dir
        data_dir = get_data_dir()
        local_dir = Path(data_dir) / "local"
        local_dir.mkdir(parents=True, exist_ok=True)

        # Copy file to local directory
        dest_file = local_dir / file_path.name
        # Handle duplicates by adding a number
        counter = 1
        original_dest = dest_file
        while dest_file.exists():
            stem = original_dest.stem
            suffix = original_dest.suffix
            dest_file = local_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        shutil.copy2(file_path, dest_file)
        print(f"  Copied file to: {dest_file}")

        # Set URI to local://local/filename
        uri = f"local://local/{dest_file.name}"

    # Prepare data dict
    data = {"source_id": source_id}
    if doc_id:
        data["doc_id"] = doc_id
    if uri:
        data["uri"] = uri

    # Determine deduplication level
    dedup_level = args.dedup_level

    # If no dedup level specified and not in batch mode, use interactive mode
    interactive = not args.batch

    # If no dedup level specified and interactive mode, check for duplicates first
    if dedup_level is None and interactive:
        # Parse the file first to check for duplicates
        try:
            from ..documents import _find_duplicates
            from ..db_models import Document
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

    # Determine image LLM description setting
    # If --parse-images is set, enable LLM descriptions by default unless --no-parse-images-llm is set
    parse_image_llm_description = args.parse_images and not args.no_parse_images_llm

    # Add document using add_from_path
    # Use a session to ensure parsing log is created in the same transaction
    try:
        with get_db_session() as session:
            documents = add_from_path(
                file_path,
                data=data,
                parse_image_additional_doc=args.parse_images,
                parse_image_llm_description=parse_image_llm_description,
                dedup_level=dedup_level,
                session=session,
            )
            session.commit()

            # Refresh all documents to ensure attributes are loaded before session closes
            for doc in documents:
                session.refresh(doc)

            # Expunge documents so they can be used after session closes
            for doc in documents:
                session.expunge(doc)

        print(f"  Added {len(documents)} document(s):")
        for doc in documents:
            print(f"  • {doc.id}")
            print(f"    Source: {doc.source_id}")
            print(f"    Doc ID: {doc.doc_id}")
            print(f"    Type: {doc.source_type}")
            if doc.uri:
                print(f"    URI: {doc.uri}")
            if doc.text:
                print(f"    Text length: {len(doc.text)} characters")

        # Full processing workflow (generate summary → chunk and embed)
        if args.no_embed and args.no_summary:
            print("\n(Skipping summary generation and chunking/embedding)")
        else:
            try:
                # Step 1: Generate summaries for text documents (not images)
                text_documents = [doc for doc in documents if doc.doc_type != "image"]
                image_documents = [doc for doc in documents if doc.doc_type == "image"]

                if not args.no_summary and text_documents:
                    print("\n=== Generating Summaries ===")
                    for doc in text_documents:
                        try:
                            print(f"Generating summary for document {doc.id}...")
                            doc.generate_summary(
                                include_title=True,
                                include_gist=True,
                                include_summary=True,
                            )
                            print(f"    Generated summary")
                            if doc.title_gen:
                                print(f"    Title: {doc.title_gen[:80]}...")
                            if doc.gist:
                                print(f"    Gist: {doc.gist[:100]}...")
                        except Exception as e:
                            print(f"  Warning: Could not generate summary: {e}")

                # Step 2: Chunk and embed documents
                if args.no_embed:
                    print("\n(Skipping chunking and embedding)")
                elif not EMBEDDING_AVAILABLE:
                    print("\n(Skipping chunking and embedding - embedding module not available)")
                else:
                    # Process text documents with regular strategy
                    if text_documents:
                        print("\n=== Chunking and Embedding Text Documents ===")
                        for doc in text_documents:
                            try:
                                print(f"Chunking and embedding document {doc.id} (default strategy)...")
                                chunks = chunk_and_embed(
                                    doc,
                                    chunk_strategy=args.strategy,
                                    chunk_config={
                                        "chunk_size": args.chunk_size,
                                        "chunk_overlap": args.chunk_overlap,
                                    } if args.chunk_size or args.chunk_overlap else None,
                                    embedding_name=args.embedding_name,
                                    provider=args.provider,
                                    model=args.model,
                                    batch_size=args.batch_size,
                                )
                                print(f"    Chunked and embedded {len(chunks)} chunk(s)")

                                # Also create summary chunks if summary was generated
                                if not args.no_summary and doc.summary and not args.no_summary_chunks:
                                    print(f"Creating summary chunk for document {doc.id}...")
                                    summary_chunks = chunk_and_embed(
                                        doc,
                                        chunk_strategy="summary",
                                        embedding_name=args.embedding_name,
                                        provider=args.provider,
                                        model=args.model,
                                        batch_size=args.batch_size,
                                    )
                                    print(f"    Created and embedded summary chunk")
                            except Exception as e:
                                print(f"  Warning: Could not chunk and embed document: {e}")

                    # Process image documents with image strategy
                    if image_documents and not args.no_image_chunks:
                        print("\n=== Chunking and Embedding Image Documents ===")
                        for doc in image_documents:
                            try:
                                print(f"Chunking and embedding image document {doc.id}...")
                                chunks = chunk_and_embed(
                                    doc,
                                    chunk_strategy="image",
                                    embedding_name=args.embedding_name,
                                    provider=args.provider,
                                    model=args.model,
                                    batch_size=args.batch_size,
                                )
                                print(f"    Chunked and embedded image (1 chunk)")
                            except Exception as e:
                                print(f"  Warning: Could not chunk and embed image: {e}")

            except Exception as e:
                print(f"Warning: Error during processing: {e}")
                import traceback
                traceback.print_exc()
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
        help="Extract images as separate documents (implies --parse-images-llm unless --no-parse-images-llm is set)"
    )
    add_parser.add_argument(
        "--no-parse-images-llm",
        action="store_true",
        help="Don't generate LLM descriptions for images (only applies if --parse-images is set)"
    )
    add_parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy file to DATA_DIR/local and set URI to local://local/FILENAME"
    )
    add_parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Skip automatic summary generation (title, gist, summary)"
    )
    add_parser.add_argument(
        "--no-embed",
        action="store_true",
        help="Skip automatic chunking and embedding after adding document"
    )
    add_parser.add_argument(
        "--no-summary-chunks",
        action="store_true",
        help="Skip creating summary chunks (only applies if summary is generated)"
    )
    add_parser.add_argument(
        "--no-image-chunks",
        action="store_true",
        help="Skip creating image chunks for image documents"
    )
    add_parser.add_argument(
        "--strategy",
        help="Chunking strategy for text documents (default: from CHUNK_STRATEGY env var or 'tokens')"
    )
    add_parser.add_argument(
        "--chunk-size",
        type=int,
        help="Chunk size in tokens (default: 1000)"
    )
    add_parser.add_argument(
        "--chunk-overlap",
        type=int,
        help="Chunk overlap in tokens (default: 200)"
    )
    add_parser.add_argument(
        "--embedding-name",
        help="Embedding name (e.g., 'openai-small')"
    )
    add_parser.add_argument(
        "--provider",
        help="Embedding provider (e.g., 'openai')"
    )
    add_parser.add_argument(
        "--model",
        help="Model name (e.g., 'text-embedding-3-small')"
    )
    add_parser.add_argument(
        "--batch-size",
        type=int,
        help="Batch size for embedding generation"
    )
    add_parser.set_defaults(func=cmd_add)

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
