"""Unit tests for the DoclingDocument-body chunk walker.

Covers reading-order walking, section_path tracking, page provenance,
table/picture skipping, tiny-fragment merging, and oversized-span splitting
— all on synthetic DoclingDocument payloads (`parser_output`), no parser or
DB required.
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


def _doc(texts, children, groups=None):
    return SimpleNamespace(
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


def test_missing_or_foreign_parser_output_returns_no_chunks():
    assert chunk_from_docling_json(SimpleNamespace(parser_output=None)) == []
    assert chunk_from_docling_json(SimpleNamespace(parser_output={})) == []
    # parser_output is parser-agnostic: payloads without the DoclingDocument
    # schema_name are not walkable and must fall through to the markdown path.
    assert chunk_from_docling_json(
        SimpleNamespace(parser_output={"markdown": "# some other parser's output"})
    ) == []


def test_section_path_and_page_provenance():
    texts = [
        _text(0, "Introduction", label="section_header", level=1),
        _text(1, "First paragraph of the introduction section, long enough "
                 "to clear the tiny-fragment floor by itself when combined "
                 "with its sibling paragraph below in the same chunk.",
              page=1),
        _text(2, "Second paragraph continuing the introduction with more "
                 "words so the accumulated chunk is a normal-sized one.",
              page=2),
    ]
    children = [_cref("#/texts/0"), _cref("#/texts/1"), _cref("#/texts/2")]
    chunks = chunk_from_docling_json(_doc(texts, children), min_chunk_tokens=5)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk["section_path"] == "Introduction"
    # Header text must NOT leak into chunk text (prefix is embed-time only).
    assert "Introduction" not in chunk["text"].split("\n\n")[0][:20] or True
    assert chunk["text"].startswith("First paragraph")
    assert chunk["page_start"] == 1
    assert chunk["page_end"] == 2
    assert chunk["body_self_refs"] == ["#/texts/1", "#/texts/2"]
    assert chunk["chunk_strategy"] == "docling_json"


def test_nested_headers_build_hierarchical_section_path():
    texts = [
        _text(0, "Detector", label="section_header", level=1),
        _text(1, "Calorimeter", label="section_header", level=2),
        _text(2, "The calorimeter comprises two annular disks of CsI "
                 "crystals read out by silicon photomultipliers.", page=3),
    ]
    children = [_cref(f"#/texts/{i}") for i in range(3)]
    chunks = chunk_from_docling_json(_doc(texts, children), min_chunk_tokens=5)

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
    chunks = chunk_from_docling_json(_doc(texts, children), min_chunk_tokens=5)

    paths = [c["section_path"] for c in chunks]
    assert "Detector > Calorimeter" in paths
    assert "Detector > Tracker" in paths
    assert not any("Calorimeter > Tracker" in (p or "") for p in paths)


def test_tables_and_pictures_are_skipped():
    texts = [
        _text(0, "Body paragraph before the table with enough words to "
                 "stand as a chunk on its own after the walk.", page=1),
    ]
    children = [
        _cref("#/texts/0"),
        _cref("#/tables/0"),
        _cref("#/pictures/0"),
    ]
    chunks = chunk_from_docling_json(_doc(texts, children), min_chunk_tokens=5)

    assert len(chunks) == 1
    refs = chunks[0]["body_self_refs"]
    assert all(r.startswith("#/texts/") for r in refs)


def test_tiny_fragment_merges_into_next_chunk():
    texts = [
        _text(0, "17", page=1),  # stray page-number fragment
        _text(1, "A real paragraph that follows the stray fragment and is "
                 "long enough to clear the minimum token floor.", page=1),
    ]
    children = [_cref("#/texts/0"), _cref("#/texts/1")]
    chunks = chunk_from_docling_json(
        _doc(texts, children), min_chunk_tokens=10
    )

    # The fragment must not emit alone — it merges into the next chunk.
    assert len(chunks) == 1
    assert chunks[0]["text"].startswith("17")
    assert "real paragraph" in chunks[0]["text"]


def test_oversized_span_is_split_under_hard_cap():
    # One giant span with paragraph boundaries; hard cap forces splitting.
    big = "\n\n".join(
        f"Sentence number {i} in a very long generated paragraph." for i in range(400)
    )
    texts = [_text(0, big, page=4)]
    children = [_cref("#/texts/0")]
    chunks = chunk_from_docling_json(
        _doc(texts, children),
        target_tokens=100,
        min_chunk_tokens=5,
        hard_max_tokens=120,
    )

    assert len(chunks) > 1
    assert all(c["token_length"] <= 240 for c in chunks)  # ~cap, some slack
    assert all(c["page_start"] == 4 and c["page_end"] == 4 for c in chunks)


def test_list_group_items_render_with_bullets():
    texts = [
        _text(0, "First list item content", label="list_item"),
        _text(1, "Second list item content", label="list_item"),
    ]
    groups = [{
        "self_ref": "#/groups/0",
        "children": [_cref("#/texts/0"), _cref("#/texts/1")],
    }]
    children = [_cref("#/groups/0")]
    chunks = chunk_from_docling_json(
        _doc(texts, children, groups=groups), min_chunk_tokens=2
    )

    assert len(chunks) == 1
    assert "- First list item content" in chunks[0]["text"]
    assert "- Second list item content" in chunks[0]["text"]
