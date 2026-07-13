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

            # Each parser occupies at least col_w chars per doc_type + 1 space between them,
            # but never less than the parser name length
            parser_span = max(
                col_w * len(doc_types) + (len(doc_types) - 1),
                max((len(p) for p in parsers), default=0),
            )

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
            limit=getattr(args, 'limit', None),
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
        if args.parser_name:
            print(f"Filtering by parser: {args.parser_name}")
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
            parser_name=args.parser_name,
            force=args.force,
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
        if args.parser_name:
            print(f"Filtering by parser: {args.parser_name}")
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
            parser_name=args.parser_name,
            batch_size=args.batch_size,
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
                for dup_id in by_id_dups[:10 - count]:  # Show up to 10 total
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


def cmd_drop_parser(args):
    """Bulk-delete all documents for a given parser (and optionally source)."""
    from ..database import get_db_session
    from ..db_models import Document

    with get_db_session(auto_expunge=False) as session:
        query = session.query(Document).filter(Document.parser_id == args.parser_id)
        if args.source_id:
            query = query.filter(Document.source_id == args.source_id)
        count = query.count()

    if count == 0:
        scope = f"source '{args.source_id}', " if args.source_id else ""
        print(f"No documents found for {scope}parser '{args.parser_id}'.")
        return

    scope = f"source '{args.source_id}', " if args.source_id else "all sources, "
    if not args.yes:
        confirmation = input(
            f"Delete {count} document(s) ({scope}parser='{args.parser_id}') "
            f"and all their chunks/embeddings? [y/N]: "
        )
        if confirmation.lower() not in ["y", "yes"]:
            print("Cancelled.")
            return

    with get_db_session(auto_expunge=False) as session:
        query = session.query(Document).filter(Document.parser_id == args.parser_id)
        if args.source_id:
            query = query.filter(Document.source_id == args.source_id)
        deleted = query.delete(synchronize_session=False)

    print(f"Deleted {deleted} document(s) (chunks/embeddings removed via cascade).")


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
    """Delete raw document(s) by ID or source_id."""
    from ..db_models import RawDocument
    from ..database import get_db_session

    source_id = getattr(args, "source_id", None)
    raw_document_id = getattr(args, "raw_document_id", None)

    if not source_id and not raw_document_id:
        print("Error: provide either a raw_document_id or --source-id")
        sys.exit(1)

    try:
        if source_id:
            # Bulk delete by source_id
            with get_db_session() as session:
                raw_docs = session.query(RawDocument).filter(
                    RawDocument.source_id == source_id
                ).all()

            if not raw_docs:
                print(f"No raw documents found for source_id '{source_id}'")
                return

            if not args.yes:
                linked_note = " and all linked documents/chunks/embeddings" if args.delete_linked else ""
                confirmation = input(
                    f"Delete {len(raw_docs)} raw document(s) for source '{source_id}'{linked_note}? [y/N]: "
                )
                if confirmation.lower() not in ['y', 'yes']:
                    print("Cancelled.")
                    return

            total_deleted_docs = 0
            for raw_doc in raw_docs:
                result = delete_raw_document(raw_doc.id, delete_linked_documents=args.delete_linked)
                total_deleted_docs += result.get("deleted_documents", 0)

            print(f"Deleted {len(raw_docs)} raw document(s) for source '{source_id}'")
            if args.delete_linked and total_deleted_docs > 0:
                print(f"  (Also deleted {total_deleted_docs} linked document(s) and their chunks/embeddings)")

        else:
            # Single delete by UUID
            if not args.yes:
                message = f"Delete raw document {raw_document_id}?"
                if args.delete_linked:
                    message += " (This will also delete all linked documents and their chunks/embeddings)"
                message += " [y/N]: "
                confirmation = input(message)
                if confirmation.lower() not in ['y', 'yes']:
                    print("Cancelled.")
                    return

            result = delete_raw_document(raw_document_id, delete_linked_documents=args.delete_linked)
            print(f"Deleted raw document {raw_document_id}")
            if args.delete_linked:
                if result.get("deleted_documents", 0) > 0:
                    print(f"  (Also deleted {result['deleted_documents']} linked document(s) and their chunks/embeddings)")
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


def cmd_filter_all(args):
    """Run the LLM privacy filter over all unclassified raw documents."""
    try:
        from ..tools import filter_all

        print(f"Running privacy filter for source_id: {args.source_id or 'all'}")
        if args.parser_name:
            print(f"  Using parser: {args.parser_name}")
        if args.model:
            print(f"  Using model: {args.model}")

        result = filter_all(
            source_id=args.source_id,
            parser_name=args.parser_name or "marker",
            model=args.model,
            batch_size=args.batch_size,
            limit=getattr(args, "limit", None),
            delay=args.delay,
        )

        print(f"\n  Completed:")
        print(f"  Processed:    {result['processed']}")
        print(f"  Classified:   {result['filtered']}")
        print(f"  Skipped:      {result['skipped']} (no parsed text or already classified)")
        if result['errors'] > 0:
            print(f"  Errors:       {result['errors']}")
        print(f"\n  Labels assigned in this run:")
        for label, count in result['by_label'].items():
            print(f"    {label:<16} {count}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_filter_list(args):
    """List documents by privacy label."""
    try:
        from ..database import get_db_session
        from ..db_models import PrivacyFilter, RawDocument
        from sqlalchemy import func

        # Determine which label to show
        if args.private:
            label = PrivacyFilter.LABEL_PRIVATE
        elif args.review:
            label = PrivacyFilter.LABEL_NEEDS_REVIEW
        else:
            label = PrivacyFilter.LABEL_PUBLIC

        with get_db_session() as session:
            query = (
                session.query(PrivacyFilter, RawDocument)
                .join(RawDocument, PrivacyFilter.raw_document_id == RawDocument.id)
                .filter(PrivacyFilter.label == label)
            )
            if args.source_id:
                query = query.filter(RawDocument.source_id == args.source_id)
            query = query.order_by(RawDocument.source_id, RawDocument.doc_id)
            if args.limit:
                query = query.limit(args.limit)
            rows = query.all()

        if not rows:
            print(f"No {label} documents found.")
            return

        if args.json:
            import json
            out = [
                {
                    "raw_document_id": pf.raw_document_id,
                    "source_id": rd.source_id,
                    "doc_id": rd.doc_id,
                    "label": pf.label,
                    "reasoning": pf.reasoning,
                    "model": pf.model,
                    "classified_time": str(pf.created_time)[:19] if pf.created_time else None,
                }
                for pf, rd in rows
            ]
            print(json.dumps(out, indent=2))
            return

        print(f"{'Source':<24}  {'Doc ID':<36}  {'Raw Document ID':<36}  Reasoning")
        print("-" * 140)
        for pf, rd in rows:
            reasoning = (pf.reasoning or "")[:60].replace("\n", " ")
            if pf.reasoning and len(pf.reasoning) > 60:
                reasoning += "…"
            print(f"{(rd.source_id or ''):<24}  {(rd.doc_id or ''):<36}  {rd.id:<36}  {reasoning}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_filter_stats(args):
    """Show privacy filter classification statistics."""
    try:
        from ..database import get_db_session
        from ..db_models import PrivacyFilter, RawDocument
        from sqlalchemy import func

        with get_db_session() as session:
            total_raw = session.query(func.count(RawDocument.id))
            if args.source_id:
                total_raw = total_raw.filter(RawDocument.source_id == args.source_id)
            total_raw = total_raw.scalar()

            pf_q = session.query(PrivacyFilter)
            if args.source_id:
                pf_q = pf_q.join(RawDocument, PrivacyFilter.raw_document_id == RawDocument.id)\
                            .filter(RawDocument.source_id == args.source_id)

            total_classified = pf_q.count()

            # Counts per label (exclude sentinel error/skipped rows from "classified" tally)
            label_counts = dict(
                session.query(PrivacyFilter.label, func.count(PrivacyFilter.id))
                .join(RawDocument, PrivacyFilter.raw_document_id == RawDocument.id)
                .filter(RawDocument.source_id == args.source_id if args.source_id else True)
                .group_by(PrivacyFilter.label)
                .all()
            )

            # Error sentinels (meta->error = true)
            from sqlalchemy import cast
            from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
            try:
                error_count = (
                    session.query(func.count(PrivacyFilter.id))
                    .join(RawDocument, PrivacyFilter.raw_document_id == RawDocument.id)
                    .filter(
                        RawDocument.source_id == args.source_id if args.source_id else True,
                        PrivacyFilter.meta["error"].astext == "true",
                    )
                    .scalar() or 0
                )
            except Exception:
                error_count = 0

            # Per-source breakdown
            by_source = (
                session.query(
                    RawDocument.source_id,
                    PrivacyFilter.label,
                    func.count(PrivacyFilter.id),
                )
                .join(RawDocument, PrivacyFilter.raw_document_id == RawDocument.id)
                .filter(RawDocument.source_id == args.source_id if args.source_id else True)
                .group_by(RawDocument.source_id, PrivacyFilter.label)
                .order_by(RawDocument.source_id, PrivacyFilter.label)
                .all()
            )

        unclassified = total_raw - total_classified

        if args.json:
            import json
            print(json.dumps({
                "total_raw": total_raw,
                "total_classified": total_classified,
                "unclassified": unclassified,
                "label_counts": label_counts,
                "error_sentinels": error_count,
            }, indent=2))
            return

        scope = f" (source: {args.source_id})" if args.source_id else " (all sources)"
        print(f"Privacy Filter Statistics{scope}")
        print("=" * 42)
        print(f"  Total raw documents:  {total_raw}")
        print(f"  Classified:           {total_classified}")
        print(f"  Unclassified:         {unclassified}")
        if error_count:
            print(f"  Error sentinels:      {error_count}  (needs_review, error=True in meta)")
        print()

        labels = [PrivacyFilter.LABEL_PUBLIC, PrivacyFilter.LABEL_NEEDS_REVIEW, PrivacyFilter.LABEL_PRIVATE]
        for label in labels:
            count = label_counts.get(label, 0)
            pct = f"{count / total_classified * 100:.1f}%" if total_classified else "-"
            print(f"  {label:<16} {count:>6}  ({pct})")

        if not args.source_id and by_source:
            # Pivot by source
            from collections import defaultdict
            src_data = defaultdict(dict)
            for src, label, cnt in by_source:
                src_data[src][label] = cnt

            print()
            print(f"  {'Source':<28}  {'public':>8}  {'needs_review':>12}  {'private':>8}  {'total':>6}")
            print("  " + "-" * 68)
            for src in sorted(src_data):
                pub = src_data[src].get(PrivacyFilter.LABEL_PUBLIC, 0)
                nr  = src_data[src].get(PrivacyFilter.LABEL_NEEDS_REVIEW, 0)
                prv = src_data[src].get(PrivacyFilter.LABEL_PRIVATE, 0)
                tot = pub + nr + prv
                print(f"  {src:<28}  {pub:>8}  {nr:>12}  {prv:>8}  {tot:>6}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_export(args):
    """Export documents from a source into a folder hierarchy."""
    try:
        from ..tools import export_source

        print(f"Exporting source_id: {args.source_id} → {args.output_dir}")
        if args.include_private:
            print(f"  Including private documents (→ {args.private_subdir}/)")
        if args.include_needs_review:
            print(f"  Including needs_review documents (→ {args.needs_review_subdir}/)")
        if args.parser_name:
            print(f"  Preferred parser for summaries: {args.parser_name}")

        result = export_source(
            source_id=args.source_id,
            output_dir=args.output_dir,
            parser_name=args.parser_name or "marker",
            include_private=args.include_private,
            include_needs_review=args.include_needs_review,
            private_subdir=args.private_subdir,
            needs_review_subdir=args.needs_review_subdir,
        )

        total = result['exported_public'] + result['exported_needs_review'] + result['exported_private']
        print(f"\n  Completed ({total} document(s) exported):")
        print(f"  Public:             {result['exported_public']}")
        print(f"  Needs review:       {result['exported_needs_review']}"
              + (" (exported)" if args.include_needs_review else " (skipped)"))
        print(f"  Private:            {result['exported_private']}"
              + (" (exported)" if args.include_private else " (skipped)"))
        if result['errors'] > 0:
            print(f"  Errors:             {result['errors']}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


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


def cmd_count_tokens(args):
    """Count tokens across all text documents matching the given filters."""
    try:
        from ..db_models import Document
        from ..database import get_db_session
        from ...chunking.chunking import count_tokens

        with get_db_session() as session:
            query = session.query(Document).filter(
                Document.text.isnot(None),
                Document.text != "",
                Document.doc_type != "image",
            )
            if args.source_id:
                query = query.filter(Document.source_id == args.source_id)
            if args.parser_name:
                query = query.filter(Document.parser_id == args.parser_name)

            documents = query.all()

        if not documents:
            print("No documents found matching the given filters.")
            return

        print(f"Counting tokens for {len(documents)} document(s)...")
        token_counts = [count_tokens(doc.text) for doc in documents]
        char_counts = [len(doc.text) for doc in documents]

        def stats(counts):
            total = sum(counts)
            mean = total / len(counts)
            s = sorted(counts)
            n = len(s)
            median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
            std = (sum((c - mean) ** 2 for c in counts) / len(counts)) ** 0.5
            return total, mean, std, median, s[0], s[-1]

        tt, tm, ts, tmed, tmin, tmax = stats(token_counts)
        ct, cm, cs, cmed, cmin, cmax = stats(char_counts)

        print(f"\n  {'':14}  {'Tokens':>14}  {'Chars':>14}")
        print(f"  {'Documents':<14}  {len(documents):>14,}  {len(documents):>14,}")
        print(f"  {'Total':<14}  {tt:>14,}  {ct:>14,}")
        print(f"  {'Mean':<14}  {tm:>14,.1f}  {cm:>14,.1f}")
        print(f"  {'Std':<14}  {ts:>14,.1f}  {cs:>14,.1f}")
        print(f"  {'Median':<14}  {tmed:>14,.1f}  {cmed:>14,.1f}")
        print(f"  {'Min':<14}  {tmin:>14,}  {cmin:>14,}")
        print(f"  {'Max':<14}  {tmax:>14,}  {cmax:>14,}")

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
    parse_all_parser.add_argument(
        "--limit", "-N",
        type=int,
        metavar="N",
        help="Stop after parsing N documents (useful for testing)"
    )
    parse_all_parser.set_defaults(func=cmd_parse_all)

    chunk_embed_all_parser = tools_subparsers.add_parser(
        "chunk-and-embed-all",
        help="Chunk and embed all documents for a source_id that don't have chunks yet"
    )
    chunk_embed_all_parser.add_argument("source_id", help="Source identifier to process documents for")
    chunk_embed_all_parser.add_argument(
        "--parser-name",
        help="Only process documents parsed by this parser (e.g., 'marker', 'nougat', 'docling', 'azure')"
    )
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
    chunk_embed_all_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-chunk and re-embed documents that already have chunks (drops existing chunks first)"
    )
    chunk_embed_all_parser.set_defaults(func=cmd_chunk_and_embed_all)

    summarize_all_parser = tools_subparsers.add_parser(
        "summarize-all",
        help="Generate summaries for all documents from a source that don't have them yet"
    )
    summarize_all_parser.add_argument("source_id", help="Source identifier to process documents for")
    summarize_all_parser.add_argument(
        "--parser-name",
        help="Only process documents parsed by this parser (e.g., 'marker', 'nougat', 'docling', 'azure')"
    )
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
    summarize_all_parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Commit to DB every N successfully summarized documents (default: 10)"
    )
    summarize_all_parser.set_defaults(func=cmd_summarize_all)

    filter_all_parser = tools_subparsers.add_parser(
        "filter-all",
        help="Run the LLM privacy filter over all unclassified raw documents"
    )
    filter_all_parser.add_argument(
        "--source-id",
        help="Only process documents from this source (default: all sources)"
    )
    filter_all_parser.add_argument(
        "--parser-name",
        default="marker",
        help="Parser whose text to use for classification (default: marker)"
    )
    filter_all_parser.add_argument(
        "--model",
        help="LLM model for classification (overrides PRIVACY_FILTER_MODEL env var)"
    )
    filter_all_parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Documents per batch; each batch is locked so parallel workers don't overlap (default: 10)"
    )
    filter_all_parser.add_argument(
        "--limit", "-N",
        type=int,
        metavar="N",
        help="Stop after processing N documents total (useful for testing)"
    )
    filter_all_parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="Seconds to sleep between batches to avoid rate limits (default: 0)"
    )
    filter_all_parser.set_defaults(func=cmd_filter_all)

    filter_list_parser = tools_subparsers.add_parser(
        "filter-list",
        help="List documents by privacy label"
    )
    label_group = filter_list_parser.add_mutually_exclusive_group()
    label_group.add_argument("--public", action="store_true", help="List public documents (default)")
    label_group.add_argument("--review", action="store_true", help="List needs_review documents")
    label_group.add_argument("--private", action="store_true", help="List private documents")
    filter_list_parser.add_argument("--source-id", help="Filter to a single source")
    filter_list_parser.add_argument("--limit", type=int, help="Maximum number of results")
    filter_list_parser.add_argument("--json", action="store_true", help="Output as JSON")
    filter_list_parser.set_defaults(func=cmd_filter_list)

    filter_stats_parser = tools_subparsers.add_parser(
        "filter-stats",
        help="Show privacy filter classification statistics"
    )
    filter_stats_parser.add_argument(
        "--source-id",
        help="Filter stats to a single source (default: all sources)"
    )
    filter_stats_parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON"
    )
    filter_stats_parser.set_defaults(func=cmd_filter_stats)

    export_parser = tools_subparsers.add_parser(
        "export",
        help="Export documents from a source into a folder hierarchy"
    )
    export_parser.add_argument("source_id", help="Source identifier to export")
    export_parser.add_argument("output_dir", help="Root output directory (created if missing)")
    export_parser.add_argument(
        "--parser-name",
        default="marker",
        help="Preferred parser for summary/gist/title text (default: marker)"
    )
    export_parser.add_argument(
        "--include-private",
        action="store_true",
        help="Also export private documents into a separate subfolder"
    )
    export_parser.add_argument(
        "--include-needs-review",
        action="store_true",
        help="Also export needs_review documents into a separate subfolder"
    )
    export_parser.add_argument(
        "--private-subdir",
        default="private",
        help="Subfolder name for private documents (default: private)"
    )
    export_parser.add_argument(
        "--needs-review-subdir",
        default="needs_review",
        help="Subfolder name for needs_review documents (default: needs_review)"
    )
    export_parser.set_defaults(func=cmd_export)

    count_tokens_parser = tools_subparsers.add_parser(
        "count-tokens",
        help="Count total tokens across all text documents matching the given filters"
    )
    count_tokens_parser.add_argument("--source-id", help="Filter by source ID")
    count_tokens_parser.add_argument(
        "--parser-name",
        help="Filter by parser (e.g., 'marker', 'nougat', 'docling')"
    )
    count_tokens_parser.set_defaults(func=cmd_count_tokens)

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

    drop_raw_parser = tools_subparsers.add_parser("drop-raw", help="Delete raw document(s) by ID or source")
    drop_raw_parser.add_argument("raw_document_id", nargs="?", help="Raw document ID (UUID)")
    drop_raw_parser.add_argument("--source-id", help="Delete all raw documents for this source_id")
    drop_raw_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    drop_raw_parser.add_argument(
        "--delete-linked",
        action="store_true",
        help="Also delete all documents linked to this raw document (and their chunks/embeddings)"
    )
    drop_raw_parser.set_defaults(func=cmd_drop_raw)
    drop_parser_parser = tools_subparsers.add_parser(
        "drop-parser",
        help="Bulk-delete all documents for a parser (and optionally a source)"
    )
    drop_parser_parser.add_argument("parser_id", help="Parser ID to delete (e.g. 'nougat', 'docling', 'marker')")
    drop_parser_parser.add_argument("--source-id", help="Restrict deletion to this source ID")
    drop_parser_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    drop_parser_parser.set_defaults(func=cmd_drop_parser)

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
