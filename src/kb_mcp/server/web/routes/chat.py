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
        self.agent = None  # Will hold NotebookAgent instance
        self.mcp_session = None
        self.mcp_client = None
        self.stdio_context = None  # Store the stdio context manager for cleanup
        self.mcp_session_ctx = None  # Store the MCP session context manager for cleanup

    async def cleanup(self):
        """Clean up MCP connection and resources."""
        # Clean up MCP session context manager if it exists
        if self.mcp_session_ctx:
            try:
                await self.mcp_session_ctx.__aexit__(None, None, None)
            except Exception as e:
                logger.error(f"Error cleaning up MCP session context for session {self.session_id}: {e}")
            finally:
                self.mcp_session_ctx = None
                self.mcp_session = None
        
        # Clean up stdio context manager
        if self.stdio_context:
            try:
                await self.stdio_context.__aexit__(None, None, None)
                logger.info(f"Cleaned up MCP connection for session {self.session_id}")
            except Exception as e:
                logger.error(f"Error cleaning up stdio context for session {self.session_id}: {e}")
            finally:
                self.stdio_context = None
                self.mcp_session = None
                self.agent = None


def setup_chat_routes(app, session_manager: WebSessionManager, require_auth_html, require_auth_api):
    """Setup chat interface routes."""

    @app.route("/web/chat", methods=["GET"])
    async def chat_page(request: Request):
        """Render chat interface page."""
        session_data, redirect = await require_auth_html(request, session_manager)
        if redirect:
            return redirect

        username = session_data.get('username', 'Unknown')

        # Check if starting chat with document context
        doc_id = request.query_params.get('doc_id')
        doc_title = request.query_params.get('title', 'Unknown Document')

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

            #chat-status.working::before {{
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
        
        <h1>Agent Chat Interface</h1>

        <div id="chat-container" class="card">
            <div id="messages" style="max-height: 500px; overflow-y: auto; margin-bottom: 20px; padding: 10px; border: 1px solid #ddd;">
                <!-- Messages will appear here -->
            </div>

            <form id="chat-form" onsubmit="sendMessage(event)">
                <input type="text" id="message-input" placeholder="Ask a question..." style="width: 80%; padding: 10px;" required>
                <button type="submit" style="width: 18%; padding: 10px;">Send</button>
            </form>
            <div id="chat-status" style="margin-top: 12px; font-size: 13px; color: #666;">
                Status: Ready
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
                setStatus('Ready', '#666');

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
                    const markdownHtml = typeof marked !== 'undefined' ? marked.parse(content || '') : content;
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
                        return `
                            <details style="display: inline; margin-left: 6px;">
                                <summary style="cursor: pointer; display: inline;"><strong>${{escapedName}}</strong></summary>
                                <pre style="margin: 8px 0 0; white-space: pre-wrap; word-break: break-word; background: rgba(0,0,0,0.04); padding: 8px; border-radius: 4px;">${{escapedArgs}}</pre>
                            </details>
                        `;
                    }}).join('')
                    : '';

                msgDiv.innerHTML = `<strong>Calling tools:</strong> ${{toolNames.join(', ') || 'unknown'}}${{detailsHtml ? detailsHtml : ''}}`;
                messagesDiv.appendChild(msgDiv);
                messagesDiv.scrollTop = messagesDiv.scrollHeight;
            }}

            function updateNotebook(notebook) {{
                document.getElementById('notebook-section').style.display = 'block';
                document.getElementById('notebook-content').textContent = notebook;
            }}

            function setStatus(text, color = '#666', isWorking = false) {{
                const statusEl = document.getElementById('chat-status');
                if (!statusEl) return;
                statusEl.textContent = `Status: ${{text}}`;
                statusEl.style.color = color;
                if (isWorking) {{
                    statusEl.classList.add('working');
                }} else {{
                    statusEl.classList.remove('working');
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

                // Stream response via SSE
                const eventSource = new EventSource(
                    `/web/api/chat/message?session_id=${{chatSessionId}}&message=${{encodeURIComponent(message)}}`
                );

                let responseText = '';

                eventSource.addEventListener('info', (e) => {{
                    const data = JSON.parse(e.data);
                    console.log('Agent:', data.message);
                    if (data.message) {{
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

                eventSource.addEventListener('token_usage', (e) => {{
                    const data = JSON.parse(e.data);
                    const total = data?.token_overview?.totals?.total_tokens;
                    if (typeof total === 'number') {{
                        setStatus(`Working on it... (${{total.toLocaleString()}} tokens)`, '#1565c0', true);
                    }}
                }});

                eventSource.addEventListener('response', (e) => {{
                    const data = JSON.parse(e.data);
                    responseText = data.content;
                }});

                eventSource.addEventListener('done', (e) => {{
                    eventSource.close();
                    addMessage('assistant', responseText);
                    setStatus('Completed', '#2e7d32', false);

                    // Re-enable form
                    document.getElementById('chat-form').style.opacity = '1';
                    document.getElementById('message-input').disabled = false;
                    document.getElementById('message-input').focus();
                }});

                eventSource.addEventListener('error', (e) => {{
                    eventSource.close();
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
            "Agent Chat",
            content,
            username=username
        ))

    @app.route("/web/api/chat/start", methods=["POST"])
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
        print("DEBUG Starting chat session:", session_id)
        chat_session = ChatSession(session_id, username, document_context)

        # Store in active sessions
        active_chat_sessions[session_id] = chat_session

        logger.info(f"Started chat session {session_id} for {username}")

        return JSONResponse({
            'session_id': session_id,
            'doc_context': document_context is not None
        })

    @app.route("/web/api/chat/message", methods=["GET"])
    async def chat_message(request: Request):
        """Process chat message with SSE streaming."""
        session_data, error_response = await require_auth_api(request, session_manager)
        if error_response:
            return error_response

        session_id = request.query_params.get('session_id')
        message = request.query_params.get('message')
        print("DEBUG Received message for session:", session_id, message)

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

        # Prepare query with document context if available
        query = message
        if chat_session.document_context:
            # kb_get already returns well-formatted text with metadata
            doc_text = chat_session.document_context['text']
            query = f"{doc_text}\n\nUser Question: {message}"

        # Stream response via SSE
        async def event_generator():
            """Generate SSE events from agent execution."""
            print(f"DEBUG event_generator started for session {session_id}")
            response_text = ""

            # Yield initial event to establish connection
            yield f"event: info\ndata: {json.dumps({'message': 'Processing your message...'})}\n\n"

            # Create callback for agent events
            event_queue = asyncio.Queue()

            async def agent_callback(event):
                print(f"DEBUG agent_callback received event: {event.get('type')}")
                await event_queue.put(event)

            # Initialize agent with callback if not already initialized
            if not chat_session.agent:
                print(f"DEBUG Initializing new agent for session {session_id}")
                try:
                    from mcp import ClientSession as MCPClientSession, StdioServerParameters
                    from mcp.client.stdio import stdio_client
                    import sys
                    import os

                    # Get LLM client (without model - model is passed to agent.run())
                    async_client = get_openai_client(use_async=True)
                    print(f"DEBUG Got LLM client")
                    
                    # Get agent model from config (like kb-agent does)
                    agent_config = get_agent_config()
                    agent_model = agent_config['agent_model']
                    print(f"DEBUG Using agent model: {agent_model}")

                    # Create MCP client session using stdio transport
                    # Use sys.executable to ensure we use the same Python as the current process
                    python_executable = sys.executable
                    print(f"DEBUG Using Python executable: {python_executable}")

                    server_params = StdioServerParameters(
                        command=python_executable,
                        args=["-m", "kb_mcp.server.mcp_stdio"],
                        env=os.environ.copy(),  # Pass through environment variables
                    )
                    print(f"DEBUG Created server params")

                    # Create and store stdio client context manager
                    # This keeps the MCP connection alive for the session
                    stdio_ctx = stdio_client(server_params)
                    chat_session.stdio_context = stdio_ctx
                    print(f"DEBUG Created stdio context")

                    read_stream, write_stream = await stdio_ctx.__aenter__()
                    print(f"DEBUG Entered stdio context, streams ready")

                    # Create session using context manager pattern for proper initialization
                    # We'll keep the context manager alive and clean it up in cleanup()
                    mcp_session_ctx = MCPClientSession(read_stream, write_stream)
                    await mcp_session_ctx.__aenter__()
                    chat_session.mcp_session_ctx = mcp_session_ctx  # Store for cleanup
                    mcp_session = mcp_session_ctx
                    print(f"DEBUG Created MCP session context")
                    
                    # Explicitly initialize the session (performs MCP handshake)
                    await mcp_session.initialize()
                    print(f"DEBUG Initialized MCP session via stdio")

                    # Store session
                    chat_session.mcp_session = mcp_session

                    # Create NotebookAgent
                    chat_session.agent = NotebookAgent(
                        session=mcp_session,
                        client=async_client,
                        depth=1,
                        agent_id="WebChat",
                        max_depth=2,
                        callback=agent_callback
                    )
                    print(f"DEBUG Created NotebookAgent")

                    await chat_session.agent.initialize_tools()
                    print(f"DEBUG Agent initialized with {len(chat_session.agent.tools)} tools")
                    logger.info(f"Initialized agent for chat session {session_id}")
                except Exception as e:
                    print(f"DEBUG Error initializing agent: {e}")
                    logger.error(f"Error initializing agent: {e}", exc_info=True)
                    await event_queue.put({'type': 'error', 'message': f"Failed to initialize agent: {str(e)}"})
                    yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
                    return

            # Temporarily set callback
            chat_session.agent.callback = agent_callback
            print(f"DEBUG Set callback on agent")

            # Run agent in background task
            async def run_agent():
                print(f"DEBUG Running agent for session: {session_id} with query: {query[:100]}...")
                try:
                    # Get agent model from config (same pattern as kb-agent)
                    agent_config = get_agent_config()
                    model = agent_config['agent_model']
                    result = await chat_session.agent.run(query, model=model)
                    print(f"DEBUG Agent completed with result: {result[:100] if result else 'None'}...")
                    await event_queue.put({'type': 'response', 'content': result})
                    await event_queue.put({'type': 'done'})
                except Exception as e:
                    print(f"DEBUG Agent error: {e}")
                    logger.error(f"Agent error: {e}", exc_info=True)
                    await event_queue.put({'type': 'error', 'message': str(e)})

            agent_task = asyncio.create_task(run_agent())
            print(f"DEBUG Agent task created")

            # Stream events as they arrive
            # Also monitor the agent task to catch any exceptions
            while True:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.5)
                    print(f"DEBUG Received event from queue: {event.get('type')}")

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
                        # Forward all other events (info, notebook_update, tool_call)
                        yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"

                except asyncio.TimeoutError:
                    # Check if agent task is done (might have failed silently)
                    if agent_task.done():
                        try:
                            await agent_task  # This will raise if there was an exception
                        except Exception as e:
                            print(f"DEBUG Agent task failed: {e}")
                            logger.error(f"Agent task failed: {e}", exc_info=True)
                            yield f"event: error\ndata: {json.dumps({'message': f'Agent task failed: {str(e)}'})}\n\n"
                            break
                    # Send heartbeat event that frontend can display.
                    heartbeat = {
                        'message': 'still_processing',
                        'ts': datetime.utcnow().isoformat() + 'Z',
                    }
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

    @app.route("/web/api/chat/close/{session_id}", methods=["POST"])
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
