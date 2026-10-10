"""Repeated and decorative images: reuse at parse time, registry, demotion of existing records."""

import types

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kb_mcp.parser import boilerplate
from kb_mcp.parser import image_descriptions


# --- the vision model's verdict ---------------------------------------------------------

@pytest.mark.parametrize("answer, label", [
    ("DECORATIVE: Fermilab logo", "decorative: Fermilab logo"),
    ("  **DECORATIVE: Mu2e logo.**", "decorative: Mu2e logo"),
    ("decorative:", "decorative"),
    ("Line plot of the trigger rate rising over time.", None),
    ("DECORATIVE: " + "a very long rambling answer " * 10, None),   # too long: treated as a description
    (None, None),
])
def test_decorative_answers(answer, label):
    assert boilerplate.parse_decorative(answer) == label


def test_prompt_offers_the_decorative_answer_but_not_for_content():
    prompt = image_descriptions._create_image_description_prompt("text", "img.png")
    assert "DECORATIVE: <what it is>" in prompt and "Never use this for a plot" in prompt


# --- parse time: describe each distinct image once, reuse the registry -------------------

LOGO, PLOT, KNOWN_PLOT, KNOWN_LOGO = b"logo-bytes", b"plot-bytes", b"known-plot", b"known-logo"


def image(binary, n, text=""):
    return {"doc_type": "image", "binary": binary, "text": text,
            "meta": {"image_name": f"_page_{n}_Figure_{n}.png", "image_number": n}}


@pytest.fixture()
def vision(monkeypatch):
    calls, remembered = [], {}
    answers = {LOGO: "DECORATIVE: Fermilab logo", PLOT: "Line plot of a rising rate."}

    def describe(client, document_text, image_base64, image_identifier, model, img_meta, title):
        import base64
        binary = base64.b64decode(image_base64)
        calls.append(binary)
        return answers[binary], None

    known = {
        boilerplate.image_hash(KNOWN_PLOT): {"decorative": False, "label": None, "description": "Known plot."},
        boilerplate.image_hash(KNOWN_LOGO): {"decorative": True, "label": "decorative: Mu2e logo",
                                             "description": "Mu2e logo with orbit."},
    }
    monkeypatch.setattr(image_descriptions, "_get_single_image_description", describe)
    monkeypatch.setattr(image_descriptions, "_preflight_model", lambda client, model: None)
    monkeypatch.setattr(boilerplate, "lookup", lambda hashes: {h: known[h] for h in hashes if h in known})
    monkeypatch.setattr(boilerplate, "remember", lambda entries, model=None: remembered.update(entries) or len(entries))
    monkeypatch.setattr(image_descriptions, "get_llm_config", lambda: {
        "openai_api_key": "k", "openai_api_key_models": {}, "openai_base_url_models": {}, "openai_base_url": None})
    monkeypatch.setattr(image_descriptions, "get_parser_config", lambda: {
        "image_description_model": "vision-model", "image_description_num_workers": 2})
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: types.SimpleNamespace())
    return types.SimpleNamespace(calls=calls, remembered=remembered)


def test_each_distinct_new_image_is_described_once(vision):
    images = [image(LOGO, 1), image(PLOT, 2, text="caption"), image(LOGO, 3), image(LOGO, 4)]
    usage = {}
    image_descriptions.generate_image_descriptions("doc", images, usage_out=usage)

    assert sorted(vision.calls) == sorted([LOGO, PLOT])          # three logo copies, one call
    for logo in (images[0], images[2], images[3]):
        assert logo["text"] == "decorative: Fermilab logo"
        assert logo["meta"]["description"] == "decorative: Fermilab logo"
        assert logo["meta"]["boilerplate"]["label"] == "decorative: Fermilab logo"
    assert images[1]["text"] == "caption\n\nLine plot of a rising rate."
    assert "boilerplate" not in images[1]["meta"]
    assert usage["images_decorative"] == 3
    assert vision.remembered[boilerplate.image_hash(LOGO)]["decorative"] is True
    assert vision.remembered[boilerplate.image_hash(PLOT)] == {
        "description": "Line plot of a rising rate.", "decorative": False, "label": None, "bytes": len(PLOT)}


def test_known_images_skip_the_vision_call(vision):
    images = [image(KNOWN_PLOT, 1, text="caption"), image(KNOWN_LOGO, 2), image(PLOT, 3)]
    usage = {}
    image_descriptions.generate_image_descriptions("doc", images, usage_out=usage)

    assert vision.calls == [PLOT]
    assert images[0]["text"] == "caption\n\nKnown plot." and images[0]["meta"]["description"] == "Known plot."
    assert images[1]["text"] == "decorative: Mu2e logo"
    assert images[1]["meta"]["boilerplate"]["description"] == "Mu2e logo with orbit."   # kept for undo
    assert usage["images_reused"] == 2
    assert all("image_hash" in img["meta"] for img in images)


def test_all_known_needs_no_endpoint(vision, monkeypatch):
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: pytest.fail("no client needed"))
    images = [image(KNOWN_PLOT, 1)]
    image_descriptions.generate_image_descriptions("doc", images)
    assert images[0]["text"] == "Known plot." and vision.calls == []


def test_failed_descriptions_are_passed_on_as_placeholders(vision, monkeypatch):
    def broken(*args):
        raise RuntimeError("endpoint down")
    monkeypatch.setattr(image_descriptions, "_get_single_image_description", broken)
    monkeypatch.setattr(image_descriptions, "call_with_backoff", lambda fn, *a, **k: fn(*a, **k))
    images = [image(PLOT, 1)]
    image_descriptions.generate_image_descriptions("doc", images)
    assert images[0]["text"] == boilerplate.UNAVAILABLE
    assert vision.remembered == {boilerplate.image_hash(PLOT): {
        "description": boilerplate.UNAVAILABLE, "decorative": False, "label": None, "bytes": len(PLOT)}}
    # ...and remember() drops the placeholder (next test).


def test_remember_skips_placeholders_and_keeps_existing_rows(db):
    from kb_mcp.kb.db_models import ImageHash

    db.add(ImageHash(image_hash="h-reviewed", decorative=False, decided_by="review", description="A plot."))
    db.commit()
    added = boilerplate.remember({
        "h-new": {"description": "A schematic.", "decorative": False, "label": None},
        "h-failed": {"description": boilerplate.UNAVAILABLE},
        "h-reviewed": {"description": "DECORATIVE", "decorative": True, "label": "decorative"},
    }, model="m")
    assert added == 1
    assert db.get(ImageHash, "h-reviewed").decided_by == "review"
    assert db.get(ImageHash, "h-new").decided_by == "vision"
    assert db.get(ImageHash, "h-failed") is None
    assert set(boilerplate.lookup(["h-new", "h-reviewed", "h-missing"])) == {"h-new", "h-reviewed"}


# --- existing corpus: registry, decisions, demotion --------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from kb_mcp.kb import database, images
    from kb_mcp.kb.db_models import Base, Source

    engine = create_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    session.add(Source(id="mu2e-docdb", name="DocDB"))
    session.commit()

    @contextmanager
    def fake_session(existing=None, **kwargs):
        if existing is not None:
            yield existing
            return
        s = Session()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    monkeypatch.setattr(database, "get_db_session", fake_session)
    monkeypatch.setattr(images, "get_db_session", fake_session)
    yield session
    session.close()


LOGO_DESC = "A logo with the word [Fermilab] in blue."


def add_deck(session, number, figures):
    """A parent document with inlined image descriptions, and its image records."""
    from kb_mcp.kb.db_models import Document

    parent = Document(source_id="mu2e-docdb", source_type="docdb", doc_type="text", doc_id=f"{number}-talk.pdf",
                      text="", meta={})
    session.add(parent)
    session.flush()
    parts = [f"Talk {number}."]
    for n, (binary, description) in enumerate(figures, start=1):
        ref = f"{number}-talk.pdf-_page_{n}_Figure_{n}.png"
        parts.append(f"![{description}]({ref}) [image_id:{ref} image_num:{n}]\nSlide {n} text.")
        session.add(Document(source_id="mu2e-docdb", source_type="docdb", doc_type="image", doc_id=ref,
                             parent_id=parent.id, binary=binary, text=f"nearby\n\n{description}",
                             meta={"description": description, "image_number": n}))
    parent.text = "\n\n".join(parts)
    session.commit()
    return parent


def test_replace_inline_description_handles_brackets_and_leaves_others():
    from kb_mcp.kb.images import replace_inline_description

    text = ("Intro ![A plot](d-1.png) [image_id:d-1.png image_num:1] mid "
            "![A logo with [brackets] inside](d-2.png) [image_id:d-2.png image_num:2] end")
    new, count = replace_inline_description(text, "d-2.png", "decorative: Fermilab logo")
    assert count == 1
    assert new == ("Intro ![A plot](d-1.png) [image_id:d-1.png image_num:1] mid "
                   "![decorative: Fermilab logo](d-2.png) [image_id:d-2.png image_num:2] end")
    assert replace_inline_description(text, "d-9.png", "x") == (text, 0)


def test_refresh_counts_copies_documents_and_entries(db):
    from kb_mcp.kb.db_models import ImageHash
    from kb_mcp.kb.images import refresh_registry

    for number in (101, 102, 103):
        add_deck(db, number, [(LOGO, LOGO_DESC), (b"plot-%d" % number, "A plot."), (LOGO, LOGO_DESC)])
    result = refresh_registry()
    assert result == {"hashed": 9, "distinct": 4, "added": 4, "updated": 0}
    logo = db.get(ImageHash, boilerplate.image_hash(LOGO))
    db.refresh(logo)
    assert (logo.copies, logo.documents, logo.entries, logo.bytes) == (6, 3, 3, len(LOGO))
    assert logo.description == LOGO_DESC and logo.decorative is None
    assert refresh_registry()["hashed"] == 0                          # hashes are stored once


def test_reviewer_decision_is_final_and_needs_a_unique_prefix(db):
    from kb_mcp.kb.db_models import ImageHash
    from kb_mcp.kb.images import classify_candidates, decide_images, refresh_registry

    add_deck(db, 101, [(LOGO, LOGO_DESC)] * 3)
    refresh_registry()
    h = boilerplate.image_hash(LOGO)
    assert [r["image_hash"] for r in classify_candidates(min_copies=3)] == [h]
    assert decide_images([h[:10]], decorative=True, what="Fermilab logo", user="alice") == 1
    row = db.get(ImageHash, h)
    db.refresh(row)
    assert (row.decorative, row.label, row.decided_by) == (True, "decorative: Fermilab logo", "review")
    assert classify_candidates(min_copies=3, include_decided=True) == []   # never re-classified
    with pytest.raises(ValueError, match="matches 0"):
        decide_images(["zzz"], decorative=False)


def test_classifier_answers_are_stored_but_not_over_a_review(db, monkeypatch):
    from kb_mcp.kb import images
    from kb_mcp.kb.db_models import ImageHash

    add_deck(db, 101, [(LOGO, LOGO_DESC)] * 3 + [(b"plot", "A plot.")] * 3)
    images.refresh_registry()
    answers = {LOGO_DESC: {"decorative": True, "what": "Fermilab logo"},
               "A plot.": {"decorative": False, "what": "rate plot"}}
    monkeypatch.setattr(images, "_classify_one", lambda client, model, row: answers[row["description"]])
    import kb_mcp.llm.llm as llm
    monkeypatch.setattr(llm, "get_openai_client", lambda model: object())

    result = images.classify_images(min_copies=3, model="m")
    assert (result["candidates"], result["decorative"], result["content"], result["errors"]) == (2, 1, 1, 0)
    logo = db.get(ImageHash, boilerplate.image_hash(LOGO))
    db.refresh(logo)
    assert (logo.decorative, logo.label, logo.decided_by) == (True, "decorative: Fermilab logo", "llm")


def test_apply_demotes_then_restores(db):
    from kb_mcp.kb.db_models import Document
    from kb_mcp.kb.embedding.db_models import Chunk
    from kb_mcp.kb.images import apply_image_decisions, decide_images, refresh_registry

    parent = add_deck(db, 101, [(LOGO, LOGO_DESC), (b"plot", "A plot.")])
    other = add_deck(db, 102, [(LOGO, LOGO_DESC)])
    original_text = parent.text
    logo_image = db.query(Document).filter(Document.doc_id == "101-talk.pdf-_page_1_Figure_1.png").one()
    for doc_id, strategy in ((parent.id, "tokens_512_51"), (parent.id, "summary_256"), (logo_image.id, "image")):
        db.add(Chunk(document_id=doc_id, text="chunk", chunk_index=0, chunk_strategy=strategy))
    db.commit()
    refresh_registry()
    decide_images([boilerplate.image_hash(LOGO)], decorative=True, what="Fermilab logo")
    db.expire_all()           # the registry was written through other sessions

    dry = apply_image_decisions(apply=False, session=db)
    assert (dry["demote_images"], dry["parents"]) == (2, 2) and dry["chars_saved"] > 0
    db.refresh(parent)
    assert parent.text == original_text                              # dry run changed nothing

    done = apply_image_decisions(apply=True, rechunk=False, session=db)
    assert (done["demote_images"], done["parents"]) == (2, 2)
    db.refresh(parent)
    db.refresh(logo_image)
    assert "![decorative: Fermilab logo](101-talk.pdf-_page_1_Figure_1.png)" in parent.text
    assert LOGO_DESC not in parent.text and "![A plot.]" in parent.text
    assert logo_image.text == "decorative: Fermilab logo"
    assert logo_image.meta["boilerplate"]["description"] == LOGO_DESC
    strategies = {c.chunk_strategy for c in db.query(Chunk)}
    assert strategies == {"summary_256"}          # parent text chunks and the logo's chunk are gone
    assert apply_image_decisions(apply=False, session=db)["demote_images"] == 0     # idempotent

    decide_images([boilerplate.image_hash(LOGO)], decorative=False)
    restored = apply_image_decisions(apply=True, rechunk=False, session=db)
    assert restored["restore_images"] == 2
    db.refresh(parent)
    db.refresh(logo_image)
    assert parent.text == original_text
    assert logo_image.text == f"nearby\n\n{LOGO_DESC}" and "boilerplate" not in logo_image.meta


def test_record_sweep_skips_decorative_images(db, monkeypatch):
    from kb_mcp.kb import tools
    from kb_mcp.kb.db_models import Document

    add_deck(db, 101, [(LOGO, LOGO_DESC), (b"plot", "A plot.")])
    logo = db.query(Document).filter(Document.doc_id.like("%Figure_1.png")).one()
    logo.meta = {**logo.meta, "boilerplate": {"label": "decorative: Fermilab logo"}}
    db.commit()
    embedded = []
    monkeypatch.setattr(Document, "chunk_and_embed", lambda self, **k: embedded.append(self.doc_id) or [1])
    result = tools.record_chunk_and_embed_all("mu2e-docdb", "image", session=db)
    assert result["chunked"] == 1 and embedded == ["101-talk.pdf-_page_2_Figure_2.png"]
