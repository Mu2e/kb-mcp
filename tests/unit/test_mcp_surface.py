"""Checks on the MCP surface the server advertises to clients.

Covers the two things a client sees before it calls anything - the
`instructions` string in the initialize response, and the registered
resources/prompts - plus a dependency-free self-test pair used to tell
"server is misconfigured" apart from "the knowledge base is empty".
"""

import json

import pytest


@pytest.fixture(scope="module")
def mcp():
    from kb_mcp.server.mcp_stdio import mcp as server

    return server


# --- server instructions -------------------------------------------------
#
# The orientation text used to live only in the kb://sys/domain_context
# resource, which clients do not auto-read, so in practice it never reached
# the model. It now also ships as MCP `instructions`.

def test_server_advertises_instructions(mcp):
    assert mcp.instructions, "server must advertise instructions on initialize"


def test_instructions_name_the_primary_sources(mcp):
    """A client with several servers connected routes on this text."""
    text = mcp.instructions
    assert "Mu2e" in text
    assert "mu2e-docdb" in text
    assert "mu2e-wiki" in text


def test_instructions_track_the_hide_graph_setting():
    """Graph tools are conditionally registered; the briefing must match."""
    import kb_mcp.config as config
    from kb_mcp.server.mcp_prompts import get_server_instructions

    original = config.get_server_config
    try:
        config.get_server_config = lambda: {**original(), "hide_graph": False}
        with_graph = get_server_instructions()
        config.get_server_config = lambda: {**original(), "hide_graph": True}
        without_graph = get_server_instructions()
    finally:
        config.get_server_config = original

    assert "kb_find_path" in with_graph
    assert "kb_find_path" not in without_graph, (
        "instructions describe graph tools that register_tools did not register"
    )


@pytest.mark.asyncio
async def test_domain_context_resource_matches_instructions(mcp):
    """Two hand-maintained copies of the briefing would drift apart, so the
    resource serves the same text the initialize response advertises."""
    from kb_mcp.server.mcp_prompts import get_server_instructions

    contents = list(await mcp.read_resource("kb://sys/domain_context"))

    assert contents[0].content == get_server_instructions()


# --- the stale prompt is gone -------------------------------------------

def test_stale_agent_system_prompt_resource_is_removed(mcp):
    """prompts://agent/system served a fork of the agent prompt that was never
    wired to an agent: it named delegate_research (a RecursiveAgent-only tool
    no MCP client has) and mandated `[doc_id](uri)` citations, which
    agents/prompts.py deliberately replaced with 【doc_id】."""
    import kb_mcp.server.mcp_prompts as prompts

    assert not hasattr(prompts, "BASE_SYSTEM_PROMPT")


@pytest.mark.asyncio
async def test_agent_system_resource_not_advertised(mcp):
    uris = {str(r.uri) for r in await mcp.list_resources()}
    assert "prompts://agent/system" not in uris


# --- dependency-free self-test pair --------------------------------------

@pytest.mark.asyncio
async def test_selftest_resource_is_registered(mcp):
    uris = {str(r.uri) for r in await mcp.list_resources()}
    assert "kb://sys/selftest" in uris


@pytest.mark.asyncio
async def test_selftest_resource_reads_without_a_knowledge_base(mcp):
    contents = list(await mcp.read_resource("kb://sys/selftest"))
    payload = json.loads(contents[0].content)

    assert payload["status"] == "ok"
    assert payload["server"] == "kb-mcp"


@pytest.mark.asyncio
async def test_selftest_prompt_renders_its_argument(mcp):
    result = await mcp.get_prompt("selftest", {"echo": "hello"})

    assert "hello" in result.messages[0].content.text


@pytest.mark.asyncio
async def test_selftest_prompt_has_a_working_default(mcp):
    result = await mcp.get_prompt("selftest", {})

    assert "ping" in result.messages[0].content.text
