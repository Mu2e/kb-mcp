"""Chunking utilities for embedding module."""

import logging
from typing import List, Optional, Dict, Any, Set
from .db_models import Chunk, Document

logger = logging.getLogger(__name__)


def chunk_from_docling_json(
    document: Document,
    target_tokens: int = 1000,
    min_chunk_tokens: int = 30,
    hard_max_tokens: int = 1500,
) -> List[Dict[str, Any]]:
    """Walk the persisted DoclingDocument (`document.parser_output["body"]
    ["children"]`) in reading order and emit chunks with page-anchored
    provenance.

    Behaviour:
      * Tables and pictures are **skipped** — they are first-class records
        emitted by the parser; chunking them again would re-introduce the
        duplication step 1 just removed.
      * Section headers (`section_header` / `title`) update an in-memory
        header stack — used to populate `section_path` on every chunk that
        follows. The header text itself is *not* added to chunk text; the
        contextual prefix is built at embed time by `Chunk.embed_text()`.
      * Group bodies (lists, key-value pairs, …) are expanded inline; list
        items get a leading `- `.
      * Each emitted chunk records `page_start` (min `prov.page_no` of the
        contributing texts), `page_end` (max), and `body_self_refs` (list
        of crefs that contributed). `bbox` is left None until per-chunk
        bbox-merging is needed — page-level alone is enough for citations.
      * Soft target: `target_tokens`. Flush early when the next text span
        would push the chunk over budget.
      * **Tiny-fragment merging** (2026-04-27): a flush whose
        accumulated text is below ``min_chunk_tokens`` does NOT emit — it
        keeps the accumulator alive so the fragment merges with the next
        chunk. Stops the corpus from getting polluted with single-token
        chunks like '2' / 'by' / '17' that escape between section
        boundaries.
      * **Oversized-span splitting** (2026-04-27): a single
        body span larger than ``hard_max_tokens`` is split on paragraph
        (`\\n\\n`) then sentence-ish (`. `) boundaries until each piece
        fits. MiniLM truncates at 256 tokens; chunks > ~1500 tokens lose
        most content at embed time, so the hard cap is load-bearing.
      * Returns chunk dicts ready for the existing chunk-save path.
        chunk_strategy = `"docling_json"`.

    Args:
        document: Source document whose `parser_output` holds a
            DoclingDocument payload.
        target_tokens: Soft per-chunk budget. Flush early if appending
            the next span would exceed it. Default 1000.
        min_chunk_tokens: Floor — chunks below this token count merge
            with the next instead of emitting. Default 30.
        hard_max_tokens: Hard ceiling — single body spans larger than
            this are split internally. Default 1500.
    """
    from ..db_models import is_docling_document

    parser_output = getattr(document, "parser_output", None)
    if not is_docling_document(parser_output):
        # `parser_output` is parser-agnostic; this walker only understands
        # DoclingDocument payloads (self-identified via `schema_name`).
        # Anything else falls back through the dispatch's 0-chunks path.
        return []

    from ...chunking import count_tokens

    body = (parser_output.get("body") or {})
    children = body.get("children") or []
    texts_by_ref = {
        t.get("self_ref") or f"#/texts/{i}": t
        for i, t in enumerate(parser_output.get("texts") or [])
    }
    groups_by_ref = {
        g.get("self_ref") or f"#/groups/{i}": g
        for i, g in enumerate(parser_output.get("groups") or [])
    }

    out: List[Dict[str, Any]] = []
    header_stack: List[tuple[int, str]] = []
    acc_parts: List[str] = []
    acc_self_refs: List[str] = []
    acc_pages: Set[int] = set()
    acc_tokens = 0

    def section_path() -> Optional[str]:
        return " > ".join(t for _, t in header_stack) or None

    def flush(*, force: bool = False) -> None:
        """Emit the accumulator as a chunk.

        If the accumulator has fewer than ``min_chunk_tokens`` tokens,
        keep it alive (don't reset) so the fragment merges with whatever
        comes next. ``force=True`` overrides this — used at end-of-doc
        and on section-boundary flushes when keeping a tiny tail open
        would mis-attribute its section_path to the next section.
        """
        nonlocal acc_parts, acc_self_refs, acc_pages, acc_tokens
        text = "\n\n".join(p for p in acc_parts if p).strip()
        if not text:
            acc_parts = []
            acc_self_refs = []
            acc_pages = set()
            acc_tokens = 0
            return
        # Tiny-fragment guard: keep accumulating unless forced.
        if not force and acc_tokens < min_chunk_tokens:
            return
        pages = sorted(p for p in acc_pages if p is not None)
        out.append({
            "text": text,
            "chunk_index": len(out),
            "char_start_index": None,
            "char_end_index": None,
            "token_length": acc_tokens,
            "section_path": section_path(),
            "page_start": pages[0] if pages else None,
            "page_end": pages[-1] if pages else None,
            "bbox": None,
            "body_self_refs": list(acc_self_refs),
            "chunk_strategy": "docling_json",
            "meta": {"source": "docling_body_walk"},
        })
        acc_parts = []
        acc_self_refs = []
        acc_pages = set()
        acc_tokens = 0

    def _split_oversized(rendered: str, ntokens: int) -> List[tuple[str, int]]:
        """Split a single oversized span into pieces ≤ hard_max_tokens.

        Strategy: paragraph (`\\n\\n`) first; if any piece is still too
        big, split that piece on sentence-ending punctuation; if still
        too big, hard-truncate every ``hard_max_tokens`` characters as
        a last resort. Returns a list of (text, token_count) pairs.
        """
        if ntokens <= hard_max_tokens:
            return [(rendered, ntokens)]
        pieces: List[str] = []
        # First pass: paragraphs
        for para in rendered.split("\n\n"):
            para = para.strip()
            if not para:
                continue
            ptokens = count_tokens(para)
            if ptokens <= hard_max_tokens:
                pieces.append(para)
            else:
                # Second pass: sentence-ish — split on ". " preserving
                # the period.
                buf: List[str] = []
                buf_tokens = 0
                for sentence in para.split(". "):
                    sentence = sentence.strip()
                    if not sentence:
                        continue
                    if not sentence.endswith("."):
                        sentence = sentence + "."
                    stokens = count_tokens(sentence)
                    # Check oversized FIRST. Otherwise a giant sentence
                    # arriving when buf is non-empty falls into the first
                    # branch (which puts it into buf) and gets emitted whole
                    # at end-of-loop, defeating the cap. Markdown tables
                    # with no `. ` separators are the typical trigger.
                    if stokens > hard_max_tokens:
                        if buf:
                            pieces.append(" ".join(buf))
                            buf = []
                            buf_tokens = 0
                        char_cap = hard_max_tokens * 4
                        for i in range(0, len(sentence), char_cap):
                            pieces.append(sentence[i:i + char_cap])
                    elif buf_tokens + stokens > hard_max_tokens and buf:
                        pieces.append(" ".join(buf))
                        buf = [sentence]
                        buf_tokens = stokens
                    else:
                        buf.append(sentence)
                        buf_tokens += stokens
                if buf:
                    pieces.append(" ".join(buf))
        return [(p, count_tokens(p)) for p in pieces]

    def add_text(self_ref: str, text_node: Dict[str, Any], formatted: Optional[str] = None) -> None:
        nonlocal acc_tokens
        body_text = (text_node.get("text") or "").strip()
        if not body_text:
            return
        rendered = formatted if formatted is not None else body_text
        ntokens = count_tokens(rendered)

        # Page-no provenance (same for all sub-pieces of an oversized span).
        prov = text_node.get("prov") or []
        page: Optional[int] = None
        if prov and isinstance(prov[0], dict):
            page = prov[0].get("page_no")

        # Internal split for oversized spans.
        pieces = _split_oversized(rendered, ntokens)
        for piece_text, piece_tokens in pieces:
            # Flush early if appending would exceed budget.
            if acc_tokens > 0 and acc_tokens + piece_tokens > target_tokens:
                flush(force=True)
            acc_parts.append(piece_text)
            acc_self_refs.append(self_ref)
            if page is not None:
                acc_pages.add(page)
            acc_tokens += piece_tokens

    for child in children:
        cref = child.get("cref") if isinstance(child, dict) else None
        if not cref:
            continue
        if cref.startswith("#/tables/") or cref.startswith("#/pictures/"):
            continue
        if cref.startswith("#/texts/"):
            t = texts_by_ref.get(cref) or {}
            label = t.get("label")
            txt = (t.get("text") or "").strip()
            if not txt:
                continue
            if label in ("section_header", "title"):
                # Section boundary: flush, then update the header stack.
                flush()
                level = t.get("level") or 1
                while header_stack and header_stack[-1][0] >= level:
                    header_stack.pop()
                header_stack.append((level, txt))
                # Header text itself does not enter chunk.text — the
                # section_path carries it via embed_text()'s prefix.
            else:
                add_text(cref, t)
        elif cref.startswith("#/groups/"):
            g = groups_by_ref.get(cref) or {}
            for sub in g.get("children") or []:
                sub_cref = sub.get("cref") if isinstance(sub, dict) else None
                if not sub_cref or not sub_cref.startswith("#/texts/"):
                    continue
                sub_t = texts_by_ref.get(sub_cref) or {}
                sub_txt = (sub_t.get("text") or "").strip()
                if not sub_txt:
                    continue
                if sub_t.get("label") == "list_item":
                    add_text(sub_cref, sub_t, formatted=f"- {sub_txt}")
                else:
                    add_text(sub_cref, sub_t)

    # End-of-doc: force-emit whatever's left so the final fragment isn't
    # silently dropped.
    flush(force=True)
    return out


def chunk_document(
    document: Document,
    chunk_strategy: Optional[str] = None,
    config: Optional[dict] = None,
    session=None,
) -> List[Chunk]:
    """
    Chunk a document and save chunks to the database.

    Args:
        document: Document object to chunk (must have text field)
        chunk_strategy: Optional chunking strategy ("tokens", "slide", or "summary").
                       If None, reads from CHUNK_STRATEGY env var, defaults to "tokens".
                       "summary" creates a single chunk from document.summary field.
        config: Optional chunking configuration. Supports embedding context flags:
                - prepend_section_path: If True, prepend section_path before embedding (default: True)
                - prepend_gist: If True, prepend document gist before embedding (default: True)
                - Other strategy-specific parameters (see chunking.chunk() for details)
        session: Optional database session. If None, creates a new session.

    Returns:
        List of Chunk objects (saved to database)

    Raises:
        ValueError: If document has no text content or if strategy="summary" and document has no summary

    Example:
        ```python
        from kb_mcp.kb import Document
        from kb_mcp.kb.embedding import chunk_document

        doc = Document.from_dict({
            "source_id": "test",
            "doc_id": "123",
            "text": "Long document text..."
        })
        # Save document first
        with get_db_session() as session:
            session.add(doc)
            session.commit()
            chunks = chunk_document(doc, session=session)
        ```
    """
    import os
    from ...chunking import chunk
    from ..database import get_db_session
    from ..database import get_db_session
    from .db_models import ChunkStrategy

    if not document.text:
        raise ValueError("Document must have text content to chunk")

    if not document.id:
        raise ValueError("Document must be saved to database first (must have id)")

    # Auto-route by doc_type for the special single-chunk-per-record types.
    # Section / table / image records carry self-contained text (caption +
    # nearby_text + grid for tables, title + body for sections, VLM
    # description for images) and would only get noisier if token-chunked
    # against the parent's text. The dedicated strategies emit one chunk
    # per record, matching how the SYNTHESIS / TABLE / FIGURE doc_type_boost
    # was designed to retrieve them. Override even if a chunk_strategy was
    # passed — it's almost always the global default ("tokens") leaking in
    # via the user's env var, and silently producing duplicates is worse
    # than ignoring an inapplicable override.
    if document.doc_type in ("image", "section", "table"):
        if chunk_strategy is not None and chunk_strategy != document.doc_type:
            logger.debug(
                f"Overriding chunk_strategy={chunk_strategy!r} -> {document.doc_type!r} "
                f"for doc_type={document.doc_type!r} (one chunk per record)"
            )
        chunk_strategy = document.doc_type
    elif chunk_strategy is None:
        from ...config import get_embedding_config
        embedding_config = get_embedding_config()
        chunk_strategy = embedding_config['chunk_strategy']

    # Extract embedding context flags from config (default to True)
    if config is None:
        config = {}
    prepend_section_path = config.get("prepend_section_path", True)
    prepend_gist = config.get("prepend_gist", True)

    # Handle "summary" strategy specially - create single chunk from document summary
    if chunk_strategy == "summary":
        if not document.summary:
            raise ValueError("Document must have summary to use 'summary' chunking strategy. Call generate_summary() first.")

        # Calculate token length for the summary
        from ...chunking import count_tokens
        summary_tokens = count_tokens(document.summary)

        # Create a single chunk dict with the summary
        chunk_dicts = [{
            "text": document.summary,
            "chunk_index": 0,
            "char_start_index": None,
            "char_end_index": None,
            "token_length": summary_tokens,
            "chunk_strategy": "summary",
            "meta": {"source": "document.summary"},
        }]
    elif chunk_strategy in ("image", "section", "table"):
        # Single-chunk-per-record strategies — section / table / image
        # records carry self-contained text and would only get noisier if
        # token-chunked against the parent. The dedicated strategies emit
        # one chunk per record (and one only) for short content.
        #
        # However: long sections / wide tables / verbose VLM descriptions
        # can blow past MiniLM's 256-token embed cap, where most of the
        # content becomes invisible to retrieval. A production-corpus audit
        # found `section` chunks up to 15,501 tokens. For long content
        # we sub-chunk internally on paragraph / sentence boundaries
        # while keeping the same chunk_strategy label so the record
        # still routes through the SYNTHESIS / TABLE / FIGURE doc_type
        # boost paths.
        if document.doc_type != chunk_strategy:
            raise ValueError(
                f"'{chunk_strategy}' chunking strategy requires "
                f"doc_type='{chunk_strategy}', got '{document.doc_type}'."
            )

        if not document.text:
            raise ValueError(
                f"{chunk_strategy} document must have text to use "
                f"'{chunk_strategy}' chunking strategy."
            )

        from ...chunking import count_tokens

        text_parts = []
        if prepend_gist and document.parent_id:
            from ..database import get_db_session
            temp_session = session if session else get_db_session().__enter__()
            try:
                parent = temp_session.query(Document).filter(
                    Document.id == document.parent_id
                ).first()
                if parent and parent.gist:
                    text_parts.append(f"Document context: {parent.gist}")
            finally:
                if session is None:
                    temp_session.close()
        text_parts.append(document.text)

        chunk_text = "\n\n".join(text_parts)
        total_tokens = count_tokens(chunk_text)

        # Hard cap for record-level chunks. Below this we keep the record
        # as one chunk (cheap, preserves "one chunk per record" semantic
        # that retrieval boost code relies on). Above it, sub-chunk.
        SECTION_CHUNK_HARD_MAX = 1000

        if total_tokens <= SECTION_CHUNK_HARD_MAX:
            chunk_dicts = [{
                "text": chunk_text,
                "chunk_index": 0,
                "char_start_index": None,
                "char_end_index": None,
                "token_length": total_tokens,
                "chunk_strategy": chunk_strategy,
                "meta": {"source": f"{chunk_strategy}_record"},
            }]
        else:
            # Sub-chunk on paragraph (`\n\n`), then sentence (`. `), then
            # hard char-cut as last resort. Each piece keeps the same
            # chunk_strategy so the doc_type_boost retrieval code still
            # lifts these chunks. meta records that this record was split
            # so the consumer can reassemble if needed.
            pieces: List[str] = []
            buf: List[str] = []
            buf_tokens = 0
            for para in chunk_text.split("\n\n"):
                para = para.strip()
                if not para:
                    continue
                ptokens = count_tokens(para)
                if buf_tokens + ptokens > SECTION_CHUNK_HARD_MAX and buf:
                    pieces.append("\n\n".join(buf))
                    buf = []
                    buf_tokens = 0
                if ptokens > SECTION_CHUNK_HARD_MAX:
                    # Single paragraph too big — split on sentence-ish.
                    if buf:
                        pieces.append("\n\n".join(buf))
                        buf = []
                        buf_tokens = 0
                    sent_buf: List[str] = []
                    sent_buf_tokens = 0
                    for sentence in para.split(". "):
                        sentence = sentence.strip()
                        if not sentence:
                            continue
                        if not sentence.endswith("."):
                            sentence = sentence + "."
                        stokens = count_tokens(sentence)
                        # Check oversized FIRST — otherwise a huge sentence
                        # arriving when sent_buf is non-empty would be put
                        # into sent_buf via the first branch and emitted
                        # whole at end-of-loop, defeating the hard cap.
                        # Markdown tables (pipe-delimited rows with no
                        # `. `) are the typical trigger.
                        if stokens > SECTION_CHUNK_HARD_MAX:
                            # Flush whatever's accumulated, then hard-cut
                            # the giant sentence by characters. ~4
                            # char/token rule of thumb.
                            if sent_buf:
                                pieces.append(" ".join(sent_buf))
                                sent_buf = []
                                sent_buf_tokens = 0
                            char_cap = SECTION_CHUNK_HARD_MAX * 4
                            for i in range(0, len(sentence), char_cap):
                                pieces.append(sentence[i:i + char_cap])
                        elif sent_buf_tokens + stokens > SECTION_CHUNK_HARD_MAX and sent_buf:
                            pieces.append(" ".join(sent_buf))
                            sent_buf = [sentence]
                            sent_buf_tokens = stokens
                        else:
                            sent_buf.append(sentence)
                            sent_buf_tokens += stokens
                    if sent_buf:
                        pieces.append(" ".join(sent_buf))
                else:
                    buf.append(para)
                    buf_tokens += ptokens
            if buf:
                pieces.append("\n\n".join(buf))

            chunk_dicts = []
            for i, piece in enumerate(pieces):
                chunk_dicts.append({
                    "text": piece,
                    "chunk_index": i,
                    "char_start_index": None,
                    "char_end_index": None,
                    "token_length": count_tokens(piece),
                    "chunk_strategy": chunk_strategy,
                    "meta": {
                        "source": f"{chunk_strategy}_record",
                        "split_index": i,
                        "split_total": len(pieces),
                    },
                })
    else:
        # Regular chunking. PDF parents may opt into the
        # DoclingDocument-aware chunker via CHUNK_FROM_DOCLING_JSON — it
        # walks the persisted parser_output["body"] in reading order,
        # populates page_start / page_end / body_self_refs on every chunk,
        # and respects section boundaries. Falls back to the Markdown
        # token-windowing path when the flag is off, the doc isn't a
        # text record, or parser_output holds no DoclingDocument payload.
        if config is None:
            config = {}
        config["prepend_gist"] = prepend_gist
        config["prepend_section_path"] = prepend_section_path

        from ...config import get_embedding_config
        from ..db_models import is_docling_document
        use_docling = (
            get_embedding_config().get("chunk_from_docling_json", False)
            and document.doc_type == "text"
            and is_docling_document(getattr(document, "parser_output", None))
        )

        if use_docling:
            target_tokens = config.get("chunk_size", 1000)
            chunk_dicts = chunk_from_docling_json(document, target_tokens=target_tokens)
            if not chunk_dicts:
                # Empty body or all tables/pictures; fall back to markdown
                # so we don't end up with a parent text doc that has zero
                # chunks.
                logger.info(
                    f"chunk_from_docling_json produced 0 chunks for {document.id}; "
                    f"falling back to Markdown {chunk_strategy} chunker"
                )
                chunk_dicts = chunk(document.text, strategy=chunk_strategy, config=config)
        else:
            chunk_dicts = chunk(document.text, strategy=chunk_strategy, config=config)


    # Determine if we need to create a session
    should_close = session is None

    with get_db_session(session) as session:
        # Get all strategy names from chunks
        strategy_names = {chunk_dict["chunk_strategy"] for chunk_dict in chunk_dicts}

        # Delete existing chunks for this document with any of these strategies
        # This ensures re-chunking with the same strategy replaces old chunks
        if strategy_names:
            deleted_count = session.query(Chunk).filter(
                Chunk.document_id == document.id,
                Chunk.chunk_strategy.in_(strategy_names)
            ).delete(synchronize_session=False)

            if deleted_count > 0:
                strategy_list = ", ".join(sorted(strategy_names))
                logger.info(f"Replacing {deleted_count} existing chunk(s) for document {document.id[:8]}... (strategies: {strategy_list})")
                session.flush()  # Ensure deletions are committed before inserts

        # Ensure ChunkStrategy records exist for all chunk strategies
        for strategy_name in strategy_names:
            # Check if strategy already exists
            existing_strategy = session.query(ChunkStrategy).filter(
                ChunkStrategy.strategy == strategy_name
            ).first()

            if not existing_strategy:
                # Create new ChunkStrategy record
                # Get meta from first chunk with this strategy
                chunk_meta = next(
                    (cd["meta"] for cd in chunk_dicts if cd["chunk_strategy"] == strategy_name),
                    {}
                )
                # Add embedding context flags to meta
                chunk_meta["prepend_section_path"] = prepend_section_path
                chunk_meta["prepend_gist"] = prepend_gist

                new_strategy = ChunkStrategy(
                    strategy=strategy_name,
                    meta=chunk_meta,
                )
                session.add(new_strategy)

        # Flush to ensure strategies are in database before creating chunks
        session.flush()

        # Create Chunk objects from dictionaries.
        # Architecture: chunk.text is the CLEAN text shown to users; the
        # contextual prefix (Document/Section/Context) is built dynamically
        # at embed time by Chunk.embed_text(). token_length reflects the
        # embed-time text so the chunker's budget targets what the embedding
        # model actually sees.
        chunks = []
        for chunk_dict in chunk_dicts:
            is_special_strategy = chunk_dict.get("chunk_strategy") in ["summary", "image"]

            # Build the embed-time text (prefix + clean) just to count its tokens
            # so we can budget for the embedder. The prefix is NOT stored on
            # chunk.text — Chunk.embed_text() rebuilds it on demand.
            embed_parts = []
            context_added = False
            if not is_special_strategy and prepend_section_path and chunk_dict.get("section_path"):
                embed_parts.append(f"Section: {chunk_dict['section_path']}")
                context_added = True
            if not is_special_strategy and prepend_gist and document.gist:
                embed_parts.append(f"Context: {document.gist}")
                context_added = True
            embed_parts.append(chunk_dict["text"])

            if context_added:
                from ...chunking import count_tokens
                final_token_length = count_tokens("\n\n".join(embed_parts))
            else:
                final_token_length = chunk_dict.get("token_length")

            # Merge chunk_dict with document_id (exclude meta since it's in ChunkStrategy now)
            chunk_data = {
                "document_id": document.id,
                "text": chunk_dict["text"],  # Clean text — prefix added at embed time
                "chunk_index": chunk_dict["chunk_index"],
                "char_start_index": chunk_dict.get("char_start_index"),
                "char_end_index": chunk_dict.get("char_end_index"),
                "token_length": final_token_length,
                "section_path": chunk_dict.get("section_path"),  # Store original section_path
                "chunk_strategy": chunk_dict.get("chunk_strategy"),
                # Page-anchored provenance. Populated only by the
                # DoclingDocument body walker (chunk_strategy
                # "docling_json"); legacy chunks leave these NULL.
                "page_start": chunk_dict.get("page_start"),
                "page_end": chunk_dict.get("page_end"),
                "bbox": chunk_dict.get("bbox"),
                "body_self_refs": chunk_dict.get("body_self_refs"),
            }
            chunk_obj = Chunk.from_dict(chunk_data)
            chunks.append(chunk_obj)

        # Save chunks to database
        _save_chunks_to_session(chunks, session, commit=False)

        # Commit if we own the session
        if should_close:
            session.commit()
            # Refresh and expunge chunks so they can be used after session closes
            for chunk in chunks:
                session.refresh(chunk)
                

        return chunks


def _save_chunks_to_session(chunks: List[Chunk], session, commit: bool = False) -> None:
    """Helper to save chunks to session and detach them."""
    for chunk_obj in chunks:
        session.add(chunk_obj)
    session.flush()  # Flush to get IDs

    # Refresh to ensure all attributes are loaded
    #for chunk_obj in chunks:
    #    session.refresh(chunk_obj)

    if commit:
        session.commit()

    # Detach chunks from session so they can be used outside
    #for chunk_obj in chunks:
    #    


def get_chunk_strategies(document_id: Optional[str] = None, session=None) -> List[Dict[str, Any]]:
    """Get chunking strategies, optionally filtered by document.

    Args:
        document_id: Optional UUID of the document. If None, returns all strategies.
        session: Optional database session. If None, creates a new session.

    Returns:
        List of dictionaries with:
        - strategy: str - The chunking strategy identifier
        - meta: dict - Strategy configuration parameters
        - count: int - Number of chunks using this strategy (for the document if document_id provided)
        - created_time: str - ISO format timestamp
    """
    from .db_models import Chunk, ChunkStrategy
    from ..database import get_db_session
    from ..database import get_db_session
    from sqlalchemy import func

    def _query(sess):
        # Build base query
        query = sess.query(
            ChunkStrategy.strategy,
            ChunkStrategy.meta,
            ChunkStrategy.created_time,
            func.count(Chunk.id).label('count')
        ).outerjoin(
            Chunk, ChunkStrategy.strategy == Chunk.chunk_strategy
        )
        
        # Filter by document if provided
        if document_id is not None:
            query = query.filter(Chunk.document_id == document_id)
        
        # Group and order
        results = query.group_by(
            ChunkStrategy.strategy,
            ChunkStrategy.meta,
            ChunkStrategy.created_time
        ).order_by(ChunkStrategy.strategy).all()

        return [
            {
                "strategy": strategy,
                "meta": meta if meta else {},
                "count": count,
                "created_time": created_time.isoformat() if created_time else None,
            }
            for strategy, meta, created_time, count in results
        ]

    if session is not None:
        return _query(session)
    else:
        with get_db_session() as sess:
            return _query(sess)


def get_chunks(
    document_id: Optional[str] = None,
    chunk_strategy: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    session=None,
):
    """Get chunks, optionally filtered by document and/or strategy.

    Args:
        document_id: Optional UUID of the document. If None, returns all chunks.
        chunk_strategy: Optional filter for specific chunking strategy
        limit: Optional limit on number of chunks returned (for pagination)
        offset: Optional offset for pagination (number of chunks to skip)
        session: Optional database session. If None, creates a new session.

    Returns:
        If session is provided: List of Chunk objects (attached to session)
        If session is None: List of chunk dictionaries (from Chunk.to_dict())

    Examples:
        ```python
        # Get all chunks for a document
        chunks = get_chunks(document_id="abc-123")

        # Get all chunks across all documents
        all_chunks = get_chunks()

        # Get first 100 chunks with pagination
        page1 = get_chunks(limit=100, offset=0)
        page2 = get_chunks(limit=100, offset=100)

        # Get chunks with specific strategy across all documents
        token_chunks = get_chunks(chunk_strategy="tokens_1000_200")
        ```
    """
    from .db_models import Chunk
    from ..database import get_db_session
    from ..database import get_db_session

    def _query(sess, return_dicts=False):
        # Build query
        query = sess.query(Chunk)

        # Apply document filter if provided
        if document_id is not None:
            query = query.filter(Chunk.document_id == document_id)

        # Apply strategy filter if provided
        if chunk_strategy:
            query = query.filter(Chunk.chunk_strategy == chunk_strategy)

        # Order by document_id and chunk_index for consistent pagination
        if document_id is not None:
            # When filtering by document, order by chunk_index only
            query = query.order_by(Chunk.chunk_index)
        else:
            # When querying all chunks, order by document_id then chunk_index
            query = query.order_by(Chunk.document_id, Chunk.chunk_index)

        # Apply pagination if specified
        if offset is not None:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)

        chunks = query.all()

        if return_dicts:
            # Convert to dictionaries while session is still open
            return [chunk.to_dict() for chunk in chunks]
        else:
            # Return actual Chunk objects
            return chunks

    if session is not None:
        # Return Chunk objects when session is provided
        return _query(session, return_dicts=False)
    else:
        # Return dictionaries when no session provided
        with get_db_session() as sess:
            return _query(sess, return_dicts=True)


def drop_chunks(
    document_id: str,
    chunk_strategy: Optional[str] = None,
    session=None,
) -> int:
    """Drop chunks for a specific document.

    Args:
        document_id: UUID of the document
        chunk_strategy: Optional filter for specific chunking strategy.
                       If provided, only drops chunks with this strategy.
                       If None, drops ALL chunks for the document.
        session: Optional database session. If None, creates a new session.

    Returns:
        Number of chunks deleted

    Example:
        ```python
        # Drop all chunks for a document
        count = drop_chunks(document_id="abc-123")
        print(f"Deleted {count} chunks")

        # Drop only chunks with specific strategy
        count = drop_chunks(document_id="abc-123", chunk_strategy="tokens_1000_200")
        print(f"Deleted {count} chunks with strategy tokens_1000_200")
        ```
    """
    from .db_models import Chunk
    from ..database import get_db_session
    from ..database import get_db_session

    def _delete(sess):
        # Build query
        query = sess.query(Chunk).filter(Chunk.document_id == document_id)

        # Apply strategy filter if provided
        if chunk_strategy:
            query = query.filter(Chunk.chunk_strategy == chunk_strategy)

        # Delete and return count
        deleted_count = query.delete(synchronize_session=False)
        sess.flush()
        return deleted_count

    if session is not None:
        # Use provided session (don't commit)
        return _delete(session)
    else:
        # Create own session and commit
        with get_db_session() as sess:
            count = _delete(sess)
            sess.commit()
            return count
