"""Protected web interface for interactive MCP tool usage (server package)."""

import json
import logging
from starlette.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from starlette.requests import Request

from .web_auth import WebSessionManager
from . import html_templates

logger = logging.getLogger(__name__)


def document_to_dict(doc, include_text: bool = True, include_binary: bool = False):
    """Convert Document object to dictionary for JSON serialization."""
    result = {
        "id": doc.id,
        "source_id": doc.source_id,
        "doc_id": doc.doc_id,
        "uri": doc.uri,
        "source_type": doc.source_type,
        "doc_type": doc.doc_type,
        "meta": doc.meta if doc.meta else {},
    }
    
    # Add timestamps
    if doc.insert_time:
        result["insert_time"] = doc.insert_time.isoformat()
    if doc.creating_time:
        result["creating_time"] = doc.creating_time.isoformat()
    if doc.update_time:
        result["update_time"] = doc.update_time.isoformat()
    
    # Add text content if requested
    if include_text and doc.text:
        result["text"] = doc.text
        result["text_preview"] = doc.text[:300] if len(doc.text) > 300 else doc.text
    elif doc.text:
        result["text_preview"] = doc.text[:300] if len(doc.text) > 300 else doc.text
        result["text_length"] = len(doc.text)
    
    # Add binary info if requested
    if include_binary and doc.binary:
        import base64
        result["binary"] = base64.b64encode(doc.binary).decode("utf-8")
        result["binary_size"] = len(doc.binary)
    elif doc.binary:
        result["has_binary"] = True
        result["binary_size"] = len(doc.binary)
    
    return result


def setup_web_routes(app, oauth_provider, session_manager: WebSessionManager):
    """Setup web interface routes."""

    @app.route("/web")
    async def web_page(request: Request):
        """Web interface (GitHub OAuth protected)."""
        # Check authentication first
        session_data = await session_manager.get_session_data(request)
        if not session_data:
            # Redirect to login with return path
            return RedirectResponse(url="/login?redirect=/web")
        
        # Get username from authenticated session
        username = session_data.get("username")

        # Get auth warning banner
        auth_warning = session_manager.get_auth_warning_html()

        # Get initial filter values from query params (for initial state)
        source_id = request.query_params.get("source_id", "")
        doc_type = request.query_params.get("doc_type", "")
        search = request.query_params.get("search", "")

        # Get filter options for initial dropdown population
        from ..kb import get_options
        options = get_options()

        # Build initial filter dropdowns (counts will be updated dynamically via JS)
        source_options = '<option value="">All Sources</option>'
        for source_option in options["source_options"]:
            source_id_val = source_option["id"]
            source_name = source_option["name"]
            count = source_option["count"]
            selected = 'selected' if source_id_val == source_id else ''
            display = source_name if source_name else source_id_val
            source_options += f'<option value="{source_id_val}" {selected} data-count="{count}">{display} ({count})</option>'

        doc_type_options = '<option value="">All Types</option>'
        for doc_type_option in options["doc_type_options"]:
            doc_type_val = doc_type_option["doc_type"]
            count = doc_type_option["count"]
            selected = 'selected' if doc_type_val == doc_type else ''
            doc_type_options += f'<option value="{doc_type_val}" {selected} data-count="{count}">{doc_type_val} ({count})</option>'

        content = f"""
        <h1>Knowledge Base Explorer</h1>
        <!--<p>Authenticated as: <strong>{username}</strong></p>-->

        {auth_warning}

        <div class="card">
            <h2>Filters</h2>
            <div class="filters">
                <div class="filter-row">
                    <div class="filter-group">
                        <label for="search-input">Search Text:</label>
                        <input type="search" id="search-input" name="search" value="{search}" placeholder="Search in document text...">
                    </div>
                    <div class="filter-group">
                        <label for="source-filter">Source:</label>
                        <select id="source-filter" name="source_id">
                            {source_options}
                        </select>
                    </div>
                    <div class="filter-group">
                        <label for="type-filter">Document Type:</label>
                        <select id="type-filter" name="doc_type">
                            {doc_type_options}
                        </select>
                    </div>
                </div>
                <button type="button" id="apply-filters" class="btn">Apply Filters</button>
                <a href="/web" class="btn">Clear Filters</a>
            </div>
        </div>

        <div class="card">
            <h2>Documents (<span id="total-count">Loading...</span>)</h2>
            <div id="document-list" class="document-list">
                <div class="info-box">Loading documents...</div>
            </div>
            <div id="loading-more" style="display: none; text-align: center; padding: 20px;">
                Loading more documents...
            </div>
        </div>
        """

        return HTMLResponse(html_templates.base_template(
            "Knowledge Base Explorer - MCP Server",
            content,
            None,
            username
        ))

    @app.route("/web/document/{doc_id}")
    async def document_detail(request: Request):
        """View full document details (HTML)."""
        # Check authentication first
        session_data = await session_manager.get_session_data(request)
        if not session_data:
            return RedirectResponse(url="/login?redirect=/web")
        
        # Get username from authenticated session
        username = session_data.get("username")

        doc_id = request.path_params["doc_id"]

        # Get document from knowledge base
        from ..kb import get

        try:
            doc = get(uuid=doc_id)
            if not doc:
                return HTMLResponse(
                    html_templates.base_template(
                        "Document Not Found",
                        '<div class="error-box"><h2>Document Not Found</h2><p>The requested document could not be found.</p><p><a href="/web">← Back to Document List</a></p></div>',
                        None,
                        username
                    ),
                    status_code=404
                )

            # Format URI as link if it exists
            uri_display = "N/A"
            if doc.uri:
                uri_display = f'<a href="{doc.uri}" target="_blank" rel="noopener noreferrer">{doc.uri}</a>'

            # Format document data
            text_content = ""
            if doc.text:
                text_content = f'<pre style="white-space: pre-wrap; max-height: 500px; overflow-y: auto;">{doc.text}</pre>'
            
            # Handle image display
            image_html = ""
            binary_info = ""
            if doc.binary:
                binary_size = len(doc.binary)
                if doc.doc_type == "image" or (doc.source_type and doc.source_type.startswith("image/")):
                    # Display as image
                    image_url = f"/api/image/{doc.id}"
                    image_html = f'''
            <div class="card">
                <h2>Image</h2>
                <img src="{image_url}" alt="Document Image" style="max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px;">
                <div class="document-meta" style="margin-top: 10px;">
                    <strong>Size:</strong> {binary_size} bytes | 
                    <strong>Type:</strong> {doc.source_type or "image"}
                </div>
            </div>
                    '''
                else:
                    # Show binary info for non-image binary data
                    binary_info = f'<div class="info-box"><strong>Binary Data:</strong> {binary_size} bytes</div>'

            meta_html = ""
            if doc.meta:
                meta_html = '<div class="card"><h2>Metadata</h2><table><tr><th>Key</th><th>Value</th></tr>'
                for key, value in doc.meta.items():
                    if isinstance(value, (str, int, float, bool)):
                        meta_html += f'<tr><td><strong>{key}</strong></td><td>{value}</td></tr>'
                    else:
                        meta_html += f'<tr><td><strong>{key}</strong></td><td><pre>{value}</pre></td></tr>'
                meta_html += '</table></div>'

            content = f"""
            <h1>Document Details</h1>
            <p><a href="/web">← Back to Document List</a></p>

            <div class="card">
                <h2>Document Information</h2>
                <table>
                    <tr><th>ID</th><td><code>{doc.id}</code></td></tr>
                    <tr><th>Source ID</th><td>{doc.source_id}</td></tr>
                    <tr><th>Document ID</th><td>{doc.doc_id or "N/A"}</td></tr>
                    <tr><th>URI</th><td>{uri_display}</td></tr>
                    <tr><th>Source Type</th><td>{doc.source_type}</td></tr>
                    <tr><th>Document Type</th><td>{doc.doc_type}</td></tr>
                    <tr><th>Insert Time</th><td>{doc.insert_time.strftime("%Y-%m-%d %H:%M:%S") if doc.insert_time else "N/A"}</td></tr>
                    <tr><th>Creating Time</th><td>{doc.creating_time.strftime("%Y-%m-%d %H:%M:%S") if doc.creating_time else "N/A"}</td></tr>
                    <tr><th>Update Time</th><td>{doc.update_time.strftime("%Y-%m-%d %H:%M:%S") if doc.update_time else "N/A"}</td></tr>
                </table>
            </div>

            {image_html}

            {binary_info}

            {meta_html if meta_html else ''}

            <div class="card">
                <h2>Text Content</h2>
                {text_content if text_content else '<div class="info-box">No text content available.</div>'}
            </div>
            """

            return HTMLResponse(html_templates.base_template(
                f"Document: {doc.doc_id or doc.id}",
                content,
                None,
                username
            ))

        except Exception as e:
            logger.error(f"Error fetching document {doc_id}: {e}", exc_info=True)
            return HTMLResponse(
                html_templates.base_template(
                    "Error",
                    f'<div class="error-box"><h2>Error</h2><p>{str(e)}</p><p><a href="/web">← Back to Document List</a></p></div>',
                    None,
                    username
                ),
                status_code=500
            )

    @app.route("/api/get")
    async def api_get(request: Request):
        """JSON API endpoint for getting documents with filters."""
        # Check authentication first
        session_data = await session_manager.get_session_data(request)
        if not session_data:
            return JSONResponse(
                {"error": "Unauthorized"},
                status_code=401
            )

        # Get query parameters for filtering
        source_id = request.query_params.get("source_id", "")
        doc_type = request.query_params.get("doc_type", "")
        search = request.query_params.get("search", "")
        limit = int(request.query_params.get("limit", "10"))
        offset = int(request.query_params.get("offset", "0"))
        include_text = request.query_params.get("include_text", "false").lower() == "true"

        # Build filter_dict for get() function
        filter_dict = {}
        if source_id:
            filter_dict["source_id"] = source_id
        if doc_type:
            filter_dict["doc_type"] = doc_type
        if search:
            filter_dict["text_contains"] = search

        try:
            # Query documents from knowledge base
            from ..kb import get, get_count, get_options

            # Get documents with filters and pagination
            documents_result = get(filter_dict=filter_dict if filter_dict else None, limit=limit, offset=offset)
            if documents_result is None:
                documents = []
            elif isinstance(documents_result, list):
                documents = documents_result
            else:
                documents = [documents_result]

            # Get total count
            total_count = get_count(filter_dict=filter_dict if filter_dict else None)

            # Get base filter options
            base_options = get_options()

            # Calculate filtered counts for options based on current filters
            # For source options: if doc_type or search is selected, show counts filtered by those
            # For doc_type options: if source_id or search is selected, show counts filtered by those
            filtered_source_options = []
            for source_option in base_options["source_options"]:
                source_id_val = source_option["id"]
                # Build filter dict for this source option
                source_filter = {"source_id": source_id_val}
                if doc_type:
                    source_filter["doc_type"] = doc_type
                if search:
                    source_filter["text_contains"] = search
                filtered_count = get_count(filter_dict=source_filter)
                filtered_source_options.append({
                    "id": source_id_val,
                    "name": source_option["name"],
                    "count": filtered_count
                })

            filtered_doc_type_options = []
            for doc_type_option in base_options["doc_type_options"]:
                doc_type_val = doc_type_option["doc_type"]
                # Build filter dict for this doc_type option
                type_filter = {"doc_type": doc_type_val}
                if source_id:
                    type_filter["source_id"] = source_id
                if search:
                    type_filter["text_contains"] = search
                filtered_count = get_count(filter_dict=type_filter)
                filtered_doc_type_options.append({
                    "doc_type": doc_type_val,
                    "count": filtered_count
                })

            filtered_options = {
                "source_options": filtered_source_options,
                "doc_type_options": filtered_doc_type_options,
            }

            # Convert documents to dictionaries
            documents_data = [document_to_dict(doc, include_text=include_text) for doc in documents]

            return JSONResponse({
                "documents": documents_data,
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
                "filters": {
                    "source_id": source_id,
                    "doc_type": doc_type,
                    "search": search,
                },
                "options": filtered_options,
            })

        except Exception as e:
            logger.error(f"Error in api_get: {e}", exc_info=True)
            return JSONResponse(
                {"error": str(e)},
                status_code=500
            )

    @app.route("/api/document/{doc_id}")
    async def api_document(request: Request):
        """JSON API endpoint for getting a single document."""
        # Check authentication first
        session_data = await session_manager.get_session_data(request)
        if not session_data:
            return JSONResponse(
                {"error": "Unauthorized"},
                status_code=401
            )

        doc_id = request.path_params["doc_id"]
        include_text = request.query_params.get("include_text", "true").lower() == "true"
        include_binary = request.query_params.get("include_binary", "false").lower() == "true"

        try:
            from ..kb import get

            doc = get(uuid=doc_id)
            if not doc:
                return JSONResponse(
                    {"error": "Document not found"},
                    status_code=404
                )

            return JSONResponse({
                "document": document_to_dict(doc, include_text=include_text, include_binary=include_binary)
            })

        except Exception as e:
            logger.error(f"Error in api_document: {e}", exc_info=True)
            return JSONResponse(
                {"error": str(e)},
                status_code=500
            )

    @app.route("/api/options")
    async def api_options(request: Request):
        """JSON API endpoint for getting filter options with filtered counts."""
        # Check authentication first
        session_data = await session_manager.get_session_data(request)
        if not session_data:
            return JSONResponse(
                {"error": "Unauthorized"},
                status_code=401
            )

        # Get query parameters for filtering
        source_id = request.query_params.get("source_id", "")
        doc_type = request.query_params.get("doc_type", "")
        search = request.query_params.get("search", "")

        try:
            from ..kb import get_count, get_options

            # Get base filter options
            base_options = get_options()

            # Build current filter dict
            current_filter = {}
            if source_id:
                current_filter["source_id"] = source_id
            if doc_type:
                current_filter["doc_type"] = doc_type
            if search:
                current_filter["text_contains"] = search

            # Calculate filtered counts for source options
            filtered_source_options = []
            for source_option in base_options["source_options"]:
                source_id_val = source_option["id"]
                # Build filter dict for this source option
                source_filter = {"source_id": source_id_val}
                if doc_type:
                    source_filter["doc_type"] = doc_type
                if search:
                    source_filter["text_contains"] = search
                filtered_count = get_count(filter_dict=source_filter)
                filtered_source_options.append({
                    "id": source_id_val,
                    "name": source_option["name"],
                    "count": filtered_count
                })

            # Calculate filtered counts for doc_type options
            filtered_doc_type_options = []
            for doc_type_option in base_options["doc_type_options"]:
                doc_type_val = doc_type_option["doc_type"]
                # Build filter dict for this doc_type option
                type_filter = {"doc_type": doc_type_val}
                if source_id:
                    type_filter["source_id"] = source_id
                if search:
                    type_filter["text_contains"] = search
                filtered_count = get_count(filter_dict=type_filter)
                filtered_doc_type_options.append({
                    "doc_type": doc_type_val,
                    "count": filtered_count
                })

            return JSONResponse({
                "source_options": filtered_source_options,
                "doc_type_options": filtered_doc_type_options,
            })

        except Exception as e:
            logger.error(f"Error in api_options: {e}", exc_info=True)
            return JSONResponse(
                {"error": str(e)},
                status_code=500
            )

    @app.route("/api/image/{doc_id}")
    async def api_image(request: Request):
        """Serve image binary data."""
        # Check authentication first
        session_data = await session_manager.get_session_data(request)
        if not session_data:
            return Response(
                content=b"Unauthorized",
                status_code=401,
                media_type="text/plain"
            )

        doc_id = request.path_params["doc_id"]

        try:
            from ..kb import get

            doc = get(uuid=doc_id)
            if not doc:
                return Response(
                    content=b"Image not found",
                    status_code=404,
                    media_type="text/plain"
                )

            # Check if document has binary data
            if not doc.binary:
                return Response(
                    content=b"No image data available",
                    status_code=404,
                    media_type="text/plain"
                )

            # Determine content type from source_type or default to image
            content_type = doc.source_type
            if not content_type or not content_type.startswith("image/"):
                # Try to infer from common image types
                if doc.doc_type == "image":
                    content_type = "image/png"  # Default fallback
                else:
                    content_type = "application/octet-stream"

            return Response(
                content=doc.binary,
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=3600",  # Cache for 1 hour
                }
            )

        except Exception as e:
            logger.error(f"Error serving image {doc_id}: {e}", exc_info=True)
            return Response(
                content=f"Error: {str(e)}".encode(),
                status_code=500,
                media_type="text/plain"
            )
