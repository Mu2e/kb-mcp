"""Unit tests for the DoclingDocument-body chunk walker.

The walker uses the DoclingDocument body to decide *where* to cut, but
every chunk's text is a slice of `document.text` — so these tests always
supply both a synthetic `parser_output` and the Markdown text the chunks
are sliced from. No parser or DB required.

Covers reading-order walking, section_path tracking, page provenance,
slice offsets, table/picture handling, tiny-fragment merging, short-section
roll-up, oversized-span splitting, the partition invariant (chunks tile
doc_text, so nothing the walker doesn't track is lost), and the embedding
budget (no chunk exceeds what the encoder will actually read).
"""

from types import SimpleNamespace

from kb_mcp.kb.embedding.chunking import chunk_from_docling_json


def _text(idx, text, label="text", page=None, level=None):
    node = {"self_ref": f"#/texts/{idx}", "text": text, "label": label}
    if page is not None:
        node["prov"] = [{"page_no": page}]
    if level is not None:
        node["level"] = level
    return node


def _doc(texts, children, groups=None, text=None):
    return SimpleNamespace(
        text=text,
        parser_output={
            # Real payloads self-identify: model_dump() always serialises
            # schema_name, and the walker guards on it.
            "schema_name": "DoclingDocument",
            "body": {"children": children},
            "texts": texts,
            "groups": groups or [],
        }
    )


def _cref(ref):
    return {"cref": ref}


def _markdown(texts, children, groups=None):
    """Render a plausible `document.text` for the given body.

    Mirrors what Docling's export_to_markdown() would emit closely enough
    for the walker to locate every element: headers prefixed with `#`,
    everything else as its own paragraph, in body order.
    """
    by_ref = {t["self_ref"]: t for t in texts}
    groups_by_ref = {g["self_ref"]: g for g in (groups or [])}
    parts = []
    for child in children:
        cref = child["cref"]
        if cref.startswith("#/texts/"):
            node = by_ref[cref]
            if node.get("label") in ("section_header", "title"):
                parts.append("#" * (node.get("level") or 1) + " " + node["text"])
            else:
                parts.append(node["text"])
        elif cref.startswith("#/groups/"):
            for sub in groups_by_ref[cref]["children"]:
                parts.append(by_ref[sub["cref"]]["text"])
        elif cref.startswith("#/tables/"):
            parts.append("| a | b |\n|---|---|\n| 1 | 2 |")
        elif cref.startswith("#/pictures/"):
            parts.append("![a figure](fig.png) [image_id:fig.png image_num:0]")
    return "\n\n".join(parts)


def _build(texts, children, groups=None, **kwargs):
    """Build a document whose text is the rendered markdown, then chunk it."""
    doc_text = _markdown(texts, children, groups)
    doc = _doc(texts, children, groups=groups, text=doc_text)
    return chunk_from_docling_json(doc, **kwargs), doc_text


def test_missing_or_foreign_parser_output_returns_no_chunks():
    assert chunk_from_docling_json(SimpleNamespace(parser_output=None, text="x")) == []
    assert chunk_from_docling_json(SimpleNamespace(parser_output={}, text="x")) == []
    # parser_output is parser-agnostic: payloads without the DoclingDocument
    # schema_name are not walkable and must fall through to the markdown path.
    assert chunk_from_docling_json(
        SimpleNamespace(parser_output={"markdown": "# other parser"}, text="x")
    ) == []


def test_missing_document_text_returns_no_chunks():
    """Without text to slice there's nothing to do — the dispatch falls
    back to the Markdown token chunker."""
    texts = [_text(0, "Some body text here that is long enough.")]
    children = [_cref("#/texts/0")]
    assert chunk_from_docling_json(_doc(texts, children, text=None)) == []


def test_chunk_text_is_an_exact_slice_of_document_text():
    """The defining property: chunk text is never reconstructed."""
    texts = [
        _text(0, "Introduction", label="section_header", level=1),
        _text(1, "First paragraph of the introduction section, long enough "
                 "to clear the tiny-fragment floor by itself.", page=1),
        _text(2, "Second paragraph continuing the introduction with more "
                 "words so the accumulated chunk is a normal-sized one.",
              page=2),
    ]
    children = [_cref(f"#/texts/{i}") for i in range(3)]
    chunks, doc_text = _build(texts, children, min_chunk_tokens=5)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert doc_text[chunk["char_start_index"]:chunk["char_end_index"]] == chunk["text"]
    assert chunk["section_path"] == "Introduction"
    # The heading opens the chunk — the reranker scores (query, chunk.text)
    # pairs and would otherwise never see the section title.
    assert chunk["text"].startswith("# Introduction")
    assert "First paragraph" in chunk["text"]
    assert chunk["page_start"] == 1
    assert chunk["page_end"] == 2
    assert chunk["body_self_refs"] == ["#/texts/0", "#/texts/1", "#/texts/2"]
    assert chunk["chunk_strategy"] == "section"


def test_slice_encloses_tables_and_pictures_between_elements():
    """Content the walker doesn't track (tables, figures, formula
    placeholders) stays inside the slice when it sits between two tracked
    elements — the chunk is a contiguous span, not a reconstruction."""
    texts = [
        _text(0, "Method", label="section_header", level=1),
        _text(1, "Text before the table which is long enough on its own to "
                 "matter for the token floor here.", page=1),
        _text(2, "Text after the table, also long enough to matter for the "
                 "token floor in this test.", page=1),
    ]
    children = [_cref("#/texts/0"), _cref("#/texts/1"),
                _cref("#/tables/0"), _cref("#/texts/2")]
    chunks, doc_text = _build(texts, children, min_chunk_tokens=5)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert doc_text[chunk["char_start_index"]:chunk["char_end_index"]] == chunk["text"]
    assert "| a | b |" in chunk["text"]
    assert chunk["text"].startswith("# Method")
    assert "Text before the table" in chunk["text"]
    assert chunk["text"].endswith("token floor in this test.")
    # Tables are not tracked as contributing elements.
    assert all(r.startswith("#/texts/") for r in chunk["body_self_refs"])


def test_heading_with_no_body_does_not_emit_a_chunk():
    """A parent heading (or one whose body the export dropped) must not
    emit as a heading-only junk chunk. The tiny-fragment floor measures
    body tokens, so heading tokens can't satisfy it on their own.

    The heading text is not discarded, though: the chunks partition
    doc_text, so it rides at the head of the chunk that follows it (and
    also reaches that chunk via section_path)."""
    texts = [
        _text(0, "Detector", label="section_header", level=1),
        _text(1, "Calorimeter", label="section_header", level=2),
        _text(2, "Calorimeter body text long enough to stand alone as its "
                 "own chunk without merging anywhere.", page=1),
    ]
    children = [_cref(f"#/texts/{i}") for i in range(3)]
    chunks, _ = _build(texts, children, min_chunk_tokens=10)

    assert len(chunks) == 1
    # "# Detector" never became a chunk of its own — it was carried into
    # the one chunk that has body text, rather than dropped.
    assert not any(c["text"].strip() == "# Detector" for c in chunks)
    assert chunks[0]["text"].startswith("# Detector")
    assert "## Calorimeter" in chunks[0]["text"]
    assert chunks[0]["section_path"] == "Detector > Calorimeter"


def test_nested_headers_build_hierarchical_section_path():
    texts = [
        _text(0, "Detector", label="section_header", level=1),
        _text(1, "Calorimeter", label="section_header", level=2),
        _text(2, "The calorimeter comprises two annular disks of CsI "
                 "crystals read out by silicon photomultipliers.", page=3),
    ]
    children = [_cref(f"#/texts/{i}") for i in range(3)]
    chunks, _ = _build(texts, children, min_chunk_tokens=5)

    assert len(chunks) == 1
    assert chunks[0]["section_path"] == "Detector > Calorimeter"


def test_sibling_header_pops_stack():
    texts = [
        _text(0, "Detector", label="section_header", level=1),
        _text(1, "Calorimeter", label="section_header", level=2),
        _text(2, "Calorimeter body text long enough to emit as a chunk "
                 "without merging into the following section.", page=1),
        _text(3, "Tracker", label="section_header", level=2),
        _text(4, "Tracker body text long enough to emit as its own chunk "
                 "with the sibling section path attached.", page=2),
    ]
    children = [_cref(f"#/texts/{i}") for i in range(5)]
    chunks, _ = _build(texts, children, min_chunk_tokens=5)

    paths = [c["section_path"] for c in chunks]
    assert "Detector > Calorimeter" in paths
    assert "Detector > Tracker" in paths
    assert not any("Calorimeter > Tracker" in (p or "") for p in paths)


def test_chunks_do_not_overlap_and_advance_monotonically():
    texts = [
        _text(0, "First", label="section_header", level=1),
        _text(1, "First section body long enough to stand alone as its own "
                 "chunk without merging forward.", page=1),
        _text(2, "Second", label="section_header", level=1),
        _text(3, "Second section body long enough to stand alone as its own "
                 "chunk without merging forward.", page=2),
        _text(4, "Third", label="section_header", level=1),
        _text(5, "Third section body long enough to stand alone as its own "
                 "chunk without merging forward.", page=3),
    ]
    children = [_cref(f"#/texts/{i}") for i in range(6)]
    chunks, doc_text = _build(texts, children, min_chunk_tokens=5)

    assert len(chunks) == 3
    prev_end = -1
    for chunk in chunks:
        assert chunk["char_start_index"] >= prev_end
        assert chunk["char_end_index"] > chunk["char_start_index"]
        assert doc_text[chunk["char_start_index"]:chunk["char_end_index"]] == chunk["text"]
        prev_end = chunk["char_end_index"]


def test_tiny_fragment_merges_into_next_chunk():
    texts = [
        _text(0, "17", page=1),  # stray page-number fragment
        _text(1, "A real paragraph that follows the stray fragment and is "
                 "long enough to clear the minimum token floor.", page=1),
    ]
    children = [_cref("#/texts/0"), _cref("#/texts/1")]
    chunks, doc_text = _build(texts, children, min_chunk_tokens=10)

    # The fragment must not emit alone — it merges into the next chunk.
    assert len(chunks) == 1
    assert chunks[0]["text"].startswith("17")
    assert "real paragraph" in chunks[0]["text"]
    assert doc_text[chunks[0]["char_start_index"]:chunks[0]["char_end_index"]] == chunks[0]["text"]


def test_short_section_rolls_up_to_parent_not_next_sibling():
    """A short-but-real section (e.g. a one-line 'Overview') must not
    bleed into the *next* sibling's chunk under the wrong section_path.
    It should roll up to the parent heading instead."""
    texts = [
        _text(0, "Detector", label="section_header", level=1),
        _text(1, "Overview", label="section_header", level=2),
        _text(2, "Short overview.", page=1),
        _text(3, "Calorimeter", label="section_header", level=2),
        _text(4, "The calorimeter section describes crystal geometry and "
                 "readout electronics in enough detail to be a real chunk "
                 "on its own, well past the token floor.", page=2),
    ]
    children = [_cref(f"#/texts/{i}") for i in range(5)]
    chunks, _ = _build(texts, children, min_chunk_tokens=10)

    assert len(chunks) == 2
    overview_chunk = next(c for c in chunks if "Short overview" in c["text"])
    calo_chunk = next(c for c in chunks if "calorimeter section" in c["text"])

    # Rolled up to the parent, not mislabeled as the next sibling.
    assert overview_chunk["section_path"] == "Detector"
    assert calo_chunk["section_path"] == "Detector > Calorimeter"


def test_short_top_level_section_rolls_up_to_no_parent():
    """Same rollup at the top level (no parent heading at all): the short
    section still must not inherit the next sibling's section_path."""
    texts = [
        _text(0, "Introduction", label="section_header", level=1),
        _text(1, "Short intro.", page=1),
        _text(2, "Calorimeter", label="section_header", level=1),
        _text(3, "The calorimeter section describes crystal geometry and "
                 "readout electronics in enough detail to be a real chunk "
                 "on its own, well past the token floor.", page=2),
    ]
    children = [_cref(f"#/texts/{i}") for i in range(4)]
    chunks, _ = _build(texts, children, min_chunk_tokens=10)

    assert len(chunks) == 2
    intro_chunk = next(c for c in chunks if "Short intro" in c["text"])
    calo_chunk = next(c for c in chunks if "calorimeter section" in c["text"])

    assert intro_chunk["section_path"] is None
    assert calo_chunk["section_path"] == "Calorimeter"


def test_oversized_span_is_split_under_hard_cap():
    # One giant span with paragraph boundaries; the cap forces splitting.
    # The cap now comes from the embedding window rather than a caller
    # argument, so target_tokens is what tightens it here.
    big = "\n\n".join(
        f"Sentence number {i} in a very long generated paragraph." for i in range(400)
    )
    texts = [_text(0, big, page=4)]
    children = [_cref("#/texts/0")]
    chunks, doc_text = _build(
        texts, children, target_tokens=100, min_chunk_tokens=5,
    )

    assert len(chunks) > 1
    assert all(c["token_length"] <= 100 for c in chunks)
    assert all(c["page_start"] == 4 and c["page_end"] == 4 for c in chunks)
    # Every piece is still an exact slice.
    for c in chunks:
        assert doc_text[c["char_start_index"]:c["char_end_index"]] == c["text"]


def test_list_group_items_are_walked_inline():
    texts = [
        _text(0, "First list item content", label="list_item"),
        _text(1, "Second list item content", label="list_item"),
    ]
    groups = [{
        "self_ref": "#/groups/0",
        "children": [_cref("#/texts/0"), _cref("#/texts/1")],
    }]
    children = [_cref("#/groups/0")]
    chunks, doc_text = _build(texts, children, groups=groups, min_chunk_tokens=2)

    assert len(chunks) == 1
    assert "First list item content" in chunks[0]["text"]
    assert "Second list item content" in chunks[0]["text"]
    assert chunks[0]["body_self_refs"] == ["#/texts/0", "#/texts/1"]
    assert doc_text[chunks[0]["char_start_index"]:chunks[0]["char_end_index"]] == chunks[0]["text"]


def test_element_missing_from_text_is_skipped_without_breaking_others():
    """An element whose text isn't in the export (escaping differences,
    omitted content) is skipped; surrounding elements still bound their
    chunks and later lookups aren't corrupted."""
    texts = [
        _text(0, "First", label="section_header", level=1),
        _text(1, "First section body text long enough to clear the floor.", page=1),
        _text(2, "Second", label="section_header", level=1),
        _text(3, "THIS TEXT IS ABSENT FROM THE EXPORT ENTIRELY", page=2),
        _text(4, "Third", label="section_header", level=1),
        _text(5, "Third section body text long enough to clear the floor.", page=3),
    ]
    children = [_cref(f"#/texts/{i}") for i in range(6)]
    # Build markdown that omits texts/3 entirely.
    doc_text = "\n\n".join([
        "# First",
        texts[1]["text"],
        "# Second",
        "# Third",
        texts[5]["text"],
    ])
    chunks = chunk_from_docling_json(
        _doc(texts, children, text=doc_text), min_chunk_tokens=5
    )

    # The two locatable sections still produce correct chunks.
    assert len(chunks) == 2
    for c in chunks:
        assert doc_text[c["char_start_index"]:c["char_end_index"]] == c["text"]
    assert chunks[0]["section_path"] == "First"
    assert chunks[1]["section_path"] == "Third"


# --- partition and embedding-budget invariants ---------------------------
#
# These two are the load-bearing properties of the walker: everything in
# doc_text reaches some chunk, and no chunk overflows the embedding
# window. Both used to be violated silently — content between tracked
# elements was dropped, and chunks sized in tiktoken against a cap
# unrelated to the model were truncated at embed time.


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


def test_untracked_content_is_carried_not_dropped():
    """Tables, pictures and formulas aren't tracked as contributing
    elements, and headings the walker declines to open a chunk on are
    discarded as anchors — but their *text* must still land in a chunk."""
    texts = [
        _text(0, "Results", label="section_header", level=1),
        _text(1, "Empty parent", label="section_header", level=2),
        _text(2, "Deep", label="section_header", level=3),
        _text(3, "Body text with enough words in it to clear the tiny "
                 "fragment floor comfortably.", page=2),
    ]
    children = [
        _cref("#/texts/0"),
        _cref("#/tables/0"),
        _cref("#/texts/1"),
        _cref("#/pictures/0"),
        _cref("#/texts/2"),
        _cref("#/texts/3"),
    ]
    chunks, doc_text = _build(texts, children, min_chunk_tokens=5)

    _assert_partitions(chunks, doc_text)
    joined = "".join(c["text"] for c in chunks)
    # The heading the walker didn't open a chunk on is still in the text.
    assert "Empty parent" in joined
    for marker in ("| a | b |", "!["):
        if marker in doc_text:
            assert marker in joined


def test_chunks_never_exceed_the_embedding_budget():
    """No chunk may exceed the content budget for its section — that is
    the number of tokens left in the encoder's window once [CLS]/[SEP]
    and the Section:/Context: prefix are paid for."""
    from kb_mcp.kb.embedding.budget import get_embed_budget

    budget = get_embed_budget()
    cap = 80
    body = " ".join(f"word{i}" for i in range(4000))
    texts = [
        _text(0, "Dense", label="section_header", level=1),
        _text(1, body, page=1),
    ]
    children = [_cref("#/texts/0"), _cref("#/texts/1")]
    chunks, doc_text = _build(texts, children, target_tokens=cap,
                              min_chunk_tokens=5)

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
    texts = [_text(0, run, page=1)]
    children = [_cref("#/texts/0")]
    chunks, doc_text = _build(texts, children, target_tokens=cap,
                              min_chunk_tokens=1)

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
    texts = [_text(0, body, page=1)]
    children = [_cref("#/texts/0")]
    chunks, doc_text = _build(texts, children, target_tokens=100_000,
                              min_chunk_tokens=5)

    _assert_partitions(chunks, doc_text)
    for c in chunks:
        assert c["token_length"] <= budget.content_budget(c["section_path"])


def test_escaped_markdown_text_is_still_located():
    """Docling's Markdown export backslash-escapes characters that the
    body tree stores raw, so a node reading `lh_d0_max` appears in
    doc_text as `lh\\_d0\\_max`. A literal search misses it — on a
    maths-heavy document that was 40% of all elements, whose text then
    counted as no body at all, leaving their headings to emit as
    3-token heading-only chunks."""
    texts = [
        _text(0, "Observations", label="section_header", level=2),
        _text(1, "Strong downward trend with increasing lh_d0_max and "
                 "TF_FOM across the scanned interval.", page=3),
    ]
    children = [_cref("#/texts/0"), _cref("#/texts/1")]
    doc_text = (
        "## Observations\n\n"
        "- Strong downward trend with increasing lh\\_d0\\_max and "
        "TF\\_FOM across the scanned interval.\n"
    )
    doc = _doc(texts, children, text=doc_text)
    chunks = chunk_from_docling_json(doc, min_chunk_tokens=5)

    assert len(chunks) == 1
    chunk = chunks[0]
    # The escaped body was found, so it counts as body text and the
    # heading did not emit alone.
    assert "lh\\_d0\\_max" in chunk["text"]
    assert chunk["text"].startswith("## Observations")
    assert chunk["page_start"] == 3
    _assert_partitions(chunks, doc_text)


def test_a_heading_only_run_still_emits_at_end_of_document():
    """Body-less slices merge forward rather than emitting alone — but
    `force` must still override that at end-of-doc, or a document whose
    only anchorable elements are headings would emit nothing at all."""
    texts = [_text(0, "GitHubWorkflow", label="title", level=1)]
    children = [_cref("#/texts/0")]
    doc_text = "# GitHubWorkflow\n\nSome body text the walker cannot anchor.\n"
    doc = _doc(texts, children, text=doc_text)
    chunks = chunk_from_docling_json(doc, min_chunk_tokens=30)

    assert len(chunks) >= 1
    _assert_partitions(chunks, doc_text)
    assert "GitHubWorkflow" in chunks[0]["text"]
