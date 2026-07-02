"""Whole-table summary generation using LLMs.

Companion to image_descriptions.py: a thin wrapper that takes a list of table
records produced by DoclingParser and asks an LLM to summarise each one in
1–2 sentences. The summary is appended to each table dict's `text` (so
chunking + embedding pick it up) and stored under `meta["summary"]` for
downstream consumers that want the structured form.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from ..config import get_llm_config, get_parser_config

logger = logging.getLogger(__name__)


def _get_single_table_summary(client, table_dict: Dict[str, Any], model: str) -> str:
    """Ask the LLM for a 1–2 sentence factual summary of one table."""
    meta = table_dict.get("meta") or {}
    caption = (meta.get("caption") or "").strip()
    nearby_text = (meta.get("nearby_text") or "").strip()
    table_md = (table_dict.get("text") or "").strip()

    prompt = _create_table_summary_prompt(caption, nearby_text, table_md)

    # gpt-oss-* and other reasoning models consume max_tokens on internal
    # thinking before emitting visible output, so a tight cap (e.g. 160)
    # produces empty content. 800 leaves comfortable room for ≈ 1–2 sentences
    # of output even after a few hundred reasoning tokens.
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=800,
    )
    content = response.choices[0].message.content
    return (content or "").strip()


def generate_table_summaries(
    table_dicts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Generate a 1–2 sentence summary for each `doc_type="table"` record.

    Modifies each table dict in place:
      - `meta["summary"]` is set to the summary string.
      - `text` gets the summary appended (so chunking + embedding sees it).

    Configuration is read from environment variables:
      - PARSE_TABLE_SUMMARY_MODEL — model name (defaults to DEFAULT_LLM_MODEL).
      - PARSE_TABLE_SUMMARY_NUMWORKERS — parallel worker count (default 6).
      - OPENAI_BASE_URL / OPENAI_API_KEY — credentials (required).

    On failure (no API key, openai package missing, request error) the
    table dicts are returned unchanged — the table records are still
    indexable from caption + nearby_text + grid even without a summary.
    """
    if not table_dicts:
        return table_dicts

    targets = [td for td in table_dicts if td.get("doc_type") == "table"]
    if not targets:
        return table_dicts

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed, skipping table summaries")
        return table_dicts

    llm_config = get_llm_config()
    parser_config = get_parser_config()
    api_key = llm_config["openai_api_key"]
    if not api_key:
        logger.warning("OPENAI_API_KEY not set, skipping table summaries")
        return table_dicts

    model = parser_config["table_summary_model"]
    max_workers = parser_config["table_summary_num_workers"]

    client_kwargs = {"api_key": api_key}
    base_url = llm_config["openai_base_url"]
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    summaries: List[str | None] = [None] * len(targets)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(_get_single_table_summary, client, td, model): i
            for i, td in enumerate(targets)
        }
        for future in as_completed(future_to_index):
            i = future_to_index[future]
            try:
                summaries[i] = future.result()
            except Exception as e:
                meta = targets[i].get("meta") or {}
                idx = meta.get("table_index")
                logger.error(f"Error generating summary for table {idx}: {e}")
                summaries[i] = None

    for td, summary in zip(targets, summaries):
        if not summary:
            continue
        td.setdefault("meta", {})["summary"] = summary
        existing = (td.get("text") or "").strip()
        td["text"] = f"{existing}\n\n{summary}" if existing else summary

    return table_dicts


def _create_table_summary_prompt(caption: str, nearby_text: str, table_md: str) -> str:
    """Build the LLM prompt used for one table.

    Inputs are clipped before insertion so a pathological table doesn't blow
    out the context budget — the LLM doesn't need every cell, it needs enough
    to identify what the table is *about*.
    """
    nearby = nearby_text[:600]
    grid = table_md[:2400]
    cap = caption or "(no caption)"

    return f"""You are summarising a table extracted from a technical document so it can be retrieved by search.

Surrounding paragraph (preceded the table):
{nearby or "(none)"}

Table caption:
{cap}

Table (Markdown):
{grid}

Write a 1–2 sentence factual description of what this table contains: what its rows and columns represent, and the kind of values it lists. Stick to what is visible in the table; do not invent values. Do not start with phrases like "This table" — begin directly with the content.
"""
