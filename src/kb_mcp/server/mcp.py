"""MCP tool definitions for the knowledge base server."""

import base64
import json
import logging
from typing import Optional, List, Dict, Any

from mcp.types import ImageContent

logger = logging.getLogger(__name__)


def _format_search_results(results: List[Dict[str, Any]], context_chars: int = 500) -> List[Dict[str, Any]]:
    """Format search results using a strict block-delimiter strategy.

    This format is designed to be model-agnostic. It works for Claude (which loves
    structure) and OSS models like Llama 3 (which rely on clear delimiters to
    distinguish data from instructions).

    Structure:
    1. [[DOCUMENT_METADATA]]: Key-Value headers with explicit tool commands.
    2. [[CONTENT_START]]: The actual text or excerpts.
    3. [[DOCUMENT_END]]: Clear termination.
    """
    formatted_results = []

    for result in results:
        doc = result.get("document")
        chunks = result.get("chunks", [])
        if not doc:
            continue

        doc_text = doc.text or ""
        doc_text_len = len(doc_text)
        # Unique ID for the model to use in subsequent calls
        doc_identifier = f"{doc.source_id}_{doc.doc_id}"
        doc_id = doc.id or doc_identifier

        # Prepare Content Body & Determine Status
        summary_chunks = [
            c for c in chunks
            if c.get("chunk_strategy") == "summary" or (c.get("char_start") is None and c.get("text"))
        ]
        positioned_chunks = [c for c in chunks if c.get("char_start") is not None and c.get("char_end") is not None]

        body_parts = []
        status = "FULL_TEXT"

        # Strategy A: Summary Chunks
        if summary_chunks:
            status = "SUMMARY_ONLY"
            body_parts.append("### AI-Generated Summary")
            for chunk in summary_chunks:
                if chunk.get("text"):
                    body_parts.append(chunk["text"])

        # Strategy B: Small Document (Return everything)
        elif doc_text_len < 2000:
            body_parts.append(doc_text)

        # Strategy C: Larger Document with Search Hits (Excerpts)
        elif positioned_chunks:
            status = "EXCERPTS_WITH_MATCHES"
            if doc.summary:
                body_parts.append(f"**Gist:** {doc.summary}\n")

            body_parts.append("RELEVANT EXCERPTS (Search matches wrapped in <match> tags):")

            # Sort and Merge Overlapping Chunks
            sorted_chunks = sorted(positioned_chunks, key=lambda x: x["char_start"])
            merged_ranges = []
            for chunk in sorted_chunks:
                start = max(0, chunk["char_start"] - context_chars)
                end = min(doc_text_len, chunk["char_end"] + context_chars)

                if merged_ranges and start <= merged_ranges[-1]["end"]:
                    # Extend previous range
                    merged_ranges[-1]["end"] = end
                    merged_ranges[-1]["highlights"].append((chunk["char_start"], chunk["char_end"]))
                else:
                    # New range
                    merged_ranges.append({
                        "start": start,
                        "end": end,
                        "highlights": [(chunk["char_start"], chunk["char_end"])]
                    })

            # Format the merged ranges with XML tags
            for i, r in enumerate(merged_ranges, 1):
                excerpt = doc_text[r["start"]:r["end"]]
                
                # Apply <match> tags working backwards to preserve indices
                for hs, he in sorted(r["highlights"], reverse=True):
                    rs = hs - r["start"]
                    re = he - r["start"]
                    if 0 <= rs < len(excerpt) and 0 <= re <= len(excerpt):
                        excerpt = f"{excerpt[:rs]}<match>{excerpt[rs:re]}</match>{excerpt[re:]}"

                prefix = "..." if r["start"] > 0 else ""
                suffix = "..." if r["end"] < doc_text_len else ""
                body_parts.append(f"\n--- Excerpt {i} ---\n{prefix}{excerpt}{suffix}")

        # Strategy D: Fallback Preview
        else:
            status = "PREVIEW_ONLY"
            if doc.summary:
                body_parts.append(f"**Summary:** {doc.summary}\n")
            preview = doc_text[:2000] + ("..." if doc_text_len > 2000 else "")
            body_parts.append(f"PREVIEW (First 2000 chars):\n{preview}")

        # Build Universal Metadata Header (Key-Value)
        header_kv = {
            "ID": doc_id,
            "TITLE": doc.title or doc.title_gen or 'Untitled',
            "URI": doc.uri or "N/A",
            "TYPE": doc.doc_type,
            "STATUS": f"{status} (Total len: {doc_text_len:,} chars)",
        }

        # Add document metadata if available
        if doc.meta:
            for k, v in doc.meta.items():
                # Things to skip
                if k in ["file_name", "file_size"]: continue 
                
                if isinstance(v, (str, int, float, bool)):
                    header_kv[k.upper()] = v

        # Add explicit commands for the LLM to use if it needs more info
        if status != "FULL_TEXT":
            header_kv["COMMAND_RETRIEVE_FULL"] = f'kb_get("{doc_identifier}")'

        # Check for image capability (PDFs, Mixed media)
        if doc.doc_type in ["pdf", "mixed", "slide"] or doc.source_type == "application/pdf":
            header_kv["COMMAND_RETRIEVE_IMAGE"] = f'kb_get_image("{doc.source_id}", "{doc.doc_id}", "image_filename.png")'

        header_str = "\n".join([f"{k}: {v}" for k, v in header_kv.items()])
        body_str = "\n\n".join(body_parts)


        # Put everything together
        final_text = (
            "[[DOCUMENT_METADATA]]\n"
            f"{header_str}\n"
            "[[CONTENT_START]]\n"
            f"{body_str}\n"
            "[[DOCUMENT_END]]"
        )

        formatted_results.append({
            "source_id": doc.source_id,
            "doc_id": doc.doc_id,
            "title": doc.title,
            "text": final_text,
            "content_status": status
        })

    return formatted_results


def kb_search(
    query: str,
    max_results: int = 5,
    search_type: str = "hybrid",
    search_filter: str | None = None,
) -> str:
    """Search the knowledge base.

    Returns formatted results with metadata blocks. Small docs (<2000 chars) in full,
    large docs as excerpts with <match> tags highlighting matches.

    Args:
        query: Search query
        max_results: Max documents to return (default: 5)
        search_type: "hybrid" (recommended), "semantic", or "fulltext" (default: "hybrid")
        search_filter: Optional JSON filter, e.g. `{"term": {"source_id": "inspire-sld"}}`

    Returns:
        JSON with results in [[DOCUMENT_METADATA]]/[[CONTENT_START]] blocks.
        Use kb_get() for full docs if STATUS shows EXCERPTS/SUMMARY/PREVIEW.
    """
    from ..kb import search
    from ..kb.search import search_semantic, search_fulltext

    try:
        filter_dict = None
        if search_filter:
            try:
                filter_dict = json.loads(search_filter)
            except json.JSONDecodeError as e:
                return json.dumps({"error": f"Invalid filter JSON: {e}"}, indent=2)

        # Select search function based on type
        if search_type == "semantic":
            search_fn = search_semantic
        elif search_type == "fulltext":
            search_fn = search_fulltext
        else:  # hybrid (default)
            search_fn = search

        response = search_fn(
            query=query,
            max_results=max_results,
            filter=filter_dict,
        )

        results = response.get("results", [])

        if not results:
            return json.dumps({"message": "No results found", "results": []}, indent=2)

        formatted_results = _format_search_results(results)

        return json.dumps({
            "count": len(formatted_results),
            "results": formatted_results,
        }, indent=2)

    except Exception as e:
        logger.error(f"Error in kb_search: {e}", exc_info=True)
        return json.dumps({"error": str(e)}, indent=2)


def kb_get(identifier: str) -> str:
    """Get the complete content of a document.

    Returns raw text with a metadata header, optimized for LLM reading.

    Args:
        identifier: Document ID (e.g., "source_id_doc_id" or UUID)

    Returns:
        Raw text with structured metadata header and full document content.
    """
    from ..kb import get

    try:
        result = get(identifier=identifier)

        if result is None:
            return "ERROR: Document not found"

        if isinstance(result, list):
            # Prefer marker parser if available
            for k, res in enumerate(result):
                if res.parser_id == "marker":
                    result = result[k]
                    break
        if isinstance(result, list):
            result = result[0]

        doc = result

        # Format metadata as a clean block
        meta_lines = [
            f"Title: {doc.title or doc.title_gen or 'Untitled'}",
            f"ID: {doc.doc_id}",
            f"Source: {doc.source_id}",
            f"Type: {doc.doc_type}",
        ]

        if doc.uri:
            meta_lines.append(f"URI: {doc.uri}")

        # Add document metadata if available
        if doc.meta:
            for k, v in doc.meta.items():
                # Skip certain fields
                if k in ["file_name", "file_size", "filename", "filesize"]:
                    continue
                if isinstance(v, (str, int, float, bool)):
                    meta_lines.append(f"{k}: {v}")
                if isinstance(v, list) and all(isinstance(i, (str, int, float, bool)) for i in v):
                    meta_lines.append(f"{k}: {', '.join(map(str, v))}")

        meta_header = "\n".join(meta_lines)

        # Return the natural text with metadata header
        return f"""[[DOCUMENT_METADATA]]
{meta_header}
[[CONTENT_START]]
{doc.text or "No content available"}
[[DOCUMENT_END]]"""

    except Exception as e:
        logger.error(f"Error in kb_get: {e}", exc_info=True)
        return f"ERROR: {str(e)}"


def kb_get_image(source_id: str, doc_id: str, image_filename: str) -> ImageContent:
    """Retrieve an image from a document as an MCP Image resource.

    Args:
        source_id: The source_id from the document metadata
        doc_id: The parent document's doc_id
        image_filename: The filename referenced in the text (e.g., "fig1.png")

    Returns:
        MCP Image with base64 data and mimeType.
    """
    from ..kb import get

    try:
        # Construct the image document identifier
        image_doc_id = f"{doc_id}-{image_filename}"
        image_identifier = f"{source_id}_{image_doc_id}"

        # Try to get the image document directly using identifier
        image_doc = get(image_identifier)

        if not image_doc:
            raise ValueError(f"Image not found: {image_filename}")

        if isinstance(image_doc, list):
            image_doc = image_doc[0]

        if image_doc.doc_type != "image" or not image_doc.binary:
            raise ValueError(f"Document found but is not an image: {image_filename}")

        # Determine format from metadata or filename
        image_format = "png"  # default
        if image_doc.meta and "format" in image_doc.meta:
            image_format = image_doc.meta["format"]
        elif "." in image_filename:
            ext = image_filename.rsplit(".", 1)[-1].lower()
            if ext in ["jpg", "jpeg", "png", "gif", "webp"]:
                image_format = ext

        # Map format to MIME type
        mime_types = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
        }
        mime_type = mime_types.get(image_format, "image/png")

        # Encode binary data to base64
        base64_data = base64.b64encode(image_doc.binary).decode('utf-8')

        return ImageContent(
            type="image",
            data=base64_data,
            mimeType=mime_type
        )

    except Exception as e:
        logger.error(f"Error in kb_get_image: {e}", exc_info=True)
        raise


def register_tools(mcp):
    """Register MCP tools with the FastMCP instance."""
    mcp.tool()(kb_search)
    mcp.tool()(kb_get)
    mcp.tool()(kb_get_image)  # Keep for explicit image retrieval by filename


def register_resources(mcp, oauth_provider, base_url):
    """Register MCP resources with the FastMCP instance."""

    @mcp.resource("kb://sources")
    def list_kb_sources() -> str:
        """List all available sources in the knowledge base."""
        from ..kb import list_sources

        try:
            sources = list_sources()
            return json.dumps({
                "sources": [
                    {
                        "source_id": s.get("source_id"),
                        "name": s.get("name"),
                        "document_count": s.get("document_count"),
                    }
                    for s in sources
                ],
            }, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)}, indent=2)

    @mcp.resource("kb://document/{doc_id}")
    def get_kb_document(doc_id: str) -> str:
        """Get a document by its UUID."""
        from ..kb import get

        try:
            doc = get(uid=doc_id)
            if doc is None:
                return json.dumps({"error": "Document not found"}, indent=2)

            return json.dumps({
                "source_id": doc.source_id,
                "doc_id": doc.doc_id,
                "title": doc.title or doc.title_gen,
                "summary": doc.summary,
                "text": doc.text,
            }, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)}, indent=2)


def register_prompts(mcp):
    """Register MCP prompts with the FastMCP instance."""

    @mcp.prompt()
    def search_kb(topic: str) -> str:
        """Search the knowledge base for information about a topic."""
        return f"""Search the knowledge base for information about: {topic}

Use the kb_search tool with the query "{topic}" to find relevant documents.
Then summarize the key findings from the search results."""

    @mcp.prompt()
    def research_question(question: str) -> str:
        """Research a question using the knowledge base."""
        return f"""Research the following question using the knowledge base:

Question: {question}

Steps:
1. Use kb_search to find relevant documents
2. Review the search results and identify the most relevant documents
3. Use kb_get to retrieve full content of key documents if needed
4. Synthesize the information to answer the question
5. Cite the source documents in your response"""