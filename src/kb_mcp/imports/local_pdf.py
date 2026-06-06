#!/usr/bin/env python3
"""Local PDF directory importer — intended to injest scanned PDFs without parsing.

Walks a directory recursively, adds every PDF as a RawDocument with
skip_parse=True so that parsing can be done separately (e.g. with a GPU pipeline for example on NERSC).

Metadata is derived from the folder hierarchy and filename:
  - title        : filename stem
  - filename     : full filename with extension
  - folder       : relative sub-folder path from the root input directory
  - folder_parts : list of individual folder name components
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_IS_MAIN = __name__ == "__main__"
if _IS_MAIN:
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

if _IS_MAIN:
    from kb_mcp.imports.base import Source
    from kb_mcp.kb import add_document
else:
    from .base import Source
    from ..kb import add_document

logger = logging.getLogger(__name__)


class LocalPDFSource(Source):
    """Importer that adds local scanned PDFs without parsing.

    Files are copied into KB storage and registered as RawDocuments.
    Run the Marker GPU pipeline afterwards to parse them.
    """

    def __init__(
        self,
        input_dir: Path,
        source_id: str = "local-pdf",
        skip_existing: bool = True,
        skip_parse: bool = True,
    ):
        """Initialise the local PDF source.

        Args:
            input_dir: Root directory to scan recursively for PDF files.
            source_id: Source identifier used in the knowledge base.
            skip_existing: If True, skip PDFs whose doc_id already exists in
                           RawDocuments (default: True — safe to re-run).
            skip_parse: If True (default), only register files without parsing.
                        Set to False to parse inline using the configured parser.
                        For scanned PDFs, leave True and run the Marker GPU
                        pipeline separately.
        """
        input_dir = Path(input_dir).resolve()
        super().__init__(
            source_id=source_id,
            name=source_id,
            description=f"Local PDFs from {input_dir}",
            base_uri=input_dir.as_uri(),
            delay=0.0,
            timeout=0.0,
            meta={"importer": "local_pdf.py", "input_dir": str(input_dir)},
        )
        self.input_dir = input_dir
        self.skip_existing = skip_existing
        self.skip_parse = skip_parse

    # ------------------------------------------------------------------
    # Source interface
    # ------------------------------------------------------------------

    def fetch_items(
        self,
        query: Optional[str] = None,
        max_results: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Walk input_dir recursively and return one item dict per PDF.

        Args:
            query: Unused — provided for interface compatibility.
            max_results: Optional cap on the number of items returned.

        Returns:
            List of item dicts, each containing:
                id       : relative path string (used as doc_id)
                file_path: absolute Path to the PDF
                meta     : derived metadata dict
        """
        pdf_files = sorted(self.input_dir.rglob("*.pdf"))
        logger.info(f"Found {len(pdf_files)} PDF file(s) under {self.input_dir}")

        items = []
        for pdf_path in pdf_files:
            rel_path = pdf_path.relative_to(self.input_dir)
            # Use the relative path as the doc_id — unique within the source.
            # Flatten slashes to '--' so add_document can use it as a filename,
            # and strip the .pdf suffix (add_document appends the extension itself).
            doc_id = str(rel_path.with_suffix("")).replace("/", "--")

            # Derive metadata from the folder/file hierarchy
            folder_parts = list(rel_path.parts[:-1])  # all parts except filename
            folder = "/".join(folder_parts) if folder_parts else ""

            meta = {
                "title": pdf_path.stem,
                "filename": pdf_path.name,
                "folder": folder,
                "folder_parts": folder_parts,
            }

            items.append({"id": doc_id, "file_path": pdf_path, "meta": meta})

        if max_results is not None:
            items = items[:max_results]

        return items

    def process_item(
        self,
        item: Dict[str, Any],
        output_dir: Path,  # unused — files are already local
        session: Any,
    ) -> Dict[str, Any]:
        """Register a single PDF as a RawDocument without parsing.

        Args:
            item: Item dict from fetch_items().
            output_dir: Unused (files are local, copied directly to KB storage).
            session: Database session.

        Returns:
            Standard result dict with document_ids, num_documents, parsed, error.
        """
        doc_id: str = item["id"]
        file_path: Path = item["file_path"]
        meta: Dict = item["meta"]

        if not file_path.exists():
            return {
                "document_ids": [],
                "num_documents": 0,
                "parsed": False,
                "error": f"File not found: {file_path}",
            }

        # Optionally skip if already registered
        if self.skip_existing:
            from ..kb.db_models import RawDocument
            existing = session.query(RawDocument).filter(
                RawDocument.source_id == self.source_id,
                RawDocument.doc_id == doc_id,
            ).first()
            if existing:
                logger.info(f"Skipping {doc_id} — already in database")
                return {
                    "document_ids": [],
                    "num_documents": 0,
                    "parsed": False,
                    "raw_document_id": existing.id,
                    "skipped": True,
                    "error": None,
                }

        uri = file_path.as_uri()

        result = add_document(
            file_path,
            source_id=self.source_id,
            doc_id=doc_id,
            uri=uri,
            meta=meta,
            copy_to_kb=True,
            skip_parse=self.skip_parse,
            session=session,
        )

        result["error"] = None
        return result


# ---------------------------------------------------------------------------
# Direct script entry point 
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    if _IS_MAIN:
        from kb_mcp.imports.cli import cmd_local_pdf
    else:
        from .cli import cmd_local_pdf

    parser = argparse.ArgumentParser(
        description="Register local scanned PDFs in the knowledge base (no parsing)."
    )
    parser.add_argument("input_dir", type=Path, help="Root directory to scan for PDFs")
    parser.add_argument(
        "--source-id",
        default="local-pdf",
        help="Source identifier for the knowledge base (default: local-pdf)",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="Re-register PDFs even if they already exist in the database",
    )
    parser.add_argument(
        "--parse",
        action="store_true",
        help="Parse documents inline (default: False — use Marker GPU pipeline separately)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()
    cmd_local_pdf(args)