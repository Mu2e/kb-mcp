"""Simple audit logging for tool calls (server package)."""

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def log_tool_call(username: str, tool_name: str, arguments: dict) -> None:
    """Log a tool call with structured data.

    Args:
        username: GitHub username from OAuth
        tool_name: Name of the tool being called
        arguments: Tool arguments
    """
    audit_entry = {
        "timestamp": datetime.now().isoformat(),
        "username": username,
        "tool": tool_name,
        "arguments": arguments,
    }

    logger.info(f"[AUDIT] {json.dumps(audit_entry)}")


