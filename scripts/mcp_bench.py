#!/usr/bin/env python3
"""Time kb_search over MCP against a running kb-mcp server.

Runs a fixed set of search queries (plain, common words, names, filtered)
through one MCP session and prints how long each call took and how many
results it returned, so the numbers can be compared before and after a change
or a deployment. Round 1 is the cold run; later rounds show warm behaviour.

Usage:
  mcp_bench.py [base-url] [--rounds N] [--queries-file FILE] [--max-results N]

The bearer token is read from KB_MCP_TOKEN, or from --token-file /
KB_MCP_TOKEN_FILE (a file only you can read); with neither, and a terminal,
the script asks for it without echoing. It is deliberately not a command-line
argument: those are visible to every user on the host in `ps`, and end up in
the shell history.

Example, from any Mu2e node:
  KB_MCP_TOKEN_FILE=~/.kb-mcp-token mcp_bench.py http://mu2eaigpvm01.fnal.gov:8008
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

# (label, kb_search arguments). Chosen from real queries in the search log:
# specific phrases, common single words (the slowest for full-text), a name,
# and filters, including the terms + doc_type combination that broke on
# 2026-09-26.
DEFAULT_QUERIES = [
    ("phrase", {"query": "tracker alignment"}),
    ("phrase", {"query": "straw tube gas leak rate"}),
    ("phrase", {"query": "CRV LED DAC value"}),
    ("common word", {"query": "calorimeter"}),
    ("common word", {"query": "tracker"}),
    ("name", {"query": "Simon Corrodi"}),
    ("question", {"query": "What is the total length of the Mu2e detector?"}),
    ("filter term", {"query": "tracker alignment",
                     "search_filter": {"term": {"source_id": "mu2e-docdb"}}}),
    ("filter terms", {"query": "tracker alignment",
                      "search_filter": {"terms": {"source_id": ["mu2e-docdb", "mu2e-wiki"]}}}),
    ("fulltext", {"query": "58408", "search_type": "fulltext"}),
    ("semantic", {"query": "how is the muon beam produced", "search_type": "semantic"}),
]


def read_token(token_file: str | None) -> str | None:
    if os.environ.get("KB_MCP_TOKEN"):
        return os.environ["KB_MCP_TOKEN"].strip()
    path = token_file or os.environ.get("KB_MCP_TOKEN_FILE")
    if not path:
        return None
    p = Path(path).expanduser()
    if p.stat().st_mode & 0o077:
        print(f"WARNING: {p} is readable by others; chmod 600 it.", file=sys.stderr)
    return p.read_text().strip()


def count_results(text: str) -> str:
    """Result count if the payload is JSON, else the number of characters."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return f"{len(text)} chars"
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return f"{len(payload['results'])} results"
    if isinstance(payload, list):
        return f"{len(payload)} results"
    return f"{len(text)} chars"


async def run(base_url: str, token: str | None, queries, rounds: int,
              max_results: int | None, timeout: float) -> int:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    client = httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(timeout))

    # A plain GET answers 401 for a missing or wrong token and 400 (no MCP
    # session) for a valid one; checking first turns an auth problem into one
    # clear line instead of an exception from deep inside the MCP client.
    try:
        probe = await client.get(f"{base_url}/mcp")
    except Exception as e:
        print(f"ERROR: cannot reach {base_url}/mcp: {e}", file=sys.stderr)
        await client.aclose()
        return 2
    if probe.status_code == 401:
        print(f"ERROR: {base_url}/mcp answered 401: the token is "
              f"{'missing' if not token else 'not accepted'}.", file=sys.stderr)
        await client.aclose()
        return 2

    timings: dict[int, list[float]] = {}
    failures = 0
    try:
        async with streamable_http_client(f"{base_url}/mcp", http_client=client) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                t = time.perf_counter()
                init = await session.initialize()
                print(f"server   : {init.server_info.name} {init.server_info.version} at {base_url}")
                print(f"session  : initialize {time.perf_counter() - t:.2f}s\n")
                print(f"{'round':>5}  {'time':>7}  {'result':<12} {'kind':<13} query")
                for rnd in range(1, rounds + 1):
                    for label, args in queries:
                        args = dict(args)
                        if max_results is not None:
                            args.setdefault("max_results", max_results)
                        t = time.perf_counter()
                        try:
                            result = await session.call_tool("kb_search", args)
                            dt = time.perf_counter() - t
                            text = " ".join(getattr(c, "text", "") for c in result.content).strip()
                            outcome = "ERROR" if result.is_error else count_results(text)
                            failures += bool(result.is_error)
                        except Exception as e:  # keep going; report it in the table
                            dt = time.perf_counter() - t
                            outcome = f"EXC {type(e).__name__}"
                            failures += 1
                        timings.setdefault(rnd, []).append(dt)
                        print(f"{rnd:>5}  {dt:>6.2f}s  {outcome:<12} {label:<13} {args['query']}")
                    print()
    except Exception as e:
        print(f"ERROR: MCP session failed: {type(e).__name__}: {e}", file=sys.stderr)
        failures += 1
    finally:
        await client.aclose()

    for rnd, ts in timings.items():
        print(f"round {rnd}: median {statistics.median(ts):.2f}s  max {max(ts):.2f}s  "
              f"total {sum(ts):.1f}s over {len(ts)} searches")
    if failures:
        print(f"\n{failures} search(es) failed", file=sys.stderr)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("base_url", nargs="?", default="http://mu2eaigpvm01.fnal.gov:8008")
    parser.add_argument("--token-file", help="File holding the bearer token (default: $KB_MCP_TOKEN_FILE).")
    parser.add_argument("--rounds", type=int, default=2, help="Passes over the queries (default 2: cold, warm).")
    parser.add_argument("--queries-file", help="One query per line, instead of the built-in set.")
    parser.add_argument("--max-results", type=int, help="Pass max_results to every search.")
    parser.add_argument("--timeout", type=float, default=300.0, help="Per-request timeout in seconds.")
    args = parser.parse_args()

    queries = DEFAULT_QUERIES
    if args.queries_file:
        lines = Path(args.queries_file).read_text().splitlines()
        queries = [("custom", {"query": q.strip()}) for q in lines if q.strip()]

    token = read_token(args.token_file)
    if not token and sys.stdin.isatty():
        import getpass
        token = getpass.getpass("MCP bearer token (not echoed): ").strip() or None
    if not token:
        print("NOTE: no token (KB_MCP_TOKEN, --token-file, or the prompt); "
              "the server will likely answer 401.", file=sys.stderr)
    return asyncio.run(run(args.base_url.rstrip("/"), token, queries, args.rounds,
                           args.max_results, args.timeout))


if __name__ == "__main__":
    sys.exit(main())
