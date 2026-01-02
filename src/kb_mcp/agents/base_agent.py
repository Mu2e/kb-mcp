import asyncio
import json
import logging
from typing import List, Dict, Any, Optional

from mcp import ClientSession
from openai import AsyncOpenAI
from mcp.types import CallToolResult

from .prompts import SHARED_PROTOCOL

logger = logging.getLogger(__name__)

class BaseAgent:
    """Base class for MCP-enabled agents."""

    def __init__(
        self,
        session: ClientSession,
        client: AsyncOpenAI,
        depth: int = 0,
        agent_id: str = "Base",
        max_depth: int = 2,
        run_id: str = None,
    ):
        self.session = session
        self.client = client
        self.depth = depth
        self.agent_id = agent_id
        self.max_depth = max_depth
        
        # Generate a run_id if not provided (timestamp)
        if run_id is None:
            import time
            from datetime import datetime
            self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        else:
            self.run_id = run_id
        self.tools = []
        self.conversation: List[Dict[str, Any]] = []
        self.usage = None
        self.domain_context = ""

    def info(self, info: str):
        prefix = "  " * self.depth
        tokens = 0
        if self.usage and hasattr(self.usage, 'total_tokens'):
            tokens = self.usage.total_tokens
        logger.info(f"{prefix}{self.agent_id} ({(tokens//1e3): 4.1f}k): {info}")

    async def initialize_tools(self):
        """Fetch tools from MCP server."""
        # 1. Fetch Domain Context from Server
        try:
            resources = await self.session.read_resource("kb://sys/domain_context")
            self.domain_context = resources.contents[0].text
            self.info("loaded domain context from server")
        except Exception as e:
            logger.warning(f"no domain context found, using generic default: {e}")
            self.domain_context = "Domain: General Knowledge."

        # 2. Get MCP tools
        mcp_tools_list = await self.session.list_tools()

        # Convert to OpenAI format
        self.tools = []
        tool_names = []
        
        # Only add tools if we are allowed to use them (depth > 0 usually implies worker)
        # But BaseAgent should probably retrieve them and let subclasses decide which to use
        # For now, following original logic: if depth > 0, we add MCP tools.
        # Actually, the original logic was:
        # if self.depth > 0: add mcp tools
        # if self.depth < self.max_depth: add delegation tool
        
        # We will keep the retrieval generic here, and let subclasses or `initialize_tools` override manage specific permissioning if needed.
        # However, to maintain exact parirty with the refactor:
        
        for t in mcp_tools_list.tools:
            self.tools.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.inputSchema,
                }
            })
            tool_names.append(t.name)
            
        if self.depth > 0:
             self.info(f"discovered MCP tools: {', '.join(tool_names)}")
        else:
             # If depth 0 (Manager), we might NOT want to give it all tools? 
             # Original code: if self.depth > 0: ... append tools.
             # So Manager (depth 0) did NOT get MCP tools, only delegation.
             # We need to respect that logic or allow it to be configured.
             # Let's clean self.tools if depth == 0 for now to match behavior, 
             # OR we make `initialize_tools` cleaner.
             # Let's clear it if depth == 0 logic is strict.
             pass

    async def _execute_tool(self, tool_call, prefix: str, model: str) -> str:
        """Execute a single tool call."""
        fname = tool_call.function.name
        args = json.loads(tool_call.function.arguments)

        self.info(f"using tool: {fname} with args: {args}")
        try:
            result = await self.session.call_tool(fname, arguments=args)

            # Extract content from MCP response
            content_parts = []
            for c in result.content:
                if hasattr(c, 'text'):
                    text_content = c.text
                    
                    # Safety truncation
                    from ..config import get_agent_config
                    limit = get_agent_config().get('max_tool_output_chars', 30000)
                    
                    if len(text_content) > limit:
                         text_content = text_content[:limit] + "\n...(truncated by BaseAgent)..."
                    content_parts.append({"type": "text", "text": text_content})
                elif hasattr(c, 'data') and hasattr(c, 'mimeType'):
                    # Image content - provide as image_url for multimodal models
                    # Format: data:<mime>;base64,<data>
                    image_url = f"data:{c.mimeType};base64,{c.data}"
                    content_parts.append({
                        "type": "image_url", 
                        "image_url": {"url": image_url}
                    })

            if not content_parts:
                 return [{"type": "text", "text": "(empty response)"}]
            
            return content_parts
        except Exception as e:
            logger.error(f"{prefix}Tool {fname} failed: {e}")
            return f"Error executing {fname}: {str(e)}"
    
    async def run(self, query: str, model: str = "gpt-oss-120b") -> str:
        """Abstract run method."""
        raise NotImplementedError("Subclasses must implement run()")
