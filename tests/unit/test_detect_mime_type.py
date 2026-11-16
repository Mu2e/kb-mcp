"""detect_mime_type describes the file a symlink points to, not the link."""

from kb_mcp.parser.utils import detect_mime_type

PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


def test_a_file(tmp_path):
    f = tmp_path / "report.pdf"
    f.write_bytes(PDF)
    assert detect_mime_type(f) == "application/pdf"


def test_a_symlink_is_typed_by_its_target(tmp_path):
    """A source file put in place as a symlink (e.g. to re-parse from another
    directory) used to come out as inode/symlink and went to the fallback
    parser instead of Docling."""
    target = tmp_path / "elsewhere" / "report.pdf"
    target.parent.mkdir()
    target.write_bytes(PDF)
    link = tmp_path / "mu2e-docdb-1-report.pdf"
    link.symlink_to(target)
    assert detect_mime_type(link) == "application/pdf"
    assert detect_mime_type(str(link)) == "application/pdf"


def test_a_broken_symlink_falls_back_to_the_extension(tmp_path):
    link = tmp_path / "talk.pdf"
    link.symlink_to(tmp_path / "missing.pdf")
    assert detect_mime_type(link) == "application/pdf"

