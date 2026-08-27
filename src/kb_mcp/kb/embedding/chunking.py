"""Chunking utilities for embedding module."""

import logging
from typing import Any, Callable, Dict, List, Optional

from ...chunking import base_strategy
from .db_models import Chunk, Document
from .section_chunker import chunk_from_docling_json

logger = logging.getLogger(__name__)

__all__ = ["chunk_from_docling_json"]  # re-exported for existing callers/tests


def _load_parser_output(document: Document, session=None) -> Optional[Dict[str, Any]]:
    """Return `document`'s structured parser output, or None.

    `Document.parser_output` is a lazy-loading relationship accessor: on a
    detached instance (e.g. one returned by `kb.documents.get()`, which
    closes its session before returning) touching it raises
    DetachedInstanceError. Query the payload by document id instead, so
    chunking works regardless of how the caller obtained the Document.
    """
    from ..db_models import DocumentParserOutput

    # Fast path: already loaded (or attached to a live session).
    try:
        return document.parser_output
    except Exception:
        pass

    if not document.id:
        return None
    try:
        from ..database import get_db_session
        with get_db_session(session) as s:
            ref = (
                s.query(DocumentParserOutput)
                .filter(DocumentParserOutput.document_id == document.id)
                .first()
            )
            return ref.output if ref is not None else None
    except Exception as e:
        logger.warning(
            f"Could not load parser_output for document {document.id}: {e}"
        )
        return None


def enforce_embed_budget(
    chunk_dicts: List[Dict[str, Any]],
    document: Document,
    prepend_section_path: bool = True,
    prepend_gist: bool = True,
) -> List[Dict[str, Any]]:
    """Re-split any token chunk that would still overflow the encoder.

    The token chunker slices by *tiktoken* offsets while the encoder reads
    *word-pieces*, and no constant converts between them: measured over 150
    real documents at `chunk_size=137`, the worst chunk came to 1085
    word-pieces — 7.9x, not the 1.32x that normal prose shows. Tables,
    formulas and URLs tokenize far denser than the ratio predicts, and the
    encoder truncates silently, so a size chosen by ratio is a size that
    quietly loses content on exactly the documents worth indexing.

    Sizing is therefore a *target* and this is the guarantee: measure what
    `embed_text()` will actually hand the encoder, and re-split anything over
    with `_split_to_budget`, which measures every piece as it cuts.

    A no-op for chunks already inside the budget, which is nearly all of them.
    """
    from .budget import get_embed_budget

    budget = get_embed_budget(
        gist=getattr(document, "gist", None),
        prepend_section_path=prepend_section_path,
        prepend_gist=prepend_gist,
    )
    text = document.text or ""

    out: List[Dict[str, Any]] = []
    for cd in chunk_dicts:
        start, end = cd.get("char_start_index"), cd.get("char_end_index")
        cap = budget.content_budget(cd.get("section_path"))

        # Without offsets there is no way to re-slice the source; leave it be
        # (the strategies that emit offset-less chunks size themselves).
        if start is None or end is None:
            out.append(cd)
            continue

        measured = budget.count(text[start:end])
        if measured <= cap:
            # Record the measured value even when nothing is re-split. The
            # token chunker reports tiktoken counts and re-split pieces report
            # word-pieces, so leaving it alone made `token_length` mean
            # different units for different rows of the same document.
            cd["token_length"] = measured
            out.append(cd)
            continue

        pieces = _split_to_budget(text, start, end, cap, budget.count)
        logger.debug(
            "Chunk %s of document %s measured over the embedding budget; "
            "re-split into %d",
            cd.get("chunk_index"), (document.id or "?")[:8], len(pieces),
        )
        for p_start, p_end, p_tokens in pieces:
            piece = dict(cd)
            piece.update({
                "text": text[p_start:p_end],
                "char_start_index": p_start,
                "char_end_index": p_end,
                "token_length": p_tokens,
            })
            out.append(piece)

    for i, cd in enumerate(out):
        cd["chunk_index"] = i
    return out


def apply_env_chunk_defaults(
    config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Fill `chunk_size` / `chunk_overlap` from CHUNK_SIZE / CHUNK_OVERLAP.

    So the env vars apply to library calls, not only to the CLI's
    `--chunk-size` / `--chunk-overlap`. An explicit value in `config` wins.

    Used by both `chunk_document` and `resolve_strategy_name`: the strategy
    name encodes these two numbers, so predicting the stored name and
    actually writing it have to start from the same config, or
    `chunk_and_embed_all` would look for `tokens_1000_100` while the chunker
    wrote whatever CHUNK_SIZE said.
    """
    config = dict(config or {})

    from ...config import get_embedding_config
    emb_cfg = get_embedding_config()

    if config.get("chunk_size") is None:
        # CHUNK_SIZE unset -> size to the embedding window, not the library's
        # model-agnostic 1000. Left at 1000 against a 256-window encoder,
        # every token chunk is silently truncated to its first quarter.
        from .budget import token_chunk_size
        config["chunk_size"] = emb_cfg.get("chunk_size") or token_chunk_size()
    # Left absent rather than None when unconfigured, so the chunker's
    # "derive 10% of chunk_size" default still applies.
    if config.get("chunk_overlap") is None:
        config.pop("chunk_overlap", None)
        if emb_cfg.get("chunk_overlap") is not None:
            config["chunk_overlap"] = emb_cfg["chunk_overlap"]
    return config


def resolve_strategy_name(
    requested: str, config: Optional[Dict[str, Any]] = None
) -> str:
    """The name a requested strategy is *stored* under.

    `summary` and `section` are what callers ask for — `CHUNK_STRATEGY`, the
    web UI dropdown, `chunk_and_embed(chunk_strategy="section")`. What lands in
    the database is `summary_{window}` / `section_{window}`, because both are
    split to fit the embedding model's window: under a 256-token encoder and a
    512-token one the same name would label two incompatible chunkings of the
    same document, with no way to tell them apart or keep both.

    `section` carried a bare name until the move to a 512-token encoder made
    the collision real: 256-window section chunks and 512-window ones sat in
    the same table under one label, and re-chunking silently replaced the old
    set instead of standing beside it.

    Everything else delegates to the pure-tiktoken resolver in
    `kb_mcp.chunking`, which already encodes its own parameters
    (`tokens_1000_200`). That one cannot resolve a window — it deliberately
    has no `kb` imports and so no access to the embedder.
    """
    from ...chunking.chunking import get_strategy_name

    if requested in ("summary", "section"):
        from .budget import get_embed_budget
        return f"{requested}_{get_embed_budget().window}"
    return get_strategy_name(requested, apply_env_chunk_defaults(config))


# Cut levels, coarsest first. A dense Markdown list (references, bullet
# points) has no blank lines between items — paragraph-level ("\n\n") sees
# the whole list as one oversized "paragraph" — but each item is its own
# line and comfortably under any real cap, so a line-level cut resolves it
# without ever falling through to sentence-splitting. That matters because
# sentence-splitting on bare ". " is not real sentence detection — it also
# fires on "Nucl. Instr. Meth. ", severing a citation between its
# abbreviations — so anything line-structured should never reach it.
_CUT_LEVELS: tuple[str, ...] = ("\n\n", "\n", ". ")


def _split_to_budget(text: str, start: int, end: int, cap: int,
                     count: Callable[[str], int]) -> List[tuple[int, int, int]]:
    """Split `text[start:end]` into (start, end, tokens) pieces of at most `cap`.

    Cuts on paragraph, then line, then sentence-ish boundaries — see
    `_CUT_LEVELS` — falling back to a measured character cut for a single
    unit that is still over cap at the finest level (a markdown table row
    with no ". " is the usual trigger). Pieces that fit are packed together
    up to `cap` at whichever level resolved them, so a small piece (a lone
    heading, a short paragraph, a short list item) merges into its
    neighbour instead of emitting alone. Offsets stay absolute into `text`
    throughout, so every piece remains an exact slice.
    """
    tokens = count(text[start:end])
    if tokens <= cap:
        return [(start, end, tokens)]
    return _pack_split(text, start, end, cap, count, 0)


def _cut(text: str, sub_start: int, sub_end: int, sep: str) -> List[tuple[int, int]]:
    """Split text[sub_start:sub_end) on `sep`, keeping absolute offsets.

    The separator is kept with the preceding piece for "\\n" and ". " (so
    a line still ends with its own newline / period), dropped for "\\n\\n"
    (a blank line is not content worth keeping on either side).
    """
    pieces: List[tuple[int, int]] = []
    pos = sub_start
    while True:
        nxt = text.find(sep, pos, sub_end)
        if nxt == -1:
            if pos < sub_end:
                pieces.append((pos, sub_end))
            break
        piece_end = nxt + (len(sep) if sep != "\n\n" else 0)
        if piece_end > pos:
            pieces.append((pos, piece_end))
        pos = nxt + len(sep)
    return pieces


def _char_cut(text: str, c_start: int, c_end: int, c_tokens: int, cap: int,
              count: Callable[[str], int]) -> List[tuple[int, int, int]]:
    """Last resort: cut by characters, measuring as it goes.

    A fixed chars-per-token constant is wrong by a wide margin on the
    content that reaches here (markdown tables, long formulas, CJK), and
    overshooting means silent truncation. Calibrating on the span average
    isn't enough either: a table's `---|---` separator row tokenizes
    several times denser than its prose rows, so an average-derived step
    still overshoots locally. Measure every piece and shrink the step
    until it fits.
    """
    pieces: List[tuple[int, int, int]] = []
    pos = c_start
    # Seed from the span average, with 10% of headroom.
    per_token = max(1.0, (c_end - c_start) / max(1, c_tokens))
    step = max(1, int(cap * per_token * 0.9))
    while pos < c_end:
        take = step
        while True:
            ce = min(pos + take, c_end)
            t = count(text[pos:ce])
            if t <= cap or take <= 1:
                break
            # Overshot: rescale on what this stretch actually measured
            # rather than halving blindly.
            take = max(1, min(take - 1, int(take * cap / t * 0.9)))
        pieces.append((pos, ce, t))
        pos = ce
        # Carry the corrected step forward — the density that tripped us
        # up usually continues for a while.
        step = max(1, take)
    return pieces


def _pack_split(text: str, start: int, end: int, cap: int,
                 count: Callable[[str], int], level: int
                 ) -> List[tuple[int, int, int]]:
    """Cut text[start:end) on `_CUT_LEVELS[level]`, packing fitting pieces
    together and recursing into any piece still over cap at the next level.

    Uniform at every level: a unit that fits joins the pending pack if the
    merge still fits under `cap`, otherwise the pack flushes as its own
    piece and this unit starts a new one. A unit that doesn't fit on its
    own — even alone — is split at the next finer level (or character-cut,
    past the last level), and *that* result's first/last pieces are what
    the pack test runs against, so a merge never skips the level in
    between: a heading immediately before an oversized paragraph, say,
    still gets a chance to merge into that paragraph's first sub-piece.
    """
    if level >= len(_CUT_LEVELS):
        tokens = count(text[start:end])
        return _char_cut(text, start, end, tokens, cap, count)

    result: List[tuple[int, int, int]] = []
    pack_start: Optional[int] = None
    pack_end = 0

    def flush_pack() -> None:
        nonlocal pack_start, pack_end
        if pack_start is not None:
            result.append((pack_start, pack_end, count(text[pack_start:pack_end])))
            pack_start, pack_end = None, 0

    for u_start, u_end in _cut(text, start, end, _CUT_LEVELS[level]):
        u_tokens = count(text[u_start:u_end])
        if u_tokens <= cap:
            if not u_tokens:
                continue
            # Measure the merged slice rather than summing: merging
            # re-includes whatever the separator's un-kept characters were
            # (blank lines at paragraph level), so a sum would drift under
            # the real cost and overflow the window.
            if pack_start is not None and count(text[pack_start:u_end]) > cap:
                flush_pack()
            if pack_start is None:
                pack_start = u_start
            pack_end = u_end
            continue
        # This unit alone is still over cap — split it at the next finer
        # level. Its first sub-piece gets the same pack-merge chance any
        # other fitting unit would: a pending pack from before this unit
        # (e.g. a lone heading) can still land inside the same chunk as
        # the start of what follows, rather than being forced out alone
        # just because the *next* unit as a whole didn't fit.
        sub_pieces = _pack_split(text, u_start, u_end, cap, count, level + 1)
        if not sub_pieces:
            continue
        first_start, first_end, first_tokens = sub_pieces[0]
        if (pack_start is not None
                and count(text[pack_start:first_end]) <= cap):
            pack_end = first_end
            flush_pack()
        else:
            flush_pack()
            result.append((first_start, first_end, first_tokens))
        result.extend(sub_pieces[1:])
    flush_pack()
    return result


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
    # Table / image records carry self-contained text (caption + nearby_text
    # + grid for tables, VLM description for images) and would only get
    # noisier if token-chunked against the parent's text. The dedicated
    # strategies emit one chunk per record, matching how the TABLE / FIGURE
    # doc_type_boost was designed to retrieve them. Override even if a
    # chunk_strategy was passed — it's almost always the global default
    # ("tokens") leaking in via the user's env var, and silently producing
    # duplicates is worse than ignoring an inapplicable override.
    #
    # doc_type="section" is legacy: the parser no longer emits separate
    # section Documents (section-level retrieval is now the "section"
    # chunk_strategy walking a doc_type="text" document's own body), but
    # existing section-doctype rows from before that change remain in the
    # database. They still need the same one-chunk-per-record, no-prefix
    # treatment as table/image — NOT the new Docling-body-walker meaning of
    # chunk_strategy="section" (which only applies to doc_type="text").
    # Without this, chunk_and_embed_all's "no chunk of this strategy yet"
    # query picks these rows up (nothing excludes doc_type="section") and,
    # with CHUNK_STRATEGY=section configured, they'd otherwise fall through
    # to plain token-chunking and get a spurious extra chunk.
    if document.doc_type in ("image", "table", "section"):
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

    config = apply_env_chunk_defaults(config)

    prepend_section_path = config.get("prepend_section_path", True)
    prepend_gist = config.get("prepend_gist", True)

    # Handle "summary" strategy specially - chunk the document summary.
    if chunk_strategy == "summary":
        if not document.summary:
            raise ValueError("Document must have summary to use 'summary' chunking strategy. Call generate_summary() first.")

        from .budget import get_embed_budget

        # `Chunk.embed_text()` returns a summary chunk's text verbatim — no
        # Section:/Context: prefix — so the whole window is the summary's.
        summary_budget = get_embed_budget(
            prepend_section_path=False, prepend_gist=False
        )
        cap = summary_budget.content_budget()

        # Summaries used to be stored as one chunk however long they were,
        # so anything past the window was embedded as nothing: 84% of the
        # corpus's summaries were over it. Split to the budget instead, in
        # the embedding model's own token units.
        spans = _split_to_budget(
            document.summary, 0, len(document.summary), cap, summary_budget.count
        )
        summary_strategy = resolve_strategy_name("summary")
        chunk_dicts = []
        for i, (s_start, s_end, s_tokens) in enumerate(spans):
            chunk_dicts.append({
                "text": document.summary[s_start:s_end],
                "chunk_index": i,
                # Offsets stay NULL. They would index into `document.summary`,
                # not `document.text`, and the search layer's positioned-chunk
                # branch would slice the wrong string with them. Search returns
                # the whole summary for a summary hit anyway, so per-piece
                # offsets buy nothing.
                "char_start_index": None,
                "char_end_index": None,
                "token_length": s_tokens,
                "chunk_strategy": summary_strategy,
                # No per-split fields here: chunk `meta` is not persisted on
                # the row, and the first chunk's meta is what seeds the
                # *global* ChunkStrategy row — so a split count from one
                # document would end up describing the strategy itself.
                "meta": {"source": "document.summary"},
            })
    elif chunk_strategy in ("image", "table", "section") and document.doc_type == chunk_strategy:
        # Single-chunk-per-record strategies — table / image / legacy
        # section records carry self-contained text and would only get
        # noisier if token-chunked against the parent. The dedicated
        # strategies emit one chunk per record (and one only) for short
        # content.
        #
        # The `document.doc_type == chunk_strategy` guard is what keeps
        # this from also catching chunk_strategy="section" requested for a
        # doc_type="text" document — that's the *new* meaning of "section"
        # (the Docling-body-walker chunking a text document's own body by
        # section boundaries, handled by the `use_section_walker` branch
        # further below) and must not be confused with this legacy,
        # doc_type="section"-only, one-chunk-per-record path.
        #
        # However: wide tables / verbose VLM descriptions can blow past
        # the embedding model's window, where most of the content becomes
        # invisible to retrieval. For long content we sub-chunk internally
        # on paragraph / sentence boundaries while keeping the same
        # chunk_strategy label so the record still routes through the
        # TABLE / FIGURE doc_type boost paths.

        if not document.text:
            raise ValueError(
                f"{chunk_strategy} document must have text to use "
                f"'{chunk_strategy}' chunking strategy."
            )

        from .budget import get_embed_budget

        # These records embed their own text verbatim (`embed_text()`
        # returns it unchanged for the image/table strategies), so the
        # whole window is theirs — no Section:/Context: prefix to reserve.
        record_budget = get_embed_budget(
            prepend_section_path=False, prepend_gist=False
        )
        count_tokens = record_budget.count

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

        # Hard cap for record-level chunks, in the embedding model's own
        # token units: exactly what fits before the encoder truncates.
        # Below it we keep the record as one chunk (cheap, and preserves
        # the "one chunk per record" semantic the retrieval boost code
        # relies on). Above it, sub-chunk.
        RECORD_CHUNK_HARD_MAX = record_budget.content_budget()

        if total_tokens <= RECORD_CHUNK_HARD_MAX:
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
                if buf_tokens + ptokens > RECORD_CHUNK_HARD_MAX and buf:
                    pieces.append("\n\n".join(buf))
                    buf = []
                    buf_tokens = 0
                if ptokens > RECORD_CHUNK_HARD_MAX:
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
                        if stokens > RECORD_CHUNK_HARD_MAX:
                            # Flush whatever's accumulated, then hard-cut
                            # the giant sentence by characters. ~4
                            # char/token rule of thumb.
                            if sent_buf:
                                pieces.append(" ".join(sent_buf))
                                sent_buf = []
                                sent_buf_tokens = 0
                            # Calibrate the cut on this sentence's own
                            # chars-per-token rather than a flat ~4: the
                            # content that reaches here (markdown tables,
                            # formulas, CJK) is exactly where a constant
                            # is most wrong, and overshooting truncates.
                            per_token = max(1.0, len(sentence) / max(1, stokens))
                            char_cap = max(
                                1, int(RECORD_CHUNK_HARD_MAX * per_token * 0.9)
                            )
                            for i in range(0, len(sentence), char_cap):
                                pieces.append(sentence[i:i + char_cap])
                        elif sent_buf_tokens + stokens > RECORD_CHUNK_HARD_MAX and sent_buf:
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
        # Regular chunking. chunk_strategy="section" walks the persisted
        # parser_output["body"] in reading order (chunk_from_docling_json),
        # emitting one chunk per section — populating page_start /
        # page_end / body_self_refs / section_path and respecting section
        # boundaries — instead of plain token windows. Falls back to the
        # Markdown token chunker when the doc isn't a text record, or
        # parser_output holds no DoclingDocument payload (or the walk
        # produces zero chunks), so a parent text doc never ends up with
        # no chunks at all.
        if config is None:
            config = {}
        config["prepend_gist"] = prepend_gist
        config["prepend_section_path"] = prepend_section_path

        from ..db_models import is_docling_document
        parser_output = None
        if chunk_strategy == "section" and document.doc_type == "text":
            parser_output = _load_parser_output(document, session=session)
        use_section_walker = (
            chunk_strategy == "section"
            and document.doc_type == "text"
            and is_docling_document(parser_output)
        )

        if use_section_walker:
            # No chunk_size default: the walker sizes itself from the
            # embedding window. An explicit config value can only tighten
            # that, never push chunks past what the encoder will read.
            chunk_dicts = chunk_from_docling_json(
                document,
                target_tokens=config.get("chunk_size"),
                parser_output=parser_output,
                prepend_section_path=prepend_section_path,
                prepend_gist=prepend_gist,
            )
            if not chunk_dicts:
                logger.info(
                    f"chunk_from_docling_json produced 0 chunks for {document.id}; "
                    f"falling back to Markdown tokens chunker"
                )
                chunk_dicts = chunk(document.text, strategy="tokens", config=config)
        elif chunk_strategy == "section":
            # Requested section chunking but no walkable Docling payload
            # (e.g. non-PDF text). Fall back to plain token chunking.
            #
            # Warn, don't whisper: CHUNK_STRATEGY=section reads as "this
            # corpus is chunked on structure", and silently degrading to
            # token windows for some documents makes the config a lie. The
            # chunks are still window-sized, so this costs structure and
            # section_path provenance rather than correctness — but which
            # documents took this path should be visible in the logs.
            logger.warning(
                "chunk_strategy='section' requested for document %s (%s) but "
                "it has no DoclingDocument parser_output; falling back to the "
                "token chunker — no section boundaries or section_path for "
                "this document",
                document.id[:8], document.source_type or "unknown type",
            )
            chunk_dicts = chunk(document.text, strategy="tokens", config=config)
        else:
            chunk_dicts = chunk(document.text, strategy=chunk_strategy, config=config)

        # The token chunker sizes in tiktoken; the encoder reads word-pieces.
        # Nothing above guarantees the result fits, so measure and re-split.
        if chunk_dicts and chunk_dicts[0].get("chunk_strategy", "").startswith("tokens"):
            chunk_dicts = enforce_embed_budget(
                chunk_dicts, document, prepend_section_path, prepend_gist
            )


    # Determine if we need to create a session
    should_close = session is None

    with get_db_session(session) as session:
        # Get all strategy names from chunks
        strategy_names = {chunk_dict["chunk_strategy"] for chunk_dict in chunk_dicts}

        # Delete existing chunks for this document with any of these strategies
        # This ensures re-chunking with the same strategy replaces old chunks.
        #
        # Window-tagged names also supersede their un-tagged predecessor:
        # writing `summary_256` while a legacy `summary` row survives would
        # leave the document with two summary chunk sets, the old one holding
        # the truncated text this change exists to fix.
        replaced_names = strategy_names | {base_strategy(n) for n in strategy_names}

        if replaced_names:
            deleted_count = session.query(Chunk).filter(
                Chunk.document_id == document.id,
                Chunk.chunk_strategy.in_(replaced_names)
            ).delete(synchronize_session=False)

            if deleted_count > 0:
                strategy_list = ", ".join(sorted(replaced_names))
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
            # Must agree with Chunk.embed_text()'s special list, since this
            # block exists only to count what that method will produce:
            # base_strategy() so `summary_256` still counts as special, and
            # "table" included because embed_text() returns table text
            # verbatim too — counting a prefix it never adds overstated
            # token_length on every table chunk.
            is_special_strategy = base_strategy(
                chunk_dict.get("chunk_strategy") or ""
            ) in ("summary", "image", "table")

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
                from .budget import get_embed_budget
                final_token_length = get_embed_budget().count(
                    "\n\n".join(embed_parts)
                )
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
