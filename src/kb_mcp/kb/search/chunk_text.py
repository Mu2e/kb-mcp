"""Rebuilding a chunk's display text at search time.

The search backends never select `chunks.text`. Text is reconstructed from the
live document on every hit, so a re-parse or a regenerated summary shows
through without re-indexing. All three backends — pgvector, full-text and the
SQLite fallback — carried their own copy of this logic; it lives here once.
"""

from typing import Any, Dict, List

from ...chunking import base_strategy


def attach_chunk_text(
    chunks: List[Dict[str, Any]],
    document: Any,
    *,
    score_key: str = "similarity",
) -> List[Dict[str, Any]]:
    """Fill in each chunk's `text`, returning the list sorted best-first.

    Positioned chunks are sliced out of `document.text`.

    Summary chunks get the whole `document.summary`. A long summary is split
    across several chunks so the *embedding* covers all of it rather than
    stopping at the model's window — but that split is an indexing device, and
    a reader wants the coherent whole, not a fragment. So the pieces collapse
    to the single best-scoring one: a document whose summary was split into
    three contributes one summary result, not three copies of the same text.

    Args:
        chunks: Chunk dicts for one document. Mutated in place to add `text`.
        document: The `Document` the chunks belong to.
        score_key: Which key ranks the chunks — `"similarity"` for the vector
            and fallback backends, `"score"` for full-text.

    Returns:
        The chunks, sorted best-first, with split summaries collapsed.
    """
    for chunk in chunks:
        if base_strategy(chunk.get("chunk_strategy") or "") == "summary":
            # Checked before the positioned branch, not after it: a summary
            # chunk's offsets (if it ever grows any) index into
            # `document.summary`, and slicing `document.text` with them
            # would return an unrelated stretch of the document.
            if document.summary is not None:
                chunk["text"] = document.summary
        elif (
            chunk.get("char_start") is not None
            and chunk.get("char_end") is not None
            and document.text is not None
        ):
            chunk["text"] = document.text[chunk["char_start"]:chunk["char_end"]]

    return collapse_summary_chunks(chunks, score_key=score_key)


def collapse_summary_chunks(
    chunks: List[Dict[str, Any]],
    score_key: str = "similarity",
) -> List[Dict[str, Any]]:
    """Sort one document's chunks best-first, keeping only its best summary.

    Split summaries each rebuild to the same full text, so more than one in a
    result is the same passage repeated — it crowds out genuinely different
    chunks and pads `max_chunks_per_doc` with duplicates.

    Applied per backend *and* again after RRF fusion: fusion merges on
    `chunk_id`, so when the vector and full-text backends happen to retain
    different pieces of the same summary, both survive into the fused
    document and have to be collapsed a second time.
    """
    chunks.sort(key=lambda c: c[score_key], reverse=True)

    collapsed: List[Dict[str, Any]] = []
    seen_summary = False
    for chunk in chunks:
        if base_strategy(chunk.get("chunk_strategy") or "") == "summary":
            if seen_summary:
                continue
            seen_summary = True
        collapsed.append(chunk)
    return collapsed
