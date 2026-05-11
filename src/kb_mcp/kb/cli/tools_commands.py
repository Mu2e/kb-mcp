"""Tools and utility commands (stats, logs, drop_table, chunk_and_embed_all, deduplicate)."""

import json
import sys

from .. import get, get_stats, deduplicate, delete_document
from ..documents import delete_raw_document, get_raw_document
from ..utils import find_all_duplicates
from ..database import get_db_session
from ..embedding import chunk_and_embed


def cmd_stats(args):
    """Show knowledge base statistics."""
    stats = get_stats()

    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        print("Knowledge Base Statistics")
        print("=" * 40)
        spt = stats.get('documents_by_source_parser_type', [])
        total_text = sum(r['count'] for r in spt if r['doc_type'] == 'text')
        total_image = sum(r['count'] for r in spt if r['doc_type'] == 'image')
        doc_breakdown = f"  ({total_text}t / {total_image}i)" if total_image else ""
        print(f"Total raw documents: {stats['total_raw_documents']}")
        print(f"Total documents:     {stats['total_documents']}{doc_breakdown}")
        print(f"Total sources:       {stats['total_sources']}")
        print()
        if stats['raw_documents_by_source'] or stats['documents_by_source']:
            raw_map = {item['source_id']: item['count'] for item in stats['raw_documents_by_source']}
            sources = sorted(set(
                [item['source_id'] for item in stats['documents_by_source']] +
                [item['source_id'] for item in stats['raw_documents_by_source']]
            ))

            # Build (source_id, parser_id, doc_type) -> count lookup
            spt = stats.get('documents_by_source_parser_type', [])
            spt_map = {(r['source_id'], r['parser_id'], r['doc_type']): r['count'] for r in spt}
            parsers = sorted(set(r['parser_id'] for r in spt))
            doc_types = sorted(set(r['doc_type'] for r in spt))
            show_images = 'image' in doc_types

            # Column widths: 5 per doc_type per parser
            col_w = 5

            # Build header lines
            # Line 1: "Source", "Raw", then parser names spanning their doc_type sub-columns
            # Line 2: sub-column labels (t, i) under each parser
            src_w = max(28, max((len(s) for s in sources), default=0))
            raw_w = 6

            # Each parser occupies col_w chars per doc_type + 1 space between them
            parser_span = col_w * len(doc_types) + (len(doc_types) - 1)

            header1 = f"  {'Source':<{src_w}}  {'Raw':>{raw_w}}"
            header2 = f"  {'':<{src_w}}  {'':{raw_w}}"
            sep_len = src_w + 2 + raw_w + 2

            for p in parsers:
                header1 += f"  {p[:parser_span]:>{parser_span}}"
                if show_images:
                    sub = f"{'t':>{col_w}} {'i':>{col_w}}"
                else:
                    sub = f"{'t':>{col_w}}"
                header2 += f"  {sub}"
                sep_len += 2 + parser_span

            print(header1)
            if show_images:
                print(header2)
            print("-" * sep_len)

            for source_id in sources:
                raw = raw_map.get(source_id, 0)
                row = f"  {source_id:<{src_w}}  {raw:>{raw_w}}"
                for p in parsers:
                    t = spt_map.get((source_id, p, 'text'), 0)
                    if show_images:
                        i = spt_map.get((source_id, p, 'image'), 0)
                        row += f"  {t:>{col_w}} {i:>{col_w}}"
                    else:
                        row += f"  {t:>{col_w}}"
                print(row)


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


def cmd_parse_all(args):
    """Parse all raw documents for a source_id that don't have processed documents yet."""
    try:
        from ..tools import parse_all

        print(f"Parsing all raw documents for source_id: {args.source_id}")
        if args.parser_name:
            print(f"Using parser: {args.parser_name}")
        if args.extract_images:
            print("Extracting images as separate documents")
        if args.describe_images:
            print("Generating LLM descriptions for images")
        if args.force_reparse:
            print("Force re-parsing enabled")

        result = parse_all(
            source_id=args.source_id,
            parser_name=args.parser_name,
            extract_images=args.extract_images,
            describe_images=args.describe_images,
            force_reparse=args.force_reparse,
            batch_size=getattr(args, 'batch_size', None),
        )

        print(f"\n  Completed:")
        print(f"  Total raw documents: {result['total_raw']}")
        print(f"  Parsed: {result['parsed']} document(s)")
        print(f"  Skipped: {result['skipped']} document(s) (file not found)")
        if result['errors'] > 0:
            print(f"  Errors: {result['errors']} document(s)")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_chunk_and_embed_all(args):
    """Chunk and embed all documents for a source_id that don't have chunks yet."""
    try:
        from ..tools import chunk_and_embed_all

        print(f"Chunking and embedding all documents for source_id: {args.source_id}")
        if args.strategy:
            print(f"Using chunking strategy: {args.strategy}")
        if args.no_gist:
            print("Gist prepending disabled (will create _no_gist strategy)")
        if args.no_section_path:
            print("Section path prepending disabled (will create _no_section strategy)")
        if args.embedding_name:
            print(f"Using embedding: {args.embedding_name}")
        elif args.provider or args.model:
            print(f"Using embedding: {args.provider or 'default'}/{args.model or 'default'}")

        # Build chunk_config if any prepending flags are set
        chunk_config = None
        if args.no_gist or args.no_section_path:
            chunk_config = {}
            if args.no_gist:
                chunk_config["prepend_gist"] = False
            if args.no_section_path:
                chunk_config["prepend_section_path"] = False

        result = chunk_and_embed_all(
            source_id=args.source_id,
            chunk_strategy=args.strategy,
            chunk_config=chunk_config,
            include_images=not args.no_images,
            embedding_name=args.embedding_name,
            provider=args.provider,
            model=args.model,
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


def cmd_list_tables(args):
    """List all database tables."""
    try:
        from sqlalchemy import inspect
        from ..database import get_db_session

        with get_db_session() as session:
            inspector = inspect(session.bind)
            tables = sorted(inspector.get_table_names())

            if args.json:
                import json
                print(json.dumps({"tables": tables}, indent=2))
            else:
                print(f"Database Tables ({len(tables)}):")
                print()
                for table in tables:
                    print(f"  {table}")
    except Exception as e:
        print(f"Error listing tables: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_summarize_all(args):
    """Generate summaries for all documents from a source that don't have them yet."""
    try:
        from ..tools import summarize_all

        print(f"Generating summaries for all documents from source_id: {args.source_id}")
        if args.model:
            print(f"Using model: {args.model}")
        if not args.no_summary_chunk:
            print("Will create summary chunks")
        if args.embed_summary_chunk:
            print("Will embed summary chunks")

        result = summarize_all(
            source_id=args.source_id,
            model=args.model,
            create_summary_chunk=not args.no_summary_chunk,
            embed_summary_chunk=args.embed_summary_chunk,
            embedding_name=args.embedding_name,
            embedding_provider=args.provider,
            embedding_model=args.embedding_model,
        )

        print(f"\n  Completed:")
        print(f"  Processed: {result['processed']} document(s)")
        print(f"  Summarized: {result['summarized']} document(s)")
        if not args.no_summary_chunk:
            print(f"  Summary chunks created: {result.get('chunked', 0)}")
        if args.embed_summary_chunk:
            print(f"  Summary chunks embedded: {result.get('embedded', 0)}")
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

            print(f"✓ Dropped table '{table_name}'")
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

    def _doc_label(doc):
        parser = f"/{doc.parser_id}" if doc.parser_id else ""
        return f"{doc.source_id}/{doc.doc_id}{parser}"

    if total_by_id > 0:
        print(f"\n  {total_by_id} duplicate(s) by source_id+doc_id+parser_id:")
        count = 0
        for keep_id, by_id_dups, _ in duplicates:
            if by_id_dups:
                keep_doc = get(uid=keep_id)
                for dup_id in by_id_dups[:10 - count]:
                    dup_doc = get(uid=dup_id)
                    if keep_doc and dup_doc:
                        print(f"    • Keep: {keep_id[:8]}... ({_doc_label(keep_doc)})")
                        print(f"      Duplicate: {dup_id[:8]}... ({_doc_label(dup_doc)})")
                    count += 1
                    if count >= 10:
                        break
                if count >= 10:
                    break
        if total_by_id > 10:
            print(f"    ... and {total_by_id - 10} more")

    if total_by_hash > 0:
        print(f"\n  {total_by_hash} duplicate(s) by content_hash+parser_id:")
        count = 0
        for keep_id, _, by_hash_dups in duplicates:
            if by_hash_dups:
                keep_doc = get(uid=keep_id)
                for dup_id in by_hash_dups[:10 - count]:
                    dup_doc = get(uid=dup_id)
                    if keep_doc and dup_doc:
                        hash_preview = keep_doc.content_hash[:16] if keep_doc.content_hash else 'N/A'
                        print(f"    • Keep: {keep_id[:8]}... ({_doc_label(keep_doc)}, hash: {hash_preview}...)")
                        print(f"      Duplicate: {dup_id[:8]}... ({_doc_label(dup_doc)})")
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

    result = deduplicate(by_hash=True, by_id=True)

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


def cmd_list_raw(args):
    """List raw documents, optionally filtered by source_id."""
    from ..database import get_db_session
    from ..db_models import RawDocument

    with get_db_session() as session:
        query = session.query(RawDocument)
        if args.source_id:
            query = query.filter(RawDocument.source_id == args.source_id)
        query = query.order_by(RawDocument.source_id, RawDocument.doc_id)
        if args.limit:
            query = query.limit(args.limit)
        rows = query.all()

    if not rows:
        print("No raw documents found.")
        return

    print(f"{'UUID':<36}  {'Source':<20}  Doc ID")
    print("-" * 80)
    for r in rows:
        print(f"{r.id:<36}  {(r.source_id or ''):<20}  {r.doc_id or ''}")


def cmd_get_raw(args):
    """Get a raw document by raw document UUID or parsed document UUID."""
    try:
        from ..database import get_db_session
        from ..db_models import RawDocument

        # Try direct raw document UUID lookup first
        raw_doc = None
        with get_db_session() as session:
            raw_doc = session.query(RawDocument).filter(RawDocument.id == args.document_id).first()
            if raw_doc:
                # Detach from session by accessing all fields
                _ = raw_doc.id, raw_doc.source_id, raw_doc.doc_id, raw_doc.file_path
                _ = raw_doc.hostname, raw_doc.uri, raw_doc.source_type
                _ = raw_doc.file_size, raw_doc.content_hash, raw_doc.created_time, raw_doc.updated_time

        # Fall back to lookup via parsed Document UUID
        if not raw_doc:
            raw_doc = get_raw_document(args.document_id)

        if not raw_doc:
            print(f"No raw document found for {args.document_id}")
            print("  (Pass either a raw document UUID or a parsed document UUID)")
            sys.exit(1)
        
        print(f"Raw Document: {raw_doc.id}")
        print(f"  Source ID: {raw_doc.source_id}")
        if raw_doc.doc_id:
            print(f"  Doc ID: {raw_doc.doc_id}")
        if raw_doc.file_path:
            print(f"  File Path: {raw_doc.file_path}")
        if raw_doc.hostname:
            print(f"  Hostname: {raw_doc.hostname}")
        if raw_doc.uri:
            print(f"  URI: {raw_doc.uri}")
        print(f"  Source Type: {raw_doc.source_type}")
        if raw_doc.file_size:
            print(f"  File Size: {raw_doc.file_size} bytes")
        if raw_doc.content_hash:
            print(f"  Content Hash: {raw_doc.content_hash}")
        if raw_doc.created_time:
            print(f"  Created: {raw_doc.created_time}")
        if raw_doc.updated_time:
            print(f"  Updated: {raw_doc.updated_time}")
        if raw_doc.meta:
            print(f"  Meta: {json.dumps(raw_doc.meta, indent=2)}")
            
    except Exception as e:
        print(f"Error getting raw document: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_drop_raw(args):
    """Delete a raw document."""
    try:
        # Get confirmation unless --yes is specified
        if not args.yes:
            message = f"Delete raw document {args.raw_document_id}?"
            if args.delete_linked:
                message += " (This will also delete all linked documents and their chunks/embeddings)"
            message += " [y/N]: "
            confirmation = input(message)
            if confirmation.lower() not in ['y', 'yes']:
                print("Cancelled.")
                return

        result = delete_raw_document(args.raw_document_id, delete_linked_documents=args.delete_linked)

        print(f" Deleted raw document {args.raw_document_id}")
        if args.delete_linked:
            if result.get("deleted_documents", 0) > 0:
                print(f"  (Also deleted {result['deleted_documents']} linked document(s) and their chunks/embeddings)")
            elif result.get("document_count", 0) == 0:
                print(f"  (No linked documents found)")
        else:
            if result.get("document_count", 0) > 0:
                print(f"  (Set raw_document_id to NULL in {result['document_count']} related document(s))")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error deleting raw document: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_db_sessions(args):
    """List PostgreSQL sessions that are idle in transaction."""
    from ..database import get_db_session
    from sqlalchemy import text

    with get_db_session() as session:
        rows = session.execute(text("""
            SELECT pid,
                   state,
                   now() - state_change  AS idle_duration,
                   now() - query_start   AS query_duration,
                   left(query, 60)       AS query
            FROM pg_stat_activity
            WHERE state = 'idle in transaction'
            ORDER BY state_change
        """)).fetchall()

    if not rows:
        print("No sessions idle in transaction.")
        return

    print(f"{'PID':<8} {'Idle for':<20} {'Query ran for':<20} Last query")
    print("-" * 100)
    for pid, state, idle_dur, query_dur, query in rows:
        print(f"{pid:<8} {str(idle_dur).split('.')[0]:<20} {str(query_dur).split('.')[0]:<20} {query}")


def cmd_db_kill_idle(args):
    """Terminate PostgreSQL sessions that are idle in transaction."""
    from ..database import get_db_session
    from sqlalchemy import text

    with get_db_session() as session:
        rows = session.execute(text("""
            SELECT pid, now() - query_start AS duration
            FROM pg_stat_activity
            WHERE state = 'idle in transaction'
            ORDER BY query_start
        """)).fetchall()

    if not rows:
        print("No sessions idle in transaction.")
        return

    print(f"Found {len(rows)} session(s) idle in transaction:")
    for pid, duration in rows:
        print(f"  PID {pid} (idle for {duration})")

    if not args.yes:
        confirm = input("Terminate all? [y/N]: ")
        if confirm.lower() not in ['y', 'yes']:
            print("Cancelled.")
            return

    with get_db_session() as session:
        result = session.execute(text("""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE state = 'idle in transaction'
        """)).fetchall()

    terminated = sum(1 for (ok,) in result if ok)
    print(f"Terminated {terminated} session(s).")


def cmd_extract_all(args):
    """Extract knowledge graph relations from all documents matching filters."""
    from ..graph import extract_all

    try:
        print(f"Extracting graph relations from documents...")
        if args.source_id:
            print(f"  Source ID: {args.source_id}")
        if args.parser_id:
            print(f"  Parser ID: {args.parser_id}")
        if args.force:
            print(f"  Force: Re-processing documents with existing relations")
        if args.limit:
            print(f"  Limit: {args.limit} documents")

        result = extract_all(
            source_id=args.source_id,
            parser_id=args.parser_id,
            force=args.force,
            limit=args.limit,
        )

        print(f"\nBatch extraction complete:")
        print(f"  Total documents: {result['total_documents']}")
        print(f"  Processed: {result['processed']}")
        print(f"  Errors: {result['errors']}")
        print(f"  Total relations extracted: {result['total_relations_extracted']}")
        print(f"  Total relations created: {result['total_relations_created']}")
        print(f"  Total relations updated: {result['total_relations_updated']}")
        print(f"  Total relation errors: {result['total_relations_errors']}")

        if result['error_details']:
            print(f"\nError Details:")
            for i, error in enumerate(result['error_details'][:5], 1):
                print(f"  {i}. Document {error.get('document_id', 'unknown')}: {error.get('error', 'Unknown error')}")
            if len(result['error_details']) > 5:
                print(f"  ... and {len(result['error_details']) - 5} more")

    except Exception as e:
        print(f"Error: {e}")
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

    parse_all_parser = tools_subparsers.add_parser(
        "parse-all",
        help="Parse all raw documents for a source_id that don't have processed documents yet"
    )
    parse_all_parser.add_argument("source_id", help="Source identifier to process raw documents for")
    parse_all_parser.add_argument(
        "--parser-name",
        help="Parser to use (default: uses KB_PARSER env var or 'kb-mcp')"
    )
    parse_all_parser.add_argument(
        "--extract-images",
        action="store_true",
        help="Create separate Document objects for extracted images"
    )
    parse_all_parser.add_argument(
        "--describe-images",
        action="store_true",
        help="Generate LLM descriptions for images using vision model"
    )
    parse_all_parser.add_argument(
        "--force-reparse",
        action="store_true",
        help="Re-parse even if documents already exist for this parser"
    )
    parse_all_parser.add_argument(
        "--batch-size",
        type=int,
        help="Batch size for parallel processing (default: from config, set to large value like 999999 to disable batching)"
    )
    parse_all_parser.set_defaults(func=cmd_parse_all)

    chunk_embed_all_parser = tools_subparsers.add_parser(
        "chunk-and-embed-all",
        help="Chunk and embed all documents for a source_id that don't have chunks yet"
    )
    chunk_embed_all_parser.add_argument("source_id", help="Source identifier to process documents for")
    chunk_embed_all_parser.add_argument(
        "--strategy",
        help="Chunking strategy (e.g., 'tokens' or 'slide'). If not specified, uses default."
    )
    chunk_embed_all_parser.add_argument(
        "--embedding-name",
        help="Embedding config short name (e.g., 'openai-small')"
    )
    chunk_embed_all_parser.add_argument(
        "--provider",
        help="Embedding provider (e.g., 'openai', 'voyage')"
    )
    chunk_embed_all_parser.add_argument(
        "--model",
        help="Embedding model name (e.g., 'text-embedding-3-small')"
    )
    chunk_embed_all_parser.add_argument(
        "--no-images",
        action="store_true",
        help="Skip processing image documents"
    )
    chunk_embed_all_parser.add_argument(
        "--no-gist",
        action="store_true",
        help="Don't prepend document gist to chunks (creates separate strategy with _no_gist suffix)"
    )
    chunk_embed_all_parser.add_argument(
        "--no-section-path",
        action="store_true",
        help="Don't prepend section path to chunks (creates separate strategy with _no_section suffix)"
    )
    chunk_embed_all_parser.set_defaults(func=cmd_chunk_and_embed_all)

    summarize_all_parser = tools_subparsers.add_parser(
        "summarize-all",
        help="Generate summaries for all documents from a source that don't have them yet"
    )
    summarize_all_parser.add_argument("source_id", help="Source identifier to process documents for")
    summarize_all_parser.add_argument(
        "--model",
        help="Model name for summary generation (overrides SUMMARY_MODEL env var)"
    )
    summarize_all_parser.add_argument(
        "--no-summary-chunk",
        action="store_true",
        help="Skip creating summary chunks (default: creates summary chunks)"
    )
    summarize_all_parser.add_argument(
        "--embed-summary-chunk",
        action="store_true",
        help="Embed summary chunks after creating them (requires summary chunks to be created)"
    )
    summarize_all_parser.add_argument(
        "--embedding-name",
        help="Embedding config short name for summary chunks (e.g., 'openai-small')"
    )
    summarize_all_parser.add_argument(
        "--provider",
        help="Embedding provider for summary chunks (e.g., 'openai', 'voyage')"
    )
    summarize_all_parser.add_argument(
        "--embedding-model",
        help="Embedding model name for summary chunks (e.g., 'text-embedding-3-small')"
    )
    summarize_all_parser.set_defaults(func=cmd_summarize_all)

    list_tables_parser = tools_subparsers.add_parser("list-tables", help="List all database tables")
    list_tables_parser.add_argument("--json", action="store_true", help="Output as JSON")
    list_tables_parser.set_defaults(func=cmd_list_tables)

    drop_table_parser = tools_subparsers.add_parser("drop-table", help="Drop a database table by name")
    drop_table_parser.add_argument("table_name", help="Name of the table to drop")
    drop_table_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    drop_table_parser.set_defaults(func=cmd_drop_table)

    list_raw_parser = tools_subparsers.add_parser("list-raw", help="List raw documents")
    list_raw_parser.add_argument("--source-id", help="Filter by source ID")
    list_raw_parser.add_argument("--limit", type=int, default=50, help="Maximum number of results (default: 50)")
    list_raw_parser.set_defaults(func=cmd_list_raw)

    get_raw_parser = tools_subparsers.add_parser("get-raw", help="Get a raw document by document ID")
    get_raw_parser.add_argument("document_id", help="Document ID (UUID)")
    get_raw_parser.set_defaults(func=cmd_get_raw)

    drop_raw_parser = tools_subparsers.add_parser("drop-raw", help="Delete a raw document by ID")
    drop_raw_parser.add_argument("raw_document_id", help="Raw document ID (UUID)")
    drop_raw_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    drop_raw_parser.add_argument(
        "--delete-linked",
        action="store_true",
        help="Also delete all documents linked to this raw document (and their chunks/embeddings)"
    )
    drop_raw_parser.set_defaults(func=cmd_drop_raw)

    # DB session management
    db_sessions_parser = tools_subparsers.add_parser("db-sessions", help="List PostgreSQL sessions idle in transaction")
    db_sessions_parser.set_defaults(func=cmd_db_sessions)

    db_kill_idle_parser = tools_subparsers.add_parser("db-kill-idle", help="Terminate PostgreSQL sessions idle in transaction")
    db_kill_idle_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    db_kill_idle_parser.set_defaults(func=cmd_db_kill_idle)

    # Extract-all command (for graph relations)
    extract_all_parser = tools_subparsers.add_parser(
        "extract-all",
        help="Extract knowledge graph relations from all documents matching filters"
    )
    extract_all_parser.add_argument(
        "--source-id",
        help="Filter by source ID"
    )
    extract_all_parser.add_argument(
        "--parser-id",
        help="Filter by parser ID"
    )
    extract_all_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-process documents even if they already have relations"
    )
    extract_all_parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of documents to process"
    )
    extract_all_parser.set_defaults(func=cmd_extract_all)

    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show knowledge base statistics")
    stats_parser.add_argument(
        "--json",
        action="store_true",
        help="Output statistics as JSON"
    )
    stats_parser.set_defaults(func=cmd_stats)
