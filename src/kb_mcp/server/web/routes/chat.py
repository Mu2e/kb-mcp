"""Chat interface routes for agent-based conversations."""

import asyncio
import json
import logging
import secrets
from datetime import datetime
from typing import Dict, Any, Optional
from starlette.responses import StreamingResponse, HTMLResponse, JSONResponse
from starlette.requests import Request

from ..auth import WebSessionManager
from .. import html_templates
from ....agents.notebook_agent import NotebookAgent
from ....llm.llm import get_openai_client
from ...mcp import kb_get
from ....config import get_agent_config

logger = logging.getLogger(__name__)

# In-memory chat sessions: session_id -> ChatSession
active_chat_sessions: Dict[str, 'ChatSession'] = {}


class ChatSession:
    """Represents an active chat session."""

    def __init__(self, session_id: str, username: str, document_context: Optional[Dict] = None):
        self.session_id = session_id
        self.username = username
        self.created_at = datetime.utcnow()
        self.document_context = document_context  # Optional doc_id and text
        self.message_history = []  # List of {role, content, timestamp}
        self.agent = None  # Will hold agent instance
        self.mcp_session = None
        self._mcp_task = None       # long-lived asyncio.Task holding the stdio context
        self._mcp_shutdown = None   # asyncio.Event to signal task to exit
        self._mcp_ready = None      # asyncio.Event set when session is ready

    async def start_mcp(self, mode: str, async_client, agent_model: str, callback):
        """Start MCP subprocess in a long-lived background task and initialize agent."""
        import sys, os
        from mcp import ClientSession as MCPClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._mcp_shutdown = asyncio.Event()
        self._mcp_ready = asyncio.Event()
        init_error: list = []

        async def _mcp_lifetime():
            server_params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "kb_mcp.server.mcp_stdio"],
                env=os.environ.copy(),
            )
            try:
                async with stdio_client(server_params) as (read, write):
                    async with MCPClientSession(read, write) as session:
                        await session.initialize()
                        self.mcp_session = session

                        if mode == 'plain':
                            from ....agents.plain_agent import PlainAgent
                            self.agent = PlainAgent(
                                session=session, client=async_client,
                                depth=1, agent_id="WebChat", callback=callback,
                            )
                        else:
                            self.agent = NotebookAgent(
                                session=session, client=async_client,
                                depth=1, agent_id="WebChat", max_depth=2, callback=callback,
                            )
                        await self.agent.initialize_tools()
                        self._mcp_ready.set()
                        # Stay alive until session cleanup
                        await self._mcp_shutdown.wait()
            except Exception as e:
                init_error.append(e)
                self._mcp_ready.set()  # unblock waiters even on error

        self._mcp_task = asyncio.get_event_loop().create_task(_mcp_lifetime())
        await self._mcp_ready.wait()
        if init_error:
            raise init_error[0]

    async def cleanup(self):
        """Clean up MCP connection and resources."""
        if self._mcp_shutdown:
            self._mcp_shutdown.set()
        if self._mcp_task:
            try:
                await asyncio.wait_for(self._mcp_task, timeout=5.0)
            except Exception as e:
                logger.error(f"Error cleaning up MCP task for session {self.session_id}: {e}")
            finally:
                self._mcp_task = None
        self.agent = None
        self.mcp_session = None


def setup_chat_routes(app, session_manager: WebSessionManager, require_auth_html, require_auth_api):
    """Setup chat interface routes."""

    async def chat_page(request: Request):
        """Render chat interface page."""
        session_data, redirect = await require_auth_html(request, session_manager)
        if redirect:
            return redirect

        username = session_data.get('username', 'Unknown')

        # Check if starting chat with document context
        doc_id = request.query_params.get('doc_id')
        doc_title = request.query_params.get('title', 'Unknown Document')

        from ....config import get_agent_config
        agent_model = get_agent_config()['agent_model']

        # Build chat page HTML
        content = f"""
        <style>
            /* Markdown styling for assistant messages */
            #messages .markdown-content {{
                line-height: 1.6;
            }}
            
            #messages .markdown-content h1,
            #messages .markdown-content h2,
            #messages .markdown-content h3,
            #messages .markdown-content h4,
            #messages .markdown-content h5,
            #messages .markdown-content h6 {{
                margin-top: 1em;
                margin-bottom: 0.5em;
                font-weight: 600;
            }}
            
            #messages .markdown-content h1 {{ font-size: 1.5em; }}
            #messages .markdown-content h2 {{ font-size: 1.3em; }}
            #messages .markdown-content h3 {{ font-size: 1.1em; }}
            
            #messages .markdown-content p {{
                margin: 0.5em 0;
            }}
            
            #messages .markdown-content ul,
            #messages .markdown-content ol {{
                margin: 0.5em 0;
                padding-left: 2em;
            }}
            
            #messages .markdown-content li {{
                margin: 0.25em 0;
            }}
            
            #messages .markdown-content code {{
                background-color: rgba(0, 0, 0, 0.05);
                padding: 0.2em 0.4em;
                border-radius: 3px;
                font-family: 'Courier New', monospace;
                font-size: 0.9em;
            }}
            
            #messages .markdown-content pre {{
                background-color: rgba(0, 0, 0, 0.05);
                padding: 1em;
                border-radius: 4px;
                overflow-x: auto;
                margin: 0.5em 0;
            }}
            
            #messages .markdown-content pre code {{
                background-color: transparent;
                padding: 0;
            }}
            
            #messages .markdown-content blockquote {{
                border-left: 3px solid #ccc;
                padding-left: 1em;
                margin: 0.5em 0;
                color: #666;
                font-style: italic;
            }}
            
            #messages .markdown-content a {{
                color: #2196F3;
                text-decoration: underline;
            }}
            
            #messages .markdown-content a:hover {{
                color: #1976D2;
            }}
            
            #messages .markdown-content table {{
                border-collapse: collapse;
                margin: 0.5em 0;
                width: 100%;
            }}
            
            #messages .markdown-content table th,
            #messages .markdown-content table td {{
                border: 1px solid #ddd;
                padding: 0.5em;
                text-align: left;
            }}
            
            #messages .markdown-content table th {{
                background-color: rgba(0, 0, 0, 0.05);
                font-weight: 600;
            }}
            
            #messages .markdown-content strong {{
                font-weight: 600;
            }}
            
            #messages .markdown-content em {{
                font-style: italic;
            }}

            #chat-status-working.working::before {{
                content: '';
                display: inline-block;
                width: 8px;
                height: 8px;
                margin-right: 8px;
                border-radius: 50%;
                background: #1565c0;
                animation: chatPulse 1s infinite ease-in-out;
                vertical-align: middle;
            }}

            @keyframes chatPulse {{
                0% {{ opacity: 0.35; transform: scale(0.9); }}
                50% {{ opacity: 1; transform: scale(1.1); }}
                100% {{ opacity: 0.35; transform: scale(0.9); }}
            }}
        </style>
        
        <div style="display: flex; align-items: baseline; gap: 20px; margin-bottom: 10px;">
            <h1 style="margin: 0;">Chat</h1>
            <span style="color: #666; font-size: 13px;">Model: <code>{agent_model}</code></span>
            <span style="font-size: 13px; color: #666;">
                <label for="agent-mode-select">Mode:</label>
                <select id="agent-mode-select" style="margin-left: 4px; font-size: 13px; padding: 2px 6px;">
                    <option value="notebook" {"" if doc_id else "selected"}>Notebook agent (multi-step scratchpad)</option>
                    <option value="plain" {"selected" if doc_id else ""}>Plain agent (tools, single-pass)</option>
                    <option value="direct">Direct (no tools)</option>
                </select>
            </span>
            <span id="context-size" style="font-size: 13px; color: rgba(100,100,100,0.8); margin-left: auto;"></span>
        </div>

        <div id="chat-container" class="card">
            <div id="messages" style="max-height: 500px; overflow-y: auto; margin-bottom: 20px; padding: 10px; border: 1px solid #ddd;">
                <!-- Messages will appear here -->
            </div>

            <form id="chat-form" onsubmit="sendMessage(event)" style="display: flex; gap: 8px; align-items: flex-end;">
                <textarea id="message-input" placeholder="Ask a question..." rows="1"
                    style="flex: 1; padding: 10px; resize: none; min-height: 40px; max-height: 120px; font-size: 14px; font-family: inherit; border: 1px solid #ccc; border-radius: 4px;"></textarea>
                <button type="submit" style="padding: 10px 20px; white-space: nowrap;">Send</button>
            </form>
            <div id="chat-status" style="margin-top: 12px; font-size: 13px; color: #666;">
            </div>
            <div id="chat-status-working" style="font-size: 13px; color: #1565c0; display: none;">
            </div>

            <div id="notebook-section" style="display: none; margin-top: 20px;">
                <button onclick="toggleNotebook()" class="btn">Toggle Notebook</button>
                <div id="notebook-content" style="display: none; max-height: 300px; overflow-y: auto; padding: 10px; background: #f5f5f5; border: 1px solid #ccc; white-space: pre-wrap; font-family: monospace; font-size: 12px; margin-top: 10px;">
                </div>
            </div>
        </div>

        <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
        <script>
            let chatSessionId = null;
            const docId = "{doc_id or ''}";
            const docTitle = "{doc_title}";

            // Auto-resize textarea
            document.getElementById('message-input').addEventListener('input', function() {{
                this.style.height = 'auto';
                this.style.height = Math.min(this.scrollHeight, 120) + 'px';
            }});

            // Enter to send, Shift+Enter for newline
            document.getElementById('message-input').addEventListener('keydown', function(e) {{
                if (e.key === 'Enter' && !e.shiftKey) {{
                    e.preventDefault();
                    document.getElementById('chat-form').dispatchEvent(new Event('submit'));
                }}
            }});

            function updateContextSize(tokens) {{
                const el = document.getElementById('context-size');
                if (!el) return;
                const maxContext = 200000;
                const pct = (tokens / maxContext * 100).toFixed(1);
                const formatted = tokens >= 1000 ? (tokens / 1000).toFixed(1) + 'k' : tokens.toLocaleString();
                el.textContent = `Context: ${{pct}}% (${{formatted}} tokens)`;
                el.style.color = pct < 50 ? 'rgba(100,100,100,0.8)' : pct < 80 ? '#f39c12' : '#e53935';
            }}

            // Initialize chat session on page load
            window.addEventListener('load', async () => {{
                await initChat();
            }});

            async function initChat() {{
                const response = await fetch('/web/api/chat/start', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{doc_id: docId || null}})
                }});
                const data = await response.json();
                chatSessionId = data.session_id;

                if (docId) {{
                    addMessage('system', `Loaded document: ${{docTitle}}`);
                }}
            }}

            function toggleNotebook() {{
                const content = document.getElementById('notebook-content');
                content.style.display = content.style.display === 'none' ? 'block' : 'none';
            }}

            function addMessage(role, content) {{
                const messagesDiv = document.getElementById('messages');
                const msgDiv = document.createElement('div');
                msgDiv.style.marginBottom = '10px';
                msgDiv.style.padding = '10px';
                msgDiv.style.borderRadius = '5px';

                if (role === 'user') {{
                    msgDiv.style.backgroundColor = '#e3f2fd';
                    // Escape HTML for user messages (plain text)
                    const escapedContent = content.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                    msgDiv.innerHTML = '<strong>You:</strong> ' + escapedContent;
                }} else if (role === 'assistant') {{
                    msgDiv.style.backgroundColor = '#f1f8e9';
                    // Convert markdown to HTML for assistant messages
                    let markdownHtml = typeof marked !== 'undefined' ? marked.parse(content || '') : content;
                    // Turn 【doc_id】 citations into clickable links
                    markdownHtml = markdownHtml.replace(/【([^\】]+)】/g, (match, docId) => {{
                        const escaped = docId.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                        return `<a href="/web/document/by-doc-id/${{encodeURIComponent(docId)}}" target="_blank" rel="noopener noreferrer" title="${{escaped}}" style="text-decoration: none; font-size: 0.85em; color: #1565c0; border: 1px solid #90caf9; border-radius: 3px; padding: 0 4px; margin: 0 1px;">[${{escaped}}]</a>`;
                    }});
                    msgDiv.innerHTML = '<strong>Agent:</strong><div class="markdown-content" style="margin-top: 5px;">' + markdownHtml + '</div>';
                }} else {{
                    msgDiv.style.backgroundColor = '#fff3e0';
                    // Escape HTML for system messages (plain text)
                    const escapedContent = content.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                    msgDiv.innerHTML = '<em>' + escapedContent + '</em>';
                }}

                messagesDiv.appendChild(msgDiv);
                messagesDiv.scrollTop = messagesDiv.scrollHeight;
            }}

            function renderToolCallMessage(data) {{
                const messagesDiv = document.getElementById('messages');
                const msgDiv = document.createElement('div');
                msgDiv.style.marginBottom = '10px';
                msgDiv.style.padding = '10px';
                msgDiv.style.borderRadius = '5px';
                msgDiv.style.backgroundColor = '#fff3e0';

                const toolCalls = Array.isArray(data?.tool_calls) && data.tool_calls.length ? data.tool_calls : [];
                const toolNames = toolCalls.length ? toolCalls.map((tool) => tool.name || '?') : (Array.isArray(data?.tools) ? data.tools : []);
                const detailsHtml = toolCalls.length
                    ? toolCalls.map((tool) => {{
                        const argsText = tool.arguments || '{{}}';
                        const escapedArgs = argsText.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                        const escapedName = (tool.name || '?').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                        const safeId = (tool.id || tool.name || '?').replace(/[^a-zA-Z0-9_-]/g, '_');
                        return `
                            <details style="display: inline-block; margin-left: 6px; vertical-align: top;" id="tool-call-${{safeId}}">
                                <summary style="cursor: pointer; display: inline;"><strong>${{escapedName}}</strong></summary>
                                <pre style="margin: 8px 0 0; white-space: pre-wrap; word-break: break-word; background: rgba(0,0,0,0.04); padding: 8px; border-radius: 4px; font-size: 11px;">${{escapedArgs}}</pre>
                            </details>
                        `;
                    }}).join('')
                    : '';

                msgDiv.innerHTML = `<strong>Tool calls:</strong>${{detailsHtml}}`;
                messagesDiv.appendChild(msgDiv);
                messagesDiv.scrollTop = messagesDiv.scrollHeight;
            }}

            function renderToolResultMessage(data) {{
                const safeId = (data.tool_id || data.tool_name || '?').replace(/[^a-zA-Z0-9_-]/g, '_');
                const detailsEl = document.getElementById(`tool-call-${{safeId}}`);
                const escapedResult = (data.result || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                if (detailsEl) {{
                    const resultEl = document.createElement('pre');
                    resultEl.style.cssText = 'margin: 4px 0 0; padding: 8px; white-space: pre-wrap; word-break: break-word; font-size: 11px; background: #f0f4e8; border-radius: 4px; border-left: 3px solid #a5d6a7;';
                    resultEl.textContent = data.result || '';
                    detailsEl.appendChild(resultEl);
                }}
            }}

            let currentInfoMsgDiv = null;

            function addInfoMessage(text) {{
                const messagesDiv = document.getElementById('messages');
                if (!currentInfoMsgDiv) {{
                    currentInfoMsgDiv = document.createElement('div');
                    currentInfoMsgDiv.style.marginBottom = '10px';
                    currentInfoMsgDiv.style.padding = '8px 10px';
                    currentInfoMsgDiv.style.borderRadius = '5px';
                    currentInfoMsgDiv.style.backgroundColor = '#fff3e0';
                    currentInfoMsgDiv.style.fontSize = '13px';
                    currentInfoMsgDiv.style.color = '#555';
                    messagesDiv.appendChild(currentInfoMsgDiv);
                }}
                const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                currentInfoMsgDiv.innerHTML = `<em>${{escaped}}</em>`;
                messagesDiv.scrollTop = messagesDiv.scrollHeight;
            }}

            function clearInfoMessage() {{
                if (currentInfoMsgDiv) {{
                    currentInfoMsgDiv.remove();
                    currentInfoMsgDiv = null;
                }}
            }}

            function updateNotebook(notebook) {{
                document.getElementById('notebook-section').style.display = 'block';
                document.getElementById('notebook-content').textContent = notebook;
            }}

            function setStatus(text, color = '#666', isWorking = false) {{
                const statusEl = document.getElementById('chat-status');
                const workingEl = document.getElementById('chat-status-working');
                if (!statusEl) return;
                if (isWorking) {{
                    // Show working detail on second line; leave first line as-is
                    if (workingEl) {{
                        workingEl.textContent = text;
                        workingEl.style.display = 'block';
                        workingEl.classList.add('working');
                    }}
                }} else {{
                    statusEl.textContent = `Status: ${{text}}`;
                    statusEl.style.color = color;
                    if (workingEl) {{
                        workingEl.style.display = 'none';
                        workingEl.classList.remove('working');
                        workingEl.textContent = '';
                    }}
                }}
            }}

            async function sendMessage(event) {{
                event.preventDefault();

                const input = document.getElementById('message-input');
                const message = input.value.trim();
                if (!message) return;

                // Add user message to UI
                addMessage('user', message);
                input.value = '';

                // Disable form during processing
                document.getElementById('chat-form').style.opacity = '0.5';
                document.getElementById('message-input').disabled = true;
                setStatus('Working on it...', '#1565c0', true);

                // Lock mode after first message
                const modeSelect = document.getElementById('agent-mode-select');
                const agentMode = modeSelect.value;
                modeSelect.disabled = true;
                const eventSource = new EventSource(
                    `/web/api/chat/message?session_id=${{chatSessionId}}&message=${{encodeURIComponent(message)}}&mode=${{agentMode}}`
                );

                let responseText = '';
                currentInfoMsgDiv = null;

                eventSource.addEventListener('info', (e) => {{
                    const data = JSON.parse(e.data);
                    if (data.message) {{
                        addInfoMessage(data.message);
                        setStatus(data.message, '#1565c0', true);
                    }}
                }});

                eventSource.addEventListener('heartbeat', (e) => {{
                    setStatus('Working on it...', '#1565c0', true);
                }});

                eventSource.addEventListener('notebook_update', (e) => {{
                    const data = JSON.parse(e.data);
                    updateNotebook(data.notebook);
                    setStatus('Working on it...', '#1565c0', true);
                }});

                eventSource.addEventListener('tool_call', (e) => {{
                    const data = JSON.parse(e.data);
                    renderToolCallMessage(data);
                    setStatus('Working on it...', '#1565c0', true);
                }});

                eventSource.addEventListener('tool_result', (e) => {{
                    const data = JSON.parse(e.data);
                    renderToolResultMessage(data);
                }});

                eventSource.addEventListener('token_usage', (e) => {{
                    const data = JSON.parse(e.data);
                    const total = data?.token_overview?.totals?.total_tokens;
                    if (typeof total === 'number') {{
                        updateContextSize(total);
                    }}
                }});

                eventSource.addEventListener('response', (e) => {{
                    const data = JSON.parse(e.data);
                    responseText = data.content;
                }});

                eventSource.addEventListener('done', (e) => {{
                    eventSource.close();
                    clearInfoMessage();
                    addMessage('assistant', responseText);
                    setStatus('Completed', '#2e7d32', false);

                    // Re-enable form
                    document.getElementById('chat-form').style.opacity = '1';
                    document.getElementById('message-input').disabled = false;
                    document.getElementById('message-input').focus();
                }});

                eventSource.addEventListener('error', (e) => {{
                    eventSource.close();
                    clearInfoMessage();
                    let serverMessage = '';
                    try {{
                        const payload = e && e.data ? JSON.parse(e.data) : null;
                        serverMessage = payload && payload.message ? payload.message : '';
                    }} catch (err) {{
                        serverMessage = '';
                    }}
                    addMessage('system', serverMessage ? `Error: ${{serverMessage}}` : 'Error: Connection failed');
                    setStatus(serverMessage ? `Error: ${{serverMessage}}` : 'Error: connection failed', '#c62828', false);
                    document.getElementById('chat-form').style.opacity = '1';
                    document.getElementById('message-input').disabled = false;
                }});
            }}
        </script>
        """

        return HTMLResponse(html_templates.base_template(
            "Chat",
            content,
            username=username
        ))

    app.add_route("/web/chat", chat_page, methods=["GET"])

    async def start_chat(request: Request):
        """Start a new chat session."""
        session_data, error_response = await require_auth_api(request, session_manager, json_response=True)
        if error_response:
            return error_response

        username = session_data.get('username', 'unknown')

        # Parse request body
        body = await request.json()
        doc_id = body.get('doc_id')

        # Load document context if provided
        document_context = None
        if doc_id:
            # Use kb_get which returns formatted text optimized for LLM
            doc_text = kb_get(doc_id)
            if doc_text and not doc_text.startswith("ERROR:"):
                document_context = {
                    'doc_id': doc_id,
                    'text': doc_text,  # Formatted text with metadata header
                }

        # Create session
        session_id = secrets.token_urlsafe(16)
        # print("DEBUG Starting chat session:", session_id)
        chat_session = ChatSession(session_id, username, document_context)

        # Store in active sessions
        active_chat_sessions[session_id] = chat_session

        logger.info(f"Started chat session {session_id} for {username}")

        return JSONResponse({
            'session_id': session_id,
            'doc_context': document_context is not None
        })

    app.add_route("/web/api/chat/start", start_chat, methods=["POST"])

    async def chat_message(request: Request):
        """Process chat message with SSE streaming."""
        session_data, error_response = await require_auth_api(request, session_manager)
        if error_response:
            return error_response

        session_id = request.query_params.get('session_id')
        message = request.query_params.get('message')
        mode = request.query_params.get('mode', 'notebook')  # notebook | plain | direct
        # print("DEBUG Received message for session:", session_id, message, "mode:", mode)

        if not session_id or not message:
            return JSONResponse({'error': 'Missing session_id or message'}, status_code=400)

        chat_session = active_chat_sessions.get(session_id)
        if not chat_session:
            return JSONResponse({'error': 'Invalid session'}, status_code=404)

        # Add message to history
        chat_session.message_history.append({
            'role': 'user',
            'content': message,
            'timestamp': datetime.utcnow()
        })

        query = message
        doc_context = None
        if chat_session.document_context:
            doc_text = chat_session.document_context['text']
            query = f"{doc_text}\n\nUser Question: {message}"

        # Stream response via SSE
        async def event_generator():
            """Generate SSE events from agent execution."""
            # print(f"DEBUG event_generator started for session {session_id}")

            yield f"event: info\ndata: {json.dumps({'message': 'Processing your message...'})}\n\n"

            # --- Direct LLM mode (no agent/MCP) ---
            if mode == 'direct':
                try:
                    client = get_openai_client(use_async=True)
                    agent_config = get_agent_config()
                    model = agent_config['agent_model']

                    messages = []
                    if chat_session.document_context:
                        messages.append({'role': 'system', 'content': f"The user has loaded the following document for context:\n\n{chat_session.document_context['text']}"})
                    messages += [{'role': m['role'], 'content': m['content']}
                                 for m in chat_session.message_history
                                 if m['role'] in ('user', 'assistant')]

                    response = await client.chat.completions.create(
                        model=model,
                        messages=messages,
                    )
                    result = response.choices[0].message.content
                    chat_session.message_history.append({'role': 'assistant', 'content': result, 'timestamp': datetime.utcnow()})
                    yield f"event: response\ndata: {json.dumps({'content': result})}\n\n"
                    yield f"event: done\ndata: {{}}\n\n"
                except Exception as e:
                    logger.error(f"Direct LLM error: {e}", exc_info=True)
                    yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
                return

            response_text = ""

            # Create callback for agent events
            event_queue = asyncio.Queue()

            async def agent_callback(event):
                # print(f"DEBUG agent_callback received event: {event.get('type')}")
                await event_queue.put(event)

            # Initialize agent with callback if not already initialized
            if not chat_session.agent:
                # print(f"DEBUG Initializing new agent for session {session_id}")
                try:
                    async_client = get_openai_client(use_async=True)
                    agent_config = get_agent_config()
                    agent_model = agent_config['agent_model']
                    await chat_session.start_mcp(mode, async_client, agent_model, agent_callback)
                    # print(f"DEBUG Agent initialized with {len(chat_session.agent.tools)} tools")
                    logger.info(f"Initialized agent for chat session {session_id}")
                except Exception as e:
                    # print(f"DEBUG Error initializing agent: {e}")
                    logger.error(f"Error initializing agent: {e}", exc_info=True)
                    yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
                    return

            # Temporarily set callback
            chat_session.agent.callback = agent_callback
            # print(f"DEBUG Set callback on agent")

            import anyio

            agent_done = anyio.Event()

            async def run_agent():
                # print(f"DEBUG Running agent for session: {session_id} with query: {query[:100]}...")
                try:
                    agent_config = get_agent_config()
                    model = agent_config['agent_model']
                    prior_history = chat_session.message_history[:-1]
                    result = await chat_session.agent.run(query, model=model, history=prior_history)
                    # print(f"DEBUG Agent completed with result: {result[:100] if result else 'None'}...")
                    await event_queue.put({'type': 'response', 'content': result})
                    await event_queue.put({'type': 'done'})
                except Exception as e:
                    # print(f"DEBUG Agent error: {e}")
                    logger.error(f"Agent error: {e}", exc_info=True)
                    await event_queue.put({'type': 'error', 'message': str(e)})
                finally:
                    agent_done.set()

            # print(f"DEBUG Agent task created")
            agent_task = asyncio.get_event_loop().create_task(run_agent())

            # Stream events as they arrive
            while True:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.5)
                    # print(f"DEBUG Received event from queue: {event.get('type')}")

                    if event['type'] == 'done':
                        yield f"event: done\ndata: {{}}\n\n"
                        break
                    elif event['type'] == 'response':
                        response_text = event['content']
                        yield f"event: response\ndata: {json.dumps(event)}\n\n"
                    elif event['type'] == 'error':
                        yield f"event: error\ndata: {json.dumps(event)}\n\n"
                        break
                    else:
                        yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"

                except asyncio.TimeoutError:
                    if agent_task.done():
                        exc = agent_task.exception()
                        if exc:
                            print(f"DEBUG Agent task failed: {exc}")
                            logger.error(f"Agent task failed: {exc}", exc_info=True)
                            yield f"event: error\ndata: {json.dumps({'message': f'Agent task failed: {str(exc)}'})}\n\n"
                            break
                    heartbeat = {'message': 'still_processing', 'ts': datetime.utcnow().isoformat() + 'Z'}
                    yield f"event: heartbeat\ndata: {json.dumps(heartbeat)}\n\n"
                    continue

            # Save response to history
            chat_session.message_history.append({
                'role': 'assistant',
                'content': response_text,
                'timestamp': datetime.utcnow()
            })

            # Clear callback
            if chat_session.agent:
                chat_session.agent.callback = None

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )

    app.add_route("/web/api/chat/message", chat_message, methods=["GET"])

    async def close_chat(request: Request):
        """Close a chat session and clean up resources."""
        session_data, error_response = await require_auth_api(request, session_manager, json_response=True)
        if error_response:
            return error_response

        session_id = request.path_params.get('session_id')

        chat_session = active_chat_sessions.get(session_id)
        if chat_session:
            await chat_session.cleanup()
            del active_chat_sessions[session_id]
            logger.info(f"Closed chat session {session_id}")
            return JSONResponse({'status': 'closed'})

        return JSONResponse({'error': 'Session not found'}, status_code=404)

    app.add_route("/web/api/chat/close/{session_id}", close_chat, methods=["POST"])

    # Background task to clean up old sessions
    async def cleanup_old_sessions():
        """Clean up sessions older than 1 hour."""
        while True:
            await asyncio.sleep(300)  # Run every 5 minutes
            now = datetime.utcnow()
            to_remove = []

            for session_id, chat_session in active_chat_sessions.items():
                age = (now - chat_session.created_at).total_seconds()
                if age > 3600:  # 1 hour
                    to_remove.append(session_id)

            for session_id in to_remove:
                chat_session = active_chat_sessions.get(session_id)
                if chat_session:
                    await chat_session.cleanup()
                    del active_chat_sessions[session_id]
                    logger.info(f"Cleaned up inactive session {session_id} (age: {age/60:.1f} min)")

    # Store the cleanup task starter for later use
    app.state.chat_cleanup_task = cleanup_old_sessions
