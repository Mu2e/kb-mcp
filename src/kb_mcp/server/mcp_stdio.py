"""Knowledge Base stdio MCP server. 

Usage:
    python -m kb_mcp.server.mcp_stdio
    
Or CLI:
    kb-server-stdio
"""

# Load environment variables early, before anything imports kb_mcp.config
# (which reads os.environ at import time). See kb_mcp.env for the resolution
# order; --env-file works here too, and KB_ENV_FILE is the no-argv equivalent.
from ..env import load_env, env_file_from_argv

load_env(env_file_from_argv())

import logging
from mcp.server.mcpserver import MCPServer

from . import mcp as mcp_tools
from .mcp_prompts import get_server_instructions

# Configure logging to stderr since stdout is used for MCP communication
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

# Create MCPServer instance (no auth for stdio)
mcp = MCPServer("kb-mcp-stdio", instructions=get_server_instructions())

# Register tools and prompts (no resources for stdio - they need server context)
mcp_tools.register_tools(mcp)
mcp_tools.register_prompts(mcp)

def main():
    """Run the stdio MCP server."""
    logger.info("Starting kb-mcp stdio server")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
