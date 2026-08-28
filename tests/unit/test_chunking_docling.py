"""Unit tests for the Markdown-heading section chunker.

The chunker reads `document.text` directly — its own `#` headings decide
section boundaries, and `<!-- page:N -->` markers (when present) decide
page provenance. Every chunk's text is a plain slice of `document.text`,
so these tests build that text directly. No parser, DoclingDocument tree,
or DB required.

Covers heading-order walking, section_path tracking, page provenance via
page markers, slice offsets, tiny-fragment merging, short-section roll-up,
oversized-span splitting, the partition invariant (chunks tile doc_text, so
nothing is lost), and the embedding budget (no chunk exceeds what the
encoder will actually read).
"""

from types import SimpleNamespace

from kb_mcp.kb.embedding.chunking import chunk_from_docling_json


def _doc(text):
    return SimpleNamespace(text=text, parser_output=None, gist=None)


def _build(text, **kwargs):
    """Chunk `text` directly. Returns (chunks, text) for convenience."""
    return chunk_from_docling_json(_doc(text), **kwargs), text


def _assert_partitions(chunks, doc_text):
    """Chunks tile doc_text: only whitespace may sit between them."""
    prev = 0
    for c in chunks:
        start, end = c["char_start_index"], c["char_end_index"]
        assert doc_text[start:end] == c["text"]
        assert not doc_text[prev:start].strip(), (
            f"lost content before chunk {c['chunk_index']}: "
            f"{doc_text[prev:start]!r}"
        )
        prev = end
    assert not doc_text[prev:].strip(), f"lost tail: {doc_text[prev:]!r}"


def test_missing_document_text_returns_no_chunks():
    """Without text to slice there's nothing to do — the dispatch falls
    back to the Markdown token chunker."""
    assert chunk_from_docling_json(_doc(None)) == []
    assert chunk_from_docling_json(_doc("")) == []


def test_heading_less_text_returns_no_chunks():
    """No `#` headings means no section structure to contribute — the
    dispatch's plain token chunker is a better fit than pretending the
    whole document is "one section"."""
    assert chunk_from_docling_json(_doc("Just a paragraph, no headings.")) == []


def test_chunk_text_is_an_exact_slice_of_document_text():
    """The defining property: chunk text is never reconstructed."""
    text = (
        "# Introduction\n\n"
        "First paragraph of the introduction section, long enough "
        "to clear the tiny-fragment floor by itself.\n\n"
        "Second paragraph continuing the introduction with more "
        "words so the accumulated chunk is a normal-sized one."
    )
    chunks, doc_text = _build(text, min_chunk_tokens=5)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert doc_text[chunk["char_start_index"]:chunk["char_end_index"]] == chunk["text"]
    assert chunk["section_path"] == "Introduction"
    # The heading opens the chunk — the reranker scores (query, chunk.text)
    # pairs and would otherwise never see the section title.
    assert chunk["text"].startswith("# Introduction")
    assert "First paragraph" in chunk["text"]
    # Window-encoded: a re-chunk under a different encoder must land under a
    # distinct name rather than silently replacing this one.
    from kb_mcp.kb.embedding.budget import get_embed_budget
    assert chunk["chunk_strategy"] == f"section_{get_embed_budget().window}"


def test_page_markers_set_page_start_and_page_end():
    """`<!-- page:N -->` markers (inserted by
    `number_docling_page_breaks`) decide page provenance; a chunk spanning
    a page transition carries both the page it opened on and the page it
    ended on."""
    text = (
        "<!-- page:4 -->\n\n"
        "# Method\n\n"
        "Text before the page turns, long enough to matter for the "
        "token floor here.\n\n"
        "<!-- page:5 -->\n\n"
        "Text after the page turns, also long enough to matter for the "
        "token floor in this test."
    )
    chunks, doc_text = _build(text, min_chunk_tokens=5)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert doc_text[chunk["char_start_index"]:chunk["char_end_index"]] == chunk["text"]
    assert chunk["page_start"] == 4
    assert chunk["page_end"] == 5
    # The markers are deliberately left in the text — visible to a reader
    # (human or LLM) tracing a passage back to its page.
    assert "<!-- page:4 -->" in chunk["text"]
    assert "<!-- page:5 -->" in chunk["text"]


def test_no_page_markers_means_no_page_provenance():
    """HTML (or any non-Docling source) has no pages — page_start /
    page_end must be None, not a guess."""
    text = "# Overview\n\nSome body text with no page markers at all here."
    chunks, _ = _build(text, min_chunk_tokens=5)

    assert len(chunks) == 1
    assert chunks[0]["page_start"] is None
    assert chunks[0]["page_end"] is None


def test_heading_with_no_body_does_not_emit_a_chunk():
    """A parent heading (or one whose body export dropped) must not emit
    as a heading-only junk chunk. The tiny-fragment floor measures body
    tokens, so heading tokens can't satisfy it on their own.

    The heading text is not discarded, though: the chunks partition
    doc_text, so it rides at the head of the chunk that follows it (and
    also reaches that chunk via section_path)."""
    text = (
        "# Detector\n\n"
        "## Calorimeter\n\n"
        "Calorimeter body text long enough to stand alone as its "
        "own chunk without merging anywhere."
    )
    chunks, _ = _build(text, min_chunk_tokens=10)

    assert len(chunks) == 1
    # "# Detector" never became a chunk of its own — it was carried into
    # the one chunk that has body text, rather than dropped.
    assert not any(c["text"].strip() == "# Detector" for c in chunks)
    assert chunks[0]["text"].startswith("# Detector")
    assert "## Calorimeter" in chunks[0]["text"]
    assert chunks[0]["section_path"] == "Detector > Calorimeter"


def test_nested_headers_build_hierarchical_section_path():
    text = (
        "# Detector\n\n"
        "## Calorimeter\n\n"
        "The calorimeter comprises two annular disks of CsI "
        "crystals read out by silicon photomultipliers."
    )
    chunks, _ = _build(text, min_chunk_tokens=5)

    assert len(chunks) == 1
    assert chunks[0]["section_path"] == "Detector > Calorimeter"


def test_sibling_header_pops_stack():
    text = (
        "# Detector\n\n"
        "## Calorimeter\n\n"
        "Calorimeter body text long enough to emit as a chunk "
        "without merging into the following section.\n\n"
        "## Tracker\n\n"
        "Tracker body text long enough to emit as its own chunk "
        "with the sibling section path attached."
    )
    # target_tokens keeps the section-cut floor (a fraction of the budget)
    # below these sections, so each heading still cuts. Merging behaviour
    # is covered separately by the candidate-cut tests.
    chunks, _ = _build(text, min_chunk_tokens=5, target_tokens=20)

    paths = [c["section_path"] for c in chunks]
    assert "Detector > Calorimeter" in paths
    assert "Detector > Tracker" in paths
    assert not any("Calorimeter > Tracker" in (p or "") for p in paths)


def test_chunks_do_not_overlap_and_advance_monotonically():
    text = "\n\n".join([
        "# First",
        "First section body long enough to stand alone as its own "
        "chunk without merging forward.",
        "# Second",
        "Second section body long enough to stand alone as its own "
        "chunk without merging forward.",
        "# Third",
        "Third section body long enough to stand alone as its own "
        "chunk without merging forward.",
    ])
    chunks, doc_text = _build(text, min_chunk_tokens=5, target_tokens=20)

    assert len(chunks) == 3
    prev_end = -1
    for chunk in chunks:
        assert chunk["char_start_index"] >= prev_end
        assert chunk["char_end_index"] > chunk["char_start_index"]
        assert doc_text[chunk["char_start_index"]:chunk["char_end_index"]] == chunk["text"]
        prev_end = chunk["char_end_index"]


def test_tiny_fragment_merges_into_next_chunk():
    text = (
        "# Section\n\n"
        "17\n\n"  # stray page-number-like fragment
        "A real paragraph that follows the stray fragment and is "
        "long enough to clear the minimum token floor."
    )
    chunks, doc_text = _build(text, min_chunk_tokens=10)

    assert len(chunks) == 1
    assert "17" in chunks[0]["text"]
    assert "real paragraph" in chunks[0]["text"]
    assert doc_text[chunks[0]["char_start_index"]:chunks[0]["char_end_index"]] == chunks[0]["text"]


def test_short_section_merges_forward_under_the_common_ancestor():
    """A short-but-real section (e.g. a one-line 'Overview') must not be
    labelled with the *next* sibling's section_path.

    It merges into the following chunk instead — better context for
    retrieval — and that chunk is labelled with the ancestor common to
    both sections, which describes the combined span honestly."""
    text = (
        "# Detector\n\n"
        "## Overview\n\n"
        "Short overview.\n\n"
        "## Calorimeter\n\n"
        "The calorimeter section describes crystal geometry and "
        "readout electronics in enough detail to be a real chunk "
        "on its own, well past the token floor."
    )
    chunks, doc_text = _build(text, min_chunk_tokens=10)

    # The short section merged into the one that follows it.
    assert len(chunks) == 1
    merged = chunks[0]
    assert "Short overview" in merged["text"]
    assert "calorimeter section" in merged["text"]
    # Labelled with the shared ancestor — never with just "Calorimeter",
    # which would claim the overview belongs to a section it precedes.
    assert merged["section_path"] == "Detector"
    _assert_partitions(chunks, doc_text)


def test_short_top_level_section_keeps_the_section_it_opened_in():
    """Same at the top level, where the merged sections share no ancestor
    at all: the chunk must still carry a section_path, and it must be the
    section the chunk *opened* in — never the next sibling's, which would
    claim the intro belongs to a section that follows it."""
    text = (
        "# Introduction\n\n"
        "Short intro.\n\n"
        "# Calorimeter\n\n"
        "The calorimeter section describes crystal geometry and "
        "readout electronics in enough detail to be a real chunk "
        "on its own, well past the token floor."
    )
    chunks, doc_text = _build(text, min_chunk_tokens=10)

    assert len(chunks) == 1
    merged = chunks[0]
    assert "Short intro" in merged["text"]
    assert "calorimeter section" in merged["text"]
    # Flat hierarchy: no common ancestor exists, so the opening section
    # is used rather than dropping the label entirely.
    assert merged["section_path"] == "Introduction"
    _assert_partitions(chunks, doc_text)


def test_oversized_span_is_split_under_hard_cap():
    # One giant span with paragraph boundaries; the cap forces splitting.
    big = "\n\n".join(
        f"Sentence number {i} in a very long generated paragraph." for i in range(400)
    )
    text = "# Dense\n\n" + big
    chunks, doc_text = _build(text, target_tokens=100, min_chunk_tokens=5)

    assert len(chunks) > 1
    assert all(c["token_length"] <= 100 for c in chunks)
    _assert_partitions(chunks, doc_text)


def test_untracked_content_is_carried_not_dropped():
    """Table rows, image markers and any other prose the walker doesn't
    specifically act on still land inside a chunk's slice — the partition
    covers 100% of doc_text by construction, not by tracking every kind of
    content explicitly."""
    text = (
        "# Results\n\n"
        "## Empty parent\n\n"
        "### Deep\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "![a figure](fig.png) [image_id:fig.png image_num:0]\n\n"
        "Body text with enough words in it to clear the tiny "
        "fragment floor comfortably."
    )
    chunks, doc_text = _build(text, min_chunk_tokens=5)

    _assert_partitions(chunks, doc_text)
    joined = "".join(c["text"] for c in chunks)
    # The heading the walker didn't open a chunk on is still in the text.
    assert "Empty parent" in joined
    assert "| a | b |" in joined
    assert "![a figure]" in joined


def test_chunks_never_exceed_the_embedding_budget():
    """No chunk may exceed the content budget for its section — that is
    the number of tokens left in the encoder's window once [CLS]/[SEP]
    and the Section:/Context: prefix are paid for."""
    from kb_mcp.kb.embedding.budget import get_embed_budget

    budget = get_embed_budget()
    cap = 80
    body = " ".join(f"word{i}" for i in range(4000))
    text = "# Dense\n\n" + body
    chunks, doc_text = _build(text, target_tokens=cap, min_chunk_tokens=5)

    assert len(chunks) > 1
    _assert_partitions(chunks, doc_text)
    for c in chunks:
        assert c["token_length"] <= cap
        # token_length is in the embedding model's units, so it must
        # agree with what the budget measures on the same text.
        assert budget.count(c["text"]) <= cap


def test_unsplittable_run_is_cut_by_characters_under_the_cap():
    """A long run with no paragraph or sentence boundary (a markdown
    table row is the usual real-world case) still has to come out under
    the cap — the character cut is measured, not estimated."""
    from kb_mcp.kb.embedding.budget import get_embed_budget

    budget = get_embed_budget()
    cap = 60
    run = "|" + "|".join("-" * 40 for _ in range(60)) + "|"
    text = "# Table\n\n" + run
    chunks, doc_text = _build(text, target_tokens=cap, min_chunk_tokens=1)

    assert len(chunks) > 1
    _assert_partitions(chunks, doc_text)
    for c in chunks:
        assert budget.count(c["text"]) <= cap


def test_target_tokens_cannot_exceed_the_model_window():
    """target_tokens can only ask for smaller chunks. A caller asking for
    more than the encoder can read must not get oversized chunks."""
    from kb_mcp.kb.embedding.budget import get_embed_budget

    budget = get_embed_budget()
    body = " ".join(f"word{i}" for i in range(6000))
    text = "# Big\n\n" + body
    chunks, doc_text = _build(text, target_tokens=100_000, min_chunk_tokens=5)

    _assert_partitions(chunks, doc_text)
    for c in chunks:
        assert c["token_length"] <= budget.content_budget(c["section_path"])


def test_escaped_markdown_body_is_still_counted():
    """Docling's Markdown export backslash-escapes characters like `_` —
    the chunker doesn't need to un-escape or otherwise interpret them,
    since it slices `document.text` as-is; this just pins that escaped
    text is ordinary body content, not something that trips up heading
    detection or token counting."""
    text = (
        "## Observations\n\n"
        "- Strong downward trend with increasing lh\\_d0\\_max and "
        "TF\\_FOM across the scanned interval.\n"
    )
    chunks, doc_text = _build(text, min_chunk_tokens=5)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert "lh\\_d0\\_max" in chunk["text"]
    assert chunk["text"].startswith("## Observations")
    _assert_partitions(chunks, doc_text)


def test_link_wrapped_heading_uses_the_link_label():
    """MediaWiki exports (Docling's HTML reader on a wiki page) wrap every
    section heading in a self-link to its own anchor, with a trailing
    "[ edit ]" suffix. Both are cosmetic — section_path must carry the
    plain title, not the raw Markdown link syntax."""
    text = (
        "## [Last Minute Check [ edit ]](/w/index.php?title=X&action=edit§ion=4)\n\n"
        "Body text long enough to clear the tiny fragment floor by itself."
    )
    chunks, _ = _build(text, min_chunk_tokens=5)

    assert len(chunks) == 1
    assert chunks[0]["section_path"] == "Last Minute Check"
    # The heading LINE itself is untouched in the emitted text — only the
    # label used for section_path is cleaned.
    assert "[Last Minute Check [ edit ]]" in chunks[0]["text"]


def test_heading_inside_fenced_code_block_is_not_a_cut_point():
    """A `#` line inside a fenced code block is code, not structure — a
    Markdown or shell snippet quoting a heading verbatim must not create a
    phantom section boundary."""
    text = (
        "# Real Section\n\n"
        "Some intro text long enough to matter for the token floor here.\n\n"
        "```\n"
        "# Not a real heading\n"
        "```\n\n"
        "More body text long enough to matter for the token floor too."
    )
    chunks, doc_text = _build(text, min_chunk_tokens=5)

    assert len(chunks) == 1
    assert chunks[0]["section_path"] == "Real Section"
    _assert_partitions(chunks, doc_text)


def test_a_heading_only_run_still_emits_at_end_of_document():
    """Body-less slices merge forward rather than emitting alone — but
    `force` must still override that at end-of-doc, or a document whose
    only anchorable elements are headings would emit nothing at all."""
    text = "# GitHubWorkflow\n\nSome body text the walker cannot anchor.\n"
    chunks, doc_text = _build(text, min_chunk_tokens=30)

    assert len(chunks) >= 1
    _assert_partitions(chunks, doc_text)
    assert "GitHubWorkflow" in chunks[0]["text"]


# --- headings as candidate cut points ------------------------------------
#
# A heading closes the current chunk only once that chunk is substantial
# enough to stand on its own. Cutting on every heading produced one chunk
# per heading no matter how little sat under it — a finding, the figure it
# describes and the implication drawn from it split three ways, each too
# partial to answer anything.


def test_sibling_sections_merge_until_the_cut_floor():
    """Small adjacent sections join into one chunk rather than each
    emitting alone."""
    text = (
        "## Observations\n\n"
        "Strong downward trend across the scanned interval.\n\n"
        "## Implication\n\n"
        "Keep the parameter tightly scanned in the next card."
    )
    chunks, doc_text = _build(text, min_chunk_tokens=5)

    assert len(chunks) == 1
    assert "Strong downward trend" in chunks[0]["text"]
    assert "tightly scanned" in chunks[0]["text"]
    _assert_partitions(chunks, doc_text)


def test_a_substantial_section_still_cuts_at_the_next_heading():
    """Merging must not run past a chunk that already stands on its own —
    otherwise every document collapses toward one chunk per budget."""
    body = " ".join(f"word{i}" for i in range(400))
    text = f"# First\n\n{body}\n\n# Second\n\n{body}"
    chunks, doc_text = _build(text, min_chunk_tokens=5)

    assert len(chunks) > 1
    _assert_partitions(chunks, doc_text)


def test_descending_into_a_subsection_keeps_the_deeper_path():
    """Opening under a parent and descending into its child is not
    merging across a boundary — the deeper path still covers the content
    and is the more useful label."""
    text = (
        "# Detector\n\n"
        "## Calorimeter\n\n"
        "Crystal geometry and readout electronics."
    )
    chunks, _ = _build(text, min_chunk_tokens=5)

    assert len(chunks) == 1
    assert chunks[0]["section_path"] == "Detector > Calorimeter"


def test_merged_chunk_never_claims_a_section_it_only_precedes():
    """The failure mode this labelling exists to prevent: content from an
    earlier section must never be filed under a later sibling."""
    text = (
        "# Detector\n\n"
        "## Overview\n\n"
        "Short overview.\n\n"
        "## Calorimeter\n\n"
        "Crystal geometry and readout electronics in detail."
    )
    chunks, _ = _build(text, min_chunk_tokens=10)

    merged = next(c for c in chunks if "Short overview" in c["text"])
    # Must not be filed under Calorimeter, which the overview precedes.
    assert merged["section_path"] != "Detector > Calorimeter"
    assert merged["section_path"] in ("Detector", "Detector > Overview")


def test_every_chunk_keeps_a_section_path_when_headings_exist():
    """A merged chunk must not lose its label: an empty section_path
    drops the `Section:` prefix at embed time and leaves the reranker
    without context. On a flat document (no shared ancestor) the opening
    section is used rather than nothing."""
    parts = []
    for i in range(6):
        parts.append(f"# Section {i}")
        parts.append(f"Body text for section {i}.")
    text = "\n\n".join(parts)
    chunks, _ = _build(text, min_chunk_tokens=5)

    assert all(c["section_path"] for c in chunks)
