"""Protected web interface for interactive MCP tool usage - document routes."""

import json
import logging
import os
from datetime import datetime, timezone
from html import escape as html_escape
from pathlib import Path
from starlette.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from starlette.requests import Request

from ..auth import WebSessionManager
from .. import html_templates

logger = logging.getLogger(__name__)


def _to_utc_iso(dt: datetime | None) -> str | None:
    """Convert datetime to UTC and return ISO format string with 'Z' suffix.
    
    Simple approach: ensure datetime is UTC, then return ISO format with 'Z' suffix.
    JavaScript will parse 'Z' as UTC and convert to local time.
    """
    if dt is None:
        return None
    
    # Convert to UTC if needed
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    
    # Get ISO format string - remove microseconds for cleaner format
    iso_str = dt.strftime('%Y-%m-%dT%H:%M:%S')
    
    # Always append 'Z' to indicate UTC
    return iso_str + 'Z'


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
    
    # Add title fields
    if doc.title:
        result["title"] = doc.title
    if doc.title_gen:
        result["title_gen"] = doc.title_gen
    
    # Add LLM-generated fields
    if doc.summary:
        result["summary"] = doc.summary
    if doc.gist:
        result["gist"] = doc.gist
    
    # Add timestamps
    if doc.insert_time:
        result["insert_time"] = _to_utc_iso(doc.insert_time)
    if doc.creating_time:
        result["creating_time"] = _to_utc_iso(doc.creating_time)
    if doc.update_time:
        result["update_time"] = _to_utc_iso(doc.update_time)
    
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


def _create_expandable_summary(field_id: str, content: str, max_length: int = 200) -> str:
    """Create an expandable summary field with + / - toggle (metadata-style).
    
    Args:
        field_id: Unique identifier for the field
        content: Content to display
        max_length: Maximum length before showing expand/collapse (default: 200)
    
    Returns:
        HTML string with expandable field
    """
    if not content:
        return "N/A"
    
    content_escaped = html_escape(content)
    needs_expansion = len(content) > max_length
    
    if not needs_expansion:
        return content_escaped
    
    # Create expandable field (metadata-style)
    collapsed_style = "white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%;"
    
    return f'''
    <div style="position: relative;">
        <div id="{field_id}-collapsed" style="display: block;">
            <div style="{collapsed_style}">{content_escaped}</div>
            <span onclick="toggleMetaField('{field_id}')" style="position: absolute; top: 8px; right: 8px; cursor: pointer; font-size: 16px; color: #666; user-select: none;">+</span>
        </div>
        <div id="{field_id}-expanded" style="display: none;">
            <div style="white-space: pre-wrap; word-wrap: break-word;">{content_escaped}</div>
            <span onclick="toggleMetaField('{field_id}')" style="position: absolute; top: 8px; right: 8px; cursor: pointer; font-size: 16px; color: #666; user-select: none;">−</span>
        </div>
    </div>
    '''


def _create_gist_field(content: str) -> str:
    """Create a gist field with enforced line breaks.
    
    Args:
        content: Content to display
    
    Returns:
        HTML string with gist field
    """
    if not content:
        return "N/A"
    
    content_escaped = html_escape(content)
    
    # Enforce line breaks using pre-wrap
    return f'<div style="white-space: pre-wrap; word-wrap: break-word;">{content_escaped}</div>'


def setup_documents_routes(app, oauth_provider, session_manager: WebSessionManager, require_auth_html, require_auth_api):
    """Setup document management web interface routes.

    Args:
        app: Starlette application instance
        oauth_provider: OAuth provider instance
        session_manager: WebSessionManager instance for authentication
        require_auth_html: Authentication helper function for HTML routes
        require_auth_api: Authentication helper function for API routes
    """

    # Get upload directory
    from ....config import get_data_dir
    data_dir = get_data_dir()
    upload_dir = Path(data_dir) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

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
        from ....kb import get_options
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
                    <div class="filter-group" style="flex: 1;">
                        <label for="search-input">Search Query:</label>
                        <input type="search" id="search-input" name="search" value="{search}" placeholder="Enter search query...">
                    </div>
                    <div class="filter-group">
                        <label for="search-type">Search Type:</label>
                        <select id="search-type" name="search_type">
                            <option value="hybrid" selected>Hybrid (Semantic + Fulltext)</option>
                            <option value="semantic">Semantic Only</option>
                            <option value="fulltext">Fulltext Only</option>
                        </select>
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
                <div class="filter-row" id="metadata-filters-row" style="margin-top: 15px; padding-top: 15px; border-top: 1px solid #ddd;">
                    <div style="flex: 1;">
                        <h3 style="margin: 0 0 10px 0; font-size: 14px; font-weight: normal;">Metadata Filters</h3>
                        
                        <!-- Date range filters (compact) -->
                        <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 10px;">
                            <label for="date-type" style="font-size: 12px; white-space: nowrap;">Date Type:</label>
                            <select id="date-type" style="padding: 4px 8px; font-size: 12px;">
                                <option value="insert_time">Insertion</option>
                                <option value="creating_time">Creation</option>
                                <option value="update_time">Update</option>
                            </select>
                            <label for="date-from" style="font-size: 12px; white-space: nowrap;">From:</label>
                            <input type="date" id="date-from" style="padding: 4px 8px; font-size: 12px;">
                            <label for="date-to" style="font-size: 12px; white-space: nowrap;">To:</label>
                            <input type="date" id="date-to" style="padding: 4px 8px; font-size: 12px;">
                        </div>
                        
                        <!-- Metadata filter input -->
                        <div style="display: flex; gap: 5px; align-items: center; margin-bottom: 10px;">
                            <select id="metadata-key-input" style="width: 120px; padding: 4px 8px; font-size: 12px;">
                                <option value="">Select key...</option>
                            </select>
                            <select id="metadata-operation" style="padding: 4px 8px; font-size: 12px;">
                                <option value="term">equals</option>
                                <option value="match">contains</option>
                                <option value="gte">≥</option>
                                <option value="lte">≤</option>
                                <option value="gt">></option>
                                <option value="lt"><</option>
                            </select>
                            <input type="text" id="metadata-value-input" placeholder="Value" style="width: 150px; padding: 4px 8px; font-size: 12px;">
                            <button type="button" id="add-metadata-filter" class="btn" style="padding: 4px 12px; font-size: 12px;">Add</button>
                        </div>
                        
                        <!-- Active filters display -->
                        <div id="metadata-filters-list" style="display: flex; flex-wrap: wrap; gap: 5px; align-items: center; margin-top: 5px;"></div>
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
        import time
        timings = {}
        t_start = time.time()
        
        # Check authentication first
        session_data = await session_manager.get_session_data(request)
        if not session_data:
            return RedirectResponse(url="/login?redirect=/web")
        
        # Get username from authenticated session
        username = session_data.get("username")
        timings['auth'] = time.time() - t_start

        doc_id = request.path_params["doc_id"]

        # Get document from knowledge base
        from ....kb import get

        try:
            t0 = time.time()
            doc = get(uid=doc_id)
            timings['get_document'] = time.time() - t0
            timings['get_document_since_start'] = time.time() - t_start
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

            # Build display title: use title, title_gen, or doc_id
            doc_title_display = doc.doc_id or "N/A"
            if doc.title:
                doc_title_display = f"{doc.title} ({doc.doc_id or doc.id})"
            elif doc.title_gen:
                doc_title_display = f"{doc.title_gen} ({doc.doc_id or doc.id})"
            
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
                from ....kb.embedding import get_chunk_strategies
                timings['get_chunk_strategies_since_last'] = time.time() - t0
                t0 = time.time()
                strategies = get_chunk_strategies(document_id=doc.id)
                timings['get_chunk_strategies'] = time.time() - t0
                timings['get_chunk_strategies_since_start'] = time.time() - t_start
                
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
            # Build shared chunking strategy options
            chunk_strategy_options_html = '<option value="">Default (tokens)</option>'
            chunk_strategy_options_html_for_similar = '<option value="summary" selected>summary</option>'
            try:
                from ....kb.embedding import get_chunk_strategies
                timings['get_all_strategies_since_last'] = time.time() - t0
                t0 = time.time()
                all_strategies = get_chunk_strategies()  # Get all strategies, not just for this document
                timings['get_all_strategies'] = time.time() - t0
                timings['get_all_strategies_since_start'] = time.time() - t_start
                for strategy_info in all_strategies:
                    strategy_name = strategy_info.get("strategy", "")
                    if strategy_name:
                        chunk_strategy_options_html += f'<option value="{html_escape(strategy_name)}">{html_escape(strategy_name)}</option>'
                        # For similar documents, include all strategies but default to summary
                        if strategy_name != "summary":
                            chunk_strategy_options_html_for_similar += f'<option value="{html_escape(strategy_name)}">{html_escape(strategy_name)}</option>'
            except (ImportError, Exception) as e:
                logger.debug(f"Could not load chunk strategies: {e}")
                # Fallback to default options
                chunk_strategy_options_html = '<option value="">Default (tokens)</option><option value="tokens">tokens</option><option value="slide">slide</option>'
                chunk_strategy_options_html_for_similar = '<option value="summary" selected>summary</option><option value="tokens">tokens</option><option value="slide">slide</option>'
            
            # For backward compatibility
            rechunk_strategy_options = chunk_strategy_options_html

            # Format document data with chunk highlighting support
            # Show summary if available, otherwise show text
            text_content = ""
            content_to_show = doc.text
            if content_to_show:
                # Wrap content in a div with id for JavaScript to access, with expand/collapse
                content_label = "Text"
                text_content = f'''
            <div id="document-text-container" style="position: relative;">
                <div id="text-expanded" style="display: block; position: relative;">
                    <pre id="document-text" style="white-space: pre-wrap; height: 600px; overflow-y: auto; padding: 10px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px; font-family: inherit; line-height: 1.6; margin: 0;">{html_escape(content_to_show)}</pre>
                    <span onclick="toggleTextContent()" style="position: absolute; top: 15px; right: 15px; cursor: pointer; font-size: 16px; color: #666; user-select: none; background: white; padding: 2px 6px; border-radius: 3px;">−</span>
                </div>
                <div id="text-collapsed" style="display: none; position: relative;">
                    <div style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding: 10px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px;">{html_escape(content_to_show[:200])}...</div>
                    <span onclick="toggleTextContent()" style="position: absolute; top: 10px; right: 10px; cursor: pointer; font-size: 16px; color: #666; user-select: none; background: white; padding: 2px 6px; border-radius: 3px;">+</span>
                </div>
            </div>
            <script>
            function toggleTextContent() {{
                const expanded = document.getElementById('text-expanded');
                const collapsed = document.getElementById('text-collapsed');
                if (expanded.style.display === 'none') {{
                    expanded.style.display = 'block';
                    collapsed.style.display = 'none';
                }} else {{
                    expanded.style.display = 'none';
                    collapsed.style.display = 'block';
                }}
            }}
            </script>
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
                    timings['get_parent_since_last'] = time.time() - t0
                    t0 = time.time()
                    parent_doc = get(uid=doc.parent_id)
                    timings['get_parent'] = time.time() - t0
                    timings['get_parent_since_start'] = time.time() - t_start
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
            from ....kb import get_children
            try:
                timings['get_children_since_last'] = time.time() - t0
                t0 = time.time()
                children = get_children(doc.id)
                timings['get_children'] = time.time() - t0
                timings['get_children_since_start'] = time.time() - t_start
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

            # Get raw document information if available
            raw_doc_html = ""
            if doc.raw_document_id:
                try:
                    from ....kb.documents.operations import get_raw_document
                    from ....kb.db_models import Document as DocumentModel
                    timings['get_raw_doc_since_last'] = time.time() - t0
                    t0 = time.time()
                    raw_doc = get_raw_document(doc.id)
                    timings['get_raw_doc'] = time.time() - t0
                    timings['get_raw_doc_since_start'] = time.time() - t_start

                    if raw_doc:
                        # Get all sibling documents (other documents from the same raw document)
                        timings['get_siblings_since_last'] = time.time() - t0
                        t0 = time.time()
                        from ....kb.database import get_db_session
                        with get_db_session() as session:
                            siblings = session.query(DocumentModel).filter(
                                DocumentModel.raw_document_id == doc.raw_document_id,
                                DocumentModel.doc_type == "text"
                            ).order_by(DocumentModel.insert_time).all()
                        timings['get_siblings'] = time.time() - t0
                        timings['get_siblings_since_start'] = time.time() - t_start

                        siblings_list = ""
                        for sibling in siblings:
                            sibling_label = sibling.doc_id or sibling.id
                            sibling_parser = sibling.parser_id if sibling.parser_id else "unknown"
                            is_current = sibling.id == doc.id
                            style = 'color: #999; font-style: italic;' if is_current else ''
                            siblings_list += f'<li><a href="/web/document/{sibling.id}" style="{style}">{html_escape(sibling_label)}</a> ({sibling_parser}){" (current)" if is_current else ""}</li>'

                        # Format file path
                        file_path_display = raw_doc.file_path or "N/A"
                        if raw_doc.hostname:
                            file_path_display = f"{raw_doc.hostname}:{file_path_display}"
                        file_path_display = html_escape(file_path_display)

                        raw_doc_html = f"""
            <div class="card">
                <h2>Raw Document</h2>
                <table>
                    <tr><th>Raw Document ID</th><td><code><a href="/web/raw/{raw_doc.id}">{raw_doc.id}</a></code></td></tr>
                    <tr><th>Host File Path</th><td>{file_path_display}</td></tr>
                    <tr><th>File Size</th><td>{raw_doc.file_size if raw_doc.file_size else "N/A"} bytes</td></tr>
                </table>
                <h3 style="margin-top: 20px; margin-bottom: 10px;">All Documents from this Raw File ({len(siblings)})</h3>
                <ul>
                    {siblings_list}
                </ul>
            </div>
                        """
                except Exception as e:
                    logger.warning(f"Could not fetch raw document for {doc.id}: {e}")

            t0 = time.time()
            timings['build_meta_html_since_last'] = time.time() - t0
            meta_html = ""
            if doc.meta:
                meta_html = '<div class="card"><h2>Metadata</h2><div style="max-height: 400px; overflow-y: auto; border: 1px solid #ddd; border-radius: 4px;"><table style="width: 100%;"><tr><th style="text-align: left; padding: 8px; border-bottom: 1px solid #ddd; position: sticky; top: 0; background: white;">Key</th><th style="text-align: left; padding: 8px; border-bottom: 1px solid #ddd; position: sticky; top: 0; background: white;">Value</th></tr>'
                field_index = 0
                for key, value in doc.meta.items():
                    field_id = f"meta-field-{field_index}"
                    # Format value based on type
                    if isinstance(value, list):
                        # Special handling for authors or other lists
                        if key.lower() == 'author' or key.lower() == 'authors':
                            # Format as comma-separated list
                            formatted_value = ', '.join(str(v) for v in value)
                            full_value = formatted_value
                        else:
                            # Format other lists as bullet points
                            list_items = ''.join(f'<li>{html_escape(str(v))}</li>' for v in value)
                            formatted_value = f'<ul style="margin: 0; padding-left: 20px;">{list_items}</ul>'
                            full_value = formatted_value
                    elif isinstance(value, dict):
                        # Format dict as JSON-like structure
                        import json
                        formatted_value = f'<pre style="margin: 0; white-space: pre-wrap;">{html_escape(json.dumps(value, indent=2))}</pre>'
                        full_value = formatted_value
                    elif isinstance(value, (str, int, float, bool)):
                        formatted_value = html_escape(str(value))
                        full_value = formatted_value
                    else:
                        formatted_value = html_escape(str(value))
                        full_value = f'<pre style="margin: 0;">{formatted_value}</pre>'
                    
                    # Create expandable field - check if content is long enough to need truncation
                    # For text content, check length; for HTML (lists/dicts), always allow expansion
                    is_html = isinstance(value, (list, dict))
                    is_long_text = isinstance(value, str) and len(str(value)) > 200
                    needs_expansion = is_html or is_long_text
                    
                    if needs_expansion:
                        # Create expandable field
                        # For collapsed view: use max-height and overflow for HTML, text-overflow for plain text
                        if is_html:
                            collapsed_style = "max-height: 1.5em; overflow: hidden;"
                        else:
                            collapsed_style = "white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%;"
                        
                        meta_html += f'''
                        <tr>
                            <td style="padding: 8px; border-bottom: 1px solid #eee; vertical-align: top;"><strong>{html_escape(str(key))}</strong></td>
                            <td style="padding: 8px; border-bottom: 1px solid #eee; position: relative;">
                                <div id="{field_id}-collapsed" style="display: block;">
                                    <div style="{collapsed_style}">{full_value}</div>
                                    <span onclick="toggleMetaField('{field_id}')" style="position: absolute; top: 8px; right: 8px; cursor: pointer; font-size: 16px; color: #666; user-select: none;">+</span>
                                </div>
                                <div id="{field_id}-expanded" style="display: none;">
                                    <div>{full_value}</div>
                                    <span onclick="toggleMetaField('{field_id}')" style="position: absolute; top: 8px; right: 8px; cursor: pointer; font-size: 16px; color: #666; user-select: none;">−</span>
                                </div>
                            </td>
                        </tr>
                        '''
                    else:
                        # Simple non-expandable field
                        meta_html += f'<tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>{html_escape(str(key))}</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{full_value}</td></tr>'
                    field_index += 1
                meta_html += '</table></div></div>'
                # Add JavaScript for toggle functionality
                meta_html += '''
                <script>
                function toggleMetaField(fieldId) {
                    const collapsed = document.getElementById(fieldId + '-collapsed');
                    const expanded = document.getElementById(fieldId + '-expanded');
                    if (collapsed.style.display === 'none') {
                        collapsed.style.display = 'block';
                        expanded.style.display = 'none';
                    } else {
                        collapsed.style.display = 'none';
                        expanded.style.display = 'block';
                    }
                }
                </script>
                '''
            timings['build_meta_html'] = time.time() - t0
            timings['build_meta_html_since_start'] = time.time() - t_start

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
            
            # Ensure toggle function is available (add if not already in meta_html)
            toggle_script = '''
            <script>
            function toggleMetaField(fieldId) {
                const collapsed = document.getElementById(fieldId + '-collapsed');
                const expanded = document.getElementById(fieldId + '-expanded');
                if (collapsed.style.display === 'none') {
                    collapsed.style.display = 'block';
                    expanded.style.display = 'none';
                } else {
                    collapsed.style.display = 'none';
                    expanded.style.display = 'block';
                }
            }
            </script>
            ''' if 'toggleMetaField' not in meta_html else ''
            
            # Build AI-generated content HTML
            ai_content_html = ""
            if doc.title_gen or doc.summary or doc.gist:
                ai_parts = []
                if doc.title_gen:
                    ai_parts.append(f'''
                <div style="margin-bottom: 20px;">
                    <h3 style="margin: 0 0 8px 0; font-size: 16px; color: #333;">Generated Title</h3>
                    <div style="white-space: pre-wrap; padding: 15px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px; line-height: 1.6;">{html_escape(doc.title_gen)}</div>
                </div>
                ''')
                if doc.gist:
                    ai_parts.append(f'''
                <div style="margin-bottom: 20px;">
                    <h3 style="margin: 0 0 8px 0; font-size: 16px; color: #333;">Gist (Key Concepts)</h3>
                    <div style="white-space: pre-line; padding: 15px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px; line-height: 1.6;">{html_escape(doc.gist)}</div>
                </div>
                ''')
                if doc.summary:
                    summary_field_id = f"summary-{doc.id}"
                    ai_parts.append(f'''
                <div style="margin-bottom: 20px;">
                    <h3 style="margin: 0 0 8px 0; font-size: 16px; color: #333;">Summary</h3>
                    <div id="{summary_field_id}-expanded" style="display: block; position: relative;">
                        <div style="white-space: pre-wrap; padding: 15px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px; line-height: 1.6;">{html_escape(doc.summary)}</div>
                        <span onclick="toggleMetaField('{summary_field_id}')" style="position: absolute; top: 15px; right: 15px; cursor: pointer; font-size: 16px; color: #666; user-select: none; background: white; padding: 2px 6px; border-radius: 3px;">−</span>
                    </div>
                    <div id="{summary_field_id}-collapsed" style="display: none; position: relative;">
                        <div style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; padding: 15px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px; line-height: 1.6;">{html_escape(doc.summary)}</div>
                        <span onclick="toggleMetaField('{summary_field_id}')" style="position: absolute; top: 15px; right: 15px; cursor: pointer; font-size: 16px; color: #666; user-select: none; background: white; padding: 2px 6px; border-radius: 3px;">+</span>
                    </div>
                </div>
                ''')
                ai_content_html = f'''
            <div class="card">
                <h2>Generated Content</h2>
                {''.join(ai_parts)}
            </div>
            '''

            # Get eval-related information (before building content string)
            eval_results_html = ""
            eval_questions_html = ""
            t_before_session_exit = None
            try:
                from ....kb.eval.db_models import EvalRetrievedDocument, EvalDataset, EvalResult
                from ....kb.database import get_db_session_ephemeral
                from sqlalchemy.orm import joinedload
                
                with get_db_session_ephemeral() as session:
                    # Get EvalRetrievedDocument records where this document was retrieved
                    timings['eval_retrieved_docs_since_last'] = time.time() - t0
                    t0 = time.time()
                    retrieved_docs = session.query(EvalRetrievedDocument).options(
                        joinedload(EvalRetrievedDocument.result).joinedload(EvalResult.run),
                        joinedload(EvalRetrievedDocument.result).joinedload(EvalResult.question)
                    ).filter(EvalRetrievedDocument.document_id == doc.id).order_by(EvalRetrievedDocument.created_time.desc()).all()
                    timings['eval_retrieved_docs'] = time.time() - t0
                    timings['eval_retrieved_docs_count'] = len(retrieved_docs)
                    timings['eval_retrieved_docs_since_start'] = time.time() - t_start
                    
                    if retrieved_docs:
                        t0 = time.time()
                        retrieved_docs_list = ""
                        for retrieved_doc in retrieved_docs:
                            result = retrieved_doc.result
                            if result:
                                # Get run name
                                run_name = ""
                                if result.run:
                                    run_name = result.run.name or f"Run {result.run.id[:8]}"
                                else:
                                    run_name = f"Run {result.run_id[:8]}"
                                
                                # Get question preview
                                question_preview = ""
                                if result.question:
                                    question_preview = result.question.question[:80] + "..." if len(result.question.question) > 80 else result.question.question
                                else:
                                    question_preview = f"Question {result.question_id[:8]}"
                                
                                similarity_display = f"{retrieved_doc.similarity:.3f}" if retrieved_doc.similarity is not None else "—"
                                
                                retrieved_docs_list += f"""
                        <tr style="border-bottom: 1px solid #eee;" onmouseover="this.style.backgroundColor='#f8f9fa'" onmouseout="this.style.backgroundColor='transparent'">
                            <td style="padding: 12px 10px;">{retrieved_doc.rank}</td>
                            <td style="padding: 12px 10px;">
                                <a href="/web/eval/result/{result.id}" style="text-decoration: none; color: #2196F3;">{html_escape(run_name)}</a>
                            </td>
                            <td style="padding: 12px 10px;">
                                <a href="/web/eval/question/{result.question_id}" style="text-decoration: none; color: #2196F3;">{html_escape(question_preview)}</a>
                            </td>
                            <td style="padding: 12px 10px; color: #666;">{similarity_display}</td>
                            <td style="padding: 12px 10px;">
                                <a href="/web/eval/result/{result.id}" style="text-decoration: none; color: #2196F3; font-weight: 500;">View</a>
                            </td>
                        </tr>
                        """
                        timings['retrieved_docs_loop'] = time.time() - t0
                        t0 = time.time()
                        
                        eval_results_html = f"""
            <div class="card" style="margin-top: 20px;">
                <h2>Evaluation Results ({len(retrieved_docs)})</h2>
                <p style="color: #666; font-size: 14px; margin-bottom: 10px;">This document was retrieved in the following evaluation results:</p>
                <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                    <thead>
                        <tr style="background-color: #f5f5f5; border-bottom: 2px solid #ddd;">
                            <th style="text-align: left; padding: 10px; font-weight: 600;">Rank</th>
                            <th style="text-align: left; padding: 10px; font-weight: 600;">Run</th>
                            <th style="text-align: left; padding: 10px; font-weight: 600;">Question</th>
                            <th style="text-align: left; padding: 10px; font-weight: 600;">Similarity</th>
                            <th style="text-align: left; padding: 10px; font-weight: 600;">View</th>
                        </tr>
                    </thead>
                    <tbody>
                        {retrieved_docs_list}
                    </tbody>
                </table>
            </div>
            """
                        timings['build_eval_results_html'] = time.time() - t0
                    
                    # Get graph nodes for this document
                    graph_nodes_html = ""
                    graph_extraction_logs_data = "[]"
                    try:
                        from ....kb.graph.queries import get_nodes_for_document
                        from ....kb.graph.db_models import GraphExtractionLog

                        timings['graph_nodes_since_last'] = time.time() - t0
                        t0 = time.time()
                        nodes = get_nodes_for_document(document_id=doc.id, session=session)
                        timings['graph_nodes'] = time.time() - t0
                        timings['graph_nodes_count'] = len(nodes)

                        if nodes:
                            t0 = time.time()
                            nodes_list = ""
                            for node_info in nodes:
                                node_id = node_info["id"]
                                node_name = html_escape(node_info["name"])
                                node_type = html_escape(node_info["type"])
                                mention_count = node_info["mention_count"]
                                aliases_str = ", ".join(html_escape(a) for a in node_info["aliases"]) if node_info["aliases"] else "—"

                                nodes_list += f"""
                            <tr style="border-bottom: 1px solid #eee; cursor: pointer;" onclick="window.location.href='/web/graph/node/{node_id}'" onmouseover="this.style.backgroundColor='#f8f9fa'" onmouseout="this.style.backgroundColor='transparent'">
                                <td style="padding: 12px 10px;">
                                    <a href="/web/graph/node/{node_id}" style="text-decoration: none; color: #2196F3;">{node_name}</a>
                                </td>
                                <td style="padding: 12px 10px; color: #666;">{node_type}</td>
                                <td style="padding: 12px 10px; color: #666;">{mention_count}</td>
                                <td style="padding: 12px 10px; color: #666; font-size: 12px;">{aliases_str}</td>
                            </tr>
                            """

                            graph_nodes_html = f"""
                <div class="card" style="margin-top: 20px;">
                    <h2>Knowledge Graph Nodes ({len(nodes)})</h2>
                    <p style="color: #666; font-size: 14px; margin-bottom: 10px;">Entities and concepts extracted from this document:</p>
                    <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                        <thead>
                            <tr style="background-color: #f5f5f5; border-bottom: 2px solid #ddd;">
                                <th style="text-align: left; padding: 10px; font-weight: 600;">Name</th>
                                <th style="text-align: left; padding: 10px; font-weight: 600;">Type</th>
                                <th style="text-align: left; padding: 10px; font-weight: 600;">Mentions</th>
                                <th style="text-align: left; padding: 10px; font-weight: 600;">Aliases</th>
                            </tr>
                        </thead>
                        <tbody>
                            {nodes_list}
                        </tbody>
                    </table>
                </div>
                """
                            timings['build_graph_nodes_html'] = time.time() - t0

                        # Get extraction logs for this document and prepare for JavaScript
                        timings['graph_extraction_logs_since_last'] = time.time() - t0
                        t0 = time.time()
                        extraction_logs = session.query(GraphExtractionLog).filter(
                            GraphExtractionLog.document_id == doc.id
                        ).order_by(GraphExtractionLog.created_time.desc()).all()
                        timings['graph_extraction_logs'] = time.time() - t0
                        timings['graph_extraction_logs_count'] = len(extraction_logs)

                        # Convert extraction logs to JSON for JavaScript
                        graph_extraction_logs_json = []
                        if extraction_logs:
                            import json as json_module
                            for log in extraction_logs:
                                graph_extraction_logs_json.append({
                                    "created_time": _to_utc_iso(log.created_time),
                                    "extraction_model": log.extraction_model or "N/A",
                                    "hostname": log.hostname or "N/A",
                                    "time_extraction": log.time_extraction,
                                    "time_processing": log.time_processing,
                                    "relations_extracted": log.relations_extracted,
                                    "relations_created": log.relations_created,
                                    "relations_updated": log.relations_updated,
                                    "relations_errors": log.relations_errors
                                })
                            graph_extraction_logs_data = json_module.dumps(graph_extraction_logs_json)
                        else:
                            graph_extraction_logs_data = "[]"

                    except (ImportError, Exception) as e:
                        logger.debug(f"Could not load graph information: {e}")
                        # Graph module may not be available, that's okay

                    # Get EvalDataset questions where this document is the source
                    timings['eval_questions_since_last'] = time.time() - t0
                    t0 = time.time()
                    questions = session.query(EvalDataset).options(
                        joinedload(EvalDataset.generation)
                    ).filter(EvalDataset.source_document_id == doc.id).order_by(EvalDataset.created_time.desc()).all()
                    timings['eval_questions'] = time.time() - t0
                    timings['eval_questions_count'] = len(questions)
                    timings['eval_questions_since_start'] = time.time() - t_start
                    
                    if questions:
                        t0 = time.time()
                        questions_list = ""
                        for question in questions:
                            question_preview = question.question[:100] + "..." if len(question.question) > 100 else question.question
                            generation_link = ""
                            if question.generation_id:
                                if question.generation:
                                    gen_name = question.generation.name or f"{question.generation.generation_type} Generation"
                                    generation_link = f'<a href="/web/eval/generation/{question.generation_id}" style="text-decoration: none; color: #2196F3;">{html_escape(gen_name)}</a>'
                                else:
                                    generation_link = f'<a href="/web/eval/generation/{question.generation_id}" style="text-decoration: none; color: #2196F3;">View</a>'
                            else:
                                generation_link = "—"
                            
                            questions_list += f"""
                        <tr style="border-bottom: 1px solid #eee; cursor: pointer;" onclick="window.location.href='/web/eval/question/{question.id}'" onmouseover="this.style.backgroundColor='#f8f9fa'" onmouseout="this.style.backgroundColor='transparent'">
                            <td style="padding: 12px 10px;">
                                <a href="/web/eval/question/{question.id}" style="text-decoration: none; color: #2196F3;">{html_escape(question_preview)}</a>
                            </td>
                            <td style="padding: 12px 10px; color: #666;">{generation_link}</td>
                        </tr>
                        """
                        
                        timings['questions_loop'] = time.time() - t0
                        t0 = time.time()
                        eval_questions_html = f"""
            <div class="card" style="margin-top: 20px;">
                <h2>Evaluation Questions ({len(questions)})</h2>
                <p style="color: #666; font-size: 14px; margin-bottom: 10px;">Questions generated from this document:</p>
                <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                    <thead>
                        <tr style="background-color: #f5f5f5; border-bottom: 2px solid #ddd;">
                            <th style="text-align: left; padding: 10px; font-weight: 600;">Question</th>
                            <th style="text-align: left; padding: 10px; font-weight: 600;">Generation</th>
                        </tr>
                    </thead>
                    <tbody>
                        {questions_list}
                    </tbody>
                </table>
            </div>
            """
                        timings['build_eval_questions_html'] = time.time() - t0
                
                # Session will close here when exiting the 'with' block
                t_before_session_exit = time.time()
                timings['time_after_eval_html'] = t_before_session_exit - t_start
            except (ImportError, Exception) as e:
                logger.error(f"Could not load eval information: {e}")
                # Eval module may not be available, that's okay
                t_before_session_exit = time.time()
            
            # Session has now closed (exited the 'with' block)
            t_after_session_exit = time.time()
            timings['session_exit_time'] = t_after_session_exit - t_before_session_exit
            timings['session_exit_since_start'] = t_after_session_exit - t_start
            
            # Measure gap between session close and content building
            t_before_content = time.time()
            timings['gap_before_content'] = t_before_content - t_after_session_exit
            timings['time_until_content_start'] = t_before_content - t_start
            t0 = time.time()
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
                    <tr><th>Parser</th><td>{doc.parser_id}</td></tr>
                    <tr><th>Title</th><td>{doc.title or "N/A"}</td></tr>
                    <tr><th>URI</th><td>{uri_display}</td></tr>
                    <tr><th>Source Type</th><td>{doc.source_type}</td></tr>
                    <tr><th>Document Type</th><td>{doc.doc_type}</td></tr>
                    <tr><th>Parent Document</th><td>{parent_display}</td></tr>
                    <tr><th>Insert Time</th><td class="utc-timestamp" data-iso="{_to_utc_iso(doc.insert_time) or ''}">{_to_utc_iso(doc.insert_time) or "N/A"}</td></tr>
                    <tr><th>Creating Time</th><td class="utc-timestamp" data-iso="{_to_utc_iso(doc.creating_time) or ''}">{_to_utc_iso(doc.creating_time) or "N/A"}</td></tr>
                    <tr><th>Update Time</th><td class="utc-timestamp" data-iso="{_to_utc_iso(doc.update_time) or ''}">{_to_utc_iso(doc.update_time) or "N/A"}</td></tr>
                </table>
            </div>

            {image_html}

            {binary_info}

            {children_html}

            {meta_html if meta_html else ''}

            {raw_doc_html}

            {ai_content_html}

            <div class="card">
                <h2>{'Full Text Content' if doc.text and not doc.summary else 'Text Content' if doc.text else 'Content'}</h2>
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
                {text_content if text_content else (f'<div class="info-box">No text content available.{" Summary is shown above." if doc.summary else ""}</div>')}
            </div>
            <div class="card">
                <h2>Document Functions</h2>
                <div style="display: flex; gap: 15px; margin-top: 20px; align-items: flex-start;">
                    <div class="card" style="flex: 1; min-width: 200px; margin-top: 0;">
                        <!-- <h2>Generate Summary</h2> -->
                        <form id="generate-summary-form" method="POST" action="/web/document/{doc.id}/generate-summary">
                            <button type="submit" id="generate-summary-submit-btn" style="width: 100%; padding: 10px 20px; background-color: #FF9800; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px;">Generate Summary</button>
                        </form>
                        <div id="generate-summary-status" style="margin-top: 10px;"></div>
                    </div>
                    
                    <div class="card" style="flex: 1; min-width: 250px; margin-top: 0;">
                        <!--<h2>Re-chunk and Embed</h2>-->
                        <form id="rechunk-form" method="POST" action="/web/document/{doc.id}/rechunk-embed">
                            <button type="submit" id="rechunk-submit-btn" style="width: 100%; padding: 10px 20px; background-color: #2196F3; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; margin-bottom: 15px;">Re-chunk and Embed</button>
                            <div>
                                <label for="rechunk-strategy" style="display: block; margin-bottom: 5px; font-size: 14px; color: #666;">Re-chunk Strategy:</label>
                                <select id="rechunk-strategy" name="strategy" style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                                    {rechunk_strategy_options}
                                </select>
                            </div>
                        </form>
                        <div id="rechunk-status" style="margin-top: 10px;"></div>
                    </div>
                    
                    <div class="card" style="flex: 1; min-width: 300px; margin-top: 0;">
                        <!--<h2>Find Similar Documents</h2>-->
                        <button id="load-similar-btn" style="width: 100%; padding: 10px 20px; background-color: #4CAF50; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; margin-bottom: 15px;">Find Similar Documents</button>
                        <div style="display: flex; gap: 10px; align-items: flex-end;">
                            <div style="flex: 1;">
                                <label for="similar-strategy" style="display: block; margin-bottom: 5px; font-size: 14px; color: #666;">Chunk Strategy:</label>
                                <select id="similar-strategy" style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                                    {chunk_strategy_options_html_for_similar}
                                </select>
                            </div>
                            <div style="min-width: 100px;">
                                <label for="similar-max-results" style="display: block; margin-bottom: 5px; font-size: 14px; color: #666;">Max Results:</label>
                                <input type="number" id="similar-max-results" value="3" min="1" max="20" style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                            </div>
                        </div>
                        <div id="similar-content" style="margin-top: 15px; color: #666;"></div>
                    </div>
                    
                    <div class="card" style="flex: 1; min-width: 200px; margin-top: 0;">
                        <!--<h2>Delete Document</h2>-->
                        <form id="delete-form" method="POST" action="/web/document/{doc.id}/delete" onsubmit="return confirm('Are you sure you want to delete this document? This will also delete all chunks and embeddings. This action cannot be undone.');">
                            <button type="submit" id="delete-submit-btn" style="width: 100%; padding: 10px 20px; background-color: #f44336; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px;">Delete Document</button>
                        </form>
                        <div id="delete-status" style="margin-top: 10px;"></div>
                    </div>
                </div>
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
                    
                    // Setup loading indicator for generate summary form
                    setupFormLoadingIndicator(
                        'generate-summary-form',
                        'generate-summary-submit-btn',
                        'generate-summary-status',
                        'Generating summary...'
                    );
                }}
            }});
            </script>

            {graph_nodes_html}

            <div id="document-logs" class="card" style="margin-top: 20px;">
                <h2>Operation Logs</h2>
                <div id="logs-content" style="color: #666;">Loading logs...</div>
            </div>

            {eval_results_html}
            {eval_questions_html}

            <script>
            // Load and display similar documents
            async function loadSimilarDocuments() {{
                const btn = document.getElementById('load-similar-btn');
                const content = document.getElementById('similar-content');
                const strategySelect = document.getElementById('similar-strategy');
                
                // Show loading state (but don't disable button permanently)
                const originalText = btn.textContent;
                btn.disabled = true;
                btn.textContent = 'Loading...';
                content.innerHTML = '<p style="color: #666;">Searching for similar documents...</p>';
                
                try {{
                    const docId = '{html_escape(doc.id)}';
                    const maxResults = document.getElementById('similar-max-results').value || '3';
                    const chunkingStrategy = strategySelect ? strategySelect.value || 'summary' : 'summary';
                    
                    // Build query parameters
                    const params = new URLSearchParams({{
                        'document_id': docId,
                        'max_results': maxResults
                    }});
                    if (chunkingStrategy) {{
                        params.append('chunking_strategy', chunkingStrategy);
                    }}
                    
                    const response = await fetch('/api/similar?' + params.toString());
                    if (!response.ok) {{
                        throw new Error('Failed to load similar documents');
                    }}
                    const data = await response.json();
                    
                    // Escape HTML to prevent XSS
                    const escapeHtml = (text) => {{
                        const div = document.createElement('div');
                        div.textContent = text;
                        return div.innerHTML;
                    }};
                    
                    if (data.documents && data.documents.length > 0) {{
                        let html = `<p style="color: #666; margin-bottom: 15px;">Found ${{data.total_results}} similar document(s):</p>`;
                        html += '<div style="display: grid; gap: 15px;">';
                        
                        for (const doc of data.documents) {{
                            const docTitle = doc.meta && doc.meta.title ? doc.meta.title : (doc.doc_id || doc.id);
                            const similarity = doc.best_similarity ? (doc.best_similarity * 100).toFixed(1) + '%' : 'N/A';
                            const chunksCount = doc.chunks ? doc.chunks.length : 0;
                            
                            html += `
                                <div style="padding: 15px; border: 1px solid #ddd; border-radius: 4px; background: #f9f9f9;">
                                    <h3 style="margin: 0 0 8px 0;">
                                        <a href="/web/document/${{escapeHtml(doc.id)}}" style="color: #2196F3; text-decoration: none;">${{escapeHtml(docTitle)}}</a>
                                    </h3>
                                    <div style="color: #666; font-size: 14px; margin-bottom: 8px;">
                                        <span>Similarity: <strong>${{similarity}}</strong></span>
                                        <span style="margin-left: 15px;">Matching chunks: <strong>${{chunksCount}}</strong></span>
                                    </div>
                                    <div style="color: #666; font-size: 12px;">
                                        Source: ${{escapeHtml(doc.source_id || 'N/A')}} / ${{escapeHtml(doc.doc_id || 'N/A')}}
                                        ${{doc.doc_type ? ' | Type: ' + escapeHtml(doc.doc_type) : ''}}
                                    </div>
                                </div>
                            `;
                        }}
                        
                        html += '</div>';
                        content.innerHTML = html;
                    }} else {{
                        content.innerHTML = '<p style="color: #666;">No similar documents found.</p>';
                    }}
                    
                    // Re-enable button (don't grey out - user can change strategy and search again)
                    btn.disabled = false;
                    btn.textContent = originalText;
                    btn.style.backgroundColor = '';
                    btn.style.cursor = '';
                }} catch (error) {{
                    console.error('Error loading similar documents:', error);
                    content.innerHTML = '<p style="color: #d32f2f;">Error loading similar documents: ' + error.message + '</p>';
                    btn.disabled = false;
                    btn.textContent = originalText;
                    btn.style.backgroundColor = '';
                    btn.style.cursor = '';
                }}
            }}
            
            // Setup button click handler
            document.addEventListener('DOMContentLoaded', function() {{
                const btn = document.getElementById('load-similar-btn');
                if (btn) {{
                    btn.addEventListener('click', loadSimilarDocuments);
                }}
            }});
            </script>
            
            <script>
            // Graph extraction logs data
            const graphExtractionLogs = {graph_extraction_logs_data};

            // Helper function to format UTC timestamp to local time with timezone info
            function formatLocalTime(utcIsoString) {{
                if (!utcIsoString) return 'N/A';
                try {{
                    const date = new Date(utcIsoString);
                    // Format: "12/31/2024, 3:45:30 PM PST" (includes timezone)
                    return date.toLocaleString(undefined, {{
                        year: 'numeric',
                        month: '2-digit',
                        day: '2-digit',
                        hour: '2-digit',
                        minute: '2-digit',
                        second: '2-digit',
                        timeZoneName: 'short'
                    }});
                }} catch (e) {{
                    return 'N/A';
                }}
            }}

            // Load and display document logs
            async function loadDocumentLogs() {{
                try {{
                    const docId = '{html_escape(doc.id)}';
                    const response = await fetch('/api/logs/' + docId);
                    if (!response.ok) {{
                        throw new Error('Failed to load logs');
                    }}
                    const logs = await response.json();
                    
                    let logsHtml = '';
                    
                    // Parsing logs
                    if (logs.parsing && logs.parsing.length > 0) {{
                        logsHtml += '<h3>Parsing/Extraction Logs</h3><table style="width: 100%; border-collapse: collapse;"><thead><tr><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Time</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Text Extraction</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Image Description</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Total</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Documents</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Hostname</th></tr></thead><tbody>';
                        for (const log of logs.parsing) {{
                            const time = formatLocalTime(log.insertion_time);
                            const textTime = log.text_extraction_time_seconds ? log.text_extraction_time_seconds.toFixed(3) + 's' : 'N/A';
                            const imgTime = log.image_description_time_seconds ? log.image_description_time_seconds.toFixed(3) + 's' : 'N/A';
                            const totalTime = log.total_time_seconds ? log.total_time_seconds.toFixed(3) + 's' : 'N/A';
                            logsHtml += `<tr><td style="padding: 8px; border-bottom: 1px solid #eee;">${{time}}</td><td style="padding: 8px; border-bottom: 1px solid #eee;">${{textTime}}</td><td style="padding: 8px; border-bottom: 1px solid #eee;">${{imgTime}}</td><td style="padding: 8px; border-bottom: 1px solid #eee;">${{totalTime}}</td><td style="padding: 8px; border-bottom: 1px solid #eee;">${{log.num_documents}}</td><td style="padding: 8px; border-bottom: 1px solid #eee;">${{log.hostname || 'N/A'}}</td></tr>`;
                        }}
                        logsHtml += '</tbody></table>';
                    }} else {{
                        logsHtml += '<p style="color: #666;">No parsing logs found.</p>';
                    }}
                    
                    // Summary logs
                    if (logs.summary && logs.summary.length > 0) {{
                        logsHtml += '<h3 style="margin-top: 20px;">Summary Generation Logs</h3><table style="width: 100%; border-collapse: collapse;"><thead><tr><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Time</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Model</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Summary Time</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Hostname</th></tr></thead><tbody>';
                        for (const log of logs.summary) {{
                            const time = formatLocalTime(log.insertion_time);
                            const summaryTime = log.time_summary ? log.time_summary.toFixed(3) + 's' : 'N/A';
                            logsHtml += `<tr><td style="padding: 8px; border-bottom: 1px solid #eee;">${{time}}</td><td style="padding: 8px; border-bottom: 1px solid #eee;">${{log.model || 'N/A'}}</td><td style="padding: 8px; border-bottom: 1px solid #eee;">${{summaryTime}}</td><td style="padding: 8px; border-bottom: 1px solid #eee;">${{log.hostname || 'N/A'}}</td></tr>`;
                        }}
                        logsHtml += '</tbody></table>';
                    }} else {{
                        logsHtml += '<p style="color: #666; margin-top: 20px;">No summary generation logs found.</p>';
                    }}

                    // Chunking logs
                    if (logs.chunking && logs.chunking.length > 0) {{
                        logsHtml += '<h3 style="margin-top: 20px;">Chunking/Embedding Logs</h3><table style="width: 100%; border-collapse: collapse;"><thead><tr><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Time</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Chunking</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Embedding</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Total</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Chunks</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Strategy</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Hostname</th></tr></thead><tbody>';
                        for (const log of logs.chunking) {{
                            const time = formatLocalTime(log.insertion_time);
                            const chunkTime = log.chunking_time_seconds ? log.chunking_time_seconds.toFixed(3) + 's' : 'N/A';
                            const embedTime = log.embedding_time_seconds ? log.embedding_time_seconds.toFixed(3) + 's' : 'N/A';
                            const totalTime = log.total_time_seconds ? log.total_time_seconds.toFixed(3) + 's' : 'N/A';
                            logsHtml += `<tr><td style="padding: 8px; border-bottom: 1px solid #eee;">${{time}}</td><td style="padding: 8px; border-bottom: 1px solid #eee;">${{chunkTime}}</td><td style="padding: 8px; border-bottom: 1px solid #eee;">${{embedTime}}</td><td style="padding: 8px; border-bottom: 1px solid #eee;">${{totalTime}}</td><td style="padding: 8px; border-bottom: 1px solid #eee;">${{log.num_chunks}}</td><td style="padding: 8px; border-bottom: 1px solid #eee;">${{log.chunk_strategy || 'N/A'}}</td><td style="padding: 8px; border-bottom: 1px solid #eee;">${{log.hostname || 'N/A'}}</td></tr>`;
                        }}
                        logsHtml += '</tbody></table>';
                    }} else {{
                        logsHtml += '<p style="color: #666; margin-top: 20px;">No chunking logs found.</p>';
                    }}

                    // Graph extraction logs (from server-side data)
                    if (graphExtractionLogs && graphExtractionLogs.length > 0) {{
                        logsHtml += '<h3 style="margin-top: 20px;">Graph Extraction Logs</h3><table style="width: 100%; border-collapse: collapse; font-size: 13px;"><thead><tr><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Time</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Model</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Hostname</th><th style="text-align: center; padding: 8px; border-bottom: 2px solid #ddd;">Extracted</th><th style="text-align: center; padding: 8px; border-bottom: 2px solid #ddd;">Created</th><th style="text-align: center; padding: 8px; border-bottom: 2px solid #ddd;">Updated</th><th style="text-align: center; padding: 8px; border-bottom: 2px solid #ddd;">Errors</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Extract Time</th><th style="text-align: left; padding: 8px; border-bottom: 2px solid #ddd;">Process Time</th></tr></thead><tbody>';
                        for (const log of graphExtractionLogs) {{
                            const time = formatLocalTime(log.created_time);
                            const extractTime = log.time_extraction ? log.time_extraction.toFixed(2) + 's' : 'N/A';
                            const processTime = log.time_processing ? log.time_processing.toFixed(2) + 's' : 'N/A';
                            const errorColor = log.relations_errors === 0 ? '#4CAF50' : '#FF9800';
                            logsHtml += `<tr><td style="padding: 8px; border-bottom: 1px solid #eee;">${{time}}</td><td style="padding: 8px; border-bottom: 1px solid #eee; font-size: 12px;">${{log.extraction_model}}</td><td style="padding: 8px; border-bottom: 1px solid #eee; font-size: 12px;">${{log.hostname}}</td><td style="padding: 8px; border-bottom: 1px solid #eee; text-align: center;">${{log.relations_extracted}}</td><td style="padding: 8px; border-bottom: 1px solid #eee; text-align: center;">${{log.relations_created}}</td><td style="padding: 8px; border-bottom: 1px solid #eee; text-align: center;">${{log.relations_updated}}</td><td style="padding: 8px; border-bottom: 1px solid #eee; text-align: center; color: ${{errorColor}}; font-weight: 500;">${{log.relations_errors}}</td><td style="padding: 8px; border-bottom: 1px solid #eee; font-size: 12px;">${{extractTime}}</td><td style="padding: 8px; border-bottom: 1px solid #eee; font-size: 12px;">${{processTime}}</td></tr>`;
                        }}
                        logsHtml += '</tbody></table>';
                    }} else {{
                        logsHtml += '<p style="color: #666; margin-top: 20px;">No graph extraction logs found.</p>';
                    }}

                    if (!logs.parsing || logs.parsing.length === 0) {{
                        if (!logs.chunking || logs.chunking.length === 0) {{
                            if (!logs.summary || logs.summary.length === 0) {{
                                if (!graphExtractionLogs || graphExtractionLogs.length === 0) {{
                                    logsHtml = '<p style="color: #666;">No logs found for this document.</p>';
                                }}
                            }}
                        }}
                    }}
                    
                    document.getElementById('logs-content').innerHTML = logsHtml;
                }} catch (error) {{
                    console.error('Error loading logs:', error);
                    document.getElementById('logs-content').innerHTML = '<p style="color: #d32f2f;">Error loading logs: ' + error.message + '</p>';
                }}
            }}
            
            // Load logs when page is ready
            if (document.readyState === 'loading') {{
                document.addEventListener('DOMContentLoaded', function() {{
                    loadDocumentLogs();
                    // Convert server-rendered UTC timestamps to local time
                    document.querySelectorAll('.utc-timestamp').forEach(function(element) {{
                        const isoString = element.getAttribute('data-iso');
                        if (isoString) {{
                            element.textContent = formatLocalTime(isoString);
                        }}
                    }});
                }});
            }} else {{
                loadDocumentLogs();
                // Convert server-rendered UTC timestamps to local time
                document.querySelectorAll('.utc-timestamp').forEach(function(element) {{
                    const isoString = element.getAttribute('data-iso');
                    if (isoString) {{
                        element.textContent = formatLocalTime(isoString);
                    }}
                }});
            }}
            </script>
            """
            timings['build_content'] = time.time() - t0
            timings['build_content_since_start'] = time.time() - t_start

            # Build page title: use title, title_gen, or doc_id
            page_title = doc.doc_id or doc.id
            if doc.title:
                page_title = f"{doc.title} ({doc.doc_id or doc.id})"
            elif doc.title_gen:
                page_title = f"{doc.title_gen} ({doc.doc_id or doc.id})"
            
            # Log timing information
            timings['total'] = time.time() - t_start
            timing_str = ", ".join(f"{k}: {v:.3f}s" for k, v in sorted(timings.items()))
            logger.debug(f"document_detail timings for {doc_id}: {timing_str}")
            
            return HTMLResponse(html_templates.base_template(
                f"Document: {page_title}",
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
        from ....kb import get
        
        try:
            doc = get(uid=doc_id)
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
                chunks = doc.chunk_and_embed(chunk_strategy=strategy)
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
        from ....kb import get, delete_document
        
        try:
            doc = get(uid=doc_id)
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

    @app.route("/web/document/{doc_id}/generate-summary", methods=["POST"])
    async def generate_summary_document(request: Request):
        """Generate summary for a document (POST)."""
        # Check authentication first
        session_data, redirect = await require_auth_html(request, session_manager)
        if redirect:
            return redirect
        
        username = session_data.get("username")
        doc_id = request.path_params["doc_id"]
        
        # Get form data
        form_data = await request.form()
        model = form_data.get("model", "").strip() or None
        
        # Get document from knowledge base
        from ....kb import get
        
        try:
            doc = get(uid=doc_id)
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
                        f'<div class="error-box"><h2>Error</h2><p>Document has no text content to summarize.</p><p><a href="/web/document/{doc_id}">← Back to Document</a></p></div>',
                        None,
                        username
                    ),
                    status_code=400
                )
            
            # Generate summary
            try:
                doc.generate_summary(
                    include_title=True,
                    include_gist=True,
                    include_summary=True,
                    model=model,
                )
                
                # Redirect back to document page with success message
                from urllib.parse import urlencode
                success_message = 'Summary generated successfully!'
                redirect_url = f"/web/document/{doc_id}?{urlencode({'message': success_message})}"
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
                logger.error(f"Error generating summary for document {doc_id}: {e}", exc_info=True)
                error_msg = html_escape(str(e))
                return HTMLResponse(
                    html_templates.base_template(
                        "Error",
                        f'<div class="error-box"><h2>Error</h2><p>Failed to generate summary: {error_msg}</p><p><a href="/web/document/{doc_id}">← Back to Document</a></p></div>',
                        None,
                        username
                    ),
                    status_code=500
                )
                
        except Exception as e:
            logger.error(f"Error processing summary generation request for {doc_id}: {e}", exc_info=True)
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
            from ....kb.embedding import get_chunk_strategies
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
            <p>Upload a file to add it to the knowledge base with optional image extraction, summary generation, and embedding.</p>
            
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
                        <label for="title"><strong>Title (optional):</strong></label>
                        <input type="text" id="title" name="title" placeholder="e.g., Project Proposal 2024" style="margin-top: 5px; display: block; width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                        <small style="color: #666;">Document title (will be stored in metadata)</small>
                    </div>

                    <div style="margin-bottom: 15px;">
                        <label for="meta"><strong>Additional Metadata (optional):</strong></label>
                        <textarea id="meta" name="meta" rows="6" placeholder='{{"author": "John Doe", "tags": ["important", "draft"], "category": "research"}}' style="margin-top: 5px; display: block; width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-family: monospace; font-size: 14px;"></textarea>
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
                            <input type="checkbox" id="parse_images" name="parse_images" checked style="margin-right: 8px; width: auto;">
                            <strong>Extract images from document</strong>
                        </label>
                        <small style="color: #666; display: block; margin-left: 24px; margin-top: 5px;">Extract images as separate documents with LLM-generated descriptions</small>
                    </div>

                    <div style="margin-bottom: 15px; padding: 15px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px;">
                        <label style="display: flex; align-items: center; cursor: pointer;">
                            <input type="checkbox" id="generate_summary" name="generate_summary" checked style="margin-right: 8px; width: auto;">
                            <strong>Generate summary (title, gist, summary)</strong>
                        </label>
                        <small style="color: #666; display: block; margin-left: 24px; margin-top: 5px;">Automatically generate title, gist, and summary for text documents</small>
                    </div>

                    <div style="margin-bottom: 15px; padding: 15px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px;">
                        <label style="display: flex; align-items: center; cursor: pointer;">
                            <input type="checkbox" id="chunk_and_embed" name="chunk_and_embed" checked style="margin-right: 8px; width: auto;">
                            <strong>Chunk and embed document after upload</strong>
                        </label>
                        <small style="color: #666; display: block; margin-left: 24px; margin-top: 5px;">Automatically chunk the document and generate embeddings. If "Generate summary" is also enabled, summary chunks will be created. If "Extract images" is also enabled, image chunks will be created.</small>

                        <div style="margin-top: 15px; margin-left: 24px; padding: 15px; background: #f0f0f0; border: 1px solid #ddd; border-radius: 4px;">
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
            title = form.get("title")
            meta_text = form.get("meta")
            creating_time_str = form.get("creating_time")
            update_time_str = form.get("update_time")

            # Parse processing options
            parse_images = form.get("parse_images") == "on"
            generate_summary = form.get("generate_summary") == "on"
            chunk_and_embed = form.get("chunk_and_embed") == "on"
            chunk_strategy = form.get("chunk_strategy")  # Can be None if checkbox unchecked

            # Automatically determine when to create summary and image chunks
            # Summary chunks: created if both generate_summary AND chunk_and_embed are enabled
            create_summary_chunks = generate_summary and chunk_and_embed
            # Image chunks: created if both parse_images AND chunk_and_embed are enabled
            create_image_chunks = parse_images and chunk_and_embed

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
            from ....config import get_server_config
            server_config = get_server_config()
            MAX_UPLOAD_SIZE = server_config['max_upload_size']
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
            from ....kb import add_source, ingest, Document
            
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

                # Add title to metadata if provided
                if title or meta:
                    if meta is None:
                        meta = {}
                    if title:
                        meta["title"] = title
                    data_dict["meta"] = meta

                if creating_time:
                    data_dict["creating_time"] = creating_time
                if update_time:
                    data_dict["update_time"] = update_time
                
                # Determine if we should parse images with LLM descriptions
                # If parse_images is enabled, also enable LLM descriptions by default
                parse_image_llm_description = parse_images

                # Extract meta and uri from data_dict
                doc_id_value = data_dict.get("doc_id")
                if not doc_id_value:
                    # Use filename stem as doc_id if not provided
                    doc_id_value = file_path.stem

                # Use the new ingest() function which handles the full workflow
                result = ingest(
                    str(file_path),
                    source_id=source_id,
                    doc_id=doc_id_value,
                    uri=uri,
                    meta=data_dict.get("meta"),
                    extract_images=parse_images,
                    describe_images=parse_image_llm_description,
                    copy_to_kb=False,  # Files already in upload directory
                    generate_summary=generate_summary,
                    chunk_and_embed=chunk_and_embed,
                    create_summary_chunks=create_summary_chunks,
                    chunk_strategy=chunk_strategy if chunk_strategy else None,
                )

                # Get result information
                doc_count = result["num_documents"]
                doc_ids = result["document_ids"]

                # Query documents for building success message
                from ....kb import get_db_session, get
                with get_db_session() as session:
                    docs = get(uid=doc_ids, session=session)
                    if not isinstance(docs, list):
                        docs = [docs] if docs else []

                    # Build success message with links to documents
                    doc_links = []
                    for doc in docs:
                        chunk_info = ""
                        if chunk_and_embed and result.get("num_chunks", 0) > 0:
                            # Note: We don't have per-document chunk counts from ingest(),
                            # but we can indicate that chunking happened
                            chunk_info = " (chunked)"
                        doc_links.append(f'<a href="/web/document/{doc.id}">Document {doc.id}</a> ({doc.doc_type}){chunk_info}')

                    # Security: Escape filename in success message to prevent XSS
                    safe_filename = html_escape(filename)
                    chunk_status = f" and chunked/embedded ({result['num_chunks']} chunks)" if chunk_and_embed and result.get("num_chunks", 0) > 0 else ""
                    summary_status = f" with summaries" if generate_summary and result.get("num_summaries", 0) > 0 else ""

                    if doc_count == 1 and docs:
                        doc = docs[0]
                        success_message = f'Upload successful! File "{safe_filename}" added as <a href="/web/document/{doc.id}">Document {doc.id}</a> ({doc.doc_type}){summary_status}{chunk_status}.'
                    else:
                        doc_list = ", ".join(doc_links)
                        success_message = f'Upload successful! File "{safe_filename}" created {doc_count} documents: {doc_list}{summary_status}{chunk_status}.'

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

    @app.route("/web/raw/{raw_doc_id}")
    async def raw_document_detail(request: Request):
        """Show details for a raw document, its parsed versions, and any parser comparison."""
        session_data, redirect = await require_auth_html(request, session_manager)
        if redirect:
            return redirect
        username = session_data.get("username")
        raw_doc_id = request.path_params["raw_doc_id"]

        try:
            from ....kb.database import get_db_session
            from ....kb.db_models import RawDocument, Document as DocumentModel
            from ....kb.compare import get_comparison, get_latest_categories

            with get_db_session() as session:
                raw_doc = session.query(RawDocument).filter(RawDocument.id == raw_doc_id).first()
                if not raw_doc:
                    return HTMLResponse(
                        html_templates.base_template(
                            "Not Found",
                            f'<div class="error-box"><h2>Not Found</h2><p>Raw document <code>{html_escape(raw_doc_id)}</code> not found.</p><p><a href="/web">← Back</a></p></div>',
                            None,
                            username,
                        ),
                        status_code=404,
                    )

                # Detach fields before session closes
                rdoc_id = raw_doc.id
                rdoc_source_id = raw_doc.source_id
                rdoc_doc_id = raw_doc.doc_id
                rdoc_file_path = raw_doc.file_path
                rdoc_hostname = raw_doc.hostname
                rdoc_uri = raw_doc.uri
                rdoc_source_type = raw_doc.source_type
                rdoc_file_size = raw_doc.file_size
                rdoc_content_hash = raw_doc.content_hash
                rdoc_created_time = raw_doc.created_time

                siblings = session.query(DocumentModel).filter(
                    DocumentModel.raw_document_id == raw_doc_id,
                    DocumentModel.doc_type == "text",
                ).order_by(DocumentModel.parser_id).all()

                sibling_data = [
                    {
                        "id": s.id,
                        "doc_id": s.doc_id or s.id,
                        "parser_id": s.parser_id or "unknown",
                    }
                    for s in siblings
                ]

                # Privacy filter — fetch most recent result if present
                from ....kb.db_models import PrivacyFilter as PrivacyFilterModel
                pf_row = (
                    session.query(PrivacyFilterModel)
                    .filter(PrivacyFilterModel.raw_document_id == raw_doc_id)
                    .order_by(PrivacyFilterModel.created_time.desc())
                    .first()
                )
                privacy_data = (
                    {
                        "label": pf_row.label,
                        "reasoning": pf_row.reasoning,
                        "model": pf_row.model,
                        "created_time": str(pf_row.created_time)[:19] if pf_row.created_time else "",
                    }
                    if pf_row else None
                )

            # Fetch comparison and source-level categories (both return plain dicts)
            comparison = get_comparison(raw_doc_id)
            categories = get_latest_categories(rdoc_source_id) if rdoc_source_id else None

            # --- Build HTML ---

            file_path_display = html_escape(
                f"{rdoc_hostname}:{rdoc_file_path}" if rdoc_hostname and rdoc_file_path
                else rdoc_file_path or "N/A"
            )

            siblings_rows = ""
            for s in sibling_data:
                siblings_rows += (
                    f'<li><a href="/web/document/{s["id"]}">'
                    f'{html_escape(s["doc_id"])}</a> ({html_escape(s["parser_id"])})</li>'
                )

            info_card = f"""
<div class="card">
    <h2>Raw Document</h2>
    <table>
        <tr><th>Raw Document ID</th><td><code>{html_escape(rdoc_id)}</code></td></tr>
        <tr><th>Source</th><td>{html_escape(rdoc_source_id or "")}</td></tr>
        <tr><th>Doc ID</th><td>{html_escape(rdoc_doc_id or "")}</td></tr>
        <tr><th>Host File Path</th><td>{file_path_display}</td></tr>
        {'<tr><th>URI</th><td>' + html_escape(rdoc_uri) + '</td></tr>' if rdoc_uri else ''}
        <tr><th>Source Type</th><td>{html_escape(rdoc_source_type or "")}</td></tr>
        <tr><th>File Size</th><td>{f"{rdoc_file_size:,} bytes" if rdoc_file_size else "N/A"}</td></tr>
        {'<tr><th>Content Hash</th><td><code>' + html_escape(rdoc_content_hash) + '</code></td></tr>' if rdoc_content_hash else ''}
        {'<tr><th>Created</th><td>' + html_escape(str(rdoc_created_time)[:19]) + '</td></tr>' if rdoc_created_time else ''}
    </table>
    <h3 style="margin-top: 20px; margin-bottom: 10px;">All Documents from this Raw File ({len(sibling_data)})</h3>
    <ul>{siblings_rows}</ul>
</div>"""

            # --- Privacy card ---
            _LABEL_COLORS = {
                "public": ("#d4edda", "#155724", "Public"),
                "needs_review": ("#fff3cd", "#856404", "Needs Review"),
                "private": ("#f8d7da", "#721c24", "Private"),
            }
            if privacy_data:
                bg, fg, label_text = _LABEL_COLORS.get(
                    privacy_data["label"], ("#f0f0f0", "#333", privacy_data["label"])
                )
                reasoning_html = (
                    f'<p style="margin: 10px 0 0 0; color: #444; line-height: 1.5;">'
                    f'{html_escape(privacy_data["reasoning"])}</p>'
                    if privacy_data.get("reasoning") else ""
                )
                privacy_card = f"""
<div class="card">
    <h2>Privacy Classification</h2>
    <div style="display: inline-block; padding: 6px 14px; border-radius: 4px; background: {bg}; color: {fg}; font-weight: bold; font-size: 1.05em;">{html_escape(label_text)}</div>
    {reasoning_html}
    <p style="margin: 10px 0 0 0; font-size: 0.85em; color: #999;">
        Model: {html_escape(privacy_data.get("model") or "N/A")} &nbsp;·&nbsp; Run at: {html_escape(privacy_data.get("created_time") or "N/A")}
    </p>
</div>"""
            else:
                privacy_card = f"""
<div class="card">
    <h2>Privacy Classification</h2>
    <p style="color: #999;">Not yet classified. Run <code>kb tools filter-all --source-id {html_escape(rdoc_source_id or "")}</code> to classify.</p>
</div>"""

            if comparison:
                comp_parsers = html_escape(", ".join(comparison["parser_ids"] or []))
                comp_model = html_escape(comparison["model"] or "")
                comp_created = html_escape(str(comparison["created_time"])[:19]) if comparison["created_time"] else ""
                comp_text = html_escape(comparison["comparison"] or "")

                doc_desc_html = ""
                if comparison.get("document_description"):
                    doc_desc_html = f"""
    <h3 style="margin-top: 24px; margin-bottom: 10px;">Document Description</h3>
    <p style="color: #444; line-height: 1.6;">{html_escape(comparison["document_description"])}</p>"""

                comp_elapsed = comparison.get("meta", {}) or {}
                comp_elapsed = comp_elapsed.get("elapsed_seconds")
                comp_elapsed_html = f"<tr><th>LLM time</th><td>{comp_elapsed}s</td></tr>" if comp_elapsed else ""

                comparison_card = f"""
<div class="card">
    <h2>Parser Comparison</h2>
    <table>
        <tr><th>Parsers compared</th><td>{comp_parsers}</td></tr>
        <tr><th>Model</th><td>{comp_model}</td></tr>
        <tr><th>Run at</th><td>{comp_created}</td></tr>
        {comp_elapsed_html}
    </table>
    {doc_desc_html}
    <h3 style="margin-top: 24px; margin-bottom: 10px;">Analysis</h3>
    <pre style="white-space: pre-wrap; background: #f8f8f8; padding: 16px; border-radius: 4px; border: 1px solid #ddd; font-size: 0.9em;">{comp_text}</pre>
</div>"""
            else:
                comparison_card = """
<div class="card">
    <h2>Parser Comparison</h2>
    <p style="color: #999;">No comparison available yet. Run <code>kb compare run --raw-document-id """ + html_escape(rdoc_id) + """</code> to generate one.</p>
</div>"""

            # Source-level parser recommendations card
            if categories:
                cat_text = html_escape(categories["categories_text"] or "")
                cat_model = html_escape(categories["model"] or "")
                cat_created = html_escape(str(categories["created_time"])[:19]) if categories["created_time"] else ""
                cat_n = categories["num_comparisons"] or "?"
                cat_extra = f'<p style="color:#666; font-style:italic;">Focus: {html_escape(categories["prompt_extra"])}</p>' if categories.get("prompt_extra") else ""
                categories_card = f"""
<div class="card">
    <h2>Parser Recommendations <span style="font-size:0.75em; color:#999;">(source-level, {cat_n} docs)</span></h2>
    <table style="margin-bottom:12px;">
        <tr><th>Model</th><td>{cat_model}</td></tr>
        <tr><th>Run at</th><td>{cat_created}</td></tr>
    </table>
    {cat_extra}
    <pre style="white-space: pre-wrap; background: #f8f8f8; padding: 16px; border-radius: 4px; border: 1px solid #ddd; font-size: 0.9em;">{cat_text}</pre>
</div>"""
            else:
                categories_card = """
<div class="card">
    <h2>Parser Recommendations</h2>
    <p style="color: #999;">No source-level categorization yet. Run <code>kb compare categorize --source-id """ + html_escape(rdoc_source_id or "") + """</code>.</p>
</div>"""

            content = f"""
<p><a href="/web">← Back to Document List</a></p>
{info_card}
{privacy_card}
{comparison_card}
{categories_card}
"""
            return HTMLResponse(
                html_templates.base_template(
                    f"Raw: {rdoc_doc_id or rdoc_id}",
                    content,
                    None,
                    username,
                )
            )

        except Exception as e:
            logger.error(f"Error fetching raw document {raw_doc_id}: {e}", exc_info=True)
            error_msg = html_escape(str(e))
            return HTMLResponse(
                html_templates.base_template(
                    "Error",
                    f'<div class="error-box"><h2>Error</h2><p>{error_msg}</p><p><a href="/web">← Back</a></p></div>',
                    None,
                    username,
                ),
                status_code=500,
            )

    @app.route("/web/compare")
    async def compare_page(request: Request):
        """List all parser categorization runs, grouped by source, expandable."""
        session_data, redirect = await require_auth_html(request, session_manager)
        if redirect:
            return redirect
        username = session_data.get("username")

        try:
            from ....kb.compare import list_categories
            from ....kb.database import get_db_session
            from ....kb.db_models import ParserCategories

            rows = list_categories()

            if not rows:
                content = """
<div class="card">
    <h2>Parser Categorization Runs</h2>
    <p style="color: #999;">No categorization runs found. Run <code>kb compare categorize --source-id &lt;source&gt;</code> to generate one.</p>
</div>"""
                return HTMLResponse(html_templates.base_template("Parser Categories", content, None, username))

            # Group by source_id, preserving order (most recent first within each source)
            from collections import OrderedDict
            by_source = OrderedDict()
            for r in rows:
                src = r["source_id"]
                if src not in by_source:
                    by_source[src] = []
                by_source[src].append(r)

            # Fetch full categories_text for all rows in one query
            with get_db_session() as db:
                all_rows = db.query(ParserCategories).order_by(ParserCategories.created_time.desc()).all()
                text_by_id = {r.id: (r.categories_text or "") for r in all_rows}

            source_cards = ""
            for src, runs in by_source.items():
                run_items = ""
                for i, r in enumerate(runs):
                    created = str(r["created_time"] or "")[:19]
                    model = html_escape(r["model"] or "")
                    n = r["num_comparisons"] or "?"
                    elapsed = (r.get("meta") or {}).get("elapsed_seconds")
                    elapsed_str = f" · {elapsed}s" if elapsed else ""
                    extra = f' · <em>{html_escape(r["prompt_extra"][:60])}</em>' if r.get("prompt_extra") else ""
                    cat_text = html_escape(text_by_id.get(r["id"], "(no text)"))
                    title_str = html_escape(r["title"]) if r.get("title") else f"{created} · {model}"
                    open_attr = " open" if i == 0 else ""

                    # Prompt section (collapsible)
                    full_prompt = html_escape(text_by_id.get(r["id"] + "_prompt", ""))
                    prompt_html = ""
                    with get_db_session() as db2:
                        prow = db2.query(ParserCategories).filter(ParserCategories.id == r["id"]).first()
                        if prow and prow.prompt:
                            prompt_html = f"""
    <details style="margin-top: 12px;">
        <summary style="cursor: pointer; color: #666; font-size: 0.85em;">Show prompt</summary>
        <pre style="white-space: pre-wrap; background: #f0f0f0; padding: 12px; border-radius: 4px; font-size: 0.8em; margin-top: 8px;">{html_escape(prow.prompt)}</pre>
    </details>"""

                    run_items += f"""
<details{open_attr} style="margin-bottom: 12px; border: 1px solid #ddd; border-radius: 6px; padding: 0;">
    <summary style="cursor: pointer; padding: 12px 16px; background: #f8f8f8; border-radius: 6px; list-style: none; display: flex; justify-content: space-between; align-items: center;">
        <span>
            <strong>{title_str}</strong>
            <span style="color: #999; font-size: 0.85em; margin-left: 12px;">{created} · {model} · {n} docs{elapsed_str}{extra}</span>
        </span>
        <span style="color: #bbb; font-size: 0.8em;">{r["id"][:8]}…</span>
    </summary>
    <div style="padding: 16px;">
        <pre style="white-space: pre-wrap; background: #fdfdfd; padding: 16px; border-radius: 4px; border: 1px solid #eee; font-size: 0.88em; margin: 0;">{cat_text}</pre>
        {prompt_html}
    </div>
</details>"""

                source_cards += f"""
<div class="card">
    <h2>{html_escape(src)} <span style="font-size: 0.7em; color: #999; font-weight: normal;">({len(runs)} run{"s" if len(runs) != 1 else ""})</span></h2>
    {run_items}
</div>"""

            content = f"""
<p><a href="/web">← Back to Document List</a></p>
<h1 style="margin-bottom: 24px;">Parser Categorization Runs</h1>
{source_cards}
"""
            return HTMLResponse(html_templates.base_template("Parser Categories", content, None, username))

        except Exception as e:
            logger.error(f"Error in compare_page: {e}", exc_info=True)
            error_msg = html_escape(str(e))
            return HTMLResponse(
                html_templates.base_template(
                    "Error",
                    f'<div class="error-box"><h2>Error</h2><p>{error_msg}</p><p><a href="/web">← Back</a></p></div>',
                    None,
                    username,
                ),
                status_code=500,
            )

    # Logs, statistics, and eval routes moved to separate files
