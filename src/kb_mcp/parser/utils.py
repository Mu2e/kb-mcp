"""Utility functions for document parsing."""

import importlib.metadata as _pkg_meta
from pathlib import Path
from typing import Optional

from .parser_base import BaseParser
from .parser_pdf import PDFParser
from .parser_pptx import PPTXParser
from .parser_docx import DOCXParser
from .parser_excel import ExcelParser
from .parser_text import TextParser


# Per-parser package list — one entry per `parser_name` that get_parser_version_info()
# can describe. Order matters only for readability.
_PARSER_PACKAGES = {
    "kb-mcp": ["kb-mcp"],
    "docling": ["docling", "docling-core", "docling-ibm-models", "docling-parse"],
    "marker": ["marker-pdf"],
    "pypdf2": ["PyPDF2"],
}


def get_parser_version_info(parser_name: str) -> dict:
    """Return a dict describing the installed versions for a parser stack.

    Used by `documents.operations.get_or_create_parser()` to populate the
    `parsers.meta` JSON column so `Document.parser_id` plus the registered
    metadata uniquely identifies the parser stack a document was processed
    with. That makes cache invalidation on parser upgrades possible — when
    Docling bumps its layout model, comparing meta tells us we need to re-parse.

    Args:
        parser_name: Logical parser name (e.g. ``"docling"``, ``"pypdf2"``,
            ``"marker"``, ``"kb-mcp"``).

    Returns:
        ``{"versions": {pkg: ver, ...}}`` covering every installed package in
        the parser stack. Missing packages are omitted (no error). Returns
        ``{"versions": {}}`` for unknown parser names.
    """
    pkgs = _PARSER_PACKAGES.get(parser_name, [])
    versions: dict = {}
    for pkg in pkgs:
        try:
            versions[pkg] = _pkg_meta.version(pkg)
        except _pkg_meta.PackageNotFoundError:
            continue
        except Exception:  # be conservative — never fail callers
            continue
    return {"versions": versions}

# Parser mapping - maps MIME type or file extension to parser class
PARSER_MAP = {
    # PDF
    'pdf': PDFParser,
    'application/pdf': PDFParser,
    # PowerPoint
    'pptx': PPTXParser,
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': PPTXParser,
    # Word
    'docx': DOCXParser,
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': DOCXParser,
    'application/msword': DOCXParser,
    'doc': DOCXParser,
    # Excel
    'xlsx': ExcelParser,
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ExcelParser,
    # Text
    'txt': TextParser,
    'text/plain': TextParser,
    'text/html': TextParser,
    'text/markdown': TextParser,
    'text/xml': TextParser,
    'application/xhtml+xml': TextParser,
}


def get_parser(file_path: str | Path, doc_type: Optional[str] = None) -> BaseParser:
    """Create appropriate parser for document type.
    
    Args:
        file_path: Path to the document file
        doc_type: Optional document type (MIME type or extension). 
                  If not provided, will be auto-detected.
    
    Returns:
        Parser instance
    
    Raises:
        NotImplementedError: If document type is not supported
    """
    file_path = Path(file_path) if not isinstance(file_path, Path) else file_path
    
    if doc_type is None:
        # Auto-detect MIME type
        doc_type = detect_mime_type(file_path)
    
    # Normalize doc_type - try both full MIME type and extension
    doc_type_lower = doc_type.lower()
    
    # Try full MIME type first
    if doc_type_lower in PARSER_MAP:
        return PARSER_MAP[doc_type_lower](file_path, doc_type_lower)
    
    # Try extension
    if '.' in doc_type_lower:
        ext = doc_type_lower.split('.')[-1]
        if ext in PARSER_MAP:
            return PARSER_MAP[ext](file_path, ext)
    
    # Try file extension from path
    file_ext = file_path.suffix.lower().lstrip('.')
    if file_ext in PARSER_MAP:
        return PARSER_MAP[file_ext](file_path, file_ext)
    
    raise NotImplementedError(
        f"Document type '{doc_type}' not supported yet. "
        f"Available: {', '.join(PARSER_MAP.keys())}"
    )


def detect_mime_type(file_path: str | Path) -> str:
    """Detect MIME type from file path.

    Uses python-magic if available, with extension-based fallback for
    ambiguous results. Falls back to mimetypes if magic is unavailable.

    Args:
        file_path: Path to the file

    Returns:
        MIME type string
    """
    file_path = Path(file_path) if not isinstance(file_path, Path) else file_path

    # Try python-magic first (more accurate)
    magic_mime_type = None
    try:
        import magic
        mime = magic.Magic(mime=True)
        magic_mime_type = mime.from_file(str(file_path))

        # If magic returns generic octet-stream, check if extension suggests otherwise
        if magic_mime_type == "application/octet-stream":
            import mimetypes
            ext_mime_type, _ = mimetypes.guess_type(str(file_path))
            # Trust extension for common document types
            if ext_mime_type and ext_mime_type.startswith(('application/pdf', 'text/', 'application/vnd.')):
                return ext_mime_type

        if magic_mime_type:
            return magic_mime_type
    except ImportError:
        pass
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.debug(f"Error using python-magic: {e}")

    # Fall back to mimetypes
    import mimetypes
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type:
        return mime_type

    # Last resort: use file extension
    ext = file_path.suffix.lower().lstrip('.')
    mime_type, _ = mimetypes.guess_type(f"dummy.{ext}")
    if mime_type:
        return mime_type

    return "application/octet-stream"


