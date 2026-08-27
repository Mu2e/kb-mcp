"""Document management commands (add, get)."""

import json
import os
import sys
from pathlib import Path

from sqlalchemy import func

from .. import add_source, get
from ..tools import ingest
from ..db_models import Document, Source
from ..documents import delete_raw_document
from ..database import get_db_session, init_db


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

    # Initialize database (creates tables if they don't exist)
    init_db(create_tables=True)

    # Determine source_id if not provided
    source_id = args.source_id
    if args.meeting and not source_id:
        source_id = "MeetingTranscripts"

    if not source_id:
        source_id = "local"
        print(f"Warning: No source_id provided, using '{source_id}'")
        print("  Use --source-id to specify a source")

    # Determine doc_id
    doc_id = args.doc_id
    if not doc_id:
        doc_id = file_path.stem

    print(f"Processing file: {file_path}")

    # Determine image LLM description setting
    # If --parse-images is set, enable LLM descriptions by default unless --no-parse-images-llm is set
    describe_images = args.parse_images and not args.no_parse_images_llm

    # Parse optional metadata JSON
    meta = None
    if args.meta_json:
        try:
            meta = json.loads(args.meta_json)
            if not isinstance(meta, dict):
                raise ValueError(f"--meta-json must decode to an object, got {type(meta).__name__}")
        except Exception as e:
            print(f"Error parsing --meta-json: {e}")
            sys.exit(1)

    # Convenience profile to mirror meeting uploader defaults.
    if args.meeting:
        if meta is None:
            meta = {}

    # Meeting comments profile should always include metadata enrichment.
    summary_include_metadata = args.summary_include_metadata or args.meeting

    # Prepare chunk_config if chunk_size or chunk_overlap are provided
    chunk_config = None
    if args.chunk_size or args.chunk_overlap:
        chunk_config = {}
        if args.chunk_size:
            chunk_config["chunk_size"] = args.chunk_size
        if args.chunk_overlap:
            chunk_config["chunk_overlap"] = args.chunk_overlap

    # Use a single session for all operations
    try:
        with get_db_session() as session:
            # Check if source exists, create if needed
            source = session.query(Source).filter(Source.id == source_id).first()
            if not source:
                print(f"Source '{source_id}' does not exist. Creating it...")
                if source_id == "MeetingTranscripts":
                    add_source(
                        source_id=source_id,
                        name="Meeting Transcripts",
                        description="Meeting notes/comments/transcripts",
                        session=session,
                    )
                else:
                    add_source(source_id=source_id, name=f"Local files ({source_id})", session=session)
                # Flush to make source available for foreign key constraints
                session.flush()

            # Use ingest() function for the full workflow with the same session
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
                meta=meta,
                parser_name=args.parser_name,
                generate_summary=not args.no_summary,
                summary_include_metadata=summary_include_metadata,
                chunk_and_embed=not args.no_embed,
                create_summary_chunks=not args.no_summary_chunks and not args.no_summary,
                chunk_strategy=args.strategy,
                chunk_config=chunk_config,
                embedding_name=args.embedding_name,
                embedding_provider=args.provider,
                embedding_model=args.model,
                session=session,
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
            if result.get('num_metadata_enriched', 0) > 0:
                print(f"  Metadata enriched for {result['num_metadata_enriched']} document(s)")
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


def _rebuild_text_from_parser_output(doc, session):
    """Re-render `doc.text` from its stored DoclingDocument, or return None.

    Exactly what the parser does — Docling's own markdown export, unescaped,
    with image markers replaced by the descriptions already on the child
    image records. No re-parse, no GPU, no LLM calls.
    """
    import html as html_module

    from docling_core.types.doc import DoclingDocument

    from ...parser.parse import (
        DOCLING_PAGE_BREAK_PLACEHOLDER,
        inline_docling_image_descriptions,
        number_docling_page_breaks,
    )

    parser_output = doc.parser_output
    if not parser_output or parser_output.get("schema_name") != "DoclingDocument":
        return None

    dl_doc = DoclingDocument.model_validate(parser_output)
    text = html_module.unescape(dl_doc.export_to_markdown(
        page_break_placeholder=DOCLING_PAGE_BREAK_PLACEHOLDER
    ))
    text = number_docling_page_breaks(text, parser_output)

    image_children = session.query(Document).filter(
        Document.parent_id == doc.id,
        Document.doc_type == "image",
    ).all()
    image_dicts = [
        {"doc_id": c.doc_id, "text": c.text, "meta": c.meta or {}}
        for c in image_children
    ]
    return inline_docling_image_descriptions(text, image_dicts, parser_output)


def _chunk_ids_for(document_id, session):
    """Ids of every chunk belonging to `document_id` and its children.

    Re-generating a document invalidates all of its chunks, but
    `chunk_and_embed()` only deletes chunks whose strategy it is about to
    re-emit (chunking.py). A document moving from `tokens_1000_200` to
    `section` would otherwise keep both sets and return each passage twice in
    search. Snapshot the ids first, delete them only once the rebuild has
    succeeded, so a failure leaves the old chunks in place.
    """
    from ..embedding.db_models import Chunk

    doc_ids = [document_id] + [
        row[0] for row in session.query(Document.id).filter(
            Document.parent_id == document_id
        ).all()
    ]
    return [
        row[0] for row in session.query(Chunk.id).filter(
            Chunk.document_id.in_(doc_ids)
        ).all()
    ]


def _delete_chunks(chunk_ids, session):
    """Drop chunks captured before a rebuild. Returns the number removed."""
    from ..embedding.db_models import Chunk

    if not chunk_ids:
        return 0
    return session.query(Chunk).filter(
        Chunk.id.in_(chunk_ids)
    ).delete(synchronize_session=False)


def _reparse_from_stored(target, args):
    """Rebuild one document from its persisted parse tree, then re-derive.

    Unlike the file path, this reuses `parser_output` — so it cannot change
    what the parser saw, only how the text is rendered from it. Summary and
    chunks are regenerated so they can't drift from the new text.
    """
    with get_db_session() as session:
        doc = session.query(Document).filter(Document.id == target["id"]).first()
        if doc is None:
            raise ValueError(f"document {target['id']} disappeared")

        text = _rebuild_text_from_parser_output(doc, session)
        if text is None:
            raise ValueError(
                "no DoclingDocument parser_output to rebuild from — re-parse "
                "from the raw file instead (drop --from-stored)"
            )

        stale = _chunk_ids_for(doc.id, session)
        old_len = len(doc.text or "")
        doc.text = text
        session.flush()
        print(f"  text: {old_len} -> {len(text)} chars")

        if not args.no_summary:
            doc.generate_summary(
                include_title=True, include_gist=True, include_summary=True,
            )
            print("  summary regenerated")

        if not args.no_embed:
            removed = _delete_chunks(stale, session)
            if removed:
                print(f"  dropped {removed} stale chunk(s)")
            chunks = doc.chunk_and_embed()
            print(f"  {len(chunks or [])} chunk(s) embedded")
            if not args.no_summary and doc.summary:
                doc.chunk_and_embed(chunk_strategy="summary")


def _reparse_from_raw(args):
    """`--from-raw`: drive the loop from documents_raw instead of documents.

    The default mode starts from `documents`, so it can only refresh what
    parsed at least once. Starting from `documents_raw` also reaches raw files
    that never produced a document at all — a parse that died, or an import
    interrupted between storing the file and parsing it. Those are invisible
    to every other command and look like nothing is wrong.

    In this mode the positional ids are RawDocument UUIDs, not Document UUIDs.
    """
    from ..db_models import RawDocument

    with get_db_session() as session:
        q = session.query(RawDocument).filter(RawDocument.file_path.isnot(None))
        if args.document_ids:
            q = q.filter(RawDocument.id.in_(args.document_ids))
        if args.source_id:
            q = q.filter(RawDocument.source_id == args.source_id)
        if args.doc_ids:
            q = q.filter(RawDocument.doc_id.in_(args.doc_ids))
        if not args.force_reparse:
            # Default to the raw files that never yielded a document — the
            # silent-loss case this mode exists for, and an additive operation.
            # Re-doing raws that already parsed is what plain `kb reparse` is
            # for, so it takes an explicit --force-reparse here.
            q = q.filter(~session.query(Document.id).filter(
                Document.raw_document_id == RawDocument.id
            ).exists())

        targets = []
        for raw in q.order_by(RawDocument.source_id, RawDocument.doc_id).all():
            # doc_id is nullable in the schema; fall back to the filename.
            doc_id = raw.doc_id or Path(raw.file_path).stem
            # Which document will ingest() actually update? It dedups on
            # (source_id, doc_id) — NOT on raw_document_id. Those differ in
            # practice: the same (source_id, doc_id) can own several raw rows
            # (re-fetched file versions), with the parent document linked to one
            # of them and its image children to another. Looking up by raw id
            # would miss the parent, then skip its timestamp restore and leave
            # its stale chunks behind.
            existing = session.query(Document).filter(
                Document.source_id == raw.source_id,
                Document.doc_id == doc_id,
                Document.parent_id.is_(None),
            ).first()
            targets.append({
                "raw_id": raw.id,
                "source_id": raw.source_id,
                "doc_id": doc_id,
                "uri": raw.uri,
                "meta": dict(raw.meta or {}),
                "file_path": raw.file_path,
                "doc_id_existing": existing.id if existing else None,
                "creating_time": existing.creating_time if existing else None,
                "update_time": existing.update_time if existing else None,
                "text_len": len(existing.text or "") if existing else None,
            })

    if not targets:
        print("No matching raw documents found.")
        return

    missing = [t for t in targets if not Path(t["file_path"]).is_file()]
    for t in missing:
        print(f"  {t['source_id']}/{t['doc_id']}: file gone, skipping ({t['file_path']})")
    targets = [t for t in targets if Path(t["file_path"]).is_file()]

    new_docs = sum(1 for t in targets if t["doc_id_existing"] is None)
    scope = (f"{new_docs} with no document yet, {len(targets) - new_docs} re-processed"
             if args.force_reparse else f"{new_docs} with no document yet")
    print(f"{len(targets)} raw file(s) to process ({scope}):")
    for t in targets:
        state = "NEW" if t["doc_id_existing"] is None else f"{t['text_len']} chars"
        print(f"  {t['source_id']}/{t['doc_id']}  ({state})  <- {t['file_path']}")

    if args.dry_run:
        print("  (dry run — nothing processed)")
        return

    ok = failed = 0
    for i, t in enumerate(targets, 1):
        print(f"\n[{i}/{len(targets)}] processing {t['source_id']}/{t['doc_id']}")
        try:
            stale = []
            if t["doc_id_existing"] and not args.no_embed:
                with get_db_session() as session:
                    stale = _chunk_ids_for(t["doc_id_existing"], session)

            ingest(
                t["file_path"],
                source_id=t["source_id"],
                doc_id=t["doc_id"],
                uri=t["uri"],
                meta=t["meta"],
                # Passed straight into add_document() so they land on whichever
                # row ends up holding the new parse — including a freshly
                # INSERTed row when identity misses (e.g. a parser change), which
                # patching doc_id_existing after the fact would miss entirely.
                creating_time=t["creating_time"],
                update_time=t["update_time"],
                force_reparse=True,
                parser_name=args.parser_name,
                # Already under data/sources/{source_id}/ — copying it onto
                # itself would rewrite documents_raw.file_path.
                copy_to_kb=False,
                generate_summary=not args.no_summary,
                chunk_and_embed=not args.no_embed,
                create_summary_chunks=not args.no_summary,
            )
        except Exception as e:
            failed += 1
            print(f"  Error: {type(e).__name__}: {e}")
            continue

        if t["doc_id_existing"]:
            with get_db_session() as session:
                removed = _delete_chunks(stale, session)
                if removed:
                    print(f"  dropped {removed} stale chunk(s) from the previous text")
        ok += 1

    print(f"\nDone: {ok} processed, {failed} failed, {len(missing)} file missing")
    if failed:
        sys.exit(1)


def cmd_reparse(args):
    """Re-parse documents, from the raw file, the stored parse tree, or raw rows."""
    from ..db_models import RawDocument

    if args.from_raw and args.from_stored:
        print("Error: --from-raw and --from-stored are different sources; pick one.")
        sys.exit(1)
    if args.from_raw and args.empty_only:
        print("Error: --empty-only selects documents by text, which raw rows don't "
              "have. Drop it: --from-raw already targets raw files with no document.")
        sys.exit(1)
    if args.force_reparse and not args.from_raw:
        print("Error: --force-reparse only applies to --from-raw. Every other mode "
              "re-parses its targets unconditionally.")
        sys.exit(1)

    selectors = (args.document_ids or args.source_id or args.doc_ids
                 or args.empty_only)
    # Bare --from-raw is bounded by construction: only raw files with no
    # document, which is a small, safe, additive set. Adding --force-reparse
    # unbounds it to every raw row, so that does need narrowing.
    if args.from_raw and not args.force_reparse:
        pass
    elif not selectors:
        narrow = ("--source-id / --doc-id" if args.from_raw
                  else "--source-id / --doc-id / --empty-only")
        noun = "raw file" if args.from_raw else "document"
        print(f"Error: refusing to re-process every {noun}. Pass UUIDs, or "
              f"narrow with {narrow}.")
        sys.exit(1)

    if args.from_raw:
        return _reparse_from_raw(args)

    with get_db_session() as session:
        # --from-stored rebuilds from parser_output and never touches the raw
        # file, so don't require one; the file path is still reported when known.
        if args.from_stored:
            q = (
                session.query(Document, RawDocument.file_path)
                .outerjoin(RawDocument, Document.raw_document_id == RawDocument.id)
                .filter(Document.doc_type == "text")
            )
        else:
            q = (
                session.query(Document, RawDocument.file_path)
                .join(RawDocument, Document.raw_document_id == RawDocument.id)
                .filter(Document.doc_type == "text", RawDocument.file_path.isnot(None))
            )
        if args.document_ids:
            q = q.filter(Document.id.in_(args.document_ids))
        if args.source_id:
            q = q.filter(Document.source_id == args.source_id)
        if args.doc_ids:
            q = q.filter(Document.doc_id.in_(args.doc_ids))
        if args.empty_only:
            q = q.filter(func.coalesce(Document.text, "") == "")

        # Snapshot the fields ingest() would otherwise overwrite: _update_document()
        # copies uri/meta/creating_time/update_time off the freshly built Document,
        # so anything not passed back through here is lost (in particular a DocDB
        # RetrieveFile URL would be replaced by a file:// path).
        targets = [
            {
                "id": doc.id,
                "source_id": doc.source_id,
                "doc_id": doc.doc_id,
                "uri": doc.uri,
                "meta": dict(doc.meta or {}),
                "creating_time": doc.creating_time,
                "update_time": doc.update_time,
                "text_len": len(doc.text or ""),
                "file_path": file_path,
            }
            for doc, file_path in q.order_by(Document.doc_id).all()
        ]

    if not targets:
        print("No matching documents found.")
        return

    missing = []
    if not args.from_stored:
        missing = [t for t in targets if not Path(t["file_path"]).is_file()]
        for t in missing:
            print(f"  {t['doc_id']}: file gone, skipping ({t['file_path']})")
        targets = [t for t in targets if Path(t["file_path"]).is_file()]

    how = "rebuild from stored parser output" if args.from_stored else "re-parse"
    doing = "rebuilding" if args.from_stored else "re-parsing"
    print(f"{len(targets)} document(s) to {how}:")
    for t in targets:
        source = "" if args.from_stored else f"  <- {t['file_path']}"
        print(f"  {t['source_id']}/{t['doc_id']}  ({t['text_len']} chars){source}")

    if args.dry_run:
        print("  (dry run — nothing changed)")
        return

    ok = failed = 0
    for i, t in enumerate(targets, 1):
        print(f"\n[{i}/{len(targets)}] {doing} {t['source_id']}/{t['doc_id']}")
        try:
            if args.from_stored:
                _reparse_from_stored(t, args)
                ok += 1
                continue

            # A re-parse rewrites the text, so every chunk built from the old
            # text is stale — including ones under a strategy this run won't
            # re-emit, which chunk_and_embed() would leave behind.
            stale = []
            if not args.no_embed:
                with get_db_session() as session:
                    stale = _chunk_ids_for(t["id"], session)

            ingest(
                t["file_path"],
                source_id=t["source_id"],
                doc_id=t["doc_id"],
                uri=t["uri"],
                meta=t["meta"],
                # Passed straight into add_document() so they land on whichever
                # row ends up holding the new parse — including a freshly
                # INSERTed row when identity misses (e.g. a parser change), which
                # patching t["id"] after the fact would miss entirely.
                creating_time=t["creating_time"],
                update_time=t["update_time"],
                force_reparse=True,
                parser_name=args.parser_name,
                # The file already lives under data/sources/{source_id}/ — copying
                # it onto itself is pointless and would rewrite file_path.
                copy_to_kb=False,
                generate_summary=not args.no_summary,
                chunk_and_embed=not args.no_embed,
                create_summary_chunks=not args.no_summary,
            )
        except Exception as e:
            failed += 1
            print(f"  Error: {type(e).__name__}: {e}")
            continue

        with get_db_session() as session:
            # Only now that the re-parse succeeded: chunks captured before it
            # ran and not replaced by it belong to the old text.
            removed = _delete_chunks(stale, session)
            if removed:
                print(f"  dropped {removed} stale chunk(s) from the previous text")
        ok += 1

    print(f"\nDone: {ok} re-parsed, {failed} failed, {len(missing)} file missing")
    if failed:
        sys.exit(1)


def setup_commands(subparsers):
    """Set up document management commands."""
    # Reparse command
    reparse_parser = subparsers.add_parser(
        "reparse",
        help="Re-parse existing documents from their stored raw file, preserving "
             "uri/meta/timestamps",
    )
    reparse_parser.add_argument(
        "document_ids",
        nargs="*",
        metavar="ID",
        help="Document UUID(s) to re-parse — or RawDocument UUID(s) with --from-raw",
    )
    reparse_parser.add_argument("--source-id", help="Restrict to one source (e.g. mu2e-docdb)")
    reparse_parser.add_argument(
        "--doc-id",
        action="append",
        dest="doc_ids",
        help="Restrict to specific doc_id(s); repeatable",
    )
    reparse_parser.add_argument(
        "--empty-only",
        action="store_true",
        help="Only documents whose text is empty (e.g. a parser backend that failed silently)",
    )
    reparse_parser.add_argument(
        "--from-raw",
        action="store_true",
        help="Drive the loop from documents_raw instead of documents, reaching raw "
             "files that never produced a document (a parse that died, or an import "
             "interrupted before parsing). Only those, unless --force-reparse. "
             "Positional ids are RawDocument UUIDs in this mode",
    )
    reparse_parser.add_argument(
        "--force-reparse",
        action="store_true",
        help="With --from-raw: also re-process raw files that already have a "
             "document, not just the ones missing one",
    )
    reparse_parser.add_argument(
        "--from-stored",
        action="store_true",
        help="Rebuild document.text from the stored DoclingDocument parser output "
             "instead of re-running the parser (no GPU, no re-fetch), then "
             "regenerate summary and chunks so they can't drift from the new text",
    )
    reparse_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be re-parsed and exit",
    )
    reparse_parser.add_argument(
        "--parser-name",
        help="Parser to use (e.g., 'kb-mcp', 'marker'). Default: uses KB_PARSER env var or 'kb-mcp'",
    )
    reparse_parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Skip summary generation",
    )
    reparse_parser.add_argument(
        "--no-embed",
        action="store_true",
        help="Skip chunking and embedding",
    )
    reparse_parser.set_defaults(func=cmd_reparse)


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
        "--parser-name",
        help="Parser to use (e.g., 'kb-mcp', 'marker'). Default: uses KB_PARSER env var or 'kb-mcp'"
    )
    ingest_parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Skip automatic summary generation (title, gist, summary)"
    )
    ingest_parser.add_argument(
        "--summary-include-metadata",
        action="store_true",
        help="Include structured metadata extraction in summary generation"
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
    ingest_parser.add_argument(
        "--meta-json",
        help="Metadata JSON object to attach to the document"
    )
    ingest_parser.add_argument(
        "--meeting",
        dest="meeting",
        action="store_true",
        help="Shortcut profile for meeting uploads (source=MeetingTranscripts if unset)"
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
