"""Main parse function for document parsing."""

import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .text_utils import clean_text
from .utils import detect_mime_type, get_parser


# MIME types the Docling defaults cover. Docling emits structural records
# (sections, tables, figures) and a `parser_output` artefact that the
# multi-view consumers need; the legacy per-type parsers returned flat text
# only. Pass parser_name="legacy" (or a specific backend) to opt out.
_DOCLING_DEFAULT_MIMES = frozenset({
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/html",
    "application/xhtml+xml",
})

# Request-level values meaning "pick the right backend for this file type",
# as opposed to naming a concrete parser. "kb-mcp" is the historical spelling.
AUTO_PARSER_NAMES = (None, "kb-mcp", "auto")


def resolve_parser_name(mime_type: Optional[str], parser_name: Optional[str]) -> Optional[str]:
    """Resolve an auto-pick request to the backend that will actually run.

    `parser_name="kb-mcp"` (or None) means "choose for me"; it is not a
    parser. Left unresolved it ends up recorded as the thing that produced a
    document, which is useless — every row says "kb-mcp" whether Docling,
    Marker or the legacy text path ran. Resolving it up front lets callers
    store the real backend in `documents.parser_id`.

    A concrete name is returned untouched, so this is safe to apply more than
    once along a call path.

    Args:
        mime_type: Detected MIME type of the file, or None if unknown.
        parser_name: Requested parser, or an auto-pick sentinel.

    Returns:
        The concrete backend name for Docling-default types, otherwise
        `parser_name` unchanged — including the sentinel, which `get_parser()`
        resolves per type further down.
    """
    if parser_name in AUTO_PARSER_NAMES and mime_type in _DOCLING_DEFAULT_MIMES:
        return "docling"
    return parser_name


def _deref_cref(structured_output: Dict[str, Any], cref: str) -> Optional[dict]:
    """Resolve a DoclingDocument cref like `#/groups/3` to its node dict."""
    try:
        _, category, idx = cref.split("/")
        return structured_output[category][int(idx)]
    except (KeyError, IndexError, ValueError, AttributeError, TypeError):
        return None


def _iter_picture_crefs(structured_output: Dict[str, Any],
                         children: List[dict]):
    """Yield `#/pictures/N` crefs reachable from `children`, recursing into
    any container node that itself carries a `children` list.

    PDFs put pictures directly under `body`, but PPTX/DOCX files wrap each
    slide/section in its own group (`body -> groups[slide] -> pictures,
    texts, ...`), and a picture pasted into a table cell nests even deeper
    (`body -> tables/N -> groups/M -> pictures/K`) — Docling's `parent`
    chain shows both shapes in real documents. A scan of only
    `body["children"]` finds none of these, which is exactly what left
    picture markers unsubstituted despite their descriptions already
    existing on the child records. Recursing generically on "does this
    node have children" rather than hardcoding container types (groups vs.
    tables) means a container type added later doesn't reopen this bug.
    """
    for child in children:
        if not isinstance(child, dict):
            continue
        cref = child.get("cref") or ""
        if cref.startswith("#/pictures/"):
            yield cref
            continue
        node = _deref_cref(structured_output, cref)
        nested = (node or {}).get("children") or []
        if nested:
            yield from _iter_picture_crefs(structured_output, nested)


def inline_docling_image_descriptions(
    text: str,
    image_dicts: List[dict],
    structured_output: Optional[Dict[str, Any]],
) -> str:
    """Replace Docling's `<!-- image -->` markers with image descriptions.

    Docling doesn't emit markdown image syntax — `export_to_markdown()`
    writes a bare `<!-- image -->` HTML comment at each picture's position.
    The `![alt](name)` regex used for other parsers never matches it, so
    without this the descriptions never reach the parent document's text.

    Each marker becomes::

        ![{description}]({image_doc_id}) [image_id:{image_doc_id} image_num:{N}]

    putting the description inline where the figure sits, tagged with the
    image record's own `doc_id` — the identifier that actually resolves via
    the KB tools. (`meta["image_name"]` is only a bare filename like
    `_page_29_Figure_1.png`, meaningful relative to its parent; the child's
    doc_id is the parent's doc_id plus that name, and is what a reader or an
    LLM can look the figure up by.) Falls back to `image_name` when the
    record carries no doc_id.

    Markers are consumed in body order and matched against the picture
    crefs reachable from `structured_output["body"]["children"]`, recursing
    into `#/groups/N` (see `_iter_picture_crefs`) — PDFs put pictures
    directly under body, but PPTX/DOCX wrap each slide/section in its own
    group. Keying on the cref — rather than counting markers positionally
    against `image_dicts` — keeps the mapping correct when a picture was
    skipped during extraction (e.g. `picture.get_image()` returned None),
    which would otherwise shift every later description onto the wrong
    image.

    Args:
        text: Markdown text from `export_to_markdown()`.
        image_dicts: Extracted picture records, carrying `doc_id`,
            `meta["self_ref"]`, `meta["image_number"]` and either
            `meta["description"]` or a `text` fallback. `meta["image_name"]`
            is used as the reference only when `doc_id` is absent.
        structured_output: The DoclingDocument payload, for body order.

    Returns:
        `text` with markers substituted. Returned unchanged when there are
        no markers, no structured output, or nothing to substitute.
    """
    if "<!-- image -->" not in text or not structured_output:
        return text

    image_by_self_ref = {}
    for img_dict in image_dicts:
        meta = img_dict.get("meta", {})
        self_ref = meta.get("self_ref")
        if self_ref and (meta.get("description") or img_dict.get("text")):
            image_by_self_ref[self_ref] = img_dict
    if not image_by_self_ref:
        return text

    body_children = (structured_output.get("body") or {}).get("children") or []
    picture_crefs = list(_iter_picture_crefs(structured_output, body_children))
    marker_iter = iter(picture_crefs)

    def _replace(match):
        cref = next(marker_iter, None)
        if cref is None:
            return match.group(0)
        img_dict = image_by_self_ref.get(cref)
        if img_dict is None:
            return match.group(0)
        meta = img_dict.get("meta", {})
        # Prefer the image record's own doc_id — that's the resolvable
        # identifier. image_name is a bare filename and only a fallback.
        image_ref = img_dict.get("doc_id") or meta.get("image_name")
        image_number = meta.get("image_number")
        # Inline the VLM description only. `img_dict["text"]` also bundles
        # nearby_text + caption for the figure record's own retrieval, but
        # those are the paragraphs already surrounding this marker —
        # re-inlining them would duplicate that prose into the parent text.
        description = (meta.get("description") or img_dict.get("text") or "").strip()
        description = " ".join(description.split())
        if not description:
            return match.group(0)
        if image_ref is None:
            return f"![{description}]()"
        if image_number is not None:
            image_tag = f"[image_id:{image_ref} image_num:{image_number}]"
        else:
            image_tag = f"[image_id:{image_ref}]"
        return f"![{description}]({image_ref}) {image_tag}"

    return re.sub(r'<!-- image -->', _replace, text)


#: Passed as `page_break_placeholder` to `DoclingDocument.export_to_markdown()`.
#: Docling emits this identical literal at every page transition — it carries
#: no page number of its own (that's a private detail of its serializer) — so
#: `number_docling_page_breaks` replaces each occurrence with the real number
#: read from the body tree.
DOCLING_PAGE_BREAK_PLACEHOLDER = "<!-- page-break -->"


def number_docling_page_breaks(text: str, structured_output: Optional[Dict[str, Any]]) -> str:
    """Replace bare page-break markers with `<!-- page:N -->`, and prefix the
    document with one for its first page.

    `export_to_markdown(page_break_placeholder=...)` marks where pages change
    but not which page it changed *to* — every marker is the same literal
    string — and it marks transitions only, so the first page has no marker
    at all. The body tree knows both: each text element carries
    `prov[0].page_no`, and Docling's serializer visits the tree in the same
    reading order it exports, so the page number at each marker is recovered
    by walking the body once and zipping the page-number sequence against
    the markers in order — the same "consume markers in body order" idiom as
    `inline_docling_image_descriptions` and `_fill_undecoded_formulas`, not a
    text search. Prepending a marker for the first page means a reader of
    `text` never needs a separate "what page did it start on" query: every
    page, including the first, is announced by exactly one `<!-- page:N -->`.

    Recurses into groups and into a text item's own children (nested HTML
    documents put a whole section under its `section_header`), because a
    page transition can happen anywhere in that subtree, not just among
    `body.children` directly — the flat PDF shape has no such children, so
    the recursion is a no-op there and this walks exactly the top level.

    Args:
        text: Markdown text from `export_to_markdown(page_break_placeholder=
            DOCLING_PAGE_BREAK_PLACEHOLDER)`.
        structured_output: The DoclingDocument payload the export came from.

    Returns:
        `text` with a leading `<!-- page:N -->` and every placeholder
        replaced by its numbered form. Unchanged if there are no page
        numbers to attribute at all.
    """
    if not structured_output:
        return text

    texts_by_ref = {
        t.get("self_ref") or f"#/texts/{i}": t
        for i, t in enumerate(structured_output.get("texts") or [])
    }
    groups_by_ref = {
        g.get("self_ref") or f"#/groups/{i}": g
        for i, g in enumerate(structured_output.get("groups") or [])
    }

    # The page number as of each marker crossed, in reading order — one
    # entry per *change*, so this lines up 1:1 with the markers Docling
    # inserted (one per transition, not one per element).
    pages_at_breaks: List[int] = []
    first_page: Optional[int] = None
    current_page: Optional[int] = None
    visited: set = set()

    def visit(cref: Optional[str]) -> None:
        nonlocal current_page, first_page
        if not cref or cref in visited:
            return
        visited.add(cref)
        if cref.startswith("#/groups/"):
            for c in (groups_by_ref.get(cref) or {}).get("children") or []:
                visit(c.get("cref") if isinstance(c, dict) else None)
            return
        if not cref.startswith("#/texts/"):
            return
        t = texts_by_ref.get(cref) or {}
        prov = t.get("prov") or []
        if prov and isinstance(prov[0], dict):
            page_no = prov[0].get("page_no")
            if page_no is not None and page_no != current_page:
                if current_page is None:
                    first_page = page_no
                else:
                    pages_at_breaks.append(page_no)
                current_page = page_no
        for c in t.get("children") or []:
            visit(c.get("cref") if isinstance(c, dict) else None)

    for child in (structured_output.get("body") or {}).get("children") or []:
        visit(child.get("cref") if isinstance(child, dict) else None)

    if first_page is None:
        # No element carries a page number at all (not a PDF, or a reader
        # that doesn't set prov) — nothing to attribute, markers or not.
        return text

    if pages_at_breaks:
        marker_iter = iter(pages_at_breaks)

        def _replace(match):
            page_no = next(marker_iter, None)
            if page_no is None:
                return match.group(0)
            return f"<!-- page:{page_no} -->"

        text = re.sub(re.escape(DOCLING_PAGE_BREAK_PLACEHOLDER), _replace, text)

    return f"<!-- page:{first_page} -->\n\n{text}"


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

        # Turn the "pick one for me" request into the backend that will
        # actually run. Callers that record which parser produced a document
        # resolve this first (see add_document) and pass the concrete name in,
        # so this is a no-op for them.
        parser_name = resolve_parser_name(mime_type, parser_name)

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
            
            
        # Extract text using the real doc_data whenever the richer hook is
        # available, regardless of create_additional_docs/generate_llm_descriptions.
        # Tables are always emitted below via parser.table_dicts (no
        # extract_images gate), and DoclingParser builds them from this same
        # parent_data argument — passing {} here (as the plain get_text()
        # path used to) left every table with source_id="local", which isn't
        # a registered Source and made add_document() fail outright for any
        # document containing one.
        if hasattr(parser, 'extract_text_and_images_dict'):
            text, image_dicts = parser.extract_text_and_images_dict(doc_data)
            text = clean_text(text)
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
        
        # Generate image descriptions in parallel if enabled.
        # Token counters are collected here and stashed in meta below: the
        # documents row doesn't exist yet at parse time, so ingest() is what
        # persists them once there's an id to attribute them to.
        image_description_usage: Dict[str, Any] = {}
        table_summary_usage: Dict[str, Any] = {}
        image_description_time = 0.0
        if generate_llm_descriptions and image_dicts:
            from .image_descriptions import generate_image_descriptions
            
            image_description_start = time.time()
            # Generate descriptions and update image dicts
            image_dicts = generate_image_descriptions(
                text, image_dicts, usage_out=image_description_usage
            )
            image_description_time = time.time() - image_description_start
            
            # Map image_name to generated description and metadata.
            image_descriptions = {}
            image_meta_by_name = {}
            image_doc_id_by_name = {}
            for img_dict in image_dicts:
                if "text" not in img_dict or not img_dict["text"]:
                    continue
                image_name = img_dict.get("meta", {}).get("image_name")
                if not image_name:
                    continue
                image_descriptions[image_name] = img_dict["text"]
                image_meta_by_name[image_name] = img_dict.get("meta", {})
                image_doc_id_by_name[image_name] = img_dict.get("doc_id")

            def replace_image_placeholder(match):
                alt_text = match.group(1)
                image_name = match.group(2)

                description = image_descriptions.get(image_name)
                if description:
                    image_meta = image_meta_by_name.get(image_name, {})
                    image_number = image_meta.get("image_number")
                    # Tag with the image record's own doc_id — the resolvable
                    # identifier — falling back to the bare filename. The link
                    # target stays `image_name`, which is what the original
                    # markdown referenced.
                    image_ref = image_doc_id_by_name.get(image_name) or image_name
                    if image_number is not None:
                        image_tag = f"[image_id:{image_ref} image_num:{image_number}]"
                    else:
                        image_tag = f"[image_id:{image_ref}]"

                    # Keep a stable image tag in the text for traceability.
                    return f"![{description}]({image_name}) {image_tag}"

                return match.group(0) # No change if no description

            # Regex to match markdown image tags: ![alt](image_name)
            text = re.sub(r'!\[(.*?)\]\((.*?)\)', replace_image_placeholder, text)

            text = inline_docling_image_descriptions(
                text, image_dicts, structured_output
            )

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
            table_dicts = generate_table_summaries(
                table_dicts, usage_out=table_summary_usage
            )
            table_summary_time = time.time() - table_summary_start

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

        # Carry LLM token usage out of parsing the same way timing is carried.
        # ingest() pops this and writes llm_usage rows against the document id.
        parsing_usage = {}
        if image_description_usage:
            parsing_usage["image_description"] = image_description_usage
        if table_summary_usage:
            parsing_usage["table_summary"] = table_summary_usage
        if parsing_usage:
            doc_data["meta"]["_parsing_llm_usage"] = parsing_usage
        
        # Return main document dict + table dicts + image document dicts.
        # Tables are always emitted (no extract_images gate) — they're
        # structural records, not raw image binaries.
        if create_additional_docs:
            return [doc_data] + table_dicts + filtered_image_dicts
        else:
            return [doc_data] + table_dicts
        
    finally:
        # Clean up temp file if we created it
        if temp_file_created:
            try:
                file_path.unlink()
            except Exception:
                pass
