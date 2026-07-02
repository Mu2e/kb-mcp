"""Protected web interface for knowledge graph exploration."""

import logging
from html import escape as html_escape
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.requests import Request

from ..auth import WebSessionManager
from .. import html_templates

logger = logging.getLogger(__name__)


def setup_graph_routes(app, session_manager: WebSessionManager):
    """Set up graph exploration routes."""

    async def graph_search(request: Request):
        """Graph search page with node search and path finding."""
        # Check authentication first
        session_data = await session_manager.get_session_data(request)
        if not session_data:
            return RedirectResponse(url="/login?redirect=/web/graph")

        # Get username from authenticated session
        username = session_data.get("username")

        content = """
        <h1>Knowledge Graph Explorer</h1>
        <p><a href="/web">← Back to Document List</a></p>

        <div class="card">
            <h2>Node Search</h2>
            <form id="node-search-form" onsubmit="searchNode(event)" style="margin-bottom: 20px;">
                <div style="display: flex; gap: 10px; align-items: flex-end;">
                    <div style="flex: 1;">
                        <label for="node-name" style="display: block; margin-bottom: 5px; font-size: 14px; color: #666;">Node Name:</label>
                        <input type="text" id="node-name" name="name" placeholder="Enter node name..." style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px;">
                    </div>
                    <div style="min-width: 200px;">
                        <label for="node-type" style="display: block; margin-bottom: 5px; font-size: 14px; color: #666;">Type (optional):</label>
                        <input type="text" id="node-type" name="type" placeholder="e.g., Person, Concept..." style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px;">
                    </div>
                    <button type="submit" style="padding: 10px 30px; background-color: #2196F3; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px;">Search</button>
                </div>
            </form>
            <div id="node-search-result" style="margin-top: 20px;"></div>
        </div>

        <div class="card" style="margin-top: 20px;">
            <h2>Path Finder</h2>
            <p style="color: #666; font-size: 14px; margin-bottom: 10px;">Find all paths between two nodes in the knowledge graph.</p>
            <form id="path-search-form" onsubmit="searchPath(event)" style="margin-bottom: 20px;">
                <div style="display: flex; gap: 10px; align-items: flex-end; margin-bottom: 10px;">
                    <div style="flex: 1;">
                        <label for="start-node-name" style="display: block; margin-bottom: 5px; font-size: 14px; color: #666;">Start Node Name:</label>
                        <input type="text" id="start-node-name" name="start_node_name" placeholder="Enter start node name..." style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px;">
                    </div>
                    <div style="flex: 1;">
                        <label for="end-node-name" style="display: block; margin-bottom: 5px; font-size: 14px; color: #666;">Target Node Name:</label>
                        <input type="text" id="end-node-name" name="end_node_name" placeholder="Enter target node name..." style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px;">
                    </div>
                </div>
                <div style="display: flex; gap: 10px; align-items: flex-end;">
                    <div style="min-width: 150px;">
                        <label for="max-depth" style="display: block; margin-bottom: 5px; font-size: 14px; color: #666;">Max Depth:</label>
                        <input type="number" id="max-depth" name="max_depth" value="4" min="1" max="10" style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px;">
                    </div>
                    <div style="min-width: 150px;">
                        <label for="path-limit" style="display: block; margin-bottom: 5px; font-size: 14px; color: #666;">Max Paths:</label>
                        <input type="number" id="path-limit" name="limit" value="5" min="1" max="20" style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px;">
                    </div>
                    <button type="submit" style="padding: 10px 30px; background-color: #4CAF50; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px;">Find Paths</button>
                </div>
            </form>
            <div id="path-search-result" style="margin-top: 20px;"></div>
        </div>

        <script>
        async function searchNode(event) {
            event.preventDefault();

            const resultDiv = document.getElementById('node-search-result');
            const name = document.getElementById('node-name').value.trim();
            const type = document.getElementById('node-type').value.trim();

            if (!name) {
                resultDiv.innerHTML = '<div class="error-box">Please enter a node name.</div>';
                return;
            }

            resultDiv.innerHTML = '<div class="info-box">Searching...</div>';

            try {
                let url = `/api/graph/node?name=${encodeURIComponent(name)}`;
                if (type) {
                    url += `&type=${encodeURIComponent(type)}`;
                }

                const response = await fetch(url);
                const data = await response.json();

                if (response.ok && data.node) {
                    // Redirect to node detail page
                    window.location.href = `/web/graph/node/${data.node.id}`;
                } else {
                    resultDiv.innerHTML = `<div class="error-box">Node not found: ${data.error || 'Unknown error'}</div>`;
                }
            } catch (error) {
                resultDiv.innerHTML = `<div class="error-box">Error: ${error.message}</div>`;
            }
        }

        async function searchPath(event) {
            event.preventDefault();

            const resultDiv = document.getElementById('path-search-result');
            const startNodeName = document.getElementById('start-node-name').value.trim();
            const endNodeName = document.getElementById('end-node-name').value.trim();
            const maxDepth = document.getElementById('max-depth').value;
            const limit = document.getElementById('path-limit').value;

            if (!startNodeName || !endNodeName) {
                resultDiv.innerHTML = '<div class="error-box">Please enter both start and target node names.</div>';
                return;
            }

            // Update URL with search parameters
            const url = new URL(window.location);
            url.searchParams.set('start', startNodeName);
            url.searchParams.set('end', endNodeName);
            url.searchParams.set('depth', maxDepth);
            url.searchParams.set('limit', limit);
            window.history.pushState({}, '', url);

            resultDiv.innerHTML = '<div class="info-box">Looking up nodes...</div>';

            try {
                // Look up start node
                const startResponse = await fetch(`/api/graph/node?name=${encodeURIComponent(startNodeName)}`);
                const startData = await startResponse.json();

                if (!startResponse.ok || !startData.node) {
                    resultDiv.innerHTML = `<div class="error-box">Start node not found: ${startData.error || 'Unknown error'}</div>`;
                    return;
                }

                // Look up end node
                const endResponse = await fetch(`/api/graph/node?name=${encodeURIComponent(endNodeName)}`);
                const endData = await endResponse.json();

                if (!endResponse.ok || !endData.node) {
                    resultDiv.innerHTML = `<div class="error-box">Target node not found: ${endData.error || 'Unknown error'}</div>`;
                    return;
                }

                const startNode = startData.node;
                const endNode = endData.node;

                // Display found nodes
                let html = `<div style="margin-bottom: 20px; padding: 15px; background: #f0f8ff; border: 1px solid #2196F3; border-radius: 4px;">`;
                html += `<h3 style="margin-top: 0;">Found Nodes:</h3>`;
                html += `<div style="display: flex; gap: 20px; margin-top: 10px;">`;
                html += `<div style="flex: 1;">`;
                html += `<strong>Start:</strong> <a href="/web/graph/node/${startNode.id}" style="color: #2196F3; text-decoration: none;">${startNode.name}</a>`;
                html += `<div style="color: #666; font-size: 12px; margin-top: 4px;">Type: ${startNode.type}</div>`;
                html += `<div style="color: #666; font-size: 12px;">ID: <code style="font-size: 10px;">${startNode.id}</code></div>`;
                html += `</div>`;
                html += `<div style="flex: 1;">`;
                html += `<strong>Target:</strong> <a href="/web/graph/node/${endNode.id}" style="color: #2196F3; text-decoration: none;">${endNode.name}</a>`;
                html += `<div style="color: #666; font-size: 12px; margin-top: 4px;">Type: ${endNode.type}</div>`;
                html += `<div style="color: #666; font-size: 12px;">ID: <code style="font-size: 10px;">${endNode.id}</code></div>`;
                html += `</div>`;
                html += `</div></div>`;

                resultDiv.innerHTML = html + '<div class="info-box">Finding paths...</div>';

                // Find paths
                const pathUrl = `/api/graph/paths?start_node_id=${encodeURIComponent(startNode.id)}&end_node_id=${encodeURIComponent(endNode.id)}&max_depth=${maxDepth}&limit=${limit}`;
                const pathResponse = await fetch(pathUrl);
                const pathData = await pathResponse.json();

                if (pathResponse.ok && pathData.paths) {
                    if (pathData.paths.length === 0) {
                        resultDiv.innerHTML = html + '<div class="info-box">No paths found between these nodes.</div>';
                    } else {
                        let pathsHtml = `<h3>Found ${pathData.paths.length} path(s):</h3>`;
                        pathData.paths.forEach((path, idx) => {
                            pathsHtml += `<div style="margin: 20px 0; padding: 15px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px;">`;
                            pathsHtml += `<div style="margin-bottom: 10px; font-weight: 600; color: #2196F3;">Path ${idx + 1} (Length: ${path.length})</div>`;
                            pathsHtml += `<div style="display: flex; flex-wrap: wrap; align-items: center; gap: 10px;">`;

                            path.chain.forEach((element, i) => {
                                if (element.element === 'node') {
                                    pathsHtml += `<div style="display: inline-flex; align-items: center; padding: 8px 12px; background: white; border: 2px solid #2196F3; border-radius: 4px;">`;
                                    pathsHtml += `<a href="/web/graph/node/${element.id}" style="text-decoration: none; color: #2196F3; font-weight: 500;">${element.name}</a>`;
                                    pathsHtml += `<span style="margin-left: 6px; color: #666; font-size: 12px;">(${element.label})</span>`;
                                    pathsHtml += `</div>`;
                                } else if (element.element === 'relationship') {
                                    const arrow = element.direction === 'forward' ? '→' : '←';
                                    pathsHtml += `<div style="display: inline-flex; align-items: center; color: #666; font-size: 14px;">`;
                                    pathsHtml += `<span style="margin: 0 4px;">${arrow}</span>`;
                                    if (element.id) {
                                        pathsHtml += `<a href="/web/graph/evidence/${element.id}" style="font-style: italic; text-decoration: none; color: #666; border-bottom: 1px dashed #999;" onmouseover="this.style.color='#2196F3'; this.style.borderColor='#2196F3';" onmouseout="this.style.color='#666'; this.style.borderColor='#999';">${element.verb}</a>`;
                                    } else {
                                        pathsHtml += `<span style="font-style: italic;">${element.verb}</span>`;
                                    }
                                    pathsHtml += `<span style="margin: 0 4px;">${arrow}</span>`;
                                    pathsHtml += `</div>`;
                                }
                            });

                            pathsHtml += `</div></div>`;
                        });
                        resultDiv.innerHTML = html + pathsHtml;
                    }
                } else {
                    resultDiv.innerHTML = html + `<div class="error-box">Error finding paths: ${pathData.error || 'Unknown error'}</div>`;
                }
            } catch (error) {
                resultDiv.innerHTML = `<div class="error-box">Error: ${error.message}</div>`;
            }
        }

        // Populate form from URL parameters on page load
        document.addEventListener('DOMContentLoaded', function() {
            const urlParams = new URLSearchParams(window.location.search);
            const startNode = urlParams.get('start');
            const endNode = urlParams.get('end');
            const depth = urlParams.get('depth');
            const limit = urlParams.get('limit');

            if (startNode) {
                document.getElementById('start-node-name').value = startNode;
            }
            if (endNode) {
                document.getElementById('end-node-name').value = endNode;
            }
            if (depth) {
                document.getElementById('max-depth').value = depth;
            }
            if (limit) {
                document.getElementById('path-limit').value = limit;
            }

            // Auto-search if both nodes are provided
            if (startNode && endNode) {
                // Trigger the search
                const form = document.getElementById('path-search-form');
                if (form) {
                    form.dispatchEvent(new Event('submit', { cancelable: true }));
                }
            }
        });
        </script>
        """

        return HTMLResponse(html_templates.base_template(
            "Knowledge Graph Explorer",
            content,
            None,
            username
        ))
    app.add_route("/web/graph", graph_search)

    async def node_detail(request: Request):
        """View detailed information about a specific node."""
        # Check authentication first
        session_data = await session_manager.get_session_data(request)
        if not session_data:
            return RedirectResponse(url="/login?redirect=/web/graph")

        # Get username from authenticated session
        username = session_data.get("username")

        # Get node ID from path parameters
        node_id = request.path_params.get("node_id")
        if not node_id:
            return HTMLResponse(
                html_templates.base_template(
                    "Error",
                    '<div class="error-box"><h2>Error</h2><p>No node ID provided.</p><p><a href="/web/graph">← Back to Graph Explorer</a></p></div>',
                    None,
                    username
                ),
                status_code=400
            )

        try:
            from ....kb.graph.queries import get_node
            from ....kb import get as get_doc

            # Get node information
            node_data = get_node(id=node_id)
            if not node_data:
                return HTMLResponse(
                    html_templates.base_template(
                        "Node Not Found",
                        '<div class="error-box"><h2>Node Not Found</h2><p>The requested node could not be found.</p><p><a href="/web/graph">← Back to Graph Explorer</a></p></div>',
                        None,
                        username
                    ),
                    status_code=404
                )

            node = node_data["node"]
            outgoing = node_data["outgoing_relations"]
            incoming = node_data["incoming_relations"]
            stats = node_data["statistics"]
            linked_docs = node_data.get("linked_documents", [])

            # Build aliases display
            aliases_html = ""
            if node["aliases"]:
                aliases_list = ", ".join(html_escape(a) for a in node["aliases"])
                aliases_html = f'<tr><th>Aliases</th><td>{aliases_list}</td></tr>'

            # Build metadata display
            meta_html = ""
            if node["meta"]:
                meta_str = "<br>".join(f"{html_escape(k)}: {html_escape(str(v))}" for k, v in node["meta"].items())
                meta_html = f'<tr><th>Metadata</th><td>{meta_str}</td></tr>'

            # Build linked documents display
            linked_docs_html = ""
            if linked_docs:
                docs_list = ""
                for doc_id in linked_docs[:10]:  # Limit to first 10
                    try:
                        doc = get_doc(uid=doc_id)
                        if doc:
                            doc_label = doc.title or doc.doc_id or doc.id[:8]
                            docs_list += f'<li><a href="/web/document/{doc_id}">{html_escape(doc_label)}</a></li>'
                        else:
                            docs_list += f'<li><code>{doc_id[:8]}...</code></li>'
                    except Exception:
                        docs_list += f'<li><code>{doc_id[:8]}...</code></li>'

                if len(linked_docs) > 10:
                    docs_list += f'<li><em>... and {len(linked_docs) - 10} more</em></li>'

                linked_docs_html = f"""
            <div class="card" style="margin-top: 20px;">
                <h2>Linked Documents ({len(linked_docs)})</h2>
                <p style="color: #666; font-size: 14px; margin-bottom: 10px;">Documents that mention this node:</p>
                <ul style="margin: 0; padding-left: 20px;">
                    {docs_list}
                </ul>
            </div>
                """

            # Build outgoing relations table
            outgoing_html = ""
            if outgoing:
                outgoing_rows = ""
                for rel in outgoing:
                    target = rel["target_node"]
                    relation_id = rel["relation_id"]
                    evidence_link = f'<a href="/web/graph/evidence/{relation_id}" style="text-decoration: none; color: #2196F3;" onclick="event.stopPropagation();">{rel["evidence_count"]}</a>' if rel["evidence_count"] > 0 else str(rel["evidence_count"])
                    outgoing_rows += f"""
                <tr style="border-bottom: 1px solid #eee; cursor: pointer;" onclick="window.location.href='/web/graph/node/{target["id"]}'" onmouseover="this.style.backgroundColor='#f8f9fa'" onmouseout="this.style.backgroundColor='transparent'">
                    <td style="padding: 12px 10px; font-style: italic; color: #666;">{html_escape(rel["verb"])}</td>
                    <td style="padding: 12px 10px;">
                        <a href="/web/graph/node/{target["id"]}" style="text-decoration: none; color: #2196F3;">{html_escape(target["name"])}</a>
                    </td>
                    <td style="padding: 12px 10px; color: #666;">{html_escape(target["type"])}</td>
                    <td style="padding: 12px 10px; color: #666; text-align: center;">{evidence_link}</td>
                    <td style="padding: 12px 10px; color: #666; text-align: center;">{rel["max_confidence"]:.2f}</td>
                </tr>
                """

                outgoing_html = f"""
            <div class="card" style="margin-top: 20px;">
                <h2>Outgoing Relations ({len(outgoing)})</h2>
                <p style="color: #666; font-size: 14px; margin-bottom: 10px;">Relations where this node is the source:</p>
                <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                    <thead>
                        <tr style="background-color: #f5f5f5; border-bottom: 2px solid #ddd;">
                            <th style="text-align: left; padding: 10px; font-weight: 600;">Verb</th>
                            <th style="text-align: left; padding: 10px; font-weight: 600;">Target Node</th>
                            <th style="text-align: left; padding: 10px; font-weight: 600;">Type</th>
                            <th style="text-align: center; padding: 10px; font-weight: 600;">Evidence</th>
                            <th style="text-align: center; padding: 10px; font-weight: 600;">Confidence</th>
                        </tr>
                    </thead>
                    <tbody>
                        {outgoing_rows}
                    </tbody>
                </table>
            </div>
                """

            # Build incoming relations table
            incoming_html = ""
            if incoming:
                incoming_rows = ""
                for rel in incoming:
                    source = rel["source_node"]
                    relation_id = rel["relation_id"]
                    evidence_link = f'<a href="/web/graph/evidence/{relation_id}" style="text-decoration: none; color: #2196F3;" onclick="event.stopPropagation();">{rel["evidence_count"]}</a>' if rel["evidence_count"] > 0 else str(rel["evidence_count"])
                    incoming_rows += f"""
                <tr style="border-bottom: 1px solid #eee; cursor: pointer;" onclick="window.location.href='/web/graph/node/{source["id"]}'" onmouseover="this.style.backgroundColor='#f8f9fa'" onmouseout="this.style.backgroundColor='transparent'">
                    <td style="padding: 12px 10px;">
                        <a href="/web/graph/node/{source["id"]}" style="text-decoration: none; color: #2196F3;">{html_escape(source["name"])}</a>
                    </td>
                    <td style="padding: 12px 10px; color: #666;">{html_escape(source["type"])}</td>
                    <td style="padding: 12px 10px; font-style: italic; color: #666;">{html_escape(rel["verb"])}</td>
                    <td style="padding: 12px 10px; color: #666; text-align: center;">{evidence_link}</td>
                    <td style="padding: 12px 10px; color: #666; text-align: center;">{rel["max_confidence"]:.2f}</td>
                </tr>
                """

                incoming_html = f"""
            <div class="card" style="margin-top: 20px;">
                <h2>Incoming Relations ({len(incoming)})</h2>
                <p style="color: #666; font-size: 14px; margin-bottom: 10px;">Relations where this node is the target:</p>
                <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                    <thead>
                        <tr style="background-color: #f5f5f5; border-bottom: 2px solid #ddd;">
                            <th style="text-align: left; padding: 10px; font-weight: 600;">Source Node</th>
                            <th style="text-align: left; padding: 10px; font-weight: 600;">Type</th>
                            <th style="text-align: left; padding: 10px; font-weight: 600;">Verb</th>
                            <th style="text-align: center; padding: 10px; font-weight: 600;">Evidence</th>
                            <th style="text-align: center; padding: 10px; font-weight: 600;">Confidence</th>
                        </tr>
                    </thead>
                    <tbody>
                        {incoming_rows}
                    </tbody>
                </table>
            </div>
                """

            content = f"""
        <h1>Node: {html_escape(node["name"])}</h1>
        <p><a href="/web/graph">← Back to Graph Explorer</a></p>

        <div class="card">
            <h2>Node Information</h2>
            <table>
                <tr><th>ID</th><td><code>{node["id"]}</code></td></tr>
                <tr><th>Name</th><td>{html_escape(node["name"])}</td></tr>
                <tr><th>Type</th><td>{html_escape(node["type"])}</td></tr>
                {aliases_html}
                <tr><th>Created Time</th><td>{node["created_time"]}</td></tr>
                {meta_html}
            </table>
        </div>

        <div class="card" style="margin-top: 20px;">
            <h2>Statistics</h2>
            <table>
                <tr><th>Total Relations</th><td>{stats["total_relations"]}</td></tr>
                <tr><th>Outgoing Relations</th><td>{stats["total_outgoing"]}</td></tr>
                <tr><th>Incoming Relations</th><td>{stats["total_incoming"]}</td></tr>
                <tr><th>Linked Documents</th><td>{stats["total_documents"]}</td></tr>
            </table>
        </div>

        {outgoing_html}
        {incoming_html}
        {linked_docs_html}
            """

            return HTMLResponse(html_templates.base_template(
                f"Node: {node['name']}",
                content,
                None,
                username
            ))

        except Exception as e:
            logger.error(f"Error fetching node {node_id}: {e}", exc_info=True)
            error_msg = html_escape(str(e))
            return HTMLResponse(
                html_templates.base_template(
                    "Error",
                    f'<div class="error-box"><h2>Error</h2><p>{error_msg}</p><p><a href="/web/graph">← Back to Graph Explorer</a></p></div>',
                    None,
                    username
                ),
                status_code=500
            )
    app.add_route("/web/graph/node/{node_id}", node_detail)

    async def evidence_detail(request: Request):
        """View evidence for a specific relation."""
        # Check authentication first
        session_data = await session_manager.get_session_data(request)
        if not session_data:
            return RedirectResponse(url="/login?redirect=/web/graph")

        # Get username from authenticated session
        username = session_data.get("username")

        # Get relation ID from path parameters
        relation_id = request.path_params.get("relation_id")
        if not relation_id:
            return HTMLResponse(
                html_templates.base_template(
                    "Error",
                    '<div class="error-box"><h2>Error</h2><p>No relation ID provided.</p><p><a href="/web/graph">← Back to Graph Explorer</a></p></div>',
                    None,
                    username
                ),
                status_code=400
            )

        try:
            from ....kb.graph.db_models import GraphRelation, GraphRelationEvidence, GraphNode, GraphNodeType, GraphVerb
            from ....kb import get as get_doc
            from ....kb.database import get_db_session

            with get_db_session() as session:
                # Get the relation with source, target, and verb info
                relation = session.query(
                    GraphRelation,
                    GraphNode.name.label('source_name'),
                    GraphNodeType.label.label('source_type'),
                    GraphNode.id.label('source_id')
                ).join(
                    GraphNode, GraphRelation.source_id == GraphNode.id
                ).join(
                    GraphNodeType, GraphNode.type_id == GraphNodeType.id
                ).filter(GraphRelation.id == relation_id).first()

                if not relation:
                    return HTMLResponse(
                        html_templates.base_template(
                            "Relation Not Found",
                            '<div class="error-box"><h2>Relation Not Found</h2><p>The requested relation could not be found.</p><p><a href="/web/graph">← Back to Graph Explorer</a></p></div>',
                            None,
                            username
                        ),
                        status_code=404
                    )

                rel_obj, source_name, source_type, source_id = relation

                # Get target and verb info
                target = session.query(
                    GraphNode.name,
                    GraphNode.id,
                    GraphNodeType.label
                ).join(
                    GraphNodeType, GraphNode.type_id == GraphNodeType.id
                ).filter(GraphNode.id == rel_obj.target_id).first()

                verb = session.query(GraphVerb.name).filter(GraphVerb.id == rel_obj.verb_id).first()

                if not target or not verb:
                    raise ValueError("Incomplete relation data")

                target_name, target_id, target_type = target
                verb_name = verb[0]

                # Get all evidence for this relation
                evidence_records = session.query(GraphRelationEvidence).filter(
                    GraphRelationEvidence.relation_id == relation_id
                ).order_by(GraphRelationEvidence.confidence.desc()).all()

                # Build evidence list
                evidence_html = ""
                if evidence_records:
                    evidence_rows = ""
                    for evidence in evidence_records:
                        # Get document info if available
                        doc_link = "N/A"
                        if evidence.document_id:
                            try:
                                doc = get_doc(uid=evidence.document_id)
                                if doc:
                                    doc_label = doc.title or doc.doc_id or evidence.document_id[:8]
                                    doc_link = f'<a href="/web/document/{evidence.document_id}" style="text-decoration: none; color: #2196F3;">{html_escape(doc_label)}</a>'
                                else:
                                    doc_link = f'<code>{evidence.document_id[:8]}...</code>'
                            except Exception:
                                doc_link = f'<code>{evidence.document_id[:8]}...</code>'

                        evidence_text = html_escape(evidence.evidence_text) if evidence.evidence_text else "<em>No text provided</em>"
                        model = html_escape(evidence.extraction_model) if evidence.extraction_model else "N/A"
                        confidence_pct = f"{evidence.confidence * 100:.1f}%" if evidence.confidence else "N/A"
                        created_time = str(evidence.created_time) if evidence.created_time else "N/A"

                        evidence_rows += f"""
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 12px 10px; max-width: 400px;">{evidence_text}</td>
                        <td style="padding: 12px 10px;">{doc_link}</td>
                        <td style="padding: 12px 10px; text-align: center;">{confidence_pct}</td>
                        <td style="padding: 12px 10px; font-size: 12px;">{model}</td>
                        <td style="padding: 12px 10px; font-size: 12px;">{created_time}</td>
                    </tr>
                        """

                    evidence_html = f"""
                <div class="card" style="margin-top: 20px;">
                    <h2>Evidence ({len(evidence_records)})</h2>
                    <p style="color: #666; font-size: 14px; margin-bottom: 10px;">Supporting evidence for this relation:</p>
                    <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                        <thead>
                            <tr style="background-color: #f5f5f5; border-bottom: 2px solid #ddd;">
                                <th style="text-align: left; padding: 10px; font-weight: 600;">Evidence Text</th>
                                <th style="text-align: left; padding: 10px; font-weight: 600;">Source Document</th>
                                <th style="text-align: center; padding: 10px; font-weight: 600;">Confidence</th>
                                <th style="text-align: left; padding: 10px; font-weight: 600;">Model</th>
                                <th style="text-align: left; padding: 10px; font-weight: 600;">Created</th>
                            </tr>
                        </thead>
                        <tbody>
                            {evidence_rows}
                        </tbody>
                    </table>
                </div>
                    """
                else:
                    evidence_html = '<div class="info-box" style="margin-top: 20px;">No evidence found for this relation.</div>'

            content = f"""
        <h1>Relation Evidence</h1>
        <p><a href="/web/graph">← Back to Graph Explorer</a></p>

        <div class="card">
            <h2>Relation Information</h2>
            <table>
                <tr><th>ID</th><td><code>{relation_id}</code></td></tr>
                <tr>
                    <th>Source</th>
                    <td>
                        <a href="/web/graph/node/{source_id}" style="text-decoration: none; color: #2196F3;">{html_escape(source_name)}</a>
                        <span style="color: #666; font-size: 14px; margin-left: 8px;">({html_escape(source_type)})</span>
                    </td>
                </tr>
                <tr><th>Verb</th><td><em>{html_escape(verb_name)}</em></td></tr>
                <tr>
                    <th>Target</th>
                    <td>
                        <a href="/web/graph/node/{target_id}" style="text-decoration: none; color: #2196F3;">{html_escape(target_name)}</a>
                        <span style="color: #666; font-size: 14px; margin-left: 8px;">({html_escape(target_type)})</span>
                    </td>
                </tr>
                <tr><th>Created Time</th><td>{rel_obj.created_time}</td></tr>
            </table>
        </div>

        {evidence_html}
            """

            return HTMLResponse(html_templates.base_template(
                f"Evidence: {source_name} → {target_name}",
                content,
                None,
                username
            ))

        except Exception as e:
            logger.error(f"Error fetching evidence for relation {relation_id}: {e}", exc_info=True)
            error_msg = html_escape(str(e))
            return HTMLResponse(
                html_templates.base_template(
                    "Error",
                    f'<div class="error-box"><h2>Error</h2><p>{error_msg}</p><p><a href="/web/graph">← Back to Graph Explorer</a></p></div>',
                    None,
                    username
                ),
                status_code=500
            )
    app.add_route("/web/graph/evidence/{relation_id}", evidence_detail)
