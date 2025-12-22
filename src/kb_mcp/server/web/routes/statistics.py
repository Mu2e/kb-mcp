"""Statistics web page route."""

import logging
from html import escape as html_escape
from starlette.responses import HTMLResponse
from starlette.requests import Request

from ..auth import WebSessionManager
from .. import html_templates

logger = logging.getLogger(__name__)


def setup_statistics_routes(app, session_manager: WebSessionManager, require_auth_html):
    """Register statistics web routes.

    Args:
        app: Starlette application instance
        session_manager: WebSessionManager instance for authentication
        require_auth_html: Authentication helper function
    """

    @app.route("/web/statistics")
    async def web_statistics(request: Request):
        """Statistics page showing chunking strategies vs embedding names."""
        # Check authentication first
        session_data, redirect = await require_auth_html(request, session_manager)
        if redirect:
            return redirect

        # Get username from authenticated session
        username = session_data.get("username")

        # Get auth warning banner
        auth_warning = session_manager.get_auth_warning_html()

        # Get filter values from query params
        source_id = request.query_params.get("source_id", "")
        doc_type = request.query_params.get("doc_type", "")

        # Get filter options for dropdowns
        from ....kb import get_options
        options = get_options()

        # Build filter dropdowns
        source_options = '<option value="">All Sources</option>'
        for source in options.get("source_options", []):
            selected = 'selected' if source["id"] == source_id else ''
            source_options += f'<option value="{html_escape(source["id"])}" {selected}>{html_escape(source["id"])} ({source["count"]})</option>'

        doc_type_options = '<option value="">All Types</option>'
        for doc_type_option in options.get("doc_type_options", []):
            selected = 'selected' if doc_type_option["doc_type"] == doc_type else ''
            doc_type_options += f'<option value="{html_escape(doc_type_option["doc_type"])}" {selected}>{html_escape(doc_type_option["doc_type"])} ({doc_type_option["count"]})</option>'

        # Build filter HTML
        filter_html = f"""
        <div class="card" style="margin-bottom: 20px;">
            <h2>Filters</h2>
            <form id="filter-form" method="get" action="/web/statistics" style="display: flex; gap: 15px; align-items: end; flex-wrap: wrap;">
                <div style="flex: 1; min-width: 200px;">
                    <label for="source-filter" style="display: block; margin-bottom: 5px; font-weight: bold;">Source:</label>
                    <select id="source-filter" name="source_id" style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                        {source_options}
                    </select>
                </div>
                <div style="flex: 1; min-width: 200px;">
                    <label for="type-filter" style="display: block; margin-bottom: 5px; font-weight: bold;">Document Type:</label>
                    <select id="type-filter" name="doc_type" style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                        {doc_type_options}
                    </select>
                </div>
                <div>
                    <button type="submit" class="btn" style="padding: 8px 20px;">Apply Filters</button>
                </div>
            </form>
        </div>
        """

        # Statistics grid will be loaded via JavaScript
        content = f"""
        <h1>Statistics</h1>
        <p><a href="/web">← Back to Document List</a></p>

        {auth_warning}

        {filter_html}

            <div class="card">
                <h2>Statistics</h2>
                <div id="statistics-container" style="margin-top: 20px;">
                    <div class="info-box">Loading statistics...</div>
                </div>
            </div>

        <script>
        // Load statistics on page load
        document.addEventListener('DOMContentLoaded', function() {{
            loadStatistics();
        }});

        // Reload when filters change
        document.getElementById('filter-form').addEventListener('submit', function(e) {{
            e.preventDefault();
            const formData = new FormData(this);
            const params = new URLSearchParams(formData);
            window.location.href = '/web/statistics?' + params.toString();
        }});

        async function loadStatistics() {{
            const container = document.getElementById('statistics-container');
            const sourceFilter = document.getElementById('source-filter');
            const typeFilter = document.getElementById('type-filter');

            const params = new URLSearchParams();
            if (sourceFilter.value) params.set('source_id', sourceFilter.value);
            if (typeFilter.value) params.set('doc_type', typeFilter.value);

            try {{
                const response = await fetch('/api/statistics?' + params.toString());
                if (!response.ok) {{
                    throw new Error(`Failed to load statistics: ${{response.status}}`);
                }}

                const data = await response.json();
                renderStatistics(data, container);
            }} catch (error) {{
                console.error('Error loading statistics:', error);
                container.innerHTML = `<div class="error-box">Error loading statistics: ${{error.message}}</div>`;
            }}
        }}

        function renderStatistics(data, container) {{
            if (!data.strategies || data.strategies.length === 0) {{
                container.innerHTML = '<div class="info-box">No statistics available. No chunks or embeddings found.</div>';
                return;
            }}

            let html = '<div style="overflow-x: auto;">';
            html += '<table style="width: 100%; border-collapse: collapse; margin-top: 10px;">';

            // Header row
            html += '<thead><tr>';
            html += '<th style="padding: 10px; border: 1px solid #ddd; background: #f5f5f5; text-align: left; position: sticky; left: 0; background: #f5f5f5; z-index: 10;">Strategy</th>';
            for (const embedding of data.embeddings) {{
                html += `<th style="padding: 10px; border: 1px solid #ddd; background: #f5f5f5; text-align: center;">${{escapeHtml(embedding)}}</th>`;
            }}
            html += '</tr></thead>';

            // Data rows
            html += '<tbody>';
            for (const strategy of data.strategies) {{
                html += '<tr>';
                html += `<td style="padding: 10px; border: 1px solid #ddd; background: #f9f9f9; font-weight: bold; position: sticky; left: 0; background: #f9f9f9; z-index: 5;">${{escapeHtml(strategy)}}</td>`;

                for (const embedding of data.embeddings) {{
                    const cell = data.data[strategy] && data.data[strategy][embedding] ? data.data[strategy][embedding] : {{documents: 0, chunks: 0, embeddings: 0}};
                    const bgColor = cell.documents > 0 ? '#e8f5e9' : '#fff';
                    html += `<td style="padding: 10px; border: 1px solid #ddd; background: ${{bgColor}}; text-align: center;">`;
                    html += `<div style="font-size: 14px;"><strong>Docs:</strong> ${{cell.documents}}</div>`;
                    html += `<div style="font-size: 12px; color: #666;"><strong>Chunks:</strong> ${{cell.chunks}}</div>`;
                    html += `<div style="font-size: 12px; color: #666;"><strong>Embeddings:</strong> ${{cell.embeddings}}</div>`;
                    html += '</td>';
                }}
                html += '</tr>';
            }}
            html += '</tbody>';

            // Totals row
            if (data.totals) {{
                html += '<tfoot><tr style="background: #f0f0f0; font-weight: bold;">';
                html += '<td style="padding: 10px; border: 1px solid #ddd; position: sticky; left: 0; background: #f0f0f0; z-index: 5;">Totals</td>';
                html += `<td colspan="${{data.embeddings.length}}" style="padding: 10px; border: 1px solid #ddd; text-align: center;">`;
                html += `<div style="font-size: 14px;"><strong>Documents:</strong> ${{data.totals.documents}}</div>`;
                html += `<div style="font-size: 12px; color: #666;"><strong>Chunks:</strong> ${{data.totals.chunks}}</div>`;
                html += `<div style="font-size: 12px; color: #666;"><strong>Embeddings:</strong> ${{data.totals.embeddings}}</div>`;
                html += '</td>';
                html += '</tr></tfoot>';
            }}

            // Add summary section
            html += '</table>';
            html += '</div>';

            // Summary cards
            html += '<div style="display: flex; gap: 20px; margin-top: 30px; flex-wrap: wrap;">';

            // Documents without chunks
            if (data.documents_without_chunks !== undefined) {{
                const bgColor = data.documents_without_chunks > 0 ? '#fff3cd' : '#d4edda';
                html += `<div class="card" style="flex: 1; min-width: 250px; background: ${{bgColor}};">`;
                html += '<h3 style="margin-top: 0;">Documents Without Chunks</h3>';
                html += `<div style="font-size: 24px; font-weight: bold; color: #333;">${{data.documents_without_chunks}}</div>`;
                html += '<div style="font-size: 12px; color: #666; margin-top: 5px;">Documents that have not been chunked</div>';
                html += '</div>';
            }}

            // Chunks without embeddings (for each embedding)
            if (data.chunks_without_embeddings) {{
                for (const [embedding, count] of Object.entries(data.chunks_without_embeddings)) {{
                    const bgColor = count > 0 ? '#fff3cd' : '#d4edda';
                    html += `<div class="card" style="flex: 1; min-width: 250px; background: ${{bgColor}};">`;
                    html += `<h3 style="margin-top: 0;">Chunks Without Embeddings (${{escapeHtml(embedding)}})</h3>`;
                    html += `<div style="font-size: 24px; font-weight: bold; color: #333;">${{count}}</div>`;
                    html += '<div style="font-size: 12px; color: #666; margin-top: 5px;">Chunks that have not been embedded with this model</div>';
                    html += '</div>';
                }}
            }}

            html += '</div>';

            html += '</table>';
            html += '</div>';

            container.innerHTML = html;
        }}

        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}
        </script>
        """

        return HTMLResponse(html_templates.base_template(
            title="Statistics",
            content=content,
            username=username
        ))
