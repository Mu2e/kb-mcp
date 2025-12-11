"""Tools and utility commands (stats, logs, drop_table, chunk_and_embed_all, deduplicate)."""

import json
import sys

from .. import get, get_stats, deduplicate, delete_document
from ..utils import find_all_duplicates
from ..database import get_db_session

# Import embedding functions (may not be available if dependencies not installed)
try:
    from ..embedding import chunk_and_embed
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False


def cmd_stats(args):
    """Show knowledge base statistics."""
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


def cmd_logs_chunking(args):
    """Show chunk_and_embed operation logs for a document."""
    from ..logs import get_chunking_logs

    logs = get_chunking_logs(args.document_id, limit=args.limit)

    if not logs:
        print(f"No chunk_and_embed logs found for document {args.document_id}")
        return

    if args.json:
        print(json.dumps(logs, indent=2))
    else:
        print(f"Chunk_and_Embed Logs for Document {args.document_id} ({len(logs)} log(s)):")
        print("=" * 80)

        for i, log in enumerate(logs, 1):
            print(f"\n  Log #{i}:")
            print(f"    ID: {log['id']}")
            print(f"    Insertion Time: {log['insertion_time']}")
            print(f"    Hostname: {log['hostname'] or 'N/A'}")
            print(f"    Timing:")
            print(f"      Chunking: {log['chunking_time_seconds']:.3f}s")
            print(f"      Embedding: {log['embedding_time_seconds']:.3f}s")
            print(f"      Total: {log['total_time_seconds']:.3f}s")
            print(f"    Counts:")
            print(f"      Chunks: {log['num_chunks']}")
            print(f"      Embeddings: {log['num_embeddings']}")
            print(f"    Configuration:")
            print(f"      Strategy: {log['chunk_strategy'] or 'N/A'}")
            print(f"      Embedding: {log['embedding_name'] or 'N/A'}")


def cmd_logs_parsing(args):
    """Show text extraction/parsing operation logs for a document."""
    from ..logs import get_parsing_logs

    logs = get_parsing_logs(args.document_id, limit=args.limit)

    if not logs:
        print(f"No parsing logs found for document {args.document_id}")
        return

    if args.json:
        print(json.dumps(logs, indent=2))
    else:
        print(f"Parsing/Extraction Logs for Document {args.document_id} ({len(logs)} log(s)):")
        print("=" * 80)

        for i, log in enumerate(logs, 1):
            print(f"\n  Log #{i}:")
            print(f"    ID: {log['id']}")
            print(f"    Insertion Time: {log['insertion_time']}")
            print(f"    Hostname: {log['hostname'] or 'N/A'}")
            print(f"    File Path: {log['file_path'] or 'N/A'}")
            print(f"    Source Type: {log['source_type'] or 'N/A'}")
            print(f"    Timing:")
            print(f"      Text Extraction: {log['text_extraction_time_seconds']:.3f}s")
            if log['image_description_time_seconds'] is not None:
                print(f"      Image Description: {log['image_description_time_seconds']:.3f}s")
            print(f"      Total: {log['total_time_seconds']:.3f}s")
            print(f"    Documents Extracted: {log['num_documents']}")
            print(f"    Text Length: {log['text_length'] or 0} characters")


def cmd_chunk_and_embed_all(args):
    """Chunk and embed all documents for a source_id that don't have chunks yet."""
    if not EMBEDDING_AVAILABLE:
        print("Error: Embedding module not available. Install with: pip install -e '.[embedding]'")
        sys.exit(1)

    try:
        from ..tools import chunk_and_embed_all

        print(f"Chunking and embedding all documents for source_id: {args.source_id}")
        if args.strategy:
            print(f"Using chunking strategy: {args.strategy}")

        result = chunk_and_embed_all(
            source_id=args.source_id,
            strategy=args.strategy,
        )

        print(f"\n  Completed:")
        print(f"  Processed: {result['processed']} document(s)")
        print(f"  Chunked: {result['chunked']} document(s)")
        print(f"  Skipped: {result['skipped']} document(s)")
        if result['errors'] > 0:
            print(f"  Errors: {result['errors']} document(s)")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_drop_table(args):
    """Drop a database table by name."""
    table_name = args.table_name

    # Get confirmation unless --yes is specified
    if not args.yes:
        confirmation = input(f"Drop table '{table_name}'? This action cannot be undone. [y/N]: ")
        if confirmation.lower() not in ['y', 'yes']:
            print("Cancelled.")
            return

    try:
        from sqlalchemy import text
        from ..database import get_db_session, get_database_url

        with get_db_session() as session:
            # Check if table exists
            from sqlalchemy import inspect
            inspector = inspect(session.bind)
            existing_tables = inspector.get_table_names()

            if table_name not in existing_tables:
                print(f"Error: Table '{table_name}' does not exist.")
                print(f"Available tables: {', '.join(sorted(existing_tables))}")
                sys.exit(1)

            # Determine database type and use appropriate DROP TABLE syntax
            database_url = get_database_url()
            if database_url.startswith("sqlite"):
                # SQLite doesn't support CASCADE
                drop_sql = f'DROP TABLE IF EXISTS "{table_name}"'
            else:
                # PostgreSQL supports CASCADE
                drop_sql = f'DROP TABLE IF EXISTS "{table_name}" CASCADE'

            # Drop the table
            session.execute(text(drop_sql))
            session.commit()

            print(f" Dropped table '{table_name}'")
    except Exception as e:
        print(f"Error dropping table: {e}")
        sys.exit(1)


def cmd_deduplicate(args):
    """Deduplicate the entire database."""
    print("Scanning database for duplicates...")

    duplicates = find_all_duplicates()

    if not duplicates:
        print(" No duplicates found.")
        return

    print(f"\nFound {len(duplicates)} duplicate group(s):")

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

    print(f" Deduplication complete:")
    print(f"  Deleted: {result['deleted']} duplicate document(s)")


def cmd_drop(args):
    """Delete a document (top-level command)."""
    try:
        # Get confirmation unless --yes is specified
        if not args.yes:
            confirmation = input(f"Delete document {args.document_id} and all its chunks/embeddings? [y/N]: ")
            if confirmation.lower() not in ['y', 'yes']:
                print("Cancelled.")
                return

        result = delete_document(args.document_id)

        print(f" Deleted document {args.document_id}")
        if result.get("chunk_count", 0) > 0:
            print(f"  (Also deleted {result['chunk_count']} chunk(s) and their embeddings)")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error deleting document: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def setup_commands(subparsers):
    """Set up tools and utility commands."""
    # Drop command (top-level, for documents)
    drop_parser = subparsers.add_parser("drop", help="Delete a document (and all its chunks and embeddings)")
    drop_parser.add_argument("document_id", help="Document ID (UUID)")
    drop_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    drop_parser.set_defaults(func=cmd_drop)

    # Logs command with subcommands
    logs_parser = subparsers.add_parser("logs", help="View operation logs")
    logs_subparsers = logs_parser.add_subparsers(dest="logs_command", help="Log types")

    # Search logs subcommand (handled in search_commands.py)
    from .search_commands import cmd_search_logs
    logs_search_parser = logs_subparsers.add_parser("search", help="List recent search logs")
    logs_search_parser.add_argument("--limit", type=int, default=10, help="Maximum number of logs to show (default: 10)")
    logs_search_parser.add_argument("--offset", type=int, default=0, help="Number of logs to skip (default: 0)")
    logs_search_parser.add_argument("--query", help="Filter by query text (partial match)")
    logs_search_parser.add_argument("--embedding-name", help="Filter by embedding name")
    logs_search_parser.add_argument("--json", action="store_true", help="Output as JSON")
    logs_search_parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed information including document IDs")
    logs_search_parser.set_defaults(func=cmd_search_logs)

    # Chunking logs subcommand
    logs_chunking_parser = logs_subparsers.add_parser("chunking", help="Show chunk_and_embed operation logs for a document")
    logs_chunking_parser.add_argument("document_id", help="Document ID to show logs for")
    logs_chunking_parser.add_argument("--json", action="store_true", help="Output as JSON")
    logs_chunking_parser.add_argument("--limit", type=int, help="Maximum number of logs to show (default: all)")
    logs_chunking_parser.set_defaults(func=cmd_logs_chunking)

    # Parsing logs subcommand
    logs_parsing_parser = logs_subparsers.add_parser("parsing", help="Show text extraction/parsing logs for a document")
    logs_parsing_parser.add_argument("document_id", help="Document ID to show logs for")
    logs_parsing_parser.add_argument("--json", action="store_true", help="Output as JSON")
    logs_parsing_parser.add_argument("--limit", type=int, help="Maximum number of logs to show (default: all)")
    logs_parsing_parser.set_defaults(func=cmd_logs_parsing)

    # Tools command (renamed from db-admin)
    tools_parser = subparsers.add_parser("tools", help="Utility tools and functions")
    tools_subparsers = tools_parser.add_subparsers(dest="tools_command", help="Tools commands")

    dedup_parser = tools_subparsers.add_parser("deduplicate", help="Find and remove duplicate documents")
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
    dedup_parser.set_defaults(func=cmd_deduplicate)

    chunk_embed_all_parser = tools_subparsers.add_parser(
        "chunk-and-embed-all",
        help="Chunk and embed all documents for a source_id that don't have chunks yet"
    )
    chunk_embed_all_parser.add_argument("source_id", help="Source identifier to process documents for")
    chunk_embed_all_parser.add_argument(
        "--strategy",
        help="Chunking strategy (e.g., 'tokens' or 'slide'). If not specified, uses default."
    )
    chunk_embed_all_parser.set_defaults(func=cmd_chunk_and_embed_all)

    drop_table_parser = tools_subparsers.add_parser("drop-table", help="Drop a database table by name")
    drop_table_parser.add_argument("table_name", help="Name of the table to drop")
    drop_table_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    drop_table_parser.set_defaults(func=cmd_drop_table)

    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show knowledge base statistics")
    stats_parser.add_argument(
        "--json",
        action="store_true",
        help="Output statistics as JSON"
    )
    stats_parser.set_defaults(func=cmd_stats)
