"""Server package.

This package exposes the main MCP server entrypoint.
The actual implementation lives in server.server.
"""

from .server import main

__all__ = ["main"]


