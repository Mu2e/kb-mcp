"""enforce_visibility on the SQLite fallback search, not only on pgvector.

Before it was an explicit parameter, the flag fell into **kwargs and became a
`meta.enforce_visibility == true` filter, dropping every normal document while
never consulting document_visibility_current.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kb_mcp.kb.db_models import Base, Document, DocumentVisibilityCurrent, RawDocument, Source
from kb_mcp.kb.embedding.db_models import Chunk, create_embedding_table
from kb_mcp.kb.search.search_fallback import _search_fallback


def test_fallback_hides_documents_hidden_from_search(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/fallback.db")
    table = create_embedding_table("fallback_visibility_test", 3)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    session.add(Source(id="mu2e-docdb"))
    for name in ("visible", "hidden"):
        session.add(RawDocument(id=f"raw-{name}", source_id="mu2e-docdb", source_type="text/plain",
                                content_hash=name))
        session.add(Document(id=f"doc-{name}", source_id="mu2e-docdb", raw_document_id=f"raw-{name}",
                             source_type="text/plain", text=name))
        session.add(Chunk(id=f"chunk-{name}", document_id=f"doc-{name}", text=name, chunk_index=0,
                          chunk_strategy="tokens_512_51"))
    session.add(DocumentVisibilityCurrent(raw_document_id="raw-hidden", group_name="*",
                                          visible_search=False, visible_get=True))
    session.flush()
    session.execute(table.insert(), [{"id": "e1", "chunk_id": "chunk-visible", "embedding": [1.0, 0.0, 0.0]},
                                     {"id": "e2", "chunk_id": "chunk-hidden", "embedding": [1.0, 0.0, 0.0]}])
    session.commit()

    def found(**kwargs):
        result = _search_fallback(session, table, [1.0, 0.0, 0.0], max_results=10, source_id=None,
                                  doc_type=None, chunking_strategy=None, filter=None,
                                  max_chunks_per_doc=3, **kwargs)
        return sorted(r["document"].id for r in result["results"])

    assert found() == ["doc-hidden", "doc-visible"]
    assert found(enforce_visibility=True) == ["doc-visible"]
