#!/usr/bin/env python3
"""Scatter plot comparing token counts per document between two parsers."""

import argparse
import sys
from pathlib import Path

# Allow running from repo root without installing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def main():
    parser = argparse.ArgumentParser(description="Scatter plot of token counts for two parsers")
    parser.add_argument("--source-id", default="sld-scanned", help="Source ID (default: sld-scanned)")
    parser.add_argument("--parser-a", default="marker", help="First parser (default: marker)")
    parser.add_argument("--parser-b", default="docling", help="Second parser (default: docling)")
    parser.add_argument("--output", default="parser_token_comparison.png", help="Output file (default: parser_token_comparison.png)")
    args = parser.parse_args()

    import matplotlib.pyplot as plt
    from kb_mcp.kb.db_models import Document
    from kb_mcp.kb.database import get_db_session
    from kb_mcp.chunking.chunking import count_tokens

    with get_db_session() as session:
        def fetch(parser_name):
            docs = session.query(Document.doc_id, Document.text).filter(
                Document.source_id == args.source_id,
                Document.parser_id == parser_name,
                Document.text.isnot(None),
                Document.text != "",
                Document.doc_type != "image",
            ).all()
            return {doc_id: text for doc_id, text in docs}

        a_docs = fetch(args.parser_a)
        b_docs = fetch(args.parser_b)

    common = set(a_docs) & set(b_docs)
    if not common:
        print(f"No documents in common between '{args.parser_a}' and '{args.parser_b}' for source '{args.source_id}'.")
        sys.exit(1)

    print(f"Found {len(a_docs)} '{args.parser_a}' docs, {len(b_docs)} '{args.parser_b}' docs, {len(common)} in common.")
    print(f"Computing token counts...")

    a_tokens = [count_tokens(a_docs[doc_id]) for doc_id in common]
    b_tokens = [count_tokens(b_docs[doc_id]) for doc_id in common]

    fig, ax = plt.subplots(figsize=(7, 7))
    ax.scatter(a_tokens, b_tokens, alpha=0.4, s=10, edgecolors="none")

    lim = max(max(a_tokens), max(b_tokens)) * 1.05
    ax.plot([0, lim], [0, lim], "k--", linewidth=0.8, label="y = x")

    ax.set_xlabel(f"Tokens ({args.parser_a})")
    ax.set_ylabel(f"Tokens ({args.parser_b})")
    ax.set_title(
        f"Token count per document: {args.parser_a} vs {args.parser_b}\n"
        f"({args.source_id}, n={len(common)})"
    )
    ax.legend()
    plt.tight_layout()
    plt.savefig(args.output, dpi=150)
    print(f"Plot saved to {args.output}")


if __name__ == "__main__":
    main()
