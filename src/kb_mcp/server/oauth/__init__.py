"""OAuth authentication package - consolidates all authentication logic."""

from .base import BaseOAuthProvider
from .github import GitHubOAuthProvider
from .globus import GlobusOAuthProvider
from .api_keys import ApiKeyManager

__all__ = [
    'BaseOAuthProvider',
    'GitHubOAuthProvider',
    'GlobusOAuthProvider',
    'ApiKeyManager',
]

