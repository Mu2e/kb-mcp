"""Unit tests for the MediaWiki importer's HTML cleanup."""

from kb_mcp.imports.mediawiki import _strip_edit_section_links


def test_strips_edit_section_span():
    html = (
        '<h2><span class="mw-headline" id="Goal">Session Goal</span>'
        '<span class="mw-editsection">'
        '<span class="mw-editsection-bracket">[</span>'
        '<a href="/w/index.php?title=X&amp;action=edit&amp;section=1">'
        '<span>edit</span></a>'
        '<span class="mw-editsection-bracket">]</span>'
        '</span></h2>'
    )
    cleaned = _strip_edit_section_links(html)

    assert "mw-editsection" not in cleaned
    assert "action=edit" not in cleaned
    assert "Session Goal" in cleaned


def test_no_edit_sections_is_a_no_op_on_content():
    html = "<h2>Plain Heading</h2><p>Body text.</p>"
    cleaned = _strip_edit_section_links(html)

    assert "Plain Heading" in cleaned
    assert "Body text." in cleaned


def test_multiple_edit_sections_all_removed():
    html = "".join(
        f'<h2>Section {i}<span class="mw-editsection">'
        f'<a href="/w/index.php?action=edit&amp;section={i}">edit</a>'
        f'</span></h2>'
        for i in range(3)
    )
    cleaned = _strip_edit_section_links(html)

    assert cleaned.count("mw-editsection") == 0
    for i in range(3):
        assert f"Section {i}" in cleaned
