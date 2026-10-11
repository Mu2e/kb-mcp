"""Recognise images the corpus has seen before, and decorative ones.

A third of the image records are copies: slide-template logos in every deck,
the same plot in each revision of a talk. Each copy used to cost a vision
call, and each logo description was embedded and inlined into its parent's
text, crowding search results and bloating chunks.

Two things fix that, both keyed on the sha256 of the image bytes:

- **Reuse.** The `image_hashes` table remembers each distinct image's
  description. A known image is not sent to the vision model again, and an
  image repeated within one document is described once.
- **Decorative images.** The description prompt lets the model answer
  `DECORATIVE: <what>` for a logo, banner or template graphic. Such an image
  is *demoted*: it keeps its record (the parent's text references it) but
  its text becomes a short label, it is not embedded, and the parent's text
  carries the label instead of a paragraph about a logo. Decisions for
  images already in the corpus come from `kb tools images classify` and a
  reviewer (`kb tools images decide`); see `kb_mcp.kb.images`.

Repetition alone is not a reliable signal -- the same CAD drawing appears in
every revision of a talk, a material photo sixty times in one report -- so
nothing here is decided by counting copies.

The registry is best-effort: if the database cannot be read, every image is
simply described as before.
"""

import hashlib
import logging
import re
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

DECORATIVE_PREFIX = "DECORATIVE:"
LABEL_PREFIX = "decorative"
MAX_LABEL_CHARS = 80
# A real description is 2-4 sentences; a DECORATIVE answer is one short line.
# A long answer that happens to start with the prefix is treated as a
# description, so a model that rambles never demotes a real figure.
MAX_DECORATIVE_ANSWER_CHARS = 160

# Placeholder the description step writes when the vision call failed.
UNAVAILABLE = "Image description unavailable"


def image_hash(binary: bytes) -> str:
    """sha256 hex digest of the image bytes: the key of `image_hashes`."""
    return hashlib.sha256(binary).hexdigest()


def make_label(what: str) -> str:
    """'Fermilab logo' -> 'decorative: Fermilab logo' (one line, bounded)."""
    what = " ".join((what or "").split()).strip(" .:-\"'")
    what = re.sub(r"^decorative\s*:?\s*", "", what, flags=re.I)
    if not what:
        return LABEL_PREFIX
    if len(what) > MAX_LABEL_CHARS:
        what = what[:MAX_LABEL_CHARS].rsplit(" ", 1)[0] + "…"
    return f"{LABEL_PREFIX}: {what}"


def parse_decorative(answer: Optional[str]) -> Optional[str]:
    """The label if the vision model answered `DECORATIVE: <what>`, else None."""
    if not answer:
        return None
    text = answer.strip().strip("*`").strip()
    if len(text) > MAX_DECORATIVE_ANSWER_CHARS:
        return None
    if not text.upper().startswith(DECORATIVE_PREFIX):
        return None
    return make_label(text[len(DECORATIVE_PREFIX):])


def is_decorative(img_dict: Dict[str, Any]) -> bool:
    return bool((img_dict.get("meta") or {}).get("boilerplate"))


def mark_decorative(img_dict: Dict[str, Any], label: str, decided_by: str,
                    description: Optional[str] = None) -> None:
    """Demote an image record in place: short label as its text and description.

    The full description, when there is one, is kept in
    meta["boilerplate"]["description"] so a demotion can be undone.
    """
    meta = img_dict.setdefault("meta", {})
    info = {"label": label, "decided_by": decided_by}
    if description and description != label:
        info["description"] = description
    meta["boilerplate"] = info
    meta["description"] = label
    img_dict["text"] = label


def lookup(hashes: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """Known images among `hashes`: {hash: {"description", "decorative", "label"}}.

    Only rows that can stand in for a vision call are returned: a decorative
    decision with its label, or a description. Empty on any database error.
    """
    hashes = sorted(set(hashes))
    if not hashes:
        return {}
    try:
        from ..kb.database import get_db_session
        from ..kb.db_models import ImageHash

        known = {}
        with get_db_session() as session:
            for start in range(0, len(hashes), 500):
                rows = session.query(ImageHash).filter(ImageHash.image_hash.in_(hashes[start:start + 500])).all()
                for row in rows:
                    if row.decorative and row.label:
                        known[row.image_hash] = {"decorative": True, "label": row.label,
                                                 "description": row.description}
                    elif row.description and row.description != UNAVAILABLE:
                        known[row.image_hash] = {"decorative": bool(row.decorative), "label": None,
                                                 "description": row.description}
        return known
    except Exception as e:  # noqa: BLE001 - the registry only saves calls
        logger.warning(f"Image registry not available, describing every image: {e}")
        return {}


def remember(entries: Dict[str, Dict[str, Any]], model: Optional[str] = None) -> int:
    """Record newly described images: {hash: {"description", "decorative", "label", "bytes"}}.

    A decision already in the registry (in particular a reviewer's) is left
    alone; an existing row that has no description yet gets this one.
    Returns the number of rows added; 0 on any database error.
    """
    entries = {h: e for h, e in entries.items()
               if (e.get("decorative") and e.get("label"))
               or (e.get("description") and e["description"] != UNAVAILABLE)}
    if not entries:
        return 0
    try:
        from ..kb.database import get_db_session
        from ..kb.db_models import ImageHash

        with get_db_session() as session:
            existing = {row.image_hash: row for row in session.query(ImageHash)
                        .filter(ImageHash.image_hash.in_(list(entries))).all()}
            added = 0
            for h, e in entries.items():
                if h in existing:
                    row = existing[h]
                    if not row.description and e.get("description") and not e.get("decorative"):
                        row.description = e["description"]
                    continue
                decorative = e.get("decorative")
                session.add(ImageHash(
                    image_hash=h,
                    description=e.get("description"),
                    decorative=decorative,
                    label=e.get("label"),
                    decided_by=ImageHash.DECIDED_BY_VISION if decorative is not None else None,
                    model=model,
                    bytes=e.get("bytes"),
                    meta={},
                ))
                added += 1
        return added
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Could not record image descriptions in the registry: {e}")
        return 0
