"""Web routes package - consolidates all route modules."""

from .documents import (
    require_auth_html,
    require_auth_api,
    setup_documents_routes,
)
from .eval import setup_eval_routes
from .logs import setup_logs_routes
from .statistics import setup_statistics_routes
from .admin import setup_admin_routes
from .api import setup_api_routes

__all__ = [
    'require_auth_html',
    'require_auth_api',
    'setup_documents_routes',
    'setup_eval_routes',
    'setup_logs_routes',
    'setup_statistics_routes',
    'setup_admin_routes',
    'setup_api_routes',
]

