"""Unit tests for kb.search.provenance.doc_provenance()."""

from types import SimpleNamespace

from kb_mcp.kb.search.provenance import doc_provenance


def _doc(doc_type, meta=None):
    return SimpleNamespace(doc_type=doc_type, meta=meta or {})


def test_text_doc_carries_only_doc_type():
    out = doc_provenance(_doc("text"))
    assert out == {"doc_type": "text"}


def test_table_doc_surfaces_table_keys():
    out = doc_provenance(_doc("table", {
        "page": 7, "caption": "Table 2: rates", "num_rows": 12, "num_cols": 4,
        "unrelated": "ignored",
    }))
    assert out["doc_type"] == "table"
    assert out["page"] == 7
    assert out["caption"] == "Table 2: rates"
    assert out["num_rows"] == 12
    assert out["num_cols"] == 4
    assert "unrelated" not in out


def test_section_doc_surfaces_section_keys():
    out = doc_provenance(_doc("section", {
        "page_start": 3, "page_end": 5, "section_title": "Calorimeter", "level": 2,
    }))
    assert out["page_start"] == 3
    assert out["page_end"] == 5
    assert out["section_title"] == "Calorimeter"
    assert out["level"] == 2


def test_image_doc_with_empty_caption_omits_caption():
    out = doc_provenance(_doc("image", {"page": 2, "caption": ""}))
    assert out["page"] == 2
    assert "caption" not in out


def test_missing_meta_is_safe():
    out = doc_provenance(SimpleNamespace(doc_type="image", meta=None))
    assert out == {"doc_type": "image"}


def test_none_doc_is_safe():
    out = doc_provenance(None)
    assert out == {"doc_type": None}
