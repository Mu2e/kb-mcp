import asyncio
import json
import logging
from typing import List, Dict, Any, Optional, Callable

from mcp import ClientSession
from openai import AsyncOpenAI
from mcp.types import CallToolResult

from .prompts import SHARED_PROTOCOL

logger = logging.getLogger(__name__)

class BaseAgent:
    """Base class for MCP-enabled agents."""

    # Tool names that subclasses should never receive from list_tools(), e.g.
    # long-running tools that would otherwise let a worker recursively spawn itself.
    EXCLUDED_TOOLS: set = set()

    def __init__(
        self,
        session: ClientSession,
        client: AsyncOpenAI,
        depth: int = 0,
        agent_id: str = "Base",
        max_depth: int = 2,
        run_id: str = None,
        callback: Optional[Callable] = None,
    ):
        self.session = session
        self.client = client
        self.depth = depth
        self.agent_id = agent_id
        self.max_depth = max_depth
        self.callback = callback

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
        self.token_totals: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "main_context_tokens": 0,
            "cached_prompt_tokens": 0,
            "requests": 0,
        }
        self.token_history: List[Dict[str, Any]] = []
        self.domain_context = ""

    def _usage_snapshot(self, usage: Any) -> Dict[str, int]:
        """Normalize provider usage object into integer token counters."""
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)

        prompt_details = getattr(usage, "prompt_tokens_details", None)
        cached_prompt_tokens = 0
        if prompt_details is not None:
            cached_prompt_tokens = int(getattr(prompt_details, "cached_tokens", 0) or 0)

        # "main_context_tokens" represents active input context actually processed this turn.
        main_context_tokens = max(prompt_tokens - cached_prompt_tokens, 0)

        # Some providers do not set total_tokens.
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens

        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "main_context_tokens": main_context_tokens,
            "cached_prompt_tokens": cached_prompt_tokens,
        }

    def record_usage(self, usage: Any, stage: str = "chat_completion") -> Dict[str, int]:
        """Track per-request and cumulative token usage for UI/telemetry."""
        snapshot = self._usage_snapshot(usage)
        self.usage = usage
        self.token_totals["prompt_tokens"] += snapshot["prompt_tokens"]
        self.token_totals["completion_tokens"] += snapshot["completion_tokens"]
        self.token_totals["total_tokens"] += snapshot["total_tokens"]
        self.token_totals["main_context_tokens"] += snapshot["main_context_tokens"]
        self.token_totals["cached_prompt_tokens"] += snapshot["cached_prompt_tokens"]
        self.token_totals["requests"] += 1

        turn = {
            "stage": stage,
            **snapshot,
            "request_index": self.token_totals["requests"],
        }
        self.token_history.append(turn)
        return turn

    def get_token_overview(self) -> Dict[str, Any]:
        """Return cumulative and per-request usage counters."""
        latest = self.token_history[-1] if self.token_history else None
        return {
            "totals": dict(self.token_totals),
            "latest": latest,
            "history": list(self.token_history),
        }

    async def emit_event(self, event: Dict[str, Any]):
        """Emit an event via callback if available."""
        if self.callback:
            if asyncio.iscoroutinefunction(self.callback):
                await self.callback(event)
            else:
                self.callback(event)

    def info(self, info: str):
        prefix = "  " * self.depth
        tokens = 0
        if self.usage and hasattr(self.usage, 'total_tokens'):
            tokens = self.usage.total_tokens
        logger.info(f"{prefix}{self.agent_id} ({(tokens//1e3): 4.1f}k): {info}")

        # Emit event via callback if available
        if self.callback:
            event = {
                'type': 'info',
                'agent_id': self.agent_id,
                'depth': self.depth,
                'message': info,
                'tokens': tokens,
                'token_overview': self.get_token_overview(),
            }
            # Create task for async callback (don't block)
            if asyncio.iscoroutinefunction(self.callback):
                asyncio.create_task(self.callback(event))
            else:
                self.callback(event)

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
            if t.name in self.EXCLUDED_TOOLS:
                continue
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
            logger.error(f"{prefix}Tool {fname} failed: {e!r}", exc_info=True)
            return [{"type": "text", "text": f"Error executing {fname}: {e!r}"}]
    
    async def run(self, query: str, model: str = "gpt-oss-120b") -> str:
        """Abstract run method."""
        raise NotImplementedError("Subclasses must implement run()")
