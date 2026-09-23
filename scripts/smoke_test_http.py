#!/usr/bin/env python3
"""Smoke-test a running kb-mcp HTTP deployment.

Checks, in order, what a client actually depends on:

  1. HTTP reachability        -- the process is up and serving
  2. MCP initialize           -- the streamable-http transport handshakes
  3. tools/list               -- the tool surface is registered
  4. kb_search (optional)     -- the database and embedder really work

Note: there is no /status endpoint on an --only-mcp deployment -- that route
is registered on the web app (server.py), which this unit does not run. Step 1
therefore just checks that the MCP endpoint answers at all.

Step 4 is where a deployment usually fails in a way the earlier steps cannot
see: the server starts fine with an unreachable database or a missing
embedding model, because both are resolved lazily on the first query.

Note that kb_search does NOT report a broken database as a tool error -- it
returns {"message": "No results found", "results": []} with is_error unset,
which is byte-for-byte what a healthy-but-unmatched query returns. So against
a populated knowledge base an empty result IS the failure signal, and --query
treats it as one. Use --allow-empty if the knowledge base really is empty.

Usage:
  kb-mcp-smoke-test <base-url> [--token TOKEN] [--query TEXT] [--timeout S]

Installed into the deployment's venv as `kb-mcp-smoke-test`, so verifying a
deploy needs no checkout on the host; also runnable from a checkout as
scripts/smoke_test_http.py.

Examples:
  kb-mcp-smoke-test http://127.0.0.1:8008
  kb-mcp-smoke-test http://mu2eaigpvm01:8008 --token mikey_xxx --query "tracker alignment"

Exits non-zero on the first failure, so it is usable as a deployment gate.
"""

import argparse
import asyncio
import json
import sys

try:
    import httpx
    # mcp 2.x: snake_case name, and headers are supplied via a pre-built
    # httpx2 client rather than a headers= kwarg (see streamable_http_client's
    # signature). The 1.x spelling was streamablehttp_client.
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
except ImportError as exc:  # pragma: no cover - environment problem, not logic
    print(f"FAIL  missing client dependency: {exc}", file=sys.stderr)
    print("      Run the copy installed in the deployment, which carries a", file=sys.stderr)
    print("      shebang pointing at that release's own interpreter:", file=sys.stderr)
    print("      <deploy-root>/current/.venv/bin/kb-mcp-smoke-test <base-url> ...", file=sys.stderr)
    sys.exit(2)


def _looks_like_timeout(exc: BaseException) -> bool:
    """True if a timeout is anywhere inside a (possibly nested) exception."""
    seen = []
    stack = [exc]
    while stack:
        e = stack.pop()
        if e is None or id(e) in seen:
            continue
        seen.append(id(e))
        if isinstance(e, (httpx.TimeoutException, TimeoutError)):
            return True
        if "SSE stream ended without a response" in str(e):
            return True
        stack.extend(getattr(e, "exceptions", []) or [])
        stack.extend(x for x in (e.__cause__, e.__context__) if x is not None)
    return False


def _ok(msg: str) -> None:
    print(f"ok    {msg}")


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")


async def run(
    base_url: str,
    token: str | None,
    query: str | None,
    timeout: float,
    allow_empty: bool = False,
) -> int:
    base_url = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    # 1. plain HTTP liveness -- distinguishes "not running" from "MCP broken".
    # A bare GET on /mcp is not a valid streamable-http request, so the status
    # code is uninteresting; any HTTP response proves the port is served.
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base_url}/mcp", headers=headers)
        _ok(f"endpoint reachable (GET /mcp -> HTTP {resp.status_code})")
    except Exception as exc:  # noqa: BLE001
        _fail(f"cannot reach {base_url}/mcp: {exc!r}")
        return 1

    # 2-4. the MCP surface
    #
    # The timeout has to go on this client: streamable_http_client takes no
    # timeout of its own, so without one httpx's 5-second default governs the
    # SSE stream. The first query after a restart loads the embedding model,
    # which takes far longer than that -- the stream was then abandoned
    # mid-call and reported as "SSE stream ended without a response", which
    # reads like a server fault rather than a client timeout.
    #
    # Always construct the client, even with no token, so the timeout applies
    # either way.
    http_client = httpx2.AsyncClient(
        headers=headers, timeout=httpx2.Timeout(timeout)
    )
    try:
        async with streamable_http_client(
            f"{base_url}/mcp", http_client=http_client
        ) as streams:
            # TransportStreams is a tuple; 1.x yielded a third session-id
            # element, 2.x does not. Take the first two either way.
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                init = await session.initialize()
                # mcp 2.x renamed this to snake_case (1.x: init.serverInfo).
                info = init.server_info
                _ok(f"initialize -> {info.name} {info.version}")

                tools = await session.list_tools()
                names = sorted(t.name for t in tools.tools)
                if not names:
                    _fail("tools/list returned no tools")
                    return 1
                _ok(f"tools/list -> {len(names)}: {', '.join(names)}")

                if query is None:
                    print("\nSkipped kb_search (pass --query to test the database "
                          "and embedder end to end).")
                    return 0

                if "kb_search" not in names:
                    _fail("kb_search not in the tool list; cannot test the query path")
                    return 1

                result = await session.call_tool("kb_search", {"query": query})
                # mcp 2.x uses snake_case on the result models (1.x: isError).
                if result.is_error:
                    text = " ".join(
                        getattr(c, "text", "") for c in result.content
                    ).strip()
                    _fail(f"kb_search returned an error: {text[:400]}")
                    return 1

                text = " ".join(getattr(c, "text", "") for c in result.content).strip()

                # Count results rather than trusting is_error: see the note in
                # the module docstring about empty results masking a broken DB.
                count = None
                try:
                    payload = json.loads(text)
                except (ValueError, TypeError):
                    payload = None
                if isinstance(payload, dict) and isinstance(payload.get("results"), list):
                    count = len(payload["results"])
                elif isinstance(payload, list):
                    count = len(payload)

                if count is None:
                    _ok(f"kb_search({query!r}) -> {len(text)} chars (unparsed payload)")
                    print(f"      first line: {text.splitlines()[0][:160]}" if text else "")
                    return 0

                if count == 0 and not allow_empty:
                    _fail(f"kb_search({query!r}) returned 0 results")
                    print("      Against a populated knowledge base this is the failure")
                    print("      signal for an unreachable/empty database, a missing")
                    print("      schema, or an embedder that does not match the index.")
                    print("      Check the server log, and pass --allow-empty if the")
                    print("      knowledge base is genuinely empty.")
                    return 1

                _ok(f"kb_search({query!r}) -> {count} result(s)")
                return 0
    except Exception as exc:  # noqa: BLE001
        # A timeout arrives wrapped in TaskGroup ExceptionGroups, and the
        # innermost message ("SSE stream ended without a response") describes
        # the symptom rather than the cause. Name the cause.
        if _looks_like_timeout(exc):
            _fail(f"MCP session timed out after {timeout:g}s")
            print("      The first query after a restart loads the embedding")
            print("      model, which can take a minute. Retry, or raise")
            print("      --timeout. The server log will show whether it was")
            print("      still working when the client gave up.")
            return 1
        _fail(f"MCP session: {exc!r}")
        if not token:
            print("      If the server requires an API key, pass --token.", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="smoke_test_http.py",
        description="Smoke-test a running kb-mcp HTTP deployment.",
    )
    parser.add_argument("base_url", help="e.g. http://127.0.0.1:8008")
    parser.add_argument("--token", help="Bearer token, if the endpoint requires one.")
    parser.add_argument(
        "--query",
        help="Run kb_search with this text, exercising the database and embedder.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Treat 0 search results as success (only for a genuinely empty KB).",
    )
    # 120s, not 30: now that the timeout actually reaches the transport, it has
    # to cover the first query after a restart, which loads the embedding model.
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="Seconds (default: 120, enough for a cold start).")
    args = parser.parse_args()

    return asyncio.run(
        run(args.base_url, args.token, args.query, args.timeout, args.allow_empty)
    )


if __name__ == "__main__":
    sys.exit(main())
