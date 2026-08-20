"""Plain agent: single-pass LLM with MCP tools, no notebook scratchpad."""

import json
import logging

from .base_agent import BaseAgent
from .prompts import WORKER_TEMPLATE, SHARED_PROTOCOL

logger = logging.getLogger(__name__)

PLAIN_AGENT_SYSTEM = """You are a helpful research assistant with access to a knowledge base.
{domain_context}

{shared_protocol}

Answer the user's question using the available tools when needed.
Be concise and cite sources inline using 【doc_id】 format (Japanese brackets).
"""


class PlainAgent(BaseAgent):
    """Single-pass agent with MCP tools but no notebook scratchpad loop."""

    async def run(self, query: str, model: str = "gpt-oss-120b", history: list | None = None) -> str:
        self.info(f"PlainAgent starting: \"{query[:100]}...\"")

        system_prompt = PLAIN_AGENT_SYSTEM.format(
            domain_context=self.domain_context,
            shared_protocol=SHARED_PROTOCOL,
        )

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages += [{"role": m["role"], "content": m["content"]}
                         for m in history if m["role"] in ("user", "assistant")]
        messages.append({"role": "user", "content": query})

        # Tool-calling loop (max 10 rounds to avoid runaway)
        for iteration in range(10):
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                tools=self.tools if self.tools else None,
                tool_choice="auto" if self.tools else None,
            )
            self.record_usage(response.usage, stage=f"step_{iteration}")

            msg = response.choices[0].message

            if not msg.tool_calls:
                result = msg.content or ""
                self.info(f"PlainAgent done after {iteration + 1} step(s)")
                await self.emit_event({"type": "response", "content": result})
                await self.emit_event({"type": "token_usage", "token_overview": self.get_token_overview()})
                return result

            # Execute tool calls
            messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]})

            # Emit a tool_call event for all tool calls in this round (yellow block in UI)
            await self.emit_event({
                "type": "tool_call",
                "tool_calls": [
                    {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                    for tc in msg.tool_calls
                ],
            })

            for tc in msg.tool_calls:
                tool_result = await self._execute_tool(tc, prefix="", model=model)
                if isinstance(tool_result, list):
                    content = "\n".join(
                        item["text"] if isinstance(item, dict) and "text" in item else str(item)
                        for item in tool_result
                    )
                else:
                    content = str(tool_result)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
                await self.emit_event({
                    "type": "tool_result",
                    "tool_id": tc.id,
                    "tool_name": tc.function.name,
                    "result": content[:2000] + ("…" if len(content) > 2000 else ""),
                })

        # Fallback if we hit the iteration limit
        self.info("PlainAgent hit iteration limit, requesting final answer")
        messages.append({"role": "user", "content": "Please provide your final answer now based on what you've found."})
        response = await self.client.chat.completions.create(model=model, messages=messages)
        self.record_usage(response.usage, stage="final")
        result = response.choices[0].message.content or ""
        await self.emit_event({"type": "token_usage", "token_overview": self.get_token_overview()})
        return result
