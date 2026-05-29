"""Export summaries for sld-scanned documents parsed with marker."""

from kb_mcp.kb.database import get_db_session
from kb_mcp.kb.db_models import Document

output_file = "analysis/sld_scanned_marker_summaries.txt"

with get_db_session() as session:
    docs = (
        session.query(Document)
        .filter(
            Document.source_id == "sld-scanned",
            Document.parser_id == "marker",
            Document.summary.isnot(None),
        )
        .order_by(Document.doc_id)
        .all()
    )

    with open(output_file, "w") as f:
        f.write(f"# SLD-Scanned Summaries (parser: marker)\n")
        f.write(f"# Total documents: {len(docs)}\n\n")

        for doc in docs:
            title = doc.title_gen or doc.title or doc.doc_id
            f.write(f"{'='*80}\n")
            f.write(f"Document: {doc.doc_id}\n")
            f.write(f"Title: {title}\n")
            f.write(f"{'='*80}\n")
            f.write(f"{doc.summary}\n\n")

print(f"Exported {len(docs)} summaries to {output_file}")
