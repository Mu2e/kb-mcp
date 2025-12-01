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

    @app.route("/web/upload", methods=["GET"])
    async def upload_page(request: Request):
        """File upload page (requires admin privileges)."""
        # Check authentication and admin privileges
        session_data, redirect = await require_auth_html(request, session_manager, require_admin=True)
        if redirect:
            return redirect

        username = session_data.get("username", "User")

        content = """
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
                        <textarea id="meta" name="meta" rows="6" placeholder='{"author": "John Doe", "tags": ["important", "draft"]}' style="margin-top: 5px; display: block; width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-family: monospace; font-size: 14px;"></textarea>
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
                    
                    <div style="margin-bottom: 15px;">
                        <button type="submit" style="padding: 10px 20px; background-color: #4CAF50; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 16px;">Upload</button>
                    </div>
                </form>
            </div>
            
            <div id="upload-status" style="margin-top: 20px;"></div>
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
                
                # Build success message with links to documents
                doc_ids = [doc.id for doc in docs]
                doc_links = []
                for doc in docs:
                    doc_links.append(f'<a href="/web/document/{doc.id}">Document {doc.id}</a> ({doc.doc_type})')
                
                # Security: Escape filename in success message to prevent XSS
                safe_filename = html_escape(filename)
                if doc_count == 1:
                    doc = docs[0]
                    success_message = f'Upload successful! File "{safe_filename}" added as <a href="/web/document/{doc.id}">Document {doc.id}</a> ({doc.doc_type}).'
                else:
                    doc_list = ", ".join(doc_links)
                    success_message = f'Upload successful! File "{safe_filename}" created {doc_count} documents: {doc_list}.'
                
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
