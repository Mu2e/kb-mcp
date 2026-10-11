"""Image registry tools: find repeated images, classify decorative ones, demote them.

New images are handled at parse time (kb_mcp.parser.boilerplate): known
images reuse their description and decorative ones get a short label. This
module brings the images already in the corpus to the same state, in three
separate steps so a person can look in between:

1. `refresh_registry`  -- hash every image record and count its copies into
   `image_hashes` (one row per distinct image).
2. `classify_images`   -- ask an LLM, from the stored description and the
   counts, whether each repeated image is decorative. Writes decisions to the
   registry only; documents are not touched.
   `decide_images` records a reviewer's decision instead (final).
3. `apply_image_decisions` -- make the documents match the registry: demote
   decorative images (label as text, chunks dropped, label inlined in the
   parent text instead of the description) and restore images a reviewer
   marked as not decorative. Dry run unless `apply=True`.

Parents whose text changed lose their text chunks and are re-chunked (or
left for the next embedding sweep with `rechunk=False`).
"""

import hashlib
import json
import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import defer

from ..parser import boilerplate
from .database import get_db_session
from .db_models import Document, ImageHash

logger = logging.getLogger(__name__)

_ENTRY_RE = re.compile(r"^(\d+)-")


def _docdb_entry(doc_id: Optional[str]) -> Optional[str]:
    """'56293-_page_6_Figure_13.png' -> '56293' (the DocDB number), else None."""
    m = _ENTRY_RE.match(doc_id or "")
    return m.group(1) if m else None


# --- 1. registry ------------------------------------------------------------------------

def _backfill_hashes(session) -> int:
    """Store meta["image_hash"] on image records that lack it. Returns the count."""
    missing = Document.meta["image_hash"].astext.is_(None)
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy import text

        result = session.execute(text(
            """UPDATE documents SET meta = coalesce(meta, '{}'::jsonb)
                   || jsonb_build_object('image_hash', encode(sha256("binary"), 'hex'))
               WHERE doc_type = 'image' AND "binary" IS NOT NULL AND meta->>'image_hash' IS NULL"""
        ))
        return result.rowcount or 0
    count = 0
    for doc in session.query(Document).filter(Document.doc_type == "image", Document.binary.isnot(None), missing):
        doc.meta = {**(doc.meta or {}), "image_hash": boilerplate.image_hash(doc.binary)}
        count += 1
    return count


def refresh_registry(session=None) -> Dict[str, int]:
    """Hash all image records and update `image_hashes`: one row per distinct image.

    New rows take the description of one of their copies; existing rows only
    get fresh counts (copies, parent documents, DocDB entries, size), so
    decisions are kept.
    """
    with get_db_session(session) as session:
        hashed = _backfill_hashes(session)
        session.flush()
        stats: Dict[str, Dict[str, Any]] = {}
        rows = session.query(
            Document.meta["image_hash"].astext,
            Document.parent_id,
            Document.doc_id,
            func.length(Document.binary),
            Document.meta["description"].astext,
            Document.meta["boilerplate"]["description"].astext,
            Document.meta["boilerplate"]["label"].astext,
        ).filter(Document.doc_type == "image", Document.meta["image_hash"].astext.isnot(None))
        for h, parent_id, doc_id, size, description, original, label in rows.yield_per(5000):
            s = stats.setdefault(h, {"copies": 0, "parents": set(), "entries": set(), "bytes": size,
                                     "description": None})
            s["copies"] += 1
            s["parents"].add(parent_id)
            entry = _docdb_entry(doc_id)
            if entry:
                s["entries"].add(entry)
            # A demoted record's own description is just its label; only the
            # original it kept (if any) describes the image.
            usable = original if label else (original or description)
            if usable and usable != boilerplate.UNAVAILABLE and not s["description"]:
                s["description"] = usable

        existing = {row.image_hash: row for row in session.query(ImageHash)}
        added = updated = 0
        for h, s in stats.items():
            counts = {"copies": s["copies"], "documents": len(s["parents"]),
                      "entries": len(s["entries"]), "bytes": s["bytes"]}
            row = existing.get(h)
            if row is None:
                session.add(ImageHash(image_hash=h, description=s["description"], meta={}, **counts))
                added += 1
            else:
                for key, value in counts.items():
                    setattr(row, key, value)
                if not row.description and s["description"]:
                    row.description = s["description"]
                updated += 1
        return {"hashed": hashed, "distinct": len(stats), "added": added, "updated": updated}


# --- 2. decisions -------------------------------------------------------------------------

CLASSIFY_PROMPT = """You sort images from the documents of a physics experiment (Mu2e at Fermilab).
Each image was described earlier by a vision model; you only see that description and how often
the exact same image occurs in the document collection.

Decide whether the image is DECORATIVE: a logo, an institution's crest or wordmark, a banner, a
slide-template graphic or background, a decorative photo on title slides, a bullet, arrow or icon.
Decorative images carry no technical information; they will be replaced by a short label.

It is NOT decorative if it is a plot, a diagram or schematic of apparatus, an engineering drawing,
a photograph of equipment, a screenshot with content, a table or an equation -- even when it is
small, and even when the same figure is reused in many talks. When unsure, answer false.

The vision model often misnames logos. In particular the Mu2e logo -- a sphere labelled "Mu2e"
inside an oval or one or more elliptical orbits -- is usually described as a "schematic diagram";
it is decorative. Likewise text-only graphics of an institution or experiment name.

Description: {description}
Occurs: {copies} times, in {documents} documents ({entries} DocDB entries), {bytes} bytes.

Answer with JSON only: {{"decorative": true or false, "what": "<2-5 words, e.g. Fermilab logo>"}}"""


def _classify_one(client, model: str, row: Dict[str, Any]) -> Dict[str, Any]:
    from ..llm.retry import call_with_backoff

    prompt = CLASSIFY_PROMPT.format(description=" ".join(row["description"].split())[:1500], **{
        k: row[k] if row[k] is not None else "?" for k in ("copies", "documents", "entries", "bytes")})
    response = call_with_backoff(
        client.chat.completions.create,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=600,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    content = (response.choices[0].message.content or "").strip()
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        raise ValueError(f"no JSON in answer: {content[:120]!r}")
    data = json.loads(match.group(0))
    if not isinstance(data.get("decorative"), bool):
        raise ValueError(f"no decorative flag in answer: {content[:120]!r}")
    return {"decorative": data["decorative"], "what": str(data.get("what") or "").strip(),
            "usage": getattr(response, "usage", None)}


def classify_candidates(min_copies: int = 3, limit: Optional[int] = None,
                        include_decided: bool = False, session=None) -> List[Dict[str, Any]]:
    """Registry rows the classifier would look at: repeated images without a decision."""
    with get_db_session(session) as session:
        query = session.query(ImageHash).filter(
            ImageHash.copies >= min_copies,
            ImageHash.description.isnot(None),
            ImageHash.description != boilerplate.UNAVAILABLE,
        )
        if include_decided:
            query = query.filter(func.coalesce(ImageHash.decided_by, "") != ImageHash.DECIDED_BY_REVIEW)
        else:
            query = query.filter(ImageHash.decorative.is_(None))
        query = query.order_by(ImageHash.copies.desc(), ImageHash.image_hash)
        if limit:
            query = query.limit(limit)
        return [{"image_hash": r.image_hash, "description": r.description, "copies": r.copies,
                 "documents": r.documents, "entries": r.entries, "bytes": r.bytes} for r in query]


def classify_images(min_copies: int = 3, limit: Optional[int] = None, model: Optional[str] = None,
                    concurrency: int = 4, include_decided: bool = False,
                    progress=None) -> Dict[str, Any]:
    """Ask an LLM which repeated images are decorative; store the answers in the registry.

    Uses the image-description model by default: the endpoint that already
    saw the images. A reviewer's decision is never overwritten.
    """
    from ..config import get_parser_config
    from ..llm.llm import get_openai_client
    from ..llm.usage import STAGE_IMAGE_CLASSIFICATION, UsageAccumulator, record_llm_usage

    model = model or get_parser_config()["image_description_model"]
    rows = classify_candidates(min_copies=min_copies, limit=limit, include_decided=include_decided)
    if not rows:
        return {"candidates": 0, "decorative": 0, "content": 0, "errors": 0, "model": model}
    client = get_openai_client(model)

    def work(row):
        try:
            return row, _classify_one(client, model, row), None
        except Exception as e:  # noqa: BLE001 - counted and reported
            return row, None, e

    results = {"candidates": len(rows), "decorative": 0, "content": 0, "errors": 0, "model": model,
               "first_error": None}
    tokens = UsageAccumulator()
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for row, answer, error in pool.map(work, rows):
            if answer is not None:
                record_llm_usage(answer.get("usage"), stage=STAGE_IMAGE_CLASSIFICATION, model=model,
                                 accumulator=tokens, meta={"image_hash": row["image_hash"]})
            if error is not None:
                results["errors"] += 1
                results["first_error"] = results["first_error"] or f"{type(error).__name__}: {error}"
                continue
            label = boilerplate.make_label(answer["what"]) if answer["decorative"] else None
            with get_db_session() as session:
                entry = session.get(ImageHash, row["image_hash"])
                if entry is None or entry.decided_by == ImageHash.DECIDED_BY_REVIEW:
                    continue
                entry.decorative = answer["decorative"]
                entry.label = label
                entry.decided_by = ImageHash.DECIDED_BY_LLM
                entry.model = model
                entry.meta = {**(entry.meta or {}), "classified_as": answer["what"]}
            results["decorative" if answer["decorative"] else "content"] += 1
            if progress:
                progress(row, answer)
    results["tokens"] = tokens.summary()
    logger.info(f"Image classification tokens — {tokens.format_summary()}")
    return results


def decide_images(hashes: Iterable[str], decorative: bool, what: Optional[str] = None,
                  user: Optional[str] = None, session=None) -> int:
    """A reviewer's decision for images (by hash or unique hash prefix). Final."""
    changed = 0
    with get_db_session(session) as session:
        for prefix in hashes:
            matches = session.query(ImageHash).filter(ImageHash.image_hash.like(f"{prefix}%")).limit(2).all()
            if len(matches) != 1:
                raise ValueError(f"{prefix!r} matches {len(matches)} images; give more of the hash")
            entry = matches[0]
            entry.decorative = decorative
            if decorative:
                entry.label = boilerplate.make_label(what) if what else (entry.label or boilerplate.LABEL_PREFIX)
            entry.decided_by = ImageHash.DECIDED_BY_REVIEW
            entry.meta = {**(entry.meta or {}), "reviewed_by": user}
            changed += 1
    return changed


def list_images(decorative: Optional[str] = None, min_copies: int = 1, decided_by: Optional[str] = None,
                limit: int = 50, session=None) -> List[Dict[str, Any]]:
    """Registry rows for review. decorative: "yes", "no", "undecided" or None (all)."""
    with get_db_session(session) as session:
        query = session.query(ImageHash).filter(func.coalesce(ImageHash.copies, 0) >= min_copies)
        if decorative == "yes":
            query = query.filter(ImageHash.decorative.is_(True))
        elif decorative == "no":
            query = query.filter(ImageHash.decorative.is_(False))
        elif decorative == "undecided":
            query = query.filter(ImageHash.decorative.is_(None))
        if decided_by:
            query = query.filter(ImageHash.decided_by == decided_by)
        query = query.order_by(ImageHash.copies.desc().nullslast(), ImageHash.image_hash).limit(limit)
        return [{"image_hash": r.image_hash, "decorative": r.decorative, "label": r.label,
                 "decided_by": r.decided_by, "copies": r.copies, "documents": r.documents,
                 "entries": r.entries, "bytes": r.bytes, "description": r.description} for r in query]


# --- 3. documents -------------------------------------------------------------------------

def replace_inline_description(text: str, image_ref: str, new_alt: str) -> tuple[str, int]:
    """Replace the alt text of `![...](image_ref) [image_id:image_ref ...]` in a parent's text.

    parse.py inlines each image as `![{description}]({ref}) [image_id:{ref} image_num:N]`.
    The match is anchored on the reference, and the alt text is taken back
    to the nearest `![` before it, so a description containing brackets is
    still replaced whole. Returns (new text, number of replacements).
    """
    # The link target is the record's doc_id (Docling) or the bare filename
    # the markdown referenced (other parsers); the image_id tag is always the
    # record's doc_id, so anchor on that and keep the link target as it is.
    anchor = re.compile(r"\]\(([^()\s]*)\) \[image_id:" + re.escape(image_ref) + r"(?=[\] ])")
    new_alt = " ".join(new_alt.split()).replace("](", "] (")
    out, pos, count = [], 0, 0
    for match in anchor.finditer(text):
        end = match.start()
        if end < pos:
            continue
        start = text.rfind("![", pos, end)
        if start < 0:
            continue
        out.append(text[pos:start])
        out.append(f"![{new_alt}")
        out.append(match.group(0))
        pos = match.end()
        count += 1
    out.append(text[pos:])
    return "".join(out), count


def _drop_text_chunks(session, document_id: str) -> int:
    """Drop a document's chunks except its summary chunk (the summary is unchanged)."""
    from .embedding.db_models import Chunk

    return session.query(Chunk).filter(
        Chunk.document_id == document_id,
        ~Chunk.chunk_strategy.like("summary%"),
    ).delete(synchronize_session=False)


def apply_image_decisions(apply: bool = False, hashes: Optional[Iterable[str]] = None,
                          parents: bool = False, rechunk: bool = True,
                          limit_parents: Optional[int] = None, session=None) -> Dict[str, Any]:
    """Make image records (and optionally parent texts) match the registry's decisions.

    Demotes images whose hash is decided decorative and restores demoted
    images whose hash is now decided as content. Dry run unless `apply`.

    By default only the image records change: a demoted image loses its
    chunks, so its copies stop showing up as separate search results. The
    parents keep the full description inlined in their text, and their
    chunks are untouched. With `parents`, the inline description is swapped
    for the label too, and each changed parent is re-chunked and re-embedded
    (`rechunk`) -- far more work, for the smaller gain of leaner parent chunks.
    """
    from .embedding.db_models import Chunk

    result = {"apply": apply, "demote_images": 0, "restore_images": 0, "parents": 0,
              "restore_missing": 0, "chars_saved": 0, "chunks_dropped": 0, "rechunked": 0, "rechunk_errors": 0,
              "examples": []}
    rechunk_ids: List[str] = []
    with get_db_session(session) as session:
        query = session.query(ImageHash).filter(ImageHash.decorative.isnot(None))
        if hashes:
            query = query.filter(ImageHash.image_hash.in_(list(hashes)))
        decisions = {r.image_hash: r for r in query}
        if not decisions:
            return result

        # Image records whose state differs from their hash's decision.
        changes = defaultdict(list)          # parent_id -> [(image doc, new alt text)]
        images = session.query(Document).options(defer(Document.binary)).filter(
            Document.doc_type == "image",
            Document.meta["image_hash"].astext.in_(list(decisions)),
        )
        for doc in images.yield_per(1000):
            meta = doc.meta or {}
            decision = decisions[meta["image_hash"]]
            demoted = bool(meta.get("boilerplate"))
            if decision.decorative and not demoted:
                label = decision.label or boilerplate.LABEL_PREFIX
                changes[doc.parent_id].append((doc, "demote", label))
            elif decision.decorative is False and demoted:
                # Demoted at parse time by the vision model, an image has no
                # description to restore: it needs `kb tools parse-all` again.
                original = meta["boilerplate"].get("description")
                if original:
                    changes[doc.parent_id].append((doc, "restore", original))
                else:
                    result["restore_missing"] += 1

        parent_ids = [p for p in changes if p]
        if limit_parents:
            parent_ids = parent_ids[:limit_parents]
            changes = {p: changes[p] for p in parent_ids}
        for parent_id, items in changes.items():
            for doc, action, _ in items:
                result["demote_images" if action == "demote" else "restore_images"] += 1
        if not apply:
            for parent_id in parent_ids[:5]:
                doc, action, alt = changes[parent_id][0]
                result["examples"].append({"image": doc.doc_id, "action": action, "alt": alt[:120]})
            if not parents:
                return result
            result["parents"] = len(parent_ids)
            for parent in session.query(Document).options(defer(Document.binary)).filter(
                    Document.id.in_(parent_ids[:2000])):
                new_text = parent.text or ""
                for doc, _, alt in changes[parent.id]:
                    new_text, _ = replace_inline_description(new_text, doc.doc_id, alt)
                result["chars_saved"] += len(parent.text or "") - len(new_text)
            return result

        for parent_id, items in changes.items():
            for doc, action, alt in items:
                meta = dict(doc.meta or {})
                if action == "demote":
                    original = meta.get("description")
                    info = {"label": alt, "decided_by": decisions[meta["image_hash"]].decided_by}
                    if original and original != alt:
                        info["description"] = original
                    info["text"] = doc.text
                    meta["boilerplate"] = info
                    meta["description"] = alt
                    doc.text = alt
                else:
                    info = meta.pop("boilerplate", {}) or {}
                    meta["description"] = alt
                    doc.text = info.get("text") or alt
                doc.meta = meta
                doc.content_hash = hashlib.sha256(doc.text.encode("utf-8")).hexdigest()
                result["chunks_dropped"] += session.query(Chunk).filter(
                    Chunk.document_id == doc.id).delete(synchronize_session=False)

            if not parents:
                continue
            parent = session.get(Document, parent_id) if parent_id else None
            if parent is None or not parent.text:
                continue
            new_text = parent.text
            for doc, _, alt in items:
                new_text, _ = replace_inline_description(new_text, doc.doc_id, alt)
            if new_text != parent.text:
                result["chars_saved"] += len(parent.text) - len(new_text)
                parent.text = new_text
                parent.content_hash = hashlib.sha256(new_text.encode("utf-8")).hexdigest()
                result["chunks_dropped"] += _drop_text_chunks(session, parent.id)
                result["parents"] += 1
                rechunk_ids.append(parent.id)
        session.commit()

    if rechunk:
        for parent_id in rechunk_ids:
            try:
                with get_db_session() as session:
                    parent = session.get(Document, parent_id)
                    parent.chunk_and_embed()
                result["rechunked"] += 1
            except Exception as e:  # noqa: BLE001 - left for the next embedding sweep
                result["rechunk_errors"] += 1
                logger.error(f"Re-chunking {parent_id} failed (the next embedding sweep retries): {e}")
    return result
