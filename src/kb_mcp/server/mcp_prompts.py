"""Orientation text the MCP server advertises to clients.

The live agent prompts are in kb_mcp/agents/prompts.py; this module holds
only what the server itself publishes.
"""


def get_server_instructions() -> str:
    """Instructions advertised in the MCP `initialize` response.

    This is the one piece of orientation every MCP client shows the model
    without a human attaching anything, so the corpus description lives here
    rather than only in the `kb://sys/domain_context` resource (which clients
    do not auto-read).

    Deliberately does NOT repeat anything the tool descriptions already carry
    - how to read STATUS, when kb_research is worth its cost. Tool descriptions
    are always in context (a client cannot call tools without them) while these
    instructions are best-effort, so a sentence in both is paid for twice and
    insures nothing. What belongs here is the cross-tool orientation that has
    no natural home in any single tool's description.
    """
    from ..config import get_server_config

    graph_section = ""
    if not get_server_config()['hide_graph']:
        graph_section = """
### KNOWLEDGE GRAPH
Alongside the document store, concepts and entities are linked by typed relationships.
- `kb_lookup_node("Name" or UUID)` - a concept's neighbours and the documents mentioning it.
- `kb_find_path("A", "B")` - how two concepts are connected.
- `kb_node_relation_evidence(relation_id)` - the text backing a relationship.
When a result names a specific component, subsystem or person, `kb_lookup_node` gives its
structural context. Graph output embeds `CMD:` hints - literal next calls you can make.
"""

    return f"""Knowledge base for the Mu2e experiment at Fermilab (search for muon-to-electron
conversion). Use these tools for any question about Mu2e hardware, subsystems, analysis,
operations, collaboration decisions, or the surrounding literature.

### PRIMARY SOURCES
Most questions should be answered from these two:
- **`mu2e-docdb`** (~24,000 docs) - the collaboration's document database: technical notes,
  talks, design reviews, run plans, meeting slides. The authoritative record for anything
  Mu2e-specific.
- **`mu2e-wiki`** (~500 docs) - the collaboration wiki: operating procedures, how-tos,
  naming conventions, current subsystem status. Best for "how do we do X" and for
  orientation before diving into DocDB.

Supporting sources: `inspire-hep` (~5,000 published papers, for external and theory
context), `MeetingTranscripts`, `upload`, `test-flow`.

Start with `kb_search`, and filter to `mu2e-docdb` or `mu2e-wiki` when the question is
clearly Mu2e-internal and `inspire-hep` papers would be noise.
{graph_section}"""
