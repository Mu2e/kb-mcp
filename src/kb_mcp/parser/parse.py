"""Main parse function for document parsing."""

import tempfile
from pathlib import Path
from typing import List, Optional

from .utils import detect_mime_type, get_parser


def parse(
    file_path: Optional[str | Path] = None,
    data: Optional[dict] = None,
    extract_images: Optional[bool] = None,
    describe_images: Optional[bool] = None,
    parser_name: Optional[str] = None,
) -> List[dict]:
    """Parse a document and return list of document dictionaries.
    
    This is the main entry point for document parsing. It returns dictionaries
    that can be converted to Document objects using Document.from_dict().
    
    Can parse from either:
    - File path: parse("document.pdf", {"source_id": "...", "doc_id": "..."})
    - Dict with binary: parse(data={"source_id": "...", "doc_id": "...", "binary": b"...", ...})
    
    Args:
        file_path: Optional path to the document file. If not provided, data must contain binary.
        data: Optional dictionary with document fields (same as Document.from_dict).
              If file_path is provided: must include source_id and doc_id.
              If file_path is not provided: must include source_id, doc_id, and binary.
              If not provided and file_path is given, creates minimal dicts with auto-generated IDs.
              Can include 'source_type' (MIME type) - if not provided, will be auto-detected.
        extract_images: If True, create separate Document objects for images.
                       If None, reads from PARSE_IMAGE_ADDITIONAL_DOC env var.
        describe_images: If True, generate LLM descriptions for images.
                        Configuration is read from env vars:
                        - PARSE_IMAGE_DESCRIPTION_MODEL (model name)
                        - PARSE_IMAGE_DESCRIPTION_NUMWORKERS
                        - OPENAI_BASE_URL
                        - OPENAI_API_KEY
                        If None, reads from PARSE_IMAGE_LLM_DESCRIPTION env var.
        parser_name: Optional parser name. Recognised values:
                     - "docling"  — IBM Docling (PDF/PPTX/DOCX/HTML/XLSX).
                                     DEFAULT for PDF, PPTX, HTML, DOCX.
                     - "marker"   — marker-pdf (PDF only).
                     - "nougat"   — Nougat OCR (PDF only, optional extra).
                     - "azure"    — Azure Document Intelligence (PDF only,
                                     optional extra).
                     - "pypdf2"   — legacy PyPDF2 fast path (PDF only).
                     - "legacy"   — bespoke parser for the given MIME
                                     (opt-out from the Docling defaults).
                     - "kb-mcp" / None — Docling-default routes above;
                                          other types via get_parser().
    
    Returns:
        List of dictionaries (can be converted to Documents):
        - First: Main document dict with text extracted
            - text: Extracted text with [Image X] placeholders replaced with descriptions
                     (if PARSE_IMAGE_LLM_DESCRIPTION is enabled)
        - Rest: Image document dicts (only if PARSE_IMAGE_ADDITIONAL_DOC=true)
            - binary: image data (resized, as bytes)
            - text: LLM-generated description (if PARSE_IMAGE_LLM_DESCRIPTION is also enabled)
            - doc_type: "image"
            - source_id, doc_id: from parent document
            - meta: includes page, image_number, etc.
        
        Note: If PARSE_IMAGE_ADDITIONAL_DOC is False, only the main document dict is returned,
        even if images were extracted for description generation.
        
    Environment variables:
        - PARSE_IMAGE_ADDITIONAL_DOC: If true, create separate Document objects for images.
                                      If false, images are only used for description generation.
        - PARSE_IMAGE_LLM_DESCRIPTION: If true, generate LLM descriptions for images.
                                      Descriptions are always added to main doc text (replacing placeholders).
                                      If PARSE_IMAGE_ADDITIONAL_DOC is also true, descriptions are also
                                      added to image dicts' text field.
        - PARSE_IMAGE_DESCRIPTION_MODEL: Model name for LLM descriptions (default: 'gpt-4o-mini')
        - PARSE_IMAGE_DESCRIPTION_NUMWORKERS: Number of workers for parallel processing (optional)
        - OPENAI_BASE_URL: Base URL for OpenAI API (optional)
        - OPENAI_API_KEY: API key for OpenAI (required if PARSE_IMAGE_LLM_DESCRIPTION is enabled)
    
    Raises:
        FileNotFoundError: If file_path is provided but file doesn't exist
        ValueError: If data is missing required fields, or neither file_path nor binary provided
        NotImplementedError: If the document type is not supported
    
    Example:
        ```
        from kb_mcp.parser import parse
        from kb_mcp.kb import Document, add, add_many
        
        # Parse from file path
        doc_dicts = parse("document.pdf", {
            "source_id": "mu2e-docdb",
            "doc_id": "1234"
        })
        
        # Parse from dict with binary
        doc_dicts = parse(data={
            "source_id": "mu2e-docdb",
            "doc_id": "1234",
            "binary": file_bytes,
            "meta": {"filename": "document.pdf"}
        })
        
        # Convert to Document objects
        documents = [Document.from_dict(d) for d in doc_dicts]
        ```
        
        # Add main document
        main_doc = add(documents[0])
    """
    # Handle binary data by creating temp file
    temp_file_created = False
    if file_path is None:
        # Parse from dict with binary data
        if data is None or "binary" not in data:
            raise ValueError("Either file_path or data with binary must be provided")
        
        doc_data = dict(data)
        
        # Validate required fields
        if "source_id" not in doc_data:
            raise ValueError("source_id is required in data dictionary")
        if "doc_id" not in doc_data:
            raise ValueError("doc_id is required in data dictionary")
        
        # Get filename from meta or use default
        filename = doc_data.get("meta", {}).get("filename", "document")
        suffix = Path(filename).suffix if Path(filename).suffix else ""
        
        # Create temporary file from binary data
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            tmp_file.write(doc_data["binary"])
            file_path = Path(tmp_file.name)
            temp_file_created = True
    else:
        # Parse from file path
        file_path = Path(file_path)

        # For marker-preloaded, we don't need the actual file to exist
        # We only use the filename stem to find pre-existing Marker output
        if parser_name != "marker-preloaded" and not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        # Prepare document data
        if data is None:
            # Create minimal document data
            doc_data = {
                "source_id": "local",  # Default source
                "doc_id": file_path.stem,  # Use filename without extension
            }
        else:
            doc_data = dict(data)
            # Validate required fields
            if "source_id" not in doc_data:
                raise ValueError("source_id is required in data dictionary")
            if "doc_id" not in doc_data:
                raise ValueError("doc_id is required in data dictionary")
    
    try:
        # Detect MIME type if not provided in data
        if "source_type" not in doc_data:
            if parser_name == "marker-preloaded":
                # For marker-preloaded, assume PDF
                mime_type = "application/pdf"
            else:
                mime_type = detect_mime_type(file_path)
            doc_data["source_type"] = mime_type
        else:
            mime_type = doc_data["source_type"]

        # Add file metadata to meta dict
        if parser_name == "marker-preloaded":
            # For marker-preloaded, we don't have the file, use placeholders
            doc_data.setdefault("meta", {}).update({
                "filename": file_path.name,
                "filepath": str(file_path.absolute()),
                "filesize": 0,
            })
        else:
            file_stat = file_path.stat()
            doc_data.setdefault("meta", {}).update({
                "filename": file_path.name,
                "filepath": str(file_path.absolute()),
                "filesize": file_stat.st_size,
            })
        if "uri" not in doc_data:
            doc_data["uri"] = f"file://{file_path.absolute()}"
            
        # Check if we need to extract images (for additional docs or descriptions)
        from ..config import get_parser_config
        parser_config = get_parser_config()
        if extract_images is None:
            create_additional_docs = parser_config['image_additional_doc']
        else:
            create_additional_docs = extract_images

        if describe_images is None:
            generate_llm_descriptions = parser_config['image_llm_description']
        else:
            generate_llm_descriptions = describe_images

        import time
        text_extraction_start = time.time()
        image_dicts = []

        # Default PDF parser is Docling (DocLayNet + TableFormer). Pass
        # parser_name="pypdf2" for the legacy fast path or
        # parser_name="marker" for marker-pdf.
        if mime_type == "application/pdf" and parser_name in (None, "kb-mcp"):
            parser_name = "docling"

        # Default PPTX parser is also Docling (since 2026-04-26). The
        # unified Docling path emits structural records (sections,
        # tables, figures) and a structured `parser_output` artefact that
        # the downstream multi-view consumers use; the legacy parser_pptx
        # returned only flat text. Pass parser_name="legacy" for the
        # python-pptx route.
        _PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        if mime_type == _PPTX_MIME and parser_name in (None, "kb-mcp"):
            parser_name = "docling"

        # Default HTML / XHTML parser is Docling (since 2026-04-26).
        # Mu2e Wiki content is the primary HTML source; the importer wraps
        # API-rendered MediaWiki HTML in a minimal shell and writes it as
        # text/html. Docling's HTML reader produces markdown-formatted
        # text with link syntax preserved, table records, and a
        # `parser_output` artefact — the previous TextParser route
        # stripped HTML to flat text. Pass parser_name="legacy" for
        # TextParser.
        if mime_type in ("text/html", "application/xhtml+xml") and parser_name in (None, "kb-mcp"):
            parser_name = "docling"

        # Default DOCX parser is Docling (since 2026-04-26).
        # Smoke on a synthetic Mu2e-style tech note (no real DOCX in our
        # local cache; DocDB has few): Docling preserves heading
        # hierarchy as Markdown, extracts tables as doc_type="table"
        # records, populates parser_output. Legacy parser_docx returned
        # only flat text with no structural records. Pass
        # parser_name="legacy" for the python-docx route.
        _DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if mime_type == _DOCX_MIME and parser_name in (None, "kb-mcp"):
            parser_name = "docling"

        # Optional PDF-only parsers, resolved lazily via importlib so their
        # heavy dependencies stay optional extras.
        _OPTIONAL_PDF_PARSERS = {
            "nougat": ("kb_mcp.parser.parser_nougat", "NougatParser"),
            "azure": ("kb_mcp.parser.parser_azure", "AzureParser"),
        }

        if parser_name in _OPTIONAL_PDF_PARSERS:
            if mime_type != "application/pdf":
                raise NotImplementedError(f"{parser_name} parser only supports PDF, got {mime_type}")
            module_path, class_name = _OPTIONAL_PDF_PARSERS[parser_name]
            import importlib
            mod = importlib.import_module(module_path)
            parser = getattr(mod, class_name)(file_path, mime_type)
        elif parser_name == "marker":
            #if mime_type != "text/plain":
            #if mime_type == "text/plain":
            #    parser = get_parser(file_path, doc_type="text/plain")
            #else:
            if True:
                # Marker-pdf implementation
                if mime_type != "application/pdf":
                    raise NotImplementedError(f"Marker parser only supports PDF, got {mime_type}")

                from .parser_marker import MarkerParser
                parser = MarkerParser(file_path, mime_type)
        elif parser_name == "docling":
            # Docling (IBM Research) implementation. Multi-format:
            # PDF + PPTX + DOCX + HTML + XLSX route through the
            # same DoclingParser, which produces the canonical
            # DoclingDocument JSON regardless of input format. The
            # multi-view extractors (sections, tables, pictures) operate
            # on the body schema and work format-agnostically. Anything
            # outside this set falls back to NotImplementedError so the
            # caller's parser_name="docling" choice doesn't silently
            # mis-route a CSV or a code file.
            _DOCLING_MIMES = {
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "text/html",
                "application/xhtml+xml",
            }
            if mime_type not in _DOCLING_MIMES:
                raise NotImplementedError(
                    f"Docling parser does not support {mime_type}. "
                    f"Supported: {sorted(_DOCLING_MIMES)}"
                )
            from .parser_docling import DoclingParser
            parser = DoclingParser(file_path, mime_type)
        elif parser_name == "pypdf2":
            # Explicit opt-in to legacy PyPDF2.
            if mime_type != "application/pdf":
                raise NotImplementedError(f"pypdf2 parser only supports PDF, got {mime_type}")
            from .parser_pdf import PDFParser
            parser = PDFParser(file_path, mime_type)
        elif parser_name == "legacy":
            # Explicit opt-out from the Docling-default routes
            # (PDF / PPTX / DOCX / HTML / XLSX). Falls through
            # to the bespoke parser registered in `get_parser()` for the
            # given MIME — e.g. parser_pptx.PPTXParser, parser_docx.DOCXParser.
            parser = get_parser(file_path, doc_type=mime_type)
        else:
            # Standard implementation for non-PDF types
            parser = get_parser(file_path, doc_type=mime_type)
            
            
        # Extract text and images (if enabled)
        if hasattr(parser, 'extract_text_and_images_dict') and (create_additional_docs or generate_llm_descriptions):
            # Parser supports image extraction (e.g., PDF)
            # Extract images if we need them for either additional docs or descriptions
            text, image_dicts = parser.extract_text_and_images_dict(doc_data)
        else:
            # Simple text extraction
            text = parser.get_text()
            image_dicts = []

        # If the parser produced a structured artifact, persist it on the doc
        # dict so it survives round-tripping through Document.from_dict,
        # which lands it in the parser-agnostic document_parser_outputs
        # table (one row per document). Any parser may expose
        # `structured_output`; the payload should self-identify its schema
        # (DoclingDocument dumps carry `schema_name`, which downstream
        # readers guard on).
        structured_output = getattr(parser, "structured_output", None)
        if structured_output is not None:
            doc_data["parser_output"] = structured_output

        text_extraction_time = time.time() - text_extraction_start
        
        # Generate image descriptions in parallel if enabled
        image_description_time = 0.0
        if generate_llm_descriptions and image_dicts:
            from .image_descriptions import generate_image_descriptions
            
            image_description_start = time.time()
            # Generate descriptions and update image dicts
            image_dicts = generate_image_descriptions(text, image_dicts)
            image_description_time = time.time() - image_description_start
            
            # Replace placeholders in main text with descriptions
            import re
            
            # Map image_name to generated description and metadata.
            image_descriptions = {}
            image_meta_by_name = {}
            for img_dict in image_dicts:
                if "text" not in img_dict or not img_dict["text"]:
                    continue
                image_name = img_dict.get("meta", {}).get("image_name")
                if not image_name:
                    continue
                image_descriptions[image_name] = img_dict["text"]
                image_meta_by_name[image_name] = img_dict.get("meta", {})

            def replace_image_placeholder(match):
                alt_text = match.group(1)
                image_name = match.group(2)
                
                description = image_descriptions.get(image_name)
                if description:
                    image_meta = image_meta_by_name.get(image_name, {})
                    image_number = image_meta.get("image_number")
                    if image_number is not None:
                        image_tag = f"[image_id:{image_name} image_num:{image_number}]"
                    else:
                        image_tag = f"[image_id:{image_name}]"

                    # Keep a stable image tag in the text for traceability.
                    return f"![{description}]({image_name}) {image_tag}"
                
                return match.group(0) # No change if no description

            # Regex to match markdown image tags: ![alt](image_name)
            text = re.sub(r'!\[(.*?)\]\((.*?)\)', replace_image_placeholder, text)

            # Fallback for old standard parser placeholders [Image n] if any remain
            for img_dict in image_dicts:
                if "text" in img_dict and img_dict["text"]:
                    img_number = img_dict["meta"].get("image_number")
                    if img_number is not None:
                        description = img_dict["text"]
                        placeholder = f"[Image {img_number}]"
                        if placeholder in text:
                            image_name = img_dict.get("meta", {}).get("image_name")
                            if image_name:
                                tag = f"[image_id:{image_name} image_num:{img_number}]"
                                text = text.replace(
                                    placeholder,
                                    f"[{placeholder}: {description}] {tag}"
                                )
                            else:
                                text = text.replace(placeholder, f"[{placeholder}: {description}]")
        
        # Filter images if needed (placeholder for future filtering logic)
        # For now, keep all images that have binary data (i.e., are real image dicts)
        filtered_image_dicts = [img_dict for img_dict in image_dicts if img_dict['doc_type'] == 'image']

        # Tables-as-records. DoclingParser populates
        # `parser.table_dicts` with one dict per detected table. We always
        # emit them — they're cheap, structurally distinct, and search code
        # can boost / filter by doc_type="table".
        table_dicts = list(getattr(parser, "table_dicts", None) or [])

        # Whole-table LLM summary. Gated on
        # PARSE_TABLE_LLM_SUMMARY — off by default so plain re-parses don't
        # incur API calls. When on, each table dict gets a 1–2 sentence
        # summary appended to text and stored in meta["summary"].
        table_summary_time = 0.0
        if parser_config['table_llm_summary'] and table_dicts:
            from .table_summaries import generate_table_summaries

            table_summary_start = time.time()
            table_dicts = generate_table_summaries(table_dicts)
            table_summary_time = time.time() - table_summary_start

        # Sections-as-records. One dict per section_header in
        # body order; lets search match at the section level via doc_type="section".
        section_dicts = list(getattr(parser, "section_dicts", None) or [])

        # Set text on main document dict
        doc_data["text"] = text
        if "doc_type" not in doc_data:
            doc_data["doc_type"] = "text"
        
        # Store timing information in meta for retrieval
        # This allows ingest() to extract timing without breaking the API
        if "meta" not in doc_data:
            doc_data["meta"] = {}
        doc_data["meta"]["_parsing_timing"] = {
            "text_extraction_time_seconds": round(text_extraction_time, 3),
            "image_description_time_seconds": round(image_description_time, 3) if image_description_time > 0 else None,
            "table_summary_time_seconds": round(table_summary_time, 3) if table_summary_time > 0 else None,
            "total_time_seconds": round(text_extraction_time + image_description_time + table_summary_time, 3),
        }
        
        # Return main document dict + section dicts + table dicts + image
        # document dicts. Tables and sections are always emitted (no
        # extract_images gate) — they're structural records, not raw image
        # binaries.
        if create_additional_docs:
            return [doc_data] + section_dicts + table_dicts + filtered_image_dicts
        else:
            return [doc_data] + section_dicts + table_dicts
        
    finally:
        # Clean up temp file if we created it
        if temp_file_created:
            try:
                file_path.unlink()
            except Exception:
                pass
