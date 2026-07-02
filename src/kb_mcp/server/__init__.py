"""MCP server and web interface for the Mu2e knowledge base.

Provides FastMCP server with 6 tools (search, get, graph) and a
Starlette web UI for document browsing, search, and evaluation.
"""

def main():
    """Lazy import to avoid loading server.py at package import time."""
    from .server import main as server_main
    return server_main()

__all__ = ["main"]

