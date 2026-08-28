"""Knowledge Base stdio MCP server. 

Usage:
    python -m kb_mcp.server.mcp_stdio
    
Or CLI:
    kb-server-stdio
"""

from dotenv import load_dotenv
from pathlib import Path

# Load environment variables early
project_root = Path(__file__).parent.parent.parent.parent
env_path = project_root / ".env"
load_dotenv(env_path)

import logging
from mcp.server.fastmcp import FastMCP

from . import mcp as mcp_tools
from .mcp_prompts import get_server_instructions

# Configure logging to stderr since stdout is used for MCP communication
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

# Create FastMCP instance (no auth for stdio)
mcp = FastMCP("kb-mcp-stdio", instructions=get_server_instructions())

# Register tools and prompts (no resources for stdio - they need server context)
mcp_tools.register_tools(mcp)
mcp_tools.register_prompts(mcp)

def main():
    """Run the stdio MCP server."""
    logger.info("Starting kb-mcp stdio server")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
