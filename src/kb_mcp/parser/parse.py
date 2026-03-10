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
        parser_name: Optional parser name (e.g., "kb-mcp", "marker"). 
                     If "marker", uses marker-pdf for parsing (PDF only).
                     If None or "kb-mcp", uses standard parsers.
    
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

        if parser_name == "marker":
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
        elif parser_name == "marker-preloaded":
            # Load pre-existing Marker output from disk instead of re-running Marker
            if mime_type != "application/pdf":
                raise NotImplementedError(f"Marker-preloaded parser only supports PDF, got {mime_type}")

            from .parser_marker_preloaded import MarkerPreloadedParser

            # Get marker output base from parser_config if available
            marker_output_base = None
            if parser_config and 'marker_output_base' in parser_config:
                marker_output_base = parser_config['marker_output_base']

            parser = MarkerPreloadedParser(file_path, mime_type, marker_output_base=marker_output_base)
        else:
            # Standard implementation
            # Get appropriate parser
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
            
            # Map of image_name to its generated description
            image_descriptions = {
                img_dict["meta"].get("image_name"): img_dict["text"]
                for img_dict in image_dicts
                if "text" in img_dict and img_dict["text"] and img_dict["meta"].get("image_name")
            }

            def replace_image_placeholder(match):
                alt_text = match.group(1)
                image_name = match.group(2)
                
                description = image_descriptions.get(image_name)
                if description:
                    # Update alt text with description
                    return f"![{description}]({image_name})"
                
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
                            text = text.replace(placeholder, f"[{placeholder}: {description}]")
        
        # Filter images if needed (placeholder for future filtering logic)
        # For now, keep all images that have binary data (i.e., are real image dicts)
        filtered_image_dicts = [img_dict for img_dict in image_dicts if img_dict['doc_type'] == 'image']
        
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
            "total_time_seconds": round(text_extraction_time + image_description_time, 3),
        }
        
        # Return main document dict + image document dicts
        if create_additional_docs:
            return [doc_data] + filtered_image_dicts
        else:
            return [doc_data]
        
    finally:
        # Clean up temp file if we created it
        if temp_file_created:
            try:
                file_path.unlink()
            except Exception:
                pass
