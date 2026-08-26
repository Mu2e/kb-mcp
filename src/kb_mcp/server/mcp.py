"""MCP tool definitions for the knowledge base server."""

import base64
import json
import logging
import re
from typing import Optional, List, Dict, Any

from mcp.types import ImageContent
from mcp.server.fastmcp import Context


logger = logging.getLogger(__name__)

#: Documents returned by kb_search when the caller doesn't specify a count and
#: the query router is off (or declines to set one).
DEFAULT_MAX_RESULTS = 5


# Graph imports
from ..kb.graph import (
    get_node,
    find_paths,
    get_nodes_for_document,
    get_relation_evidence,
)



def _is_strict_uuid4(val):
    uuid4_regex = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', 
    re.IGNORECASE
    )
    return bool(uuid4_regex.match(val))

def _format_node_output(node_data: Dict[str, Any]) -> str:
    """Format a graph node and its relations into a structured text block."""
    node = node_data["node"]
    outgoing = node_data.get("outgoing_relations", [])
    incoming = node_data.get("incoming_relations", [])
    linked_docs = node_data.get("linked_documents", [])
    stats = node_data.get("statistics", {})

    lines = []

    # Header Block
    lines.append("[[NODE_METADATA]]")
    lines.append(f"ID: {node['id']}")
    lines.append(f"NAME: {node['name']}")
    lines.append(f"TYPE: {node['type']}")
    if node['aliases']:
        lines.append(f"ALIASES: {', '.join(node['aliases'])}")
    lines.append(f"TOTAL_RELATIONS: {stats.get('total_relations', 0)}")

    # Relations Block
    lines.append("\n[[RELATIONSHIPS]]")

    if outgoing:
        lines.append("\n=> OUTGOING (This Node acts as Source):")
        for rel in outgoing:
            target = rel["target_node"]
            line = f"  --[{rel['verb']}]--> {target['name']} ({target['type']})"

            # Metadata
            meta = []
            if rel['evidence_count'] > 0:
                meta.append(f"{rel['evidence_count']} evidence")
            if rel['max_confidence']:
                meta.append(f"conf: {rel['max_confidence']:.2f}")

            if meta:
                line += f"  [{', '.join(meta)}]"
            line += f"  CMD_NODE: kb_lookup_node(\"{target['id']}\")"
            if rel['evidence_count'] > 0:
                line += f"  CMD_EVIDENCE: kb_node_relation_evidence(\"{rel['relation_id']}\")"
            lines.append(line)

    if incoming:
        lines.append("\n<= INCOMING (This Node acts as Target):")
        for rel in incoming:
            source = rel["source_node"]
            line = f"  <--[{rel['verb']}]-- {source['name']} ({source['type']})"

            meta = []
            if rel['evidence_count'] > 0:
                meta.append(f"{rel['evidence_count']} evidence")
            if rel['max_confidence']:
                meta.append(f"conf: {rel['max_confidence']:.2f}")

            if meta:
                line += f"  [{', '.join(meta)}]"
            line += f"  CMD_NODE: kb_lookup_node(\"{source['id']}\")"
            if rel['evidence_count'] > 0:
                line += f"  CMD_EVIDENCE: kb_node_relation_evidence(\"{rel['relation_id']}\")"
            lines.append(line)

    # Evidence / Documents Block
    lines.append("\n[[LINKED_CONTENT]]")
    if linked_docs:
        lines.append(f"Node is mentioned in {len(linked_docs)} documents.")
        # Only show first 20 to avoid token overflow
        for doc in linked_docs[:20]:
            # Handle both formats: simple string (doc_id) or dict with metadata
            if isinstance(doc, dict):
                doc_type = doc.get('type', 'unknown')
                id = doc.get('id')
                source_id = doc.get('source_id')
                doc_id = doc.get('doc_id') or doc.get('id')
                title = doc.get('title', doc_id)
                if title is None:
                    title = doc_id
                mention_count = doc.get('mention_count', 0)
                # Use source_id/doc_id format for cleaner identifiers
                doc_identifier = f"{source_id}/{doc_id}" if source_id and doc_id else (id or doc_id)
                line = f"- \"{title}\" ({doc_type}) [Source: {source_id}, Mentions: {mention_count}] "
                if doc_type == "image":
                    line += f"-> CMD: kb_get_image(\"{doc_identifier}\")"
                else:
                    line += f"-> CMD: kb_get(\"{doc_identifier}\")"
                lines.append(line)
            else:
                # Backward compatibility: simple string format
                lines.append(f"- Document ID: {doc} -> CMD: kb_get(\"{doc}\")")

        if len(linked_docs) > 20:
            lines.append(f"... (and {len(linked_docs) - 20} more)")
    else:
        lines.append("No direct document links found.")

    return "\n".join(lines)


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
        from ..chunking import base_strategy
        summary_chunks = [
            c for c in chunks
            if base_strategy(c.get("chunk_strategy") or "") == "summary"
            or (c.get("char_start") is None and c.get("text"))
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
            header_kv["COMMAND_RETRIEVE_FULL"] = f'kb_get("{doc.source_id}/{doc.doc_id}")'

        # Check for image capability (PDFs, Mixed media)
        if doc.doc_type in ["pdf", "mixed", "slide"] or doc.source_type == "application/pdf":
            header_kv["COMMAND_RETRIEVE_IMAGE"] = f'kb_get_image("{doc.source_id}/{doc.doc_id}", "image_filename.png")'

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
    max_results: int | None = None,
    search_type: str = "hybrid",
    search_filter: dict | None = None,
    rerank: bool | None = None,
) -> str:
    """Search the Mu2e experiment knowledge base (Fermilab muon-to-electron conversion).

    Mu2e DocDB and the Mu2e collaboration wiki are the authoritative sources and
    answer most questions; published papers, meeting transcripts and uploads are
    also indexed. The server instructions describe each source in detail.

    Returns formatted results with metadata blocks. Small docs (<2000 chars) in full,
    large docs as excerpts with <match> tags highlighting matches.

    Args:
        query: Search query
        max_results: Max documents to return. Omit to let the server choose
                (5, or a count the query router picks for this kind of query).
        search_type: "hybrid" (default, use this). "fulltext" only to match an exact
                string such as a DocDB number or filename; "semantic" only when the
                wording is expected to differ entirely from the source.
        search_filter: Optional filter, written as a subset of the Elasticsearch
                Query DSL. Filter to the Mu2e sources when the question is
                Mu2e-internal and papers would be noise:
                  {"term": {"source_id": "mu2e-docdb"}}
                  {"terms": {"source_id": ["mu2e-docdb", "mu2e-wiki"]}}
                  {"bool": {"must": [{"term": {"source_id": "mu2e-docdb"}},
                                     {"term": {"doc_type": "table"}}]}}
                Fields: source_id (mu2e-docdb, mu2e-wiki, inspire-hep,
                MeetingTranscripts, upload, test-flow), doc_type (text, section,
                table, image, meeting_comment), doc_id, title, title_gen, the
                insert_time/creating_time/update_time timestamps, and any
                document metadata key.
                Queries: term, terms, range ({"gte":..,"lte":..}), match,
                wildcard (* and ?), bool (must / should / must_not).
                Three departures from Elasticsearch: `match` is a plain substring
                test, not analysed full-text (put full-text in `query` instead);
                `range` on a metadata key compares as text, so numeric ranges only
                sort correctly for zero-padded or ISO-8601 values; and `must_not`
                also excludes documents that lack the field entirely.
                Anything unsupported is reported as an error, not matched silently.
        rerank: True to force cross-encoder reranking, False to disable. Omit to let
                the server decide.

    Returns:
        JSON. Each result carries source_id, doc_id, title and a `text` field
        holding a [[DOCUMENT_METADATA]] header (ID, TITLE, URI, TYPE, STATUS)
        followed by the content. Pass the header's ID to kb_get for the full
        document whenever STATUS shows EXCERPTS, SUMMARY or PREVIEW.
        No matches returns {"message": "No results found", "results": []} - a valid
        empty answer, not an error to retry. A malformed or unsupported
        search_filter returns {"error": "Invalid search_filter: ..."} naming the
        supported queries; fix the filter rather than concluding nothing matched.
    """
    from ..kb import search
    from ..kb.search import search_semantic, search_fulltext

    try:
        filter_dict = None
        if search_filter:
            if isinstance(search_filter, dict):
                filter_dict = search_filter
            elif isinstance(search_filter, str):
                try:
                    filter_dict = json.loads(search_filter)
                except json.JSONDecodeError as e:
                    return json.dumps({"error": f"Invalid filter JSON: {e}"}, indent=2)

        # Validate the filter before searching. search_hybrid catches a failing
        # branch and reports "No results found", so without this an unsupported
        # or malformed filter is indistinguishable from one that legitimately
        # matched nothing - and the caller has no way to learn it wrote a bad
        # filter. Parsing here is cheap and builds no query.
        if filter_dict:
            from sqlalchemy.orm import aliased
            from ..kb.db_models import Document
            from ..kb.search.filters import _parse_elasticsearch_filter

            try:
                _parse_elasticsearch_filter(
                    aliased(Document, name="d"), filter_dict, dialect_name="postgresql"
                )
            except ValueError as e:
                return json.dumps({"error": f"Invalid search_filter: {e}"}, indent=2)

        # Select search function based on type
        if search_type == "semantic":
            search_fn = search_semantic
        elif search_type == "fulltext":
            search_fn = search_fulltext
        else:  # hybrid (default)
            search_fn = search

        # Apply query router if enabled and using hybrid search
        route_info = None
        doc_type_boost = None
        chunk_strategy_boost = None
        if search_fn is search:
            from ..config import get_search_config
            search_config = get_search_config()
            if search_config.get('router_enabled', False):
                from ..kb.search.router import get_router
                router = get_router()
                route = router.route(query)
                route_info = {"query_type": route.query_type.value, "reasoning": route.reasoning}
                # Router overrides defaults unless caller explicitly set them.
                # None means "not specified", so an explicit max_results=5 is
                # honoured rather than being mistaken for the default.
                if max_results is None:
                    max_results = route.max_results
                if rerank is None:
                    rerank = route.rerank
                # doc_type boost: e.g. {"table": 1.7} for table-shaped queries.
                # search_hybrid applies it after RRF.
                doc_type_boost = route.doc_type_boost
                # chunk_strategy boost: e.g. {"section": 1.3} for synthesis
                # queries — keyed on the chunk's own strategy rather than its
                # parent document's doc_type.
                chunk_strategy_boost = route.chunk_strategy_boost

        # Build search kwargs
        search_kwargs = dict(
            query=query,
            max_results=DEFAULT_MAX_RESULTS if max_results is None else max_results,
            filter=filter_dict,
        )
        # Only pass rerank / doc_type_boost / chunk_strategy_boost to hybrid
        # search (which supports them).
        if search_fn is search:
            if rerank is not None:
                search_kwargs["rerank"] = rerank
            if doc_type_boost:
                search_kwargs["doc_type_boost"] = doc_type_boost
            if chunk_strategy_boost:
                search_kwargs["chunk_strategy_boost"] = chunk_strategy_boost

        response = search_fn(**search_kwargs)

        results = response.get("results", [])

        if not results:
            return json.dumps({"message": "No results found", "results": []}, indent=2)

        formatted_results = _format_search_results(results)

        result_json = {
            "count": len(formatted_results),
            "results": formatted_results,
        }
        if route_info:
            result_json["route"] = route_info

        return json.dumps(result_json, indent=2)

    except Exception as e:
        logger.error(f"Error in kb_search: {e}", exc_info=True)
        return json.dumps({"error": str(e)}, indent=2)


def kb_get(identifier: str) -> str:
    """Get the complete text of one Mu2e knowledge base document.

    Use this after kb_search whenever STATUS shows EXCERPTS_WITH_MATCHES,
    SUMMARY_ONLY or PREVIEW_ONLY - those results contain only part of the document.

    Args:
        identifier: Pass the value on the `ID:` line of a kb_search result's
                   [[DOCUMENT_METADATA]] header, verbatim. Also accepted:
                   - UUID (the document's unique identifier)
                   - "source_id/doc_id" (e.g. "mu2e-docdb/56353-opsmeeting_10thApr")
                   - "source_id_doc_id" (underscore form; kb_search emits this when
                     the document has no UUID)
                   - "doc_id" alone

    Returns:
        A metadata header, a [[DETECTED_CONCEPTS]] block listing knowledge-graph
        nodes mentioned in the document (each with a kb_lookup_node CMD to follow),
        then the full text between [[CONTENT_START]] and [[DOCUMENT_END]].
        A string starting with "ERROR:" means the document was not found - that is
        final, do not retry the same identifier.
    """
    from ..kb import get

    try:
        result = get(identifier=identifier)

        if result is None:
            return "ERROR: Document not found"

        doc = None
        if isinstance(result, list):
            # Prefer marker parser if available
            for k, res in enumerate(result):
                if res.parser_id == "marker":
                    doc = result[k]
                    break
            if doc is None:
                doc = result[0]
        else:
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

        # Fetch Associated Nodes from Knowledge Graph (unless disabled via HIDE_GRAPH)
        from ..config import get_server_config

        graph_section = ""
        if not get_server_config()['hide_graph']:
            graph_nodes = get_nodes_for_document(doc.doc_id)

            if graph_nodes:
                node_lines = []
                # Sort by mention count and take top 15
                for gn in graph_nodes[:15]:
                    node_lines.append(
                        f"- {gn['name']} ({gn['type']}) [Mentions: {gn['mention_count']}] "
                        f"-> CMD: kb_lookup_node(\"{gn['id']}\")"
                    )

                graph_section = "\n[[DETECTED_CONCEPTS]]\n" + "\n".join(node_lines) + "\n"

        # Return the natural text with metadata header
        return f"""[[DOCUMENT_METADATA]]
{meta_header}
{graph_section}[[CONTENT_START]]
{doc.text or "No content available"}
[[DOCUMENT_END]]"""

    except Exception as e:
        logger.error(f"Error in kb_get: {e}", exc_info=True)
        return f"ERROR: {str(e)}"


def kb_get_image(identifier: str, image_filename: Optional[str] = None) -> ImageContent:
    """Retrieve an image from a Mu2e knowledge base document as an MCP Image resource.

    Image filenames are discovered from markdown image references in kb_get output -
    e.g. `![](image-1.png)` or `![](_page_9_Figure_7.png)`. Pass the parent
    document's identifier plus that filename to fetch the figure it refers to.

    Args:
        identifier: Document identifier in one of these formats:
                   - "source_id/doc_id" for the parent document (requires image_filename),
                     e.g. "mu2e-docdb/56353-opsmeeting_10thApr"
                   - "source_id/doc_id-image.png" for the image directly
                   - UUID of the image document
        image_filename: Image filename taken from a markdown reference in the parent
                       document (e.g. "image-1.png"). Combined with the parent doc_id
                       as "doc_id-image_filename".

    Returns:
        MCP Image with base64 data and mimeType. Raises if no such image exists.
    """
    from ..kb import get

    try:
        # Parse identifier and construct image document ID
        if image_filename:
            # Format: "source_id/doc_id" + image_filename
            if "/" in identifier:
                source_id, doc_id = identifier.split("/", 1)
                image_doc_id = f"{doc_id}-{image_filename}"
                image_identifier = f"{source_id}/{image_doc_id}"
            else:
                # Just doc_id provided
                image_doc_id = f"{identifier}-{image_filename}"
                image_identifier = image_doc_id
        else:
            # Full image identifier provided - pass through as-is
            image_identifier = identifier

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
        elif "." in identifier:
            ext = identifier.rsplit(".", 1)[-1].lower()
            if ext in ["jpg", "jpeg", "png", "gif", "webp"]:
                image_format = ext
        elif image_filename and "." in image_filename:
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


def kb_lookup_node(identifier: str, node_type: Optional[str] = None) -> str:
    """Get details about a concept/node in the knowledge graph.

    Use this to understand relationships, find connected concepts, or see
    which documents mention a specific entity.

    Args:
        identifier: The Node ID (UUID) or Node Name.
        node_type: Optional node type to filter by when searching by name.

    Returns:
        Structured text describing the node, its neighbors, and linked documents.
        Relations carry CMD_NODE / CMD_EVIDENCE hints - literal follow-up calls.
        "Node not found: <identifier>" means no such concept exists; try kb_search
        rather than retrying with a variation of the name.
    """
    try:
        # Try finding by ID first, then by Name
        # Enable document metadata fetching for richer output
        if _is_strict_uuid4(identifier):
            node_data = get_node(id=identifier, include_document_metadata=True)
        else:
            node_data = get_node(name=identifier, type=node_type, include_document_metadata=True)

        if not node_data:
            return f"Node not found: {identifier}"

        return _format_node_output(node_data)

    except Exception as e:
        logger.error(f"Error in kb_lookup_node: {e}", exc_info=True)
        return f"ERROR: {str(e)}"


def kb_node_relation_evidence(relation_id: str) -> str:
    """Get the text excerpts that justify a relationship between two nodes.

    Args:
        relation_id: The ID of the relationship (found in kb_lookup_node output).

    Returns:
        Text excerpts from documents that support this relationship, each with a
        kb_get CMD for the source document. "No specific text evidence found."
        means the relationship has no stored evidence - that is final.
    """
    try:
        evidence = get_relation_evidence(relation_id)

        if not evidence:
            return "No specific text evidence found."

        lines = ["[[RELATION_EVIDENCE]]"]
        for item in evidence:
            # Display rich document info
            source_id = item.get('source_id', 'unknown')
            doc_id = item.get('doc_id', 'unknown')
            title = item.get('title', 'Untitled')

            # Use source_id/doc_id format
            doc_identifier = f"{source_id}/{doc_id}"

            lines.append(f"Document: \"{title}\" ({doc_identifier})")
            lines.append(f"Confidence: {item['confidence']}")
            lines.append(f"Excerpt: \"{item['text']}\"")
            lines.append(f"CMD: kb_get(\"{doc_identifier}\")")
            lines.append("---")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Error in kb_node_relation_evidence: {e}", exc_info=True)
        return f"ERROR: {str(e)}"


def kb_find_path(start_node: str, end_node: str, max_depth: int = 4) -> str:
    """Find connection paths between two concepts.

    Useful for "How is X related to Y?" questions.

    Args:
        start_node: Name or ID of start node.
        end_node: Name or ID of end node.
        max_depth: Maximum path length to search (default: 4).

    Returns:
        Paths connecting the two nodes, each step carrying a CMD to look up the node
        or its supporting evidence. "No paths found" and "Could not find start/end
        node" are final answers, not errors to retry.
    """
    try:
        # Resolve names to IDs
        if _is_strict_uuid4(start_node):
            s_node = get_node(id=start_node)
        else:
            s_node = get_node(name=start_node)
        if _is_strict_uuid4(end_node):
            e_node = get_node(id=end_node)
        else:
            e_node = get_node(name=end_node)

        if not s_node:
            return f"Could not find start node: {start_node}"
        if not e_node:
            return f"Could not find end node: {end_node}"

        paths = find_paths(
            start_node_id=s_node["node"]["id"],
            end_node_id=e_node["node"]["id"],
            max_depth=max_depth
        )

        if not paths:
            return f"No paths found between these nodes (max depth {max_depth})."

        output = [f"Found {len(paths)} paths connecting '{s_node['node']['name']}' and '{e_node['node']['name']}':\n"]

        for i, path in enumerate(paths, 1):
            chain_parts = []
            for item in path["chain"]:
                if item["element"] == "node":
                    node_str = f"({item['name']} [{item['label']}])"
                    node_str += f" CMD: kb_lookup_node(\"{item['id']}\")"
                    chain_parts.append(node_str)
                elif item["element"] == "relationship":
                    if item["direction"] == "forward":
                        arrow = f"--[{item['verb']}]-->"
                    else:
                        arrow = f"<--[{item['verb']}]--"
                    arrow += f" CMD: kb_node_relation_evidence(\"{item['id']}\")"
                    chain_parts.append(arrow)

            output.append(f"\nPath {i} (Length {path['length']}):")
            output.append("  " + "\n  ".join(chain_parts))

        return "\n".join(output)

    except Exception as e:
        logger.error(f"Error in kb_find_path: {e}", exc_info=True)
        return f"ERROR: {str(e)}"


async def kb_research(question: str, ctx: Context = None) -> str:
    """Run a multi-step research agent over the knowledge base and return a synthesized answer.

    Use this for open-ended questions that require multiple searches, cross-referencing
    documents, or exploring the knowledge graph before an answer can be composed.

    It runs up to 10 agent iterations, each making several LLM calls, so expect it to
    take minutes and to cost far more than a direct lookup - prefer kb_search/kb_get
    whenever a search or two would settle the question. Progress is reported as the
    run proceeds, so a long silence is the agent working, not a stall.

    Args:
        question: The research question to investigate.

    Returns:
        A synthesized answer composed from the agent's findings.
    """
    import sys
    from datetime import datetime
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from ..agents.notebook_agent import NotebookAgent
    from ..llm import get_openai_client
    from ..config import get_agent_config

    # NotebookAgent runs up to MAX_ITERATIONS steps, each involving one or more
    # LLM calls, and can comfortably exceed a client's idle timeout. Report
    # progress on every step so MCP clients (e.g. Claude Code) that key their
    # idle timeout off notifications/progress don't abort the call early.
    NOTEBOOK_MAX_ITERATIONS = 10

    async def report_progress(event: dict):
        if ctx is None:
            return
        etype = event.get("type")
        iteration = event.get("iteration")
        if etype == "info":
            message = event.get("message", "")
        elif etype == "tool_call":
            message = f"calling tools: {', '.join(event.get('tools', []))}"
        elif etype == "notebook_update":
            message = "updated research notes"
        else:
            return
        try:
            await ctx.report_progress(
                progress=iteration or 0,
                total=NOTEBOOK_MAX_ITERATIONS,
                message=message,
            )
        except Exception:
            logger.debug("failed to report kb_research progress", exc_info=True)

    try:
        agent_config = get_agent_config()
        model = agent_config["agent_model"]

        server_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "kb_mcp.server.mcp_stdio"],
            env=None,
        )

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                client = get_openai_client(use_async=True)
                run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

                worker = NotebookAgent(
                    session=session,
                    client=client,
                    depth=1,
                    agent_id="kb_research",
                    run_id=run_id,
                    callback=report_progress,
                )
                await worker.initialize_tools()
                return await worker.run(question, model=model)

    except Exception as e:
        logger.error(f"Error in kb_research: {e}", exc_info=True)
        return f"ERROR: {str(e)}"


def register_tools(mcp):
    """Register MCP tools with the FastMCP instance."""
    from ..config import get_server_config

    mcp.tool()(kb_search)
    mcp.tool()(kb_get)
    mcp.tool()(kb_get_image)  # Keep for explicit image retrieval by filename
    # Graph Tools (skipped when HIDE_GRAPH is set)
    if not get_server_config()['hide_graph']:
        mcp.tool()(kb_lookup_node)
        mcp.tool()(kb_find_path)
        mcp.tool()(kb_node_relation_evidence)
    # Agentic research (long-running; excluded from worker agents' own tool lists)
    mcp.tool()(kb_research)


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
    """Register MCP prompts and prompt resources with the FastMCP instance."""

    # Register domain context resource (NEW)
    @mcp.resource("kb://sys/domain_context")
    def get_domain_context() -> str:
        """Returns domain-specific context and tool usage tips."""
        # Same text the server advertises via MCP `instructions`, so a client
        # that reads this resource and one that only sees the initialize
        # response get the same briefing.
        from .mcp_prompts import get_server_instructions

        return get_server_instructions()

    # A resource and a prompt with no database or model dependency, so a
    # client (or a test) can verify that resource and prompt plumbing works
    # without a populated knowledge base. Everything else here reads real KB
    # state, which makes it useless for telling "server is misconfigured"
    # apart from "the knowledge base is empty".
    @mcp.resource("kb://sys/selftest")
    def selftest_resource() -> str:
        """Static resource for checking that resource reads work."""
        return json.dumps(
            {
                "status": "ok",
                "server": "kb-mcp",
                "resource": "kb://sys/selftest",
                "note": "Static self-test payload; reads no knowledge base state.",
            },
            indent=2,
        )

    @mcp.prompt()
    def selftest(echo: str = "ping") -> str:
        """Static prompt for checking that prompt rendering and arguments work."""
        return f"kb-mcp self-test. Echo: {echo}"

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

    @mcp.prompt()
    def explore_connections(topic: str) -> str:
        """Explore the knowledge graph around a specific topic."""
        return f"""Explore the connections around: {topic}

Strategy:
1. START: Use kb_lookup_node("{topic}") to find the entry point.
2. TRAVERSE: Look for "CMD_NODE" hints in the output to follow interesting paths.
3. VERIFY: Use "CMD_EVIDENCE" commands to check the text backing up specific relationships.
4. EXPAND: If you see "CMD: kb_get" in linked documents, use it to get context.
5. GOAL: Summarize the structural relationships and supporting evidence found.

Tip: Use kb_find_path if you need to connect two specific distinct concepts."""