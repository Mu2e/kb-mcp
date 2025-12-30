"""Web interface package - consolidates all web routes."""

from .auth import WebSessionManager, setup_shared_auth_routes
from .routes.documents import (
    require_auth_html,
    require_auth_api,
    setup_documents_routes,
)
from .routes.eval import setup_eval_routes
from .routes.logs import setup_logs_routes
from .routes.statistics import setup_statistics_routes
from .routes.admin import setup_admin_routes
from .routes.api import setup_api_routes
from .routes.graph import setup_graph_routes


def setup_web_routes(app, oauth_provider, session_manager: WebSessionManager):
    """Setup all web interface routes.

    This function consolidates route registration for:
    - Document management (/web, /web/document/*, /web/upload)
    - Evaluation interface (/web/eval/*)
    - Logs viewing (/web/logs)
    - Statistics (/web/statistics)

    Args:
        app: Starlette application instance
        oauth_provider: OAuth provider instance
        session_manager: WebSessionManager instance for authentication
    """

    # Setup document management routes (main web interface)
    setup_documents_routes(app, oauth_provider, session_manager, require_auth_html, require_auth_api)

    # Setup evaluation routes
    setup_eval_routes(app, session_manager, require_auth_html)

    # Setup logs routes
    setup_logs_routes(app, session_manager, require_auth_html)

    # Setup statistics routes
    setup_statistics_routes(app, session_manager, require_auth_html)

    # Setup admin routes (API key management)
    setup_admin_routes(app, oauth_provider, session_manager)

    # Setup API routes (JSON endpoints)
    setup_api_routes(app, session_manager)

    # Setup graph routes (knowledge graph exploration)
    setup_graph_routes(app, session_manager)


__all__ = [
    'WebSessionManager',
    'setup_shared_auth_routes',
    'setup_web_routes',
    'require_auth_html',
    'require_auth_api',
]
