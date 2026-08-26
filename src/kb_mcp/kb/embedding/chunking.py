"""Chunking utilities for embedding module."""

import logging
from typing import List, Optional, Dict, Any, Set
from .db_models import Chunk, Document

logger = logging.getLogger(__name__)

# Body labels that are page furniture rather than document content.
# Docling's Markdown export omits them, so they have no position to
# anchor to — and they're typically short, repeated strings ("3 / 41",
# a running title) that would match spuriously if searched for.
_PAGE_FURNITURE_LABELS = frozenset({"page_header", "page_footer"})

# A string shorter than this is a weak anchor; don't let one match more
# than `_MAX_SHORT_JUMP` characters ahead of the cursor. See `locate()`.
_MIN_ANCHOR_LEN = 24
_MAX_SHORT_JUMP = 400

# Characters Docling's Markdown export backslash-escapes. The body tree
# stores the *raw* text, so a node containing any of these never matches
# the export literally — on a maths-heavy document that is 40% of all
# elements, every one of which used to be unlocatable. See `_find_loose`.
_MD_ESCAPABLE = set(r"\`*_{}[]()#+-.!|<>~")


def _find_loose(haystack: str, needle: str, start: int):
    """Find `needle` in `haystack` at/after `start`, tolerating escaping.

    Exact search first — that is the common case and stays O(n). Only if
    it fails do we retry character by character, letting a backslash in
    the haystack stand in for nothing in the needle (`lh\_d0` matching
    `lh_d0`), since Docling's Markdown export escapes characters the body
    tree stores raw.

    Returns `(start, end)` of the match, or None. The end is returned
    rather than recomputed by the caller because escaping makes the
    matched span longer than the needle.
    """
    pos = haystack.find(needle, start)
    if pos != -1:
        return (pos, pos + len(needle))
    if not any(c in _MD_ESCAPABLE for c in needle):
        # Nothing the export would have escaped — a genuine absence.
        return None

    n = len(haystack)
    first = needle[0]
    i = start
    while i < n:
        # Cheap gate: a candidate must start with the needle's first
        # character, or with a backslash escaping it.
        if haystack[i] != first and not (
            haystack[i] == "\\" and i + 1 < n and haystack[i + 1] == first
        ):
            i += 1
            continue
        end = _match_loose_at(haystack, needle, i)
        if end != -1:
            return (i, end)
        i += 1
    return None


def _match_loose_at(haystack: str, needle: str, start: int) -> int:
    """Try to match `needle` at exactly `start`. Returns end offset or -1."""
    h, k = start, 0
    n, m = len(haystack), len(needle)
    while k < m:
        if h >= n:
            return -1
        hc, nc = haystack[h], needle[k]
        if hc == nc:
            h += 1
            k += 1
        elif hc == "\\" and h + 1 < n and haystack[h + 1] == nc:
            # Escaped in the export, bare in the tree.
            h += 2
            k += 1
        else:
            return -1
    return h


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

def chunk_from_docling_json(
    document: Document,
    target_tokens: Optional[int] = None,
    min_chunk_tokens: int = 30,
    parser_output: Optional[Dict[str, Any]] = None,
    prepend_section_path: bool = True,
    prepend_gist: bool = True,
) -> List[Dict[str, Any]]:
    """Chunk `document.text` at section boundaries found in the persisted
    DoclingDocument (`document.parser_output["body"]["children"]`).

    The DoclingDocument tree decides *where* to cut; the text itself is
    always a slice of `document.text`. That is the key property: chunk
    text is never reconstructed from the body tree, so it can't drift from
    the canonical text the way a reconstruction does (the tree's raw node
    text differs from the Markdown export in list spacing, escaping, and
    `html.unescape()` handling). Slicing also makes
    `char_start_index` / `char_end_index` exact by construction rather
    than something to recover by fuzzy matching afterwards.

    The emitted chunks **partition** `document.text`: chunk N+1 starts
    exactly where chunk N ended. Anything the walker doesn't itself track —
    tables, images, formula placeholders, headings it declined to open a
    chunk on — is therefore carried inside a neighbouring chunk rather than
    dropped. Coverage is 100% by construction, which is what a reader
    highlighting a chunk expects and what keeps retrieval from having blind
    spots the walk happened to step over.

    Behaviour:
      * Each body text element is located in `document.text` once, with a
        forward-only cursor so repeated boilerplate can't match backwards.
        Located elements decide *where* to cut; they never decide what the
        text is, which is always `doc_text[start:end]`.
      * Section headers (`section_header` / `title`) update an in-memory
        header stack — used to populate `section_path` on every chunk that
        follows. Header text is not part of the chunk *body* for the
        tiny-fragment floor, but it does sit inside the emitted slice; the
        contextual prefix is built separately at embed time by
        `Chunk.embed_text()`.
      * Group bodies (lists, key-value pairs, …) are walked inline.
      * Each emitted chunk records `page_start` (min `prov.page_no` of the
        contributing texts), `page_end` (max), and `body_self_refs` (list
        of crefs that contributed). `bbox` is left None until per-chunk
        bbox-merging is needed — page-level alone is enough for citations.
      * **Sizing is done in the embedding model's own tokenizer** (see
        :mod:`kb_mcp.kb.embedding.budget`), not tiktoken, and the budget is
        the model's window minus `[CLS]`/`[SEP]` minus the
        `Section:` / `Context:` prefix `embed_text()` will prepend. Past
        that window the encoder silently truncates, so an over-budget chunk
        is embedded as its opening fragment and nothing else. The budget
        moves on its own when the model or the gist changes.
      * **Tiny-fragment merging**: a flush whose accumulated body is below
        ``min_chunk_tokens`` does NOT emit — it keeps the accumulator alive
        so the fragment merges with the next chunk. Stops the corpus from
        getting polluted with single-token chunks like '2' / 'by' / '17'
        that escape between section boundaries.
      * **Oversized-slice splitting**: the cap is enforced on the slice
        that is actually emitted, so untracked interstitial content counts
        toward it. An over-cap slice is split on paragraph (`\\n\\n`) then
        sentence-ish (`. `) boundaries, with a self-calibrating character
        cut as the last resort; offsets stay absolute throughout, so every
        piece is still an exact slice and the partition still holds.
      * Returns chunk dicts ready for the existing chunk-save path.
        chunk_strategy = `"section"`. `token_length` is in the embedding
        model's units.

    Falls back to `[]` (the dispatch then uses the Markdown token chunker)
    when there's no DoclingDocument payload or no `document.text` to slice.

    Args:
        document: Source document whose `parser_output` holds a
            DoclingDocument payload and whose `text` is the Markdown the
            chunks are sliced from.
        target_tokens: Soft per-chunk budget, in embedding-model tokens.
            Clamped to the content budget — it can only ask for *smaller*
            chunks, never for ones the embedder would truncate. Default
            None = use the whole content budget.
        min_chunk_tokens: Floor — chunks below this token count merge
            with the next instead of emitting. Default 30.
        parser_output: Pre-loaded DoclingDocument payload. Pass this when
            the caller already resolved it (see `_load_parser_output`) —
            reading `document.parser_output` here would lazy-load, which
            raises on a Document detached from its session.
        prepend_section_path: Whether `embed_text()` will prepend
            `Section: …`. Mirrored here so its token cost is reserved.
        prepend_gist: Likewise for `Context: {document.gist}`.
    """
    from ..db_models import is_docling_document

    if parser_output is None:
        parser_output = _load_parser_output(document)
    if not is_docling_document(parser_output):
        # `parser_output` is parser-agnostic; this walker only understands
        # DoclingDocument payloads (self-identified via `schema_name`).
        # Anything else falls back through the dispatch's 0-chunks path.
        return []

    doc_text = getattr(document, "text", None)
    if not doc_text:
        # Nothing to slice. The dispatch falls back to the token chunker.
        return []

    from .budget import get_embed_budget

    embed_budget = get_embed_budget(
        gist=getattr(document, "gist", None),
        prepend_section_path=prepend_section_path,
        prepend_gist=prepend_gist,
    )
    tok = embed_budget.count

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
    # Accumulator: (start, end, self_ref, page) per located element. Spans
    # are character offsets into doc_text, never text fragments.
    acc_items: List[tuple[int, int, str, Optional[int]]] = []
    acc_pages: Set[int] = set()
    # Partition cursor: the next chunk starts here, wherever the walk's
    # tracked elements happen to begin. Everything between `emitted_upto`
    # and `acc_end` rides inside the emitted slice, so untracked content
    # (tables, figures, formula placeholders) is carried, not dropped.
    emitted_upto = 0
    # Furthest character the accumulator reaches.
    acc_end = 0
    # Tokens of doc_text[emitted_upto:acc_end], accumulated as deltas so
    # each character is tokenized exactly once — and so interstitial
    # content counts toward the budget, because it counts at embed time.
    acc_tokens = 0
    # Body tokens only — excludes the section heading that opens the chunk.
    # The tiny-fragment guard measures this, so a heading with no body of
    # its own (a parent heading, or one whose text the export dropped)
    # can't satisfy the floor on its own and emit as a junk chunk.
    acc_body_tokens = 0
    # Forward-only search cursor into doc_text.
    cursor = 0
    # Whether any element was located at all. If none was, this walker has
    # no opinion on where the cuts belong and the caller should fall back
    # to the Markdown token chunker rather than take a blind split.
    located_any = False

    def section_path() -> Optional[str]:
        return " > ".join(t for _, t in header_stack) or None

    def budget_cap() -> int:
        """Max body tokens for a chunk in the current section.

        The embedding window, minus `[CLS]`/`[SEP]`, minus the prefix
        `embed_text()` will prepend — optionally tightened by the caller's
        `target_tokens`, which can only ask for *smaller* chunks, never for
        ones the embedder would truncate.
        """
        cap = embed_budget.content_budget(section_path())
        if target_tokens is not None:
            cap = min(cap, target_tokens)
        return max(1, cap)

    def emit(start: int, end: int) -> None:
        """Emit doc_text[start:end], split so no piece exceeds the cap."""
        for p_start, p_end, p_tokens in _split_span(start, end, budget_cap()):
            if p_end <= p_start:
                continue
            refs: List[str] = []
            pages: Set[int] = set()
            for s, e, ref, page in acc_items:
                if s >= p_end or e <= p_start:
                    continue
                if ref not in refs:
                    refs.append(ref)
                if page is not None:
                    pages.add(page)
            if not pages:
                # Interstitial-only piece — a table or figure with no
                # tracked text of its own. Attribute it to the nearest
                # neighbour that does have a page, so citations still land
                # somewhere rather than on None.
                before = [p for s, e, _r, p in acc_items
                          if p is not None and e <= p_start]
                after = [p for s, e, _r, p in acc_items
                         if p is not None and s >= p_end]
                if before:
                    pages = {before[-1]}
                elif after:
                    pages = {after[0]}
            page_list = sorted(pages)
            out.append({
                "text": doc_text[p_start:p_end],
                "chunk_index": len(out),
                "char_start_index": p_start,
                "char_end_index": p_end,
                "token_length": p_tokens,
                "section_path": section_path(),
                "page_start": page_list[0] if page_list else None,
                "page_end": page_list[-1] if page_list else None,
                "bbox": None,
                "body_self_refs": refs,
                "chunk_strategy": "section",
                "meta": {"source": "docling_body_walk"},
            })

    def _split_span(start: int, end: int, cap: int) -> List[tuple[int, int, int]]:
        """Split a span into (start, end, tokens) pieces of at most `cap`.

        Cuts on paragraph (`\\n\\n`) then sentence-ish (`. `) boundaries,
        with a character cut as the last resort. Offsets stay absolute
        into doc_text throughout, so every piece remains an exact slice.
        """
        tokens = tok(doc_text[start:end])
        if tokens <= cap:
            return [(start, end, tokens)]

        def cut(sub_start: int, sub_end: int, sep: str) -> List[tuple[int, int]]:
            """Split [sub_start, sub_end) on `sep`, keeping absolute offsets."""
            pieces: List[tuple[int, int]] = []
            pos = sub_start
            while True:
                nxt = doc_text.find(sep, pos, sub_end)
                if nxt == -1:
                    if pos < sub_end:
                        pieces.append((pos, sub_end))
                    break
                # Keep the separator with the preceding piece for ". ",
                # drop it for paragraph breaks.
                piece_end = nxt + (len(sep) if sep == ". " else 0)
                if piece_end > pos:
                    pieces.append((pos, piece_end))
                pos = nxt + len(sep)
            return pieces

        def char_cut(c_start: int, c_end: int, c_tokens: int
                     ) -> List[tuple[int, int, int]]:
            """Last resort: cut by characters, measuring as it goes.

            A fixed chars-per-token constant is wrong by a wide margin on
            the content that reaches here (markdown tables, long formulas,
            CJK), and overshooting means silent truncation. Calibrating on
            the span average isn't enough either: a table's `---|---`
            separator row tokenizes several times denser than its prose
            rows, so an average-derived step still overshoots locally.
            Measure every piece and shrink the step until it fits.
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
                    t = tok(doc_text[pos:ce])
                    if t <= cap or take <= 1:
                        break
                    # Overshot: rescale on what this stretch actually
                    # measured rather than halving blindly.
                    take = max(1, min(take - 1, int(take * cap / t * 0.9)))
                pieces.append((pos, ce, t))
                pos = ce
                # Carry the corrected step forward — the density that
                # tripped us up usually continues for a while.
                step = max(1, take)
            return pieces

        result: List[tuple[int, int, int]] = []
        for p_start, p_end in cut(start, end, "\n\n"):
            p_tokens = tok(doc_text[p_start:p_end])
            if p_tokens <= cap:
                if p_tokens:
                    result.append((p_start, p_end, p_tokens))
                continue
            # Paragraph still too big — sentence-ish.
            buf_start: Optional[int] = None
            buf_end = p_start
            buf_tokens = 0
            for s_start, s_end in cut(p_start, p_end, ". "):
                s_tokens = tok(doc_text[s_start:s_end])
                if s_tokens > cap:
                    # A single sentence over the cap (markdown tables with
                    # no ". " are the usual trigger). Checked before the
                    # buffer test so it can't be appended to a buffer and
                    # emitted whole: flush, then cut by characters.
                    if buf_start is not None:
                        result.append((buf_start, buf_end, buf_tokens))
                        buf_start, buf_tokens = None, 0
                    result.extend(char_cut(s_start, s_end, s_tokens))
                elif buf_start is not None and buf_tokens + s_tokens > cap:
                    result.append((buf_start, buf_end, buf_tokens))
                    buf_start, buf_end, buf_tokens = s_start, s_end, s_tokens
                else:
                    if buf_start is None:
                        buf_start = s_start
                    buf_end = s_end
                    buf_tokens += s_tokens
            if buf_start is not None:
                result.append((buf_start, buf_end, buf_tokens))
        return result

    def flush(*, force: bool = False) -> bool:
        """Emit everything pending as one chunk. Returns True if flushed.

        The emitted slice runs from `emitted_upto` — where the previous
        chunk stopped — to the end of the accumulator, so the chunks
        partition doc_text and nothing between two tracked elements is
        lost. Leading and trailing whitespace is trimmed off the slice
        itself, but `emitted_upto` still advances past it, so the trim
        can't reintroduce a gap.

        If the accumulated *body* is below ``min_chunk_tokens``, keep the
        accumulator alive (don't reset) so the fragment merges with
        whatever comes next, and return False. ``force=True`` overrides
        this — used at end-of-doc, where there is no "next".

        A body-less slice (a heading whose section holds only a figure or
        a formula, or a run of consecutive headings) is held back the same
        way, so it merges into the next chunk instead of emitting 3 tokens
        of title on its own. ``force`` still overrides that at end-of-doc
        — otherwise a document whose only anchorable elements are headings
        would emit nothing at all.
        """
        nonlocal acc_tokens, acc_body_tokens, emitted_upto
        if acc_end <= emitted_upto:
            return True
        if not force and acc_body_tokens < min_chunk_tokens:
            return False
        start, end = emitted_upto, acc_end
        while start < end and doc_text[start].isspace():
            start += 1
        while end > start and doc_text[end - 1].isspace():
            end -= 1
        if start < end:
            emit(start, end)
        emitted_upto = acc_end
        acc_items.clear()
        acc_pages.clear()
        acc_tokens = 0
        acc_body_tokens = 0
        return True

    def locate(node_text: str) -> Optional[tuple[int, int]]:
        """Find `node_text` in doc_text at or after the cursor.

        Refuses to match a short string far ahead of the cursor. Docling's
        Markdown export drops page furniture, so a stray element like a
        `'1 / 41'` page number has no legitimate position — but it *will*
        match some digits deep inside a later figure description, dragging
        the cursor thousands of characters forward and making every
        subsequent element unfindable. Requiring a long string for a long
        jump keeps a spurious match from destroying the rest of the walk.
        """
        nonlocal cursor, located_any
        if not node_text:
            return None
        span = _find_loose(doc_text, node_text, cursor)
        if span is None:
            return None
        pos, end = span
        if len(node_text) < _MIN_ANCHOR_LEN and (pos - cursor) > _MAX_SHORT_JUMP:
            return None
        cursor = end
        located_any = True
        return (pos, cursor)

    def add_text(self_ref: str, text_node: Dict[str, Any]) -> None:
        nonlocal acc_tokens, acc_body_tokens, acc_end
        body_text = (text_node.get("text") or "").strip()
        if not body_text:
            return
        span = locate(body_text)
        if span is None:
            # Element not present in the Markdown export at/after the
            # cursor (escaping differences, or content the export omits).
            # Skip it rather than guessing a position — the surrounding
            # elements still bound the chunk, so its text is not lost: the
            # partition carries it inside a neighbouring chunk anyway.
            return

        prov = text_node.get("prov") or []
        page: Optional[int] = None
        if prov and isinstance(prov[0], dict):
            page = prov[0].get("page_no")

        p_start, p_end = span
        # Cost of extending the emitted slice out to this element — which
        # includes any untracked content sitting between the two, because
        # that content is in the slice and reaches the embedder too.
        delta = tok(doc_text[acc_end:p_end]) if p_end > acc_end else 0
        if delta and acc_end > emitted_upto and acc_tokens + delta > budget_cap():
            # Appending would overflow the embedding window: close the
            # chunk here instead. A non-forcing flush, so an under-floor
            # fragment merges forward rather than emitting as a scrap —
            # and `flush` leaves acc_end untouched, so `delta` still holds.
            flush()
        acc_items.append((p_start, p_end, self_ref, page))
        if page is not None:
            acc_pages.add(page)
        if p_end > acc_end:
            acc_tokens += delta
            acc_body_tokens += delta
            acc_end = p_end

    for child in children:
        cref = child.get("cref") if isinstance(child, dict) else None
        if not cref:
            continue
        if cref.startswith("#/tables/") or cref.startswith("#/pictures/"):
            # Not tracked as contributing elements, but they still fall
            # inside a chunk's slice when they sit between two tracked
            # elements — which is what we want.
            continue
        if cref.startswith("#/texts/"):
            t = texts_by_ref.get(cref) or {}
            label = t.get("label")
            txt = (t.get("text") or "").strip()
            if not txt:
                continue
            if label in _PAGE_FURNITURE_LABELS:
                # Running headers/footers and page numbers: dropped by the
                # Markdown export, so there's nothing to anchor to, and
                # they're not content worth retrieving.
                continue
            if label in ("section_header", "title"):
                # Section boundary. Close the section that's ending while
                # `header_stack` still describes it — section_path is read
                # at flush time, so the stack must not be touched first.
                emitted = flush()
                level = t.get("level") or 1
                if not emitted and acc_body_tokens > 0:
                    # The closing section had body text but too little to
                    # stand alone. Roll it up into the parent rather than
                    # letting it inherit the *next* header's section_path
                    # (which would mislabel it as belonging to a sibling it
                    # isn't part of): drop the closing header from the
                    # stack, then force-flush so the held text is
                    # attributed one level higher.
                    if header_stack:
                        header_stack.pop()
                    flush(force=True)
                elif not emitted:
                    # Nothing but the heading itself was accumulated — a
                    # parent heading, or one whose body the export dropped.
                    # Don't force-emit a heading-only chunk: leave the span
                    # pending so it opens the next chunk instead. Its text
                    # is kept (`emitted_upto` is untouched, so it lands in
                    # the next slice) and the heading also reaches every
                    # chunk beneath it via `section_path`.
                    pass

                # Now retarget the stack at the new heading's depth.
                while header_stack and header_stack[-1][0] >= level:
                    header_stack.pop()
                header_stack.append((level, txt))

                # The heading opens the next chunk: seed the accumulator
                # with its span so the section title is part of
                # `chunk.text`. That matters beyond display — the reranker
                # scores (query, chunk.text) pairs and would otherwise
                # never see the heading. The span is widened back over the
                # Markdown `#` markers so the slice is the whole heading
                # line. Heading tokens count toward `acc_tokens` (the embed
                # budget) but deliberately NOT toward `acc_body_tokens`, so
                # a heading with no body of its own can't satisfy the
                # tiny-fragment floor and emit as a junk chunk.
                header_span = locate(txt)
                if header_span is not None:
                    h_start, h_end = header_span
                    line_start = doc_text.rfind("\n", 0, h_start) + 1
                    if doc_text[line_start:h_start].strip("# ") == "":
                        h_start = line_start
                    h_page: Optional[int] = None
                    prov = t.get("prov") or []
                    if prov and isinstance(prov[0], dict):
                        h_page = prov[0].get("page_no")
                    acc_items.append((h_start, h_end, cref, h_page))
                    if h_page is not None:
                        acc_pages.add(h_page)
                    if h_end > acc_end:
                        acc_tokens += tok(doc_text[acc_end:h_end])
                        acc_end = h_end
            else:
                add_text(cref, t)
        elif cref.startswith("#/groups/"):
            g = groups_by_ref.get(cref) or {}
            for sub in g.get("children") or []:
                sub_cref = sub.get("cref") if isinstance(sub, dict) else None
                if not sub_cref or not sub_cref.startswith("#/texts/"):
                    continue
                sub_t = texts_by_ref.get(sub_cref) or {}
                if not (sub_t.get("text") or "").strip():
                    continue
                if sub_t.get("label") in _PAGE_FURNITURE_LABELS:
                    continue
                add_text(sub_cref, sub_t)

    if not located_any:
        # Not a single element could be anchored in the Markdown export.
        # The walk has no idea where the section boundaries are, so return
        # nothing and let the dispatch fall back to the token chunker
        # rather than emit one blind character-split of the whole document.
        return []

    # End-of-doc: extend to the last character so the tail isn't dropped,
    # then force-emit whatever's left (there's no "next" to merge into).
    if len(doc_text) > acc_end:
        acc_tokens += tok(doc_text[acc_end:])
        acc_end = len(doc_text)
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
            chunk_dicts = chunk(document.text, strategy="tokens", config=config)
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
