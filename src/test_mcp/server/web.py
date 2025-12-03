"""Protected web interface for interactive MCP tool usage (server package)."""

import json
import logging
import os
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from starlette.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from starlette.requests import Request

from .web_auth import WebSessionManager
from . import html_templates

logger = logging.getLogger(__name__)


async def require_auth_html(
    request: Request,
    session_manager: WebSessionManager,
    redirect_url: str | None = None,
    require_admin: bool = False,
) -> tuple[dict | None, RedirectResponse | None]:
    """Check authentication for HTML routes.
    
    Args:
        request: Starlette request object
        session_manager: WebSessionManager instance
        redirect_url: Optional redirect URL (default: /login with return path)
        require_admin: If True, also require admin privileges
    
    Returns:
        Tuple of (session_data, redirect_response):
        - If authenticated (and admin if required): (session_data, None)
        - If not authenticated: (None, RedirectResponse)
    """
    session_data = await session_manager.get_session_data(request)
    if not session_data:
        if redirect_url is None:
            # Include return path in login redirect
            return_path = str(request.url.path)
            redirect_url = f"/login?redirect={return_path}"
        return None, RedirectResponse(url=redirect_url, status_code=302)
    
    # Check admin privileges if required
    if require_admin:
        if not await session_manager.has_admin_access(request, force_reverify=True):
            return None, RedirectResponse(url="/login?redirect=/admin", status_code=302)
    
    return session_data, None


async def require_auth_api(
    request: Request,
    session_manager: WebSessionManager,
    json_response: bool = False,
) -> tuple[dict | None, Response | JSONResponse | None]:
    """Check authentication for API routes.
    
    Args:
        request: Starlette request object
        session_manager: WebSessionManager instance
        json_response: If True, return JSONResponse instead of plain Response
    
    Returns:
        Tuple of (session_data, error_response):
        - If authenticated: (session_data, None)
        - If not authenticated: (None, Response/JSONResponse with 401)
    """
    session_data = await session_manager.get_session_data(request)
    if not session_data:
        if json_response:
            return None, JSONResponse(
                {"error": "Unauthorized"},
                status_code=401
            )
        return None, Response(
            content=b"Unauthorized",
            status_code=401,
            media_type="text/plain"
        )
    return session_data, None


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
    
    # Get upload directory
    data_dir = os.getenv("DATA_DIR", "data")
    upload_dir = Path(data_dir) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    # Import statistics route
    from . import web_statistics

    @app.route("/web")
    async def web_page(request: Request):
        """Web interface (GitHub OAuth protected)."""
        # Check authentication first
        session_data, redirect = await require_auth_html(request, session_manager)
        if redirect:
            return redirect
        
        # Get username from authenticated session
        username = session_data.get("username")

        # Get auth warning banner
        auth_warning = session_manager.get_auth_warning_html()

        # Get initial filter values from query params (for initial state)
        source_id = request.query_params.get("source_id", "")
        doc_type = request.query_params.get("doc_type", "")
        search = request.query_params.get("search", "")
        message = request.query_params.get("message", "")
        uploaded_docs = request.query_params.get("uploaded_docs", "")  # Comma-separated doc IDs

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

        # Build message display if present
        message_html = ""
        if message:
            # Message can contain HTML (e.g., links), so don't escape it
            message_html = f"""
        <div class="success-box" id="user-message" style="margin-bottom: 20px;">
            {message}
            <button onclick="document.getElementById('user-message').style.display='none'" style="float: right; background: none; border: none; color: #155724; font-size: 20px; cursor: pointer; padding: 0 10px; line-height: 1;">×</button>
        </div>
            """

        content = f"""
        <h1>Knowledge Base Explorer</h1>
        <!--<p>Authenticated as: <strong>{username}</strong></p>-->

        {auth_warning}
        {message_html}

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
                # Convert local://uploads/filename to /files/uploaded/filename
                if doc.uri.startswith("local://uploads/"):
                    filename = doc.uri.replace("local://uploads/", "")
                    uri_href = f"/files/uploaded/{filename}"
                    uri_display = f'<a href="{uri_href}" target="_blank" rel="noopener noreferrer">{doc.uri}</a>'
                # Convert local://local/filename to /files/local/filename
                elif doc.uri.startswith("local://local/"):
                    filename = doc.uri.replace("local://local/", "")
                    uri_href = f"/files/local/{filename}"
                    uri_display = f'<a href="{uri_href}" target="_blank" rel="noopener noreferrer">{doc.uri}</a>'
                else:
                    uri_display = f'<a href="{doc.uri}" target="_blank" rel="noopener noreferrer">{doc.uri}</a>'

            # Get chunk strategies for this document
            chunk_strategies_html = ""
            default_strategy = None
            try:
                from ..kb.embedding import get_chunk_strategies
                strategies = get_chunk_strategies(document_id=doc.id)
                if strategies:
                    # Get first strategy as default
                    default_strategy = strategies[0].get("strategy", "") if isinstance(strategies[0], dict) else strategies[0].strategy
                    
                    strategy_radios = ""
                    for idx, strategy in enumerate(strategies):
                        strategy_name = strategy.get("strategy", "") if isinstance(strategy, dict) else strategy.strategy
                        count = strategy.get("count", 0) if isinstance(strategy, dict) else strategy.count
                        meta = strategy.get("meta", {}) if isinstance(strategy, dict) else (strategy.meta if hasattr(strategy, 'meta') else {})
                        
                        # Build strategy description from meta
                        strategy_desc = ""
                        if meta:
                            if "chunk_size" in meta:
                                strategy_desc += f"Size: {meta.get('chunk_size')} tokens"
                            if "chunk_overlap" in meta:
                                if strategy_desc:
                                    strategy_desc += ", "
                                strategy_desc += f"Overlap: {meta.get('chunk_overlap')} tokens"
                        
                        checked = 'checked' if idx == 0 else ''
                        strategy_radios += f'''
                        <label style="display: block; padding: 8px; margin: 4px 0; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; background: {'#f0f8ff' if idx == 0 else '#fff'};">
                            <input type="radio" name="chunk-strategy" value="{html_escape(strategy_name)}" {checked} style="margin-right: 8px;">
                            <strong>{html_escape(strategy_name)}</strong> ({count} chunks)
                            {f'<span style="color: #666; font-size: 12px; display: block; margin-left: 24px; margin-top: 4px;">{html_escape(strategy_desc)}</span>' if strategy_desc else ''}
                        </label>
                        '''
                    
                    chunk_strategies_html = f"""
            <div style="padding: 15px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px;">
                <h3 style="margin-top: 0; margin-bottom: 10px;">Chunking Strategies</h3>
                <div id="chunk-strategies-container">
                    {strategy_radios}
                </div>
                <div id="chunk-info" style="margin-top: 10px; color: #666; font-size: 14px;"></div>
            </div>
                    """
            except (ImportError, Exception) as e:
                logger.debug(f"Could not load chunk strategies: {e}")
                # Chunking module may not be available, that's okay

            # Get available chunking strategies for the re-chunk form
            rechunk_strategy_options = '<option value="">Default (tokens)</option>'
            try:
                from ..kb.embedding import get_chunk_strategies
                all_strategies = get_chunk_strategies()  # Get all strategies, not just for this document
                for strategy_info in all_strategies:
                    strategy_name = strategy_info.get("strategy", "")
                    if strategy_name:
                        rechunk_strategy_options += f'<option value="{html_escape(strategy_name)}">{html_escape(strategy_name)}</option>'
            except (ImportError, Exception) as e:
                logger.debug(f"Could not load chunk strategies for re-chunk form: {e}")
                # Fallback to default options
                rechunk_strategy_options = '<option value="">Default (tokens)</option>'
                rechunk_strategy_options += '<option value="tokens">tokens</option>'
                rechunk_strategy_options += '<option value="slide">slide</option>'

            # Format document data with chunk highlighting support
            text_content = ""
            if doc.text:
                # Wrap text in a div with id for JavaScript to access
                text_content = f'''
            <div id="document-text-container" style="position: relative;">
                <pre id="document-text" style="white-space: pre-wrap; max-height: 600px; overflow-y: auto; position: relative; padding: 10px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px; font-family: inherit; line-height: 1.6;">{html_escape(doc.text)}</pre>
            </div>
                '''
            
            # Handle image display
            image_html = ""
            binary_info = ""
            if doc.binary:
                binary_size = len(doc.binary)
                if doc.doc_type == "image" or (doc.source_type and doc.source_type.startswith("image/")):
                    # Display as image
                    image_url = f"/files/image/{doc.id}"
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

            # Get parent document if parent_id exists
            parent_display = "N/A"
            if doc.parent_id:
                try:
                    parent_doc = get(uuid=doc.parent_id)
                    if parent_doc:
                        parent_label = parent_doc.doc_id or parent_doc.id
                        parent_display = f'<a href="/web/document/{doc.parent_id}">{parent_label}</a>'
                    else:
                        parent_display = f'<code>{doc.parent_id}</code> (not found)'
                except Exception as e:
                    logger.warning(f"Could not fetch parent document {doc.parent_id}: {e}")
                    parent_display = f'<code>{doc.parent_id}</code>'

            # Get child documents
            children_html = ""
            from ..kb import get_children
            try:
                children = get_children(doc.id)
                if children:
                    children_list = ""
                    for child in children:
                        child_label = child.doc_id or child.id
                        children_list += f'<li><a href="/web/document/{child.id}">{child_label}</a> ({child.doc_type})</li>'
                    children_html = f"""
            <div class="card">
                <h2>Child Documents ({len(children)})</h2>
                <ul>
                    {children_list}
                </ul>
            </div>
                    """
            except Exception as e:
                logger.warning(f"Could not fetch child documents for {doc.id}: {e}")

            meta_html = ""
            if doc.meta:
                meta_html = '<div class="card"><h2>Metadata</h2><table><tr><th>Key</th><th>Value</th></tr>'
                for key, value in doc.meta.items():
                    if isinstance(value, (str, int, float, bool)):
                        meta_html += f'<tr><td><strong>{key}</strong></td><td>{value}</td></tr>'
                    else:
                        meta_html += f'<tr><td><strong>{key}</strong></td><td><pre>{value}</pre></td></tr>'
                meta_html += '</table></div>'

            # Get success/error message from query params
            message_html = ""
            message = request.query_params.get("message", "")
            if message:
                # Message can contain HTML (e.g., links), so don't escape it
                message_html = f"""
        <div class="success-box" id="user-message" style="margin-bottom: 20px;">
            {message}
            <button onclick="document.getElementById('user-message').style.display='none'" style="float: right; background: none; border: none; color: #155724; font-size: 20px; cursor: pointer; padding: 0 10px; line-height: 1;">×</button>
        </div>
                """

            content = f"""
            <h1>Document Details</h1>
            <p><a href="/web">← Back to Document List</a></p>
            
            {message_html if message_html else ''}

            <div class="card">
                <h2>Document Information</h2>
                <table>
                    <tr><th>ID</th><td><code>{doc.id}</code></td></tr>
                    <tr><th>Source ID</th><td>{doc.source_id}</td></tr>
                    <tr><th>Document ID</th><td>{doc.doc_id or "N/A"}</td></tr>
                    <tr><th>URI</th><td>{uri_display}</td></tr>
                    <tr><th>Source Type</th><td>{doc.source_type}</td></tr>
                    <tr><th>Document Type</th><td>{doc.doc_type}</td></tr>
                    <tr><th>Parent Document</th><td>{parent_display}</td></tr>
                    <tr><th>Insert Time</th><td>{doc.insert_time.strftime("%Y-%m-%d %H:%M:%S") if doc.insert_time else "N/A"}</td></tr>
                    <tr><th>Creating Time</th><td>{doc.creating_time.strftime("%Y-%m-%d %H:%M:%S") if doc.creating_time else "N/A"}</td></tr>
                    <tr><th>Update Time</th><td>{doc.update_time.strftime("%Y-%m-%d %H:%M:%S") if doc.update_time else "N/A"}</td></tr>
                </table>
            </div>

            {image_html}

            {binary_info}

            {children_html}

            {meta_html if meta_html else ''}

            <div class="card">
                <h2>Text Content</h2>
                <div style="display: flex; gap: 20px; margin-bottom: 15px;">
                    <div style="flex: 1;">
                        {chunk_strategies_html if chunk_strategies_html else ''}
                    </div>
                    <div id="chunk-details-panel" style="width: 300px; height: 150px; padding: 15px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px; overflow-y: auto; display: none;">
                        <div id="chunk-details-content" style="color: #666; font-size: 14px;">
                            <p>Hover over a highlighted chunk to see details.</p>
                        </div>
                    </div>
                </div>
                {text_content if text_content else '<div class="info-box">No text content available.</div>'}
            </div>
            
            <div class="card">
                <h2>Document Actions</h2>
                <div style="display: flex; gap: 15px; align-items: flex-end; flex-wrap: wrap;">
                    <form id="rechunk-form" method="POST" action="/web/document/{doc.id}/rechunk-embed" style="display: flex; gap: 10px; align-items: flex-end; flex: 1; min-width: 300px;">
                        <div style="flex: 1; min-width: 200px;">
                            <label for="rechunk-strategy" style="display: block; margin-bottom: 5px; font-size: 14px; color: #666;">Chunking Strategy:</label>
                            <select id="rechunk-strategy" name="strategy" style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                                {rechunk_strategy_options}
                            </select>
                        </div>
                        <button type="submit" id="rechunk-submit-btn" style="padding: 10px 20px; background-color: #2196F3; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; white-space: nowrap;">Re-chunk and Embed</button>
                    </form>
                    <form id="delete-form" method="POST" action="/web/document/{doc.id}/delete" style="display: inline-block;" onsubmit="return confirm('Are you sure you want to delete this document? This will also delete all chunks and embeddings. This action cannot be undone.');">
                        <button type="submit" id="delete-submit-btn" style="padding: 10px 20px; background-color: #f44336; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; white-space: nowrap;">Delete Document</button>
                    </form>
                </div>
                <div id="rechunk-status" style="margin-top: 10px;"></div>
                <div id="delete-status" style="margin-top: 10px;"></div>
            </div>
            
            <script>
            // Initialize chunk highlighting for this document
            let retryCount = 0;
            const maxRetries = 20; // 20 * 50ms = 1 second max wait
            
            function tryInitChunkHighlighting() {{
                if (typeof initChunkHighlighting === 'function') {{
                    initChunkHighlighting("{doc.id}");
                }} else if (retryCount < maxRetries) {{
                    retryCount++;
                    setTimeout(tryInitChunkHighlighting, 50);
                }} else {{
                    console.error('initChunkHighlighting function not found after', maxRetries, 'retries');
                }}
            }}
            
            if (document.readyState === 'loading') {{
                document.addEventListener('DOMContentLoaded', tryInitChunkHighlighting);
            }} else {{
                // DOM is already loaded
                tryInitChunkHighlighting();
            }}
            </script>
            
            <script>
            // Setup loading indicator for re-chunk form
            document.addEventListener('DOMContentLoaded', function() {{
                if (typeof setupFormLoadingIndicator === 'function') {{
                    setupFormLoadingIndicator(
                        'rechunk-form',
                        'rechunk-submit-btn',
                        'rechunk-status',
                        'Re-chunking and embedding document...'
                    );
                    
                    // Setup loading indicator for delete form
                    setupFormLoadingIndicator(
                        'delete-form',
                        'delete-submit-btn',
                        'delete-status',
                        'Deleting document...'
                    );
                }}
            }});
            </script>
            """

            return HTMLResponse(html_templates.base_template(
                f"Document: {doc.doc_id or doc.id}",
                content,
                None,
                username
            ))

        except Exception as e:
            logger.error(f"Error fetching document {doc_id}: {e}", exc_info=True)
            # Security: Escape error message to prevent XSS
            error_msg = html_escape(str(e))
            return HTMLResponse(
                html_templates.base_template(
                    "Error",
                    f'<div class="error-box"><h2>Error</h2><p>{error_msg}</p><p><a href="/web">← Back to Document List</a></p></div>',
                    None,
                    username
                ),
                status_code=500
            )

    @app.route("/web/document/{doc_id}/rechunk-embed", methods=["POST"])
    async def rechunk_embed_document(request: Request):
        """Re-chunk and embed a document (POST)."""
        # Check authentication first
        session_data, redirect = await require_auth_html(request, session_manager)
        if redirect:
            return redirect
        
        username = session_data.get("username")
        doc_id = request.path_params["doc_id"]
        
        # Get form data
        form_data = await request.form()
        strategy = form_data.get("strategy", "").strip() or None
        
        # Get document from knowledge base
        from ..kb import get
        from ..kb.embedding import chunk_and_embed
        
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
            
            # Check if document has text
            if not doc.text:
                return HTMLResponse(
                    html_templates.base_template(
                        "Error",
                        f'<div class="error-box"><h2>Error</h2><p>Document has no text content to chunk.</p><p><a href="/web/document/{doc_id}">← Back to Document</a></p></div>',
                        None,
                        username
                    ),
                    status_code=400
                )
            
            # Re-chunk and embed
            try:
                chunks = chunk_and_embed(doc, strategy=strategy)
                chunk_count = len(chunks) if chunks else 0
                
                # Redirect back to document page with success message
                from urllib.parse import urlencode
                success_message = f'Document re-chunked and embedded successfully! Generated {chunk_count} chunks.'
                redirect_url = f"/web/document/{doc_id}?{urlencode({'message': success_message})}"
                return RedirectResponse(url=redirect_url, status_code=303)
                
            except ValueError as e:
                # Document might not have text or other validation error
                error_msg = html_escape(str(e))
                return HTMLResponse(
                    html_templates.base_template(
                        "Error",
                        f'<div class="error-box"><h2>Error</h2><p>{error_msg}</p><p><a href="/web/document/{doc_id}">← Back to Document</a></p></div>',
                        None,
                        username
                    ),
                    status_code=400
                )
            except Exception as e:
                logger.error(f"Error re-chunking and embedding document {doc_id}: {e}", exc_info=True)
                error_msg = html_escape(str(e))
                return HTMLResponse(
                    html_templates.base_template(
                        "Error",
                        f'<div class="error-box"><h2>Error</h2><p>Failed to re-chunk and embed document: {error_msg}</p><p><a href="/web/document/{doc_id}">← Back to Document</a></p></div>',
                        None,
                        username
                    ),
                    status_code=500
                )
                
        except Exception as e:
            logger.error(f"Error processing re-chunk request for {doc_id}: {e}", exc_info=True)
            error_msg = html_escape(str(e))
            return HTMLResponse(
                html_templates.base_template(
                    "Error",
                    f'<div class="error-box"><h2>Error</h2><p>{error_msg}</p><p><a href="/web">← Back to Document List</a></p></div>',
                    None,
                    username
                ),
                status_code=500
            )

    @app.route("/web/document/{doc_id}/delete", methods=["POST"])
    async def delete_document_route(request: Request):
        """Delete a document (POST)."""
        # Check authentication first
        session_data, redirect = await require_auth_html(request, session_manager)
        if redirect:
            return redirect
        
        username = session_data.get("username")
        doc_id = request.path_params["doc_id"]
        
        # Get document from knowledge base
        from ..kb import get, delete_document
        
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
            
            # Delete the document
            try:
                result = delete_document(doc_id)
                chunk_count = result.get("chunk_count", 0)
                
                # Redirect to document list with success message
                from urllib.parse import urlencode
                success_message = f'Document deleted successfully.'
                if chunk_count > 0:
                    success_message += f' Also deleted {chunk_count} chunk(s) and their embeddings.'
                redirect_url = f"/web?{urlencode({'message': success_message})}"
                return RedirectResponse(url=redirect_url, status_code=303)
                
            except ValueError as e:
                error_msg = html_escape(str(e))
                return HTMLResponse(
                    html_templates.base_template(
                        "Error",
                        f'<div class="error-box"><h2>Error</h2><p>{error_msg}</p><p><a href="/web/document/{doc_id}">← Back to Document</a></p></div>',
                        None,
                        username
                    ),
                    status_code=400
                )
            except Exception as e:
                logger.error(f"Error deleting document {doc_id}: {e}", exc_info=True)
                error_msg = html_escape(str(e))
                return HTMLResponse(
                    html_templates.base_template(
                        "Error",
                        f'<div class="error-box"><h2>Error</h2><p>Failed to delete document: {error_msg}</p><p><a href="/web/document/{doc_id}">← Back to Document</a></p></div>',
                        None,
                        username
                    ),
                    status_code=500
                )
                
        except Exception as e:
            logger.error(f"Error processing delete request for {doc_id}: {e}", exc_info=True)
            error_msg = html_escape(str(e))
            return HTMLResponse(
                html_templates.base_template(
                    "Error",
                    f'<div class="error-box"><h2>Error</h2><p>{error_msg}</p><p><a href="/web">← Back to Document List</a></p></div>',
                    None,
                    username
                ),
                status_code=500
            )

    @app.route("/web/upload", methods=["GET"])
    async def upload_page(request: Request):
        """File upload page (requires admin privileges)."""
        # Check authentication and admin privileges
        session_data, redirect = await require_auth_html(request, session_manager, require_admin=True)
        if redirect:
            return redirect

        username = session_data.get("username", "User")

        # Get available chunking strategies
        chunk_strategy_options = '<option value="">Default (tokens)</option>'
        try:
            from ..kb.embedding import get_chunk_strategies
            strategies = get_chunk_strategies()
            for strategy_info in strategies:
                strategy_name = strategy_info.get("strategy", "")
                if strategy_name:
                    chunk_strategy_options += f'<option value="{html_escape(strategy_name)}">{html_escape(strategy_name)}</option>'
        except (ImportError, Exception) as e:
            logger.debug(f"Could not load chunk strategies: {e}")
            # Fallback to default options
            chunk_strategy_options = '<option value="">Default (tokens)</option>'
            chunk_strategy_options += '<option value="tokens">tokens</option>'
            chunk_strategy_options += '<option value="slide">slide</option>'

        content = f"""
            <h1>Upload Document</h1>
            <p>Upload a file to add it to the knowledge base. Images will be extracted and described using AI.</p>
            
            <div class="card">
                <form id="upload-form" enctype="multipart/form-data" method="POST" action="/web/upload">
                    <div style="margin-bottom: 15px;">
                        <label for="file"><strong>File:</strong></label>
                        <input type="file" id="file" name="file" required style="margin-top: 5px; display: block; width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                        <small style="color: #666;">Supported formats: PDF, DOCX, PPTX, XLSX, TXT, images (PNG, JPG, etc.)</small>
                    </div>
                    
                    <div style="margin-bottom: 15px;">
                        <label for="doc_id"><strong>Document ID (optional):</strong></label>
                        <input type="text" id="doc_id" name="doc_id" placeholder="e.g., doc-123" style="margin-top: 5px; display: block; width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                        <small style="color: #666;">Optional unique identifier for this document</small>
                    </div>
                    
                    <div style="margin-bottom: 15px;">
                        <label for="meta"><strong>Metadata (optional):</strong></label>
                        <textarea id="meta" name="meta" rows="6" placeholder='{{"author": "John Doe", "tags": ["important", "draft"]}}' style="margin-top: 5px; display: block; width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-family: monospace; font-size: 14px;"></textarea>
                        <small style="color: #666;">JSON object with additional metadata (e.g., author, tags, category)</small>
                    </div>
                    
                    <div style="margin-bottom: 15px;">
                        <label for="creating_time"><strong>Creating Time (optional):</strong></label>
                        <input type="datetime-local" id="creating_time" name="creating_time" style="margin-top: 5px; display: block; width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                        <small style="color: #666;">When the document was created in the source system</small>
                    </div>
                    
                    <div style="margin-bottom: 15px;">
                        <label for="update_time"><strong>Update Time (optional):</strong></label>
                        <input type="datetime-local" id="update_time" name="update_time" style="margin-top: 5px; display: block; width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                        <small style="color: #666;">When the document was last updated in the source system</small>
                    </div>
                    
                    <div style="margin-bottom: 15px; padding: 15px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px;">
                        <label style="display: flex; align-items: center; cursor: pointer;">
                            <input type="checkbox" id="chunk_and_embed" name="chunk_and_embed" checked style="margin-right: 8px; width: auto;">
                            <strong>Chunk and embed document after upload</strong>
                        </label>
                        <small style="color: #666; display: block; margin-left: 24px; margin-top: 5px;">Automatically chunk the document and generate embeddings</small>
                    </div>
                    
                    <div style="margin-bottom: 15px; margin-left: 24px; padding: 15px; background: #f0f0f0; border: 1px solid #ddd; border-radius: 4px;">
                        <div style="margin-bottom: 10px;">
                            <label for="chunk_strategy"><strong>Chunking Strategy:</strong></label>
                            <select id="chunk_strategy" name="chunk_strategy" style="margin-top: 5px; display: block; width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                                {chunk_strategy_options}
                            </select>
                            <small style="color: #666;">Strategy for chunking the document text (default: tokens)</small>
                        </div>
                    </div>
                    
                    <div style="margin-bottom: 15px;">
                        <button type="submit" id="upload-submit-btn" style="padding: 10px 20px; background-color: #4CAF50; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 16px;">Upload</button>
                    </div>
                </form>
            </div>
            
            <div id="upload-status" style="margin-top: 20px;"></div>
            
            <script>
            // Setup loading indicator for upload form
            document.addEventListener('DOMContentLoaded', function() {{
                if (typeof setupFormLoadingIndicator === 'function') {{
                    setupFormLoadingIndicator(
                        'upload-form',
                        'upload-submit-btn',
                        'upload-status',
                        'Uploading and processing file...'
                    );
                }}
            }});
            </script>
        """

        html = html_templates.base_template(
            title="Upload Document",
            content=content,
            username=username
        )
        return HTMLResponse(html)

    @app.route("/web/upload", methods=["POST"])
    async def upload_file(request: Request):
        """Handle file upload (requires admin privileges)."""
        # Check authentication and admin privileges
        session_data, redirect = await require_auth_html(request, session_manager, require_admin=True)
        if redirect:
            return redirect

        username = session_data.get("username", "User")

        try:
            # Parse multipart form data
            form = await request.form()
            file = form.get("file")
            source_id = "upload"
            doc_id = form.get("doc_id")
            meta_text = form.get("meta")
            creating_time_str = form.get("creating_time")
            update_time_str = form.get("update_time")
            chunk_and_embed = form.get("chunk_and_embed") == "on"  # Checkbox returns "on" when checked
            chunk_strategy = form.get("chunk_strategy")  # Can be None if checkbox unchecked
            
            # Parse metadata JSON if provided
            meta = None
            if meta_text:
                try:
                    meta = json.loads(meta_text)
                    if not isinstance(meta, dict):
                        return HTMLResponse(
                            html_templates.base_template(
                                title="Upload Error",
                                content=f'<div class="card"><h2>Error</h2><p>Metadata must be a JSON object (dictionary), not a {type(meta).__name__}.</p><p><a href="/web/upload">← Back to Upload</a></p></div>',
                                username=username
                            ),
                            status_code=400
                        )
                except json.JSONDecodeError as e:
                    # Security: Escape error message to prevent XSS
                    error_msg = html_escape(str(e))
                    return HTMLResponse(
                        html_templates.base_template(
                            title="Upload Error",
                            content=f'<div class="card"><h2>Error</h2><p>Invalid JSON in metadata field: {error_msg}</p><p><a href="/web/upload">← Back to Upload</a></p></div>',
                            username=username
                        ),
                        status_code=400
                    )
            
            # Parse datetime fields if provided
            creating_time = None
            if creating_time_str:
                try:
                    # datetime-local format is "YYYY-MM-DDTHH:mm" (no seconds, no timezone)
                    # Parse and make timezone-aware (assume UTC)
                    from datetime import timezone
                    creating_time = datetime.fromisoformat(creating_time_str)
                    if creating_time.tzinfo is None:
                        creating_time = creating_time.replace(tzinfo=timezone.utc)
                except (ValueError, AttributeError) as e:
                    # Security: Escape error message to prevent XSS
                    error_msg = html_escape(str(e))
                    return HTMLResponse(
                        html_templates.base_template(
                            title="Upload Error",
                            content=f'<div class="card"><h2>Error</h2><p>Invalid creating_time format: {error_msg}</p><p><a href="/web/upload">← Back to Upload</a></p></div>',
                            username=username
                        ),
                        status_code=400
                    )
            
            update_time = None
            if update_time_str:
                try:
                    from datetime import timezone
                    update_time = datetime.fromisoformat(update_time_str)
                    if update_time.tzinfo is None:
                        update_time = update_time.replace(tzinfo=timezone.utc)
                except (ValueError, AttributeError) as e:
                    # Security: Escape error message to prevent XSS
                    error_msg = html_escape(str(e))
                    return HTMLResponse(
                        html_templates.base_template(
                            title="Upload Error",
                            content=f'<div class="card"><h2>Error</h2><p>Invalid update_time format: {error_msg}</p><p><a href="/web/upload">← Back to Upload</a></p></div>',
                            username=username
                        ),
                        status_code=400
                    )

            if not file:
                return HTMLResponse(
                    html_templates.base_template(
                        title="Upload Error",
                        content='<div class="card"><h2>Error</h2><p>No file provided.</p><p><a href="/web/upload">← Back to Upload</a></p></div>',
                        username=username
                    ),
                    status_code=400
                )

            # Get filename and save to upload directory
            filename = file.filename
            if not filename:
                filename = "uploaded_file"
            
            # Security: Validate file extension before processing
            ALLOWED_EXTENSIONS = {
                ".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md",
                ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
                ".csv", ".json", ".xml", ".html", ".htm"
            }
            file_ext = Path(filename).suffix.lower()
            if file_ext not in ALLOWED_EXTENSIONS:
                return HTMLResponse(
                    html_templates.base_template(
                        title="Upload Error",
                        content=f'<div class="card"><h2>Error</h2><p>File type not allowed. Allowed extensions: {", ".join(sorted(ALLOWED_EXTENSIONS))}</p><p><a href="/web/upload">← Back to Upload</a></p></div>',
                        username=username
                    ),
                    status_code=400
                )
            
            # Sanitize filename
            safe_filename = "".join(c for c in filename if c.isalnum() or c in ".-_ ").strip()
            if not safe_filename:
                safe_filename = "uploaded_file"
            
            # Read file content
            file_content = await file.read()
            
            # Security: Check file size (100MB default limit, configurable via MAX_UPLOAD_SIZE env var)
            MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", "104857600"))  # Default: 100MB
            if len(file_content) > MAX_UPLOAD_SIZE:
                size_mb = MAX_UPLOAD_SIZE / (1024 * 1024)
                return HTMLResponse(
                    html_templates.base_template(
                        title="Upload Error",
                        content=f'<div class="card"><h2>Error</h2><p>File too large. Maximum size: {size_mb:.0f}MB</p><p><a href="/web/upload">← Back to Upload</a></p></div>',
                        username=username
                    ),
                    status_code=400
                )
            
            # Save file to upload directory
            file_path = upload_dir / safe_filename
            # Handle duplicates by adding a number
            counter = 1
            original_path = file_path
            while file_path.exists():
                stem = original_path.stem
                suffix = original_path.suffix
                file_path = upload_dir / f"{stem}_{counter}{suffix}"
                counter += 1
            
            file_path.write_bytes(file_content)
            logger.info(f"Saved uploaded file: {file_path}")
            
            # Set URI to local://uploads/filename for serving from data/uploads
            # Use the final filename (after duplicate handling)
            final_filename = file_path.name
            uri = f"local://uploads/{final_filename}"

            # Ensure source exists
            from ..kb import add_source, add_from_path
            
            # Create source if it doesn't exist (using add_source which handles upsert)
            add_source(
                source_id=source_id,
                name="Upload",
                description="Documents uploaded via web interface",
            )

            # Add document to knowledge base with image extraction and LLM descriptions
            try:
                # Build data dict with optional fields
                data_dict = {
                    "source_id": source_id,
                    "uri": uri,
                }
                if doc_id:
                    data_dict["doc_id"] = doc_id
                if meta:
                    data_dict["meta"] = meta
                if creating_time:
                    data_dict["creating_time"] = creating_time
                if update_time:
                    data_dict["update_time"] = update_time
                
                docs = add_from_path(
                    str(file_path),
                    data=data_dict,
                    parse_image_additional_doc=True,
                    parse_image_llm_description=True,
                )
                
                doc_count = len(docs)
                
                # Chunk and embed if requested, track chunk counts
                chunk_counts = {}
                if chunk_and_embed:
                    try:
                        from ..kb.embedding import chunk_and_embed
                        for doc in docs:
                            # Chunk and embed all documents (chunk_and_embed will handle documents without text gracefully)
                            try:
                                chunks = chunk_and_embed(
                                    doc,
                                    strategy=chunk_strategy if chunk_strategy else None
                                )
                                chunk_counts[doc.id] = len(chunks) if chunks else 0
                            except ValueError as e:
                                # Document might not have text, skip it
                                logger.debug(f"Skipping chunk_and_embed for document {doc.id}: {e}")
                                chunk_counts[doc.id] = 0
                                continue
                    except Exception as e:
                        logger.warning(f"Error chunking and embedding documents: {e}", exc_info=True)
                        # Don't fail the upload if chunking/embedding fails
                
                # Build success message with links to documents
                doc_ids = [doc.id for doc in docs]
                doc_links = []
                for doc in docs:
                    chunk_info = ""
                    if chunk_and_embed and doc.id in chunk_counts:
                        chunk_count = chunk_counts[doc.id]
                        if chunk_count > 0:
                            chunk_info = f" ({chunk_count} chunk{'s' if chunk_count != 1 else ''})"
                    doc_links.append(f'<a href="/web/document/{doc.id}">Document {doc.id}</a> ({doc.doc_type}){chunk_info}')
                
                # Security: Escape filename in success message to prevent XSS
                safe_filename = html_escape(filename)
                chunk_status = " and chunked/embedded" if chunk_and_embed else ""
                if doc_count == 1:
                    doc = docs[0]
                    chunk_info = ""
                    if chunk_and_embed and doc.id in chunk_counts:
                        chunk_count = chunk_counts[doc.id]
                        if chunk_count > 0:
                            chunk_info = f" ({chunk_count} chunk{'s' if chunk_count != 1 else ''})"
                    success_message = f'Upload successful! File "{safe_filename}" added as <a href="/web/document/{doc.id}">Document {doc.id}</a> ({doc.doc_type}){chunk_info}{chunk_status}.'
                else:
                    doc_list = ", ".join(doc_links)
                    success_message = f'Upload successful! File "{safe_filename}" created {doc_count} documents: {doc_list}{chunk_status}.'
                
                # Redirect to knowledge base explorer with success message and filter to upload source
                from urllib.parse import urlencode
                redirect_params = {
                    "source_id": source_id,
                    "message": success_message,
                    "uploaded_docs": ",".join(doc_ids)
                }
                redirect_url = f"/web?{urlencode(redirect_params)}"
                
                return RedirectResponse(url=redirect_url, status_code=303)

            except Exception as e:
                logger.error(f"Error adding document to KB: {e}", exc_info=True)
                # Clean up uploaded file on error
                if file_path.exists():
                    file_path.unlink()
                
                # Security: Escape error message to prevent XSS
                error_msg = html_escape(str(e))
                error_content = f"""
                    <div class="card">
                        <h2>Error Processing File</h2>
                        <p>An error occurred while processing the uploaded file:</p>
                        <div class="info-box" style="margin-top: 10px;">
                            <strong>Error:</strong> {error_msg}
                        </div>
                        <p style="margin-top: 20px;">
                            <a href="/web/upload">← Back to Upload</a>
                        </p>
                    </div>
                """
                
                return HTMLResponse(
                    html_templates.base_template(
                        title="Upload Error",
                        content=error_content,
                        username=username
                    ),
                    status_code=500
                )

        except Exception as e:
            logger.error(f"Error in upload_file: {e}", exc_info=True)
            # Security: Escape error message to prevent XSS
            error_msg = html_escape(str(e))
            error_content = f"""
                <div class="card">
                    <h2>Upload Error</h2>
                    <p>An error occurred during upload:</p>
                    <div class="info-box" style="margin-top: 10px;">
                        <strong>Error:</strong> {error_msg}
                    </div>
                    <p style="margin-top: 20px;">
                        <a href="/web/upload">← Back to Upload</a>
                    </p>
                </div>
            """
            
            return HTMLResponse(
                html_templates.base_template(
                    title="Upload Error",
                    content=error_content,
                    username=username
                ),
                status_code=500
            )

    @app.route("/web/statistics")
    async def web_statistics_route(request: Request):
        """Statistics page route."""
        return await web_statistics.web_statistics(request, session_manager)
