"""An undecoded formula should fall back to its raw text, not a bare comment.

Docling leaves `text` empty on a formula it could not decode but keeps the
layout model's reading in `orig`. The Markdown export only reads `text`, so
without a fallback the equation reaches the index as
`<!-- formula-not-decoded -->` — a comment carrying no signal, while the
symbols someone would search for sit unused in the parse tree.

Enrichment being off is the normal case for documents whose math density
doesn't justify the model, so this is the common path, not an edge case.
"""

from kb_mcp.parser.parser_docling import _FORMULA_PLACEHOLDER, _fill_undecoded_formulas

FORMULA = "R µe = Γ( µ - + N ( A,Z ) → e - + N ( A,Z ))"


def _payload(*formulas):
    texts, children = [], []
    for i, orig in enumerate(formulas):
        ref = f"#/texts/{i}"
        texts.append({"self_ref": ref, "label": "formula", "text": "", "orig": orig})
        children.append({"cref": ref})
    return {"texts": texts, "body": {"children": children}}


def test_placeholder_is_replaced_with_orig():
    out = _fill_undecoded_formulas(f"before\n\n{_FORMULA_PLACEHOLDER}\n\nafter",
                                   _payload(FORMULA))
    assert _FORMULA_PLACEHOLDER not in out
    assert FORMULA in out
    assert out.startswith("before") and out.endswith("after")


def test_ragged_spacing_is_collapsed():
    out = _fill_undecoded_formulas(_FORMULA_PLACEHOLDER, _payload("a   =    b\n\n  c"))
    assert out == "a = b c"


def test_markers_pair_in_body_order():
    text = f"{_FORMULA_PLACEHOLDER} then {_FORMULA_PLACEHOLDER}"
    out = _fill_undecoded_formulas(text, _payload("first = 1", "second = 2"))
    assert out == "first = 1 then second = 2"


def test_empty_orig_leaves_the_placeholder():
    """Nothing to say is better than an empty gap that hides the omission."""
    out = _fill_undecoded_formulas(_FORMULA_PLACEHOLDER, _payload(""))
    assert out == _FORMULA_PLACEHOLDER


def test_missing_parse_tree_is_a_noop():
    text = f"x {_FORMULA_PLACEHOLDER} y"
    assert _fill_undecoded_formulas(text, None) == text
    assert _fill_undecoded_formulas(text, {}) == text


def test_text_without_placeholders_is_untouched():
    assert _fill_undecoded_formulas("plain text", _payload(FORMULA)) == "plain text"
