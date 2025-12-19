"""Server package.

This package exposes the main MCP server entrypoint.
The actual implementation lives in server.server.
"""

def main():
    """Lazy import to avoid loading server.py at package import time."""
    from .server import main as server_main
    return server_main()

__all__ = ["main"]

