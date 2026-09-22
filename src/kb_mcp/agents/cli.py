"""CLI for the recursive research agent."""

import argparse
import asyncio
import logging
import sys

# Load the env file before kb_mcp.config is imported, which reads os.environ at
# import time. Same contract as the servers: --env-file, or KB_ENV_FILE.
from ..env import env_file_from_argv, load_env

load_env(env_file_from_argv())

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kb_mcp.llm import get_openai_client
from kb_mcp.config import get_agent_config
from .recursive_agent import RecursiveAgent

logger = logging.getLogger(__name__)


async def async_main(args):
    """Run the agent, interactively or for a single query."""
    agent_config = get_agent_config()
    model = args.model or agent_config['agent_model']
    max_depth = args.max_depth if args.max_depth is not None else agent_config['max_depth']

    if not args.query:
        print("Recursive Research Agent")
        print(f"   Model: {model}")
        print(f"   Max Depth: {max_depth}")
        print("   Commands: 'q' to quit\n")

    # Setup single connection
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "kb_mcp.server.mcp_stdio"],
        env=None,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            client = get_openai_client(model=model, use_async=True)

            # Generate run_id
            import time
            from datetime import datetime
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Create agent once and reuse for follow-up questions
            root = RecursiveAgent(session, client, depth=0, max_depth=max_depth, run_id=run_id)
            await root.initialize_tools()

            # One-shot mode: answer and exit, so the agent is scriptable.
            if args.query:
                print(await root.run(args.query, model=model))
                return

            # Interactive loop
            while True:
                try:
                    query = input("\nQuery > ")
                    if query.lower() in ["q", "quit"]:
                        break

                    print("\nWork  >")
                    answer = await root.run(query, model=model)
                    print(f"\nAnswer:\n{answer}\n")

                # EOF and Ctrl-C end the session. EOFError must be caught
                # before the generic handler below: stdin closed (a pipe, a
                # cron job, </dev/null) makes input() raise on every pass, and
                # swallowing it here spun this loop forever while holding the
                # stdio server subprocess open.
                except (EOFError, KeyboardInterrupt):
                    break
                except Exception as e:
                    logger.error(f"Error: {e}", exc_info=True)

def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="kb-agent",
        description=(
            "Recursive research agent over the knowledge base. Runs "
            "interactively, or answers a single --query and exits."
        ),
    )
    parser.add_argument("--query", help="Answer this question and exit.")
    parser.add_argument("--model", help="Override AGENT_MODEL / DEFAULT_LLM_MODEL.")
    parser.add_argument(
        "--max-depth", type=int, help="Override AGENT_MAX_DEPTH."
    )
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        help=(
            "Env file to load before reading configuration. Already applied "
            "by the time this parser runs; declared here for --help and "
            "validation. KB_ENV_FILE is the equivalent without argv."
        ),
    )
    return parser.parse_args(argv)


def main():
    """CLI entry point (sync wrapper for async_main)."""
    # Parse first. Everything below this line spawns a stdio MCP server
    # subprocess and builds an LLM client, so doing it the other way round
    # meant `kb-agent --help` started a server and blocked instead of
    # printing usage.
    args = _parse_args()

    # Custom format without logger name for cleaner output
    logging.basicConfig(
        level=logging.INFO,
        format='      - %(message)s'
    )

    # Suppress httpx INFO logs (they're too verbose)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
