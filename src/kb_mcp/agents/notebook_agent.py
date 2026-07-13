import asyncio
import json
import logging
from typing import List, Dict, Any

from .base_agent import BaseAgent
from .prompts import (
    NOTEBOOK_WORKER_TEMPLATE, 
    NOTEBOOK_UPDATE_PROMPT, 
    NOTEBOOK_FINAL_ANSWER_PROMPT,
    SHARED_PROTOCOL
)

logger = logging.getLogger(__name__)

class NotebookAgent(BaseAgent):
    """Worker agent that maintains a persistent notebook state."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.notebook = "(Notebook is empty)"
        self.call_log = []
        self.iteration = 0

    def info(self, info: str):
        """Override to include iteration number."""
        iter_prefix = f"[{self.iteration}] " if hasattr(self, 'iteration') and self.iteration > 0 else ""
        super().info(f"{iter_prefix}{info}")

    async def initialize_tools(self):
        # Domain context (reuse base)
        try:
            resources = await self.session.read_resource("kb://sys/domain_context")
            self.domain_context = resources.contents[0].text
        except Exception:
            self.domain_context = "Domain: General Knowledge."

        # Get MCP tools
        mcp_tools_list = await self.session.list_tools()

        self.tools = []
        tool_names = []
        for t in mcp_tools_list.tools:
            self.tools.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.inputSchema,
                }
            })
            tool_names.append(t.name)
            
        self.info(f"NotebookAgent initialized with tools: {', '.join(tool_names)}")        

    async def run(self, query: str, model: str = "gpt-oss-120b") -> str:
        """Execute the notebook loop."""
        self.info(f"starting NotebookAgent task: \"{query}\"")
        self.notebook = "(Initial empty notebook)"
        self.call_log = []

        MAX_ITERATIONS = 10 # Safety break

        self.iteration = 0
        while self.iteration < MAX_ITERATIONS:
            self.iteration += 1
            
            # Construct Prompt
            # We explicitly format the dynamic parts here
            log_str = "\n".join([f"- {entry}" for entry in self.call_log]) if self.call_log else "(No actions yet)"
            
            sys_prompt = NOTEBOOK_WORKER_TEMPLATE.format(
                domain_context=self.domain_context,
                shared_protocol=SHARED_PROTOCOL,
                user_query=query,
                call_log=log_str,
                notebook=self.notebook
            )
            
            # don't keep a conversation history
            # recreate the messages array every turn.
            messages = [
                {"role": "system", "content": sys_prompt},
                # We optionally add a reminder user message
                {"role": "user", "content": f"Task: {query}\n\nProceed with the next step or return the final answer based on your notebook."}
            ]

            # Get Action
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                tools=self.tools,
                temperature=0.1,
            )
             
            # Track usage
            if hasattr(response, 'usage') and response.usage:
                turn_usage = self.record_usage(response.usage, stage="notebook_action")
                await self.emit_event({
                    'type': 'token_usage',
                    'iteration': self.iteration,
                    'agent_id': self.agent_id,
                    'depth': self.depth,
                    'stage': 'notebook_action',
                    'turn': turn_usage,
                    'token_overview': self.get_token_overview(),
                })
            
            message = response.choices[0].message
            
            # Handle Answer
            if not message.tool_calls:
                self.info(f"NotebookAgent finished (no tool calls).")
                return message.content

            # Handle Tool Calls
            # We execute them, THEN we update the notebook.
            # Note: Parallel tool calls are possible.
            
            # Execute tool calls in parallel and aggregate results
            # Result from _execute_tool is now List[Dict] (content parts)
            tool_outputs_content = []
            
            from ..config import get_agent_config
            total_text_len = 0
            MAX_TOTAL_TEXT_LEN = get_agent_config().get("max_aggregated_tool_output_chars", 100000)

            prefix = "  "*self.depth
            tool_results_map = await asyncio.gather(*[self._execute_tool(tc, prefix, model) for tc in message.tool_calls])

            # Emit tool call event
            await self.emit_event({
                'type': 'tool_call',
                'iteration': self.iteration,
                'tools': [tc.function.name for tc in message.tool_calls],
                'tool_calls': [
                    {
                        'id': tc.id,
                        'name': tc.function.name,
                        'arguments': tc.function.arguments,
                    }
                    for tc in message.tool_calls
                ],
                'count': len(message.tool_calls)
            })

            for i, result_parts in enumerate(tool_results_map):
                tc = message.tool_calls[i]
                fname = tc.function.name
                
                # Header for the tool result
                header_text = f"\n\n--- Result from {fname} (ID: {tc.id}) ---"
                tool_outputs_content.append({
                    "type": "text", 
                    "text": header_text
                })
                total_text_len += len(header_text)
                
                # Append the parts (text or image)
                if isinstance(result_parts, list):
                    for part in result_parts:
                        if part["type"] == "text":
                            remaining = MAX_TOTAL_TEXT_LEN - total_text_len
                            if remaining <= 0:
                                tool_outputs_content.append({"type": "text", "text": "\n...(remaining text truncated due to global context limit)..."})
                                break # Stop adding text parts for this tool (and effectively others)
                            
                            text = part["text"]
                            if len(text) > remaining:
                                text = text[:remaining] + "\n...(truncated global)..."
                            
                            total_text_len += len(text)
                            tool_outputs_content.append({"type": "text", "text": text})
                        else:
                            # Pass images through (ignoring text limit for images, or maybe count token equivalent?)
                            # For safety, we pass them.
                            tool_outputs_content.append(part)
                else:
                    # Fallback if error string returned
                    s = str(result_parts)
                    total_text_len += len(s)
                    tool_outputs_content.append({"type": "text", "text": s})
                    
                tool_outputs_content.append({
                    "type": "text", 
                    "text": "\n----------------"
                })
                
                # Log usage
                fname = tc.function.name
                args = tc.function.arguments
                self.call_log.append(f"Called {fname}({args})")

            # Validate combined_results for logging (text only)
            combined_results_log_str = ""
            for item in tool_outputs_content:
                if item["type"] == "text":
                    combined_results_log_str += item["text"]
                elif item["type"] == "image_url":
                    combined_results_log_str += "\n[Image Content Omitted for Log]\n"
            
            # Truncate if too long to avoid huge log files? Maybe. 
            # But mostly we want to avoid crashing the file write with base64.

            # Update Notebook with Tool Results
            # We now use a User message to pass the tool results, allowing images to apply
            update_messages = [
                {"role": "system", "content": NOTEBOOK_UPDATE_PROMPT.format(
                    notebook=self.notebook,
                    last_action=self.call_log[-1],
                    shared_protocol=SHARED_PROTOCOL
                )},
                {"role": "user", "content": tool_outputs_content}
            ]
            
            self.info(f"processing tool results to update notebook...")
            update_response = await self.client.chat.completions.create(
                model=model,
                messages=update_messages,
                temperature=0.1,
            )
            
            new_notebook = update_response.choices[0].message.content
            self.notebook = new_notebook
            usage_str = "N/A"
            if hasattr(update_response, 'usage') and update_response.usage:
                turn_usage = self.record_usage(update_response.usage, stage="notebook_update")
                usage_str = f"{update_response.usage.total_tokens/1e3:.2f}k"
                await self.emit_event({
                    'type': 'token_usage',
                    'iteration': self.iteration,
                    'agent_id': self.agent_id,
                    'depth': self.depth,
                    'stage': 'notebook_update',
                    'turn': turn_usage,
                    'token_overview': self.get_token_overview(),
                })
            
            self.info(f"processed {usage_str} to update notebook with new length: {len(self.notebook)} chars")

            # Emit notebook update event
            await self.emit_event({
                'type': 'notebook_update',
                'iteration': self.iteration,
                'notebook': self.notebook,
                'action': self.call_log[-1] if self.call_log else None
            })

            # Dump notebook for spying
            try:
                import os
                # Structure: logs/{run_id}/{agent_id}/
                log_dir = f"logs/{self.run_id}/{self.agent_id}"
                os.makedirs(log_dir, exist_ok=True)
                
                # Dump Notebook
                with open(f"{log_dir}/step_{self.iteration}_notebook.md", "w") as f:
                    f.write(self.notebook)
                    
                # Dump Tool Results
                with open(f"{log_dir}/step_{self.iteration}_tools.md", "w") as f:
                    f.write(combined_results_log_str)

            except Exception as e:
                self.info(f"Failed to dump notebook log: {e}")

        self.info(f"reached max iterations ({MAX_ITERATIONS}). forcing final answer.")
        
        # Force a final answer generation
        final_messages = [
             {"role": "system", "content": NOTEBOOK_FINAL_ANSWER_PROMPT.format(
                 query=query,
                 notebook=self.notebook,
                 shared_protocol=SHARED_PROTOCOL
             )}
        ]
        
        response = await self.client.chat.completions.create(
            model=model,
            messages=final_messages,
            temperature=0.1,
        )
        if hasattr(response, 'usage') and response.usage:
            turn_usage = self.record_usage(response.usage, stage="notebook_final")
            await self.emit_event({
                'type': 'token_usage',
                'iteration': self.iteration,
                'agent_id': self.agent_id,
                'depth': self.depth,
                'stage': 'notebook_final',
                'turn': turn_usage,
                'token_overview': self.get_token_overview(),
            })
        return response.choices[0].message.content
