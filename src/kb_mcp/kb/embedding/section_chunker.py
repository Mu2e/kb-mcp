"""Section-aware chunking of a parsed document's Markdown text.

Split out of `chunking.py` because this walker is the single largest and
most intricate piece of that module, and it doesn't participate in the
general chunk save/query/dispatch API the rest of the module provides.

Earlier versions of this walker read `document.parser_output` — the
DoclingDocument JSON tree — to find section boundaries, then searched for
each tree element's text inside `document.text` (the Markdown export) to
recover its position. That anchor search was most of the complexity here:
tolerating Markdown escaping, refusing short-string matches that could jump
the cursor arbitrarily far ahead, guarding against the tree's parent/child
refs cycling back on themselves. It also meant this walker only understood
Docling's particular JSON schema, and broke silently on a *nested* body
tree (HTML source documents put a whole section under its heading as
children, rather than the flat list PDFs produce) — a walk that only
opened one level of `#/groups/*` found 2 of 47 elements on a real wiki
page.

None of that is necessary. `document.text` already carries the section
structure directly, as Markdown `#` headings — the tree was never the only
place it lived, just the only place this walker looked. Scanning the text
itself for headings finds every one of them regardless of how deeply the
source reader nested its JSON, needs no fuzzy matching because there is
nothing to correlate, and works on any Markdown-producing parser, not only
Docling's.
"""

import bisect
import logging
import re
from typing import Any, Dict, List, Optional

from .db_models import Chunk, Document

logger = logging.getLogger(__name__)

# A heading is a *candidate* cut point, not a mandatory one. Cutting on
# every heading gives a chunk per heading regardless of how little sits
# under it, which on finely-sectioned documents means a corpus of 30-token
# fragments: a bare finding with the figure it describes and the
# implication drawn from it split across three chunks, each too partial to
# answer anything on its own.
#
# So a heading closes the current chunk only once that chunk carries at
# least this fraction of the embedding budget. Below it, the heading joins
# the chunk in progress and accumulation continues.
#
# Not 1.0: packing to the window makes a chunk whose embedding is the
# average of several topics and sharply matches none of them. Retrieval
# wants a coherent passage somewhat under the window, so this leaves room
# to finish a section rather than stopping mid-thought.
_SECTION_CUT_FRACTION = 0.6

# `#{1,6} text` at the start of a line — ATX-style Markdown headings, which
# is what `DoclingDocument.export_to_markdown()` (and every other parser in
# this codebase) emits for `section_header` / `title` elements. Requires at
# least one space after the `#`s so `#include` or a stray `#` in prose
# doesn't count.
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)

# Inserted by `kb_mcp.parser.parse.number_docling_page_breaks` at the start
# of the document and at every page transition. Read for page_start /
# page_end and left in the emitted chunk text — see `emit()`.
_PAGE_MARK_RE = re.compile(r"<!-- page:(\d+) -->")

# A fenced code block toggles at each ``` line; headings inside one are
# code, not structure, and must not be treated as cut points. Cheap to
# track and this corpus has shown false positives are not otherwise
# impossible (a Markdown or shell snippet quoting a heading verbatim).
_FENCE_RE = re.compile(r"^```", re.MULTILINE)

# Legacy-document defense: `kb_mcp.imports.mediawiki` now strips MediaWiki's
# `<span class="mw-editsection">` before a wiki page ever reaches Docling, so
# a freshly-imported page's headings are already plain. Documents imported
# before that fix still have their old Markdown on file until re-parsed, and
# for those `document.text` renders a heading as
# `[Section Title [ edit ]](/w/index.php?...&action=edit&section=N)` — a
# whole-heading Markdown link (`label` below) whose label carries the
# trailing "[ edit ]" chrome. Cleaned here so `section_path` on an
# unmigrated document doesn't read `"Detector > Calorimeter [ edit ]"`,
# which looks like the whole path needs editing, not just the leaf.
_LINK_HEADING_RE = re.compile(r"^\[(.*)\]\([^)]*\)$")
_WIKI_EDIT_SUFFIX_RE = re.compile(r"\s*\[\s*edit\s*\]\s*$")


def _heading_title(raw: str) -> str:
    """Clean a heading's captured text into the title `section_path` uses.

    A no-op on a plain heading. On an unmigrated wiki document's
    link-wrapped heading (see the module comment above `_LINK_HEADING_RE`)
    it unwraps the link to its label and strips the trailing MediaWiki
    "[ edit ]". The heading's *position* in doc_text (and therefore the
    chunk text it opens) is untouched either way; only the label used for
    `header_stack` / `section_path` is cleaned.
    """
    m = _LINK_HEADING_RE.match(raw)
    label = m.group(1) if m else raw
    return _WIKI_EDIT_SUFFIX_RE.sub("", label).strip()


def _heading_events(text: str) -> List[re.Match]:
    """Headings in `text`, excluding any that fall inside a fenced code block."""
    fence_starts = sorted(m.start() for m in _FENCE_RE.finditer(text))
    if not fence_starts:
        return list(_HEADING_RE.finditer(text))

    def in_fence(pos: int) -> bool:
        # Odd count of fence markers before `pos` means we're inside one.
        idx = 0
        for f in fence_starts:
            if f > pos:
                break
            idx += 1
        return idx % 2 == 1

    return [m for m in _HEADING_RE.finditer(text) if not in_fence(m.start())]


def chunk_from_docling_json(
    document: Document,
    target_tokens: Optional[int] = None,
    min_chunk_tokens: int = 30,
    parser_output: Optional[Dict[str, Any]] = None,
    prepend_section_path: bool = True,
    prepend_gist: bool = True,
) -> List[Dict[str, Any]]:
    """Chunk `document.text` at its own Markdown section boundaries.

    `document.text` decides *where* to cut — literally, by its `#`
    headings — so chunk text is a plain slice of it and can never drift the
    way a reconstruction from a separate structure could.
    `char_start_index` / `char_end_index` are exact by construction rather
    than something to recover by matching text back into a tree.

    The emitted chunks **partition** `document.text`: chunk N+1 starts
    exactly where chunk N ended. Anything this walker doesn't specifically
    act on — table rows, image markers, prose with no heading above it —
    still rides inside whichever chunk's span it falls in, so coverage is
    100% by construction.

    Behaviour:
      * Every ATX heading (`#` through `######`) is a section boundary that
        updates an in-memory header stack, used to populate `section_path`
        on every chunk that follows. A heading inside a fenced code block
        is not a boundary — it's code, not structure.
      * `<!-- page:N -->` markers (inserted by
        :func:`kb_mcp.parser.parse.number_docling_page_breaks` for
        page-based documents) are read for `page_start` / `page_end` and
        left in place in the emitted text — deliberately visible, so a
        reader (human or LLM) can tell which page a passage came from
        without the chunk having to say so separately. Documents with no
        such markers (HTML, or any non-Docling source) get `page_start` /
        `page_end` of `None`, which is correct: they have no pages.
      * **Sizing is done in the embedding model's own tokenizer** (see
        :mod:`kb_mcp.kb.embedding.budget`), not tiktoken, and the budget is
        the model's window minus `[CLS]`/`[SEP]` minus the
        `Section:` / `Context:` prefix `embed_text()` will prepend. Past
        that window the encoder silently truncates, so an over-budget chunk
        is embedded as its opening fragment and nothing else.
      * **Tiny-fragment merging**: a flush whose accumulated body is below
        ``min_chunk_tokens`` does NOT emit — it keeps the accumulator alive
        so the fragment merges with the next chunk.
      * **Heading as candidate cut**: a heading only closes the current
        chunk once that chunk already carries at least
        `_SECTION_CUT_FRACTION` of the embedding budget. Below that, the
        heading joins the chunk in progress and `section_path` resolves to
        the ancestor common to where the chunk opened and where it ends
        (see `section_path` below) — so a chunk that merged across
        sibling headings is never mislabelled with just the last one.
      * **Oversized-slice splitting**: an over-cap slice is split on
        paragraph (`\\n\\n`) then sentence-ish (`. `) boundaries, with a
        self-calibrating character cut as the last resort; offsets stay
        absolute throughout, so every piece is still an exact slice.
      * Returns chunk dicts ready for the existing chunk-save path.
        chunk_strategy = `"section"`. `token_length` is in the embedding
        model's units.

    Falls back to `[]` (the dispatch then uses the Markdown token chunker)
    when there's no `document.text` to slice, or the text has no headings
    at all — a heading-less document has no section structure for this
    walker to contribute, so the caller's plain token chunker is a better
    fit than pretending the whole thing is "one section."

    Args:
        document: Source document whose `text` is the Markdown the chunks
            are sliced from.
        target_tokens: Soft per-chunk budget, in embedding-model tokens.
            Clamped to the content budget — it can only ask for *smaller*
            chunks, never for ones the embedder would truncate. Default
            None = use the whole content budget.
        min_chunk_tokens: Floor — chunks below this token count merge
            with the next instead of emitting. Default 30.
        parser_output: Unused — kept for call-site compatibility with
            callers that resolved it for the previous JSON-tree walker.
        prepend_section_path: Whether `embed_text()` will prepend
            `Section: …`. Mirrored here so its token cost is reserved.
        prepend_gist: Likewise for `Context: {document.gist}`.
    """
    from .chunking import _split_to_budget, resolve_strategy_name

    doc_text = getattr(document, "text", None)
    if not doc_text:
        # Nothing to slice. The dispatch falls back to the token chunker.
        return []

    headings = _heading_events(doc_text)
    if not headings:
        # No section structure to contribute — let the caller's plain
        # token chunker handle it instead of emitting one "chunk" that is
        # really just the whole document under no section at all.
        return []

    from .budget import get_embed_budget

    embed_budget = get_embed_budget(
        gist=getattr(document, "gist", None),
        prepend_section_path=prepend_section_path,
        prepend_gist=prepend_gist,
    )
    tok = embed_budget.count

    # Page-marker offsets and the page each announces, in document order.
    # The markers themselves stay in doc_text and therefore in emitted
    # chunk text — this pair of lists exists only to answer "what page is
    # in effect at position X" (see `page_at`), for page_start / page_end.
    _page_offsets = [m.start() for m in _PAGE_MARK_RE.finditer(doc_text)]
    _page_numbers = [int(m.group(1)) for m in _PAGE_MARK_RE.finditer(doc_text)]

    def page_at(pos: int) -> Optional[int]:
        """The page in effect at character `pos` of doc_text.

        A marker announces the page starting *at* its own offset, so the
        page in effect at `pos` is whichever marker's offset is the last
        one at or before `pos` — None before the first marker (a document
        with no page data at all, or `pos` inside the front matter that
        precedes it, which doesn't happen here since the first marker is
        always at offset 0 when there is any page data).
        """
        i = bisect.bisect_right(_page_offsets, pos) - 1
        return _page_numbers[i] if i >= 0 else None

    out: List[Dict[str, Any]] = []
    header_stack: List[tuple[int, str]] = []
    emitted_upto = 0
    acc_end = 0
    acc_tokens = 0
    # Body tokens only — excludes the heading line(s) that open the chunk.
    # The tiny-fragment guard measures this, so a heading with no body of
    # its own can't satisfy the floor on its own and emit as a junk chunk.
    acc_body_tokens = 0
    open_stack: List[tuple[int, str]] = []

    def section_path() -> Optional[str]:
        """Section path describing everything in the current chunk.

        For a chunk that lies inside one section this is just that
        section's path. For a chunk that merged across sibling headings,
        it is the deepest path common to where the chunk opened and where
        it now ends — the shared ancestor that honestly covers all of it.
        Labelling such a chunk with only its *last* heading would attribute
        content to a sibling section it isn't part of, which retrieves
        confidently and wrongly.

        Descending into a subsection is not merging across a boundary: if
        the chunk opened at `Detector` and the walk is now inside
        `Detector > Calorimeter`, the opening path is a prefix of the
        current one and the deeper path describes the chunk's content
        better, so it wins. Only when the two paths actually diverge —
        sibling sections — does the shared ancestor apply.

        When divergent headings share no ancestor at all — the common
        case on flat documents, where everything sits at one level — fall
        back to the section the chunk opened in rather than to nothing.
        """
        if not open_stack:
            return " > ".join(t for _, t in header_stack) or None
        common: List[str] = []
        for (lvl_a, txt_a), (lvl_b, txt_b) in zip(open_stack, header_stack):
            if lvl_a != lvl_b or txt_a != txt_b:
                break
            common.append(txt_a)
        if len(common) == len(open_stack):
            return " > ".join(t for _, t in header_stack) or None
        if common:
            return " > ".join(common)
        return " > ".join(t for _, t in open_stack) or None

    def note_open() -> None:
        if not open_stack:
            open_stack[:] = header_stack

    def budget_cap() -> int:
        cap = embed_budget.content_budget(section_path())
        if target_tokens is not None:
            cap = min(cap, target_tokens)
        return max(1, cap)

    def emit(start: int, end: int) -> None:
        """Emit doc_text[start:end] verbatim — including any `<!-- page:N
        -->` markers it contains, so a reader (human or LLM) can tell which
        page a passage came from — split so no piece exceeds the cap."""
        pieces = _split_to_budget(doc_text, start, end, budget_cap(), tok)
        for p_start, p_end, p_tokens in pieces:
            if p_end <= p_start:
                continue
            piece = doc_text[p_start:p_end]
            if not piece.strip():
                continue
            # The page in effect at the start and at the end of this piece.
            # They differ only when a page marker sits inside the piece
            # (an oversized single-page span never splits mid-page, but a
            # section spanning a page break can), in which case both ends
            # are still correct: start's page and end's page.
            page_start = page_at(p_start)
            page_end = page_at(max(p_start, p_end - 1))
            out.append({
                "text": piece,
                "chunk_index": len(out),
                "char_start_index": p_start,
                "char_end_index": p_end,
                "token_length": p_tokens,
                "section_path": section_path(),
                "page_start": page_start,
                "page_end": page_end,
                "bbox": None,
                "body_self_refs": [],
                # Window-encoded (section_512), so a re-chunk under a
                # different encoder stands beside the old set instead of
                # silently replacing it. See resolve_strategy_name.
                "chunk_strategy": resolve_strategy_name("section"),
                "meta": {"source": "docling_body_walk"},
            })

    def flush(*, force: bool = False) -> bool:
        """Emit everything pending as one chunk. Returns True if flushed.

        If the accumulated *body* is below ``min_chunk_tokens``, keep the
        pending span alive (don't reset) so it merges with whatever comes
        next, and return False. ``force=True`` overrides this — used at
        end-of-doc, where there is no "next".
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
        acc_tokens = 0
        acc_body_tokens = 0
        open_stack.clear()
        return True

    def extend_to(new_end: int, *, is_body: bool) -> None:
        """Grow the pending span out to `new_end`, splitting first if that
        would overflow the current section's budget.

        Mirrors the tree walker's `add_text`: the cost of extending is
        measured once as `delta`, a non-forcing flush closes the chunk if
        appending would overflow (flush doesn't touch `acc_end`, so `delta`
        still holds afterwards), and only then does the span actually grow.
        """
        nonlocal acc_tokens, acc_body_tokens, acc_end
        if new_end <= acc_end:
            return
        delta = tok(doc_text[acc_end:new_end])
        if delta and acc_end > emitted_upto and acc_tokens + delta > budget_cap():
            flush()
        acc_tokens += delta
        if is_body:
            acc_body_tokens += delta
        acc_end = new_end

    # Interleave headings and the text between them in one forward pass.
    cursor = 0
    for m in headings:
        h_start, h_end = m.start(), m.end()
        level = len(m.group(1))
        title = _heading_title(m.group(2).strip())

        # Body text strictly between the previous event and this heading.
        if h_start > cursor:
            note_open()
            extend_to(h_start, is_body=True)

        # Section boundary — a *candidate* cut point. See module docstring
        # and `_SECTION_CUT_FRACTION` for why this doesn't cut
        # unconditionally.
        #
        # Closing happens while `header_stack` still describes the section
        # that's ending — section_path is read at flush time, so the
        # stack must not be touched first.
        cut_floor = int(budget_cap() * _SECTION_CUT_FRACTION)
        would_orphan = level <= 1 and bool(open_stack)
        if acc_body_tokens >= cut_floor or would_orphan:
            flush()
        # else: deliberately holding — this heading joins the chunk in
        # progress, and `section_path` will resolve to the shared ancestor.

        while header_stack and header_stack[-1][0] >= level:
            header_stack.pop()
        header_stack.append((level, title))
        if acc_end <= emitted_upto or acc_body_tokens <= 0:
            # Either nothing is pending, or only headings have landed so
            # far — either way this heading is where the chunk's content
            # begins, so it defines the label rather than leaving the
            # snapshot on an ancestor that would let a later subsection
            # claim content that precedes it.
            open_stack[:] = header_stack

        # The heading opens the next chunk: seed the span with its own
        # line so the section title is part of `chunk.text` — the
        # reranker scores (query, chunk.text) pairs and would otherwise
        # never see it. Heading text counts toward `acc_tokens` (the embed
        # budget) but deliberately NOT toward `acc_body_tokens`, so a
        # heading with no body of its own can't satisfy the tiny-fragment
        # floor and emit as a junk chunk.
        extend_to(h_end, is_body=False)
        cursor = h_end

    # Trailing body text after the last heading.
    if len(doc_text) > cursor:
        note_open()
        extend_to(len(doc_text), is_body=True)

    flush(force=True)
    return out
