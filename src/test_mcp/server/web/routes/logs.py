"""Search logs web page route."""

import logging
from html import escape as html_escape
from starlette.responses import HTMLResponse
from starlette.requests import Request

from ..auth import WebSessionManager
from ... import html_templates

logger = logging.getLogger(__name__)


def setup_logs_routes(app, session_manager: WebSessionManager, require_auth_html):
    """Register logs web routes.

    Args:
        app: Starlette application instance
        session_manager: WebSessionManager instance for authentication
        require_auth_html: Authentication helper function
    """

    @app.route("/web/logs")
    async def web_logs(request: Request):
        """Search logs page with filtering."""
        # Check authentication first
        session_data, redirect = await require_auth_html(request, session_manager)
        if redirect:
            return redirect

        # Get username from authenticated session
        username = session_data.get("username")

        # Get auth warning banner
        auth_warning = session_manager.get_auth_warning_html()

        # Get filter values from query params
        date_from = request.query_params.get("date_from", "")
        date_to = request.query_params.get("date_to", "")
        min_time = request.query_params.get("min_time_search_total", "")
        query_filter = request.query_params.get("query", "")

        # Build filter HTML
        filter_html = f"""
        <div class="card" style="margin-bottom: 20px;">
            <h2>Filters</h2>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px;">
                <div>
                    <label for="log-date-from" style="display: block; margin-bottom: 5px; font-weight: bold;">Date From:</label>
                    <input type="date" id="log-date-from" value="{html_escape(date_from)}" style="width: 100%; padding: 8px;">
                </div>
                <div>
                    <label for="log-date-to" style="display: block; margin-bottom: 5px; font-weight: bold;">Date To:</label>
                    <input type="date" id="log-date-to" value="{html_escape(date_to)}" style="width: 100%; padding: 8px;">
                </div>
                <div>
                    <label for="log-min-time" style="display: block; margin-bottom: 5px; font-weight: bold;">Min Search Time (s):</label>
                    <input type="number" id="log-min-time" value="{html_escape(min_time)}" step="0.1" min="0" placeholder="0.0" style="width: 100%; padding: 8px;">
                </div>
                <div>
                    <label for="log-query" style="display: block; margin-bottom: 5px; font-weight: bold;">Query Text:</label>
                    <input type="text" id="log-query" value="{html_escape(query_filter)}" placeholder="Filter by query text..." style="width: 100%; padding: 8px;">
                </div>
            </div>
            <div style="margin-top: 15px;">
                <button type="button" id="apply-log-filters" class="btn" style="padding: 10px 20px;">Apply Filters</button>
                <button type="button" id="clear-log-filters" class="btn" style="padding: 10px 20px; margin-left: 10px; background: #666;">Clear</button>
            </div>
        </div>
        """

        content = f"""
        {auth_warning}
        <div class="card">
            <h1>Search Logs</h1>
            <p>View and filter search query logs.</p>
        </div>

        {filter_html}

        <div class="card">
            <h2>Logs</h2>
            <div id="logs-list">
                <div style="text-align: center; padding: 20px; color: #666;">Loading logs...</div>
            </div>
            <div id="logs-loading" style="text-align: center; padding: 20px; display: none;">
                <div>Loading more logs...</div>
            </div>
        </div>
        """

        return HTMLResponse(html_templates.base_template(
            title="Search Logs",
            content=content,
            username=username,
        ))

