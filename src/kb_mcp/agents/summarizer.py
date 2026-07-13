"""Tool result summarization for context window management."""

import logging
from typing import TYPE_CHECKING, Dict, Any

if TYPE_CHECKING:
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


def _create_summarization_prompt(
    original_query: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    result_description: str,
) -> str:
    """Create a reusable summarization prompt.

    Args:
        original_query: The original research question
        tool_name: Name of the tool
        tool_args: Arguments passed to the tool
        result_description: Description of what's being summarized (e.g., "partial tool result (part 1/3)")

    Returns:
        Formatted summarization prompt
    """
    args_str = ", ".join(f"{k}={v}" for k, v in tool_args.items())

    return f"""You are a research assistant that summarizes tool results.

Research Question: {original_query}
Tool Called: {tool_name}({args_str})

Your task: Condense the following {result_description} to focus on information relevant to the research question.

CRITICAL - You MUST preserve:
- ALL document IDs and references (e.g., doc_id: 123, document_id, etc.)
- ALL URIs
- ALL key findings and facts relevant to the research question
- ALL citations and sources
- ALL numerical data and specific details
"""


async def summarize_tool_result(
    tool_name: str,
    tool_args: Dict[str, Any],
    result: str,
    original_query: str,
    client: "AsyncOpenAI",
    model: str,
) -> str:
    """Summarize a long tool result to reduce context usage.

    Uses the original research query to focus the summary on relevant information.
    CRITICAL: Must preserve all document references, IDs, and citations.

    For very large results (>80k chars / ~20k tokens), chunks them and summarizes
    each chunk, then combines the summaries.

    Args:
        tool_name: Name of the tool that produced the result
        result: The tool result to summarize
        original_query: The original research question for context
        client: AsyncOpenAI client
        model: Model to use for summarization

    Returns:
        Summarized result with metadata header
    """
    # ~20k tokens = ~80k chars (rough estimate: 1 token ≈ 4 chars)
    MAX_CHUNK_SIZE = 80000

    if len(result) > MAX_CHUNK_SIZE:
        num_chunks = (len(result) + MAX_CHUNK_SIZE - 1) // MAX_CHUNK_SIZE
        logger.info(f"Chunking {len(result)} chars into {num_chunks} parts for summarization...")

        # Split into chunks
        chunks = []
        for i in range(0, len(result), MAX_CHUNK_SIZE):
            chunks.append(result[i:i + MAX_CHUNK_SIZE])

        # Summarize each chunk
        chunk_summaries = []
        total_input_tokens = 0
        total_output_tokens = 0

        for i, chunk in enumerate(chunks):
            try:
                chunk_prompt = _create_summarization_prompt(
                    original_query=original_query,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    result_description=f"partial tool result (part {i+1}/{len(chunks)})"
                )
                chunk_prompt += f"\n\nPartial result to summarize:\n{chunk}\n\nProvide a focused summary that maintains all references and information relevant to: \"{original_query}\"\n"

                response = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": chunk_prompt}],
                    temperature=0.1,
                    max_tokens=2048,
                )

                chunk_summaries.append(response.choices[0].message.content)

                if hasattr(response, 'usage') and response.usage:
                    total_input_tokens += response.usage.prompt_tokens
                    total_output_tokens += response.usage.completion_tokens

            except Exception as e:
                logger.error(f"Chunk {i+1} summarization failed: {e}")
                chunk_summaries.append(f"[CHUNK {i+1} SUMMARIZATION FAILED]\n{chunk[:2000]}...")

        # Combine summaries
        combined_summary = "\n\n".join(chunk_summaries)

        # Create metadata header
        compression_info = (
            f"~{total_input_tokens/1e3:.1f}k tok -> {total_output_tokens/1e3:.1f}k tok ({len(chunks)} chunks)"
            if total_input_tokens > 0
            else f"{len(result)} -> {len(combined_summary)} chars ({len(chunks)} chunks)"
        )

        meta_header = (
            f"--- TOOL RESULT SUMMARY ---\n"
            f"Source Tool: {tool_name}\n"
            f"Original Data Size: {len(result)} chars\n"
            f"Compression: {compression_info}\n"
            f"Summary Context: Focused on query '{original_query}'\n"
            f"---------------------------\n\n"
        )

        logger.info(f"Compressed {compression_info}")
        return meta_header + combined_summary

    # For smaller results, summarize directly
    summarize_prompt = _create_summarization_prompt(
        original_query=original_query,
        tool_name=tool_name,
        tool_args=tool_args,
        result_description="tool result"
    )
    summarize_prompt += f"\n\nResult to summarize:\n{result}\n\nProvide a focused summary that maintains all references and information relevant to: \"{original_query}\"\n"

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": summarize_prompt}],
            temperature=0.1,
            max_tokens=2048,
        )

        summary = response.choices[0].message.content

        # Create metadata header
        compression_info = ""
        if hasattr(response, 'usage') and response.usage:
            input_tokens = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens
            compression_info = f"~{input_tokens/1e3:.1f}k tok -> {output_tokens/1e3:.1f}k tok"
            logger.info(f"Compressed {compression_info}")
        else:
            compression_info = f"{len(result)} -> {len(summary)} chars"
            logger.info(f"Compressed {compression_info}")

        meta_header = (
            f"--- TOOL RESULT SUMMARY ---\n"
            f"Source Tool: {tool_name}\n"
            f"Original Data Size: {len(result)} chars\n"
            f"Compression: {compression_info}\n"
            f"Summary Context: Focused on query '{original_query}'\n"
            f"---------------------------\n\n"
        )

        return meta_header + summary

    except Exception as e:
        logger.error(f"Summarization failed: {e}, returning truncated original")
        # Fallback: return truncated result with warning
        return f"[TRUNCATED - summarization failed]\n{result[:2000]}..."
