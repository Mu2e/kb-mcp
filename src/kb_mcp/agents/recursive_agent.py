import asyncio
import json
import logging

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI

from .prompts import SHARED_PROTOCOL, MANAGER_TEMPLATE
from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


class RecursiveAgent(BaseAgent):
    """Self-orchestrating research agent with recursive delegation."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.child_counter = 0
        self.agent_id = "Doc"

    async def initialize_tools(self):
        """Initialize tools for the Manager."""
        # 1. Domain Context
        try:
            resources = await self.session.read_resource("kb://sys/domain_context")
            self.domain_context = resources.contents[0].text
            self.info("loaded domain context from server")
        except Exception:
            self.domain_context = "Domain: General Knowledge."

        # 2. Manager specific tools (Delegation)
        # Manager does NOT get raw MCP tools by default (per prompt), only delegation.
        self.tools = []
        
        # Add delegation tool
        if self.depth < self.max_depth:
            self.tools.append({
                "type": "function",
                "function": {
                    "name": "delegate_research",
                    "description": "Spawn a specialized worker agent. USE THIS to read documents, perform searches, or analyze sub-topics.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task_description": {
                                "type": "string",
                                "description": "Precise instructions for the worker."
                            },
                        },
                        "required": ["task_description"]
                    }
                }
            })
            self.info(f"added delegation tool (depth={self.depth}/{self.max_depth})")

    async def run(self, query: str, model: str = "gpt-oss-120b") -> str:
        """Execute research for the given query (Manager Loop)."""
        prefix = "  " * self.depth
        self.info(f"starting Manager: \"{query}\"")
        self.original_query = query

        # 1. Format Manager Prompt
        sys_prompt = MANAGER_TEMPLATE.format(
            domain_context=self.domain_context,
            shared_protocol=SHARED_PROTOCOL
        )

        # Initialize Conversation
        if not self.conversation:
            self.conversation = [
                {"role": "system", "content": sys_prompt},
            ]

        self.conversation.append({"role": "user", "content": query})

        # LLM loop (Standard Chat History for Manager)
        while True:
            response = await self.client.chat.completions.create(
                model=model,
                messages=self.conversation,
                tools=self.tools if self.tools else None,
                temperature=0.1,
            )

            if hasattr(response, 'usage') and response.usage:
                self.usage = response.usage

            message = response.choices[0].message
            
            # Helper to convert to dict
            message_dict = {
                "role": message.role,
                "content": message.content if message.content is not None else "",
            }
            if message.tool_calls:
                message_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                    }
                    for tc in message.tool_calls
                ]
            
            self.conversation.append(message_dict)

            # Check if answer
            if message.content and not message.tool_calls:
                self.info(f"finished")
                return message.content

            # Execute tool calls in parallel
            if message.tool_calls:
                async def process_tool_call(tool_call):
                    # We only support delegate_research here really
                    if tool_call.function.name == "delegate_research":
                        args = json.loads(tool_call.function.arguments)
                        result = await self._delegate(args, prefix, model)
                        return (tool_call.id, result)
                    else:
                        # Fallback for other tools if we added them
                        result = await self._execute_tool(tool_call, prefix, model)
                        return (tool_call.id, result)

                results = await asyncio.gather(*[process_tool_call(tc) for tc in message.tool_calls])

                for tool_call_id, result in results:
                    self.conversation.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result
                    })

    async def _delegate(self, args: dict, prefix: str, model: str) -> str:
        """Spawn a NOTEBOOK WORKER agent for a specific task."""
        from .notebook_agent import NotebookAgent
        
        task_description = args.get("task_description")
        
        if not task_description:
            return "Error: No task_description provided"

        self.info(f"delegating task to NotebookAgent: {task_description[:50]}...")
        
        # Create NotebookAgent
        # We pass self.session/client to share connection
        worker = NotebookAgent(
            session=self.session,
            client=self.client,
            depth=self.depth + 1,
            agent_id=f"{self.agent_id}.{self.child_counter}",
            max_depth=self.max_depth,
            run_id=self.run_id,
            callback=self.callback  # Propagate callback to child agents
        )
        self.child_counter += 1
        
        await worker.initialize_tools()
        return await worker.run(task_description, model=model)


# --- Main Entry Point ---
async def research(query: str) -> str:
    """
    Execute a research query using the recursive agent.

    This is the main public API. It:
    1. Connects to kb-server-stdio
    2. Creates shared MCP session and LLM client
    3. Runs the root agent
    4. Returns the final answer

    Args:
        query: Research question

    Returns:
        Final answer string

    Environment Variables (via config.py):
        - AGENT_MODEL: LLM model (default: from get_default_llm_model())
        - AGENT_MAX_DEPTH: Max recursion depth (default: 2)
        - OPENAI_API_KEY: API key for LLM
        - OPENAI_BASE_URL: Optional custom LLM endpoint
    """
    from kb_mcp.llm import get_openai_client
    from kb_mcp.config import get_agent_config

    import sys
    
    # Get configuration
    agent_config = get_agent_config()
    model = agent_config['agent_model']
    max_depth = agent_config['max_depth']

    # Connect to MCP server
    # Use sys.executable to ensure we use the same python environment
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "kb_mcp.server.mcp_stdio"],
        env=None,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Create async LLM client
            client = get_openai_client(use_async=True)

            # Generate run_id for this session
            import time
            from datetime import datetime
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Run root agent
            root = RecursiveAgent(session, client, depth=0, max_depth=max_depth, run_id=run_id)
            await root.initialize_tools()

            return await root.run(query, model=model)
