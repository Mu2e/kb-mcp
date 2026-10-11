"""Record every MCP tool call in `logs_mcp_calls`, for usage statistics (`kb logs mcp`).

Each registered tool is wrapped by `logged()`. The wrapper times the call,
classifies the answer (ok / error) and writes one row: tool, MCP session id
(`Mcp-Session-Id`, a random id per conversation that groups its calls),
transport, duration and answer size. Nothing else: no user, no arguments, no
error text -- the point is what is used how often, not by whom or for what.

The session id comes from the HTTP request that carried the call.
The SDK hands it to a tool through a `Context` parameter, so the wrapper
declares one of its own (hidden from the tool's input schema, like
kb_research's `ctx`). Over stdio there is no HTTP request: the row says
transport "stdio" and has no session.

Logging is best-effort and off the request path: rows are written by a
background thread, and a failure to write is logged, never raised.
"""

import functools
import inspect
import json
import logging
import time
import typing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from mcp.server.mcpserver import Context

logger = logging.getLogger(__name__)

# No leading underscore: the SDK rejects such parameter names.
_CTX_PARAM = "usage_ctx"
_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-usage-log")


def request_info(ctx: Optional[Context]) -> Dict[str, Optional[str]]:
    """Session id and transport of the call, from the SDK's request context."""
    request = None
    try:
        request = ctx.request_context.request if ctx is not None else None
    except Exception:  # noqa: BLE001 - no request context (stdio, tests)
        request = None
    if request is None or not hasattr(request, "headers"):
        return {"session_id": None, "transport": "stdio"}
    return {"session_id": request.headers.get("mcp-session-id"), "transport": "http"}


def is_error_answer(result: Any) -> bool:
    """Whether a tool answered with an error. Tools report errors as text, not exceptions."""
    if isinstance(result, str):
        stripped = result.lstrip()
        if stripped.startswith("ERROR"):
            return True
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
            except ValueError:
                return False
            return isinstance(data, dict) and bool(data.get("error"))
    return False


def _size(result: Any) -> Optional[int]:
    if isinstance(result, str):
        return len(result)
    data = getattr(result, "data", None)  # ImageContent: base64 image
    return len(data) if isinstance(data, str) else None


def _write(row: Dict[str, Any]) -> None:
    try:
        from ..kb.database import get_db_session
        from ..kb.db_models import McpCallLog

        with get_db_session() as session:
            session.add(McpCallLog(**row))
    except Exception as e:  # noqa: BLE001 - statistics must never break a tool
        logger.debug(f"Could not record MCP call {row.get('tool')}: {e}")


def record(row: Dict[str, Any]) -> None:
    """Queue a row for the background writer (replaced in tests)."""
    _writer.submit(_write, row)


def logged(fn):
    """Wrap a tool so each call is recorded; the schema clients see is unchanged."""
    sig = inspect.signature(fn)
    try:
        hints = typing.get_type_hints(fn)
    except Exception:  # noqa: BLE001
        hints = dict(getattr(fn, "__annotations__", {}))
    # A tool that already takes a Context (kb_research) gets the same one passed on.
    own_ctx = next((name for name, hint in hints.items()
                    if inspect.isclass(hint) and issubclass(hint, Context)), None)
    ctx_name = own_ctx or _CTX_PARAM

    def before(kwargs):
        ctx = kwargs.get(ctx_name)
        if own_ctx is None:
            kwargs.pop(_CTX_PARAM, None)
        return ctx, kwargs

    def after(ctx, started, result=None, exc=None):
        if exc is not None:
            error_kind = type(exc).__name__
        else:
            error_kind = "error answer" if is_error_answer(result) else None
        seconds = time.perf_counter() - started
        record({
            # Stamped here, not by the database when the background writer
            # gets to it, so a backed-up queue cannot shift calls in time.
            "created_time": datetime.now(timezone.utc) - timedelta(seconds=seconds),
            "tool": fn.__name__,
            **request_info(ctx),
            "status": "error" if error_kind else "ok",
            "error_kind": error_kind,
            "duration_ms": round(seconds * 1000, 1),
            "response_chars": _size(result) if exc is None else None,
        })

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            ctx, kwargs = before(kwargs)
            started = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except Exception as e:
                after(ctx, started, exc=e)
                raise
            after(ctx, started, result=result)
            return result
    else:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            ctx, kwargs = before(kwargs)
            started = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                after(ctx, started, exc=e)
                raise
            after(ctx, started, result=result)
            return result

    if own_ctx is None:
        params = list(sig.parameters.values())
        params.append(inspect.Parameter(_CTX_PARAM, inspect.Parameter.KEYWORD_ONLY, default=None,
                                        annotation=Optional[Context]))
        wrapper.__signature__ = sig.replace(parameters=params)
        wrapper.__annotations__ = {**hints, _CTX_PARAM: Optional[Context]}
    return wrapper
