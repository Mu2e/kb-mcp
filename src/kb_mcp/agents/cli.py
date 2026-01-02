"""CLI for the recursive research agent."""

import asyncio
import logging

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kb_mcp.llm import get_openai_client
from kb_mcp.config import get_agent_config
from .recursive_agent import RecursiveAgent

logger = logging.getLogger(__name__)


async def async_main():
    """Interactive CLI for testing the agent."""
    # Get configuration
    agent_config = get_agent_config()
    model = agent_config['agent_model']
    max_depth = agent_config['max_depth']

    print("Recursive Research Agent")
    print(f"   Model: {model}")
    print(f"   Max Depth: {max_depth}")
    print("   Commands: 'q' to quit\n")

    import sys
    
    # Setup single connection
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "kb_mcp.server.mcp_stdio"],
        env=None,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            client = get_openai_client(use_async=True)

            # Generate run_id
            import time
            from datetime import datetime
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Create agent once and reuse for follow-up questions
            root = RecursiveAgent(session, client, depth=0, max_depth=max_depth, run_id=run_id)
            await root.initialize_tools()

            # Interactive loop
            while True:
                try:
                    query = input("\nQuery > ")
                    if query.lower() in ["q", "quit"]:
                        break

                    print("\nWork  >")
                    answer = await root.run(query, model=model)
                    print(f"\nAnswer:\n{answer}\n")

                except KeyboardInterrupt:
                    break
                except Exception as e:
                    logger.error(f"Error: {e}", exc_info=True)

def main():
    """CLI entry point (sync wrapper for async_main)."""
    # Custom format without logger name for cleaner output
    logging.basicConfig(
        level=logging.INFO,
        format='      - %(message)s'
    )

    # Suppress httpx INFO logs (they're too verbose)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    asyncio.run(async_main())


if __name__ == "__main__":
    main()
