"""Detailed pipeline statistics report for sld-scanned documents.

Covers:
  - Parsing (text extraction + image description) per parser
  - Summary / gist generation per model
  - Chunk embedding generation per strategy and embedding config
"""

import statistics
from collections import defaultdict
from datetime import timezone

from sqlalchemy import func

from kb_mcp.kb.database import get_db_session
from kb_mcp.kb.db_models import Document
from kb_mcp.kb.embedding.db_models import ChunkEmbeddingLog, ParsingLog, SummaryLog

SOURCE = "sld-scanned"
OUTPUT_FILE = "analysis/pipeline_stats_report.txt"

SEP = "=" * 80
SEP2 = "-" * 80


def fmt_time(seconds):
    if seconds is None:
        return "N/A"
    if seconds >= 3600:
        return f"{seconds/3600:.2f} h"
    if seconds >= 60:
        return f"{seconds/60:.2f} min"
    return f"{seconds:.2f} s"


def fmt_count(n):
    return f"{n:,}"


def pct(part, total):
    if total == 0:
        return "N/A"
    return f"{100*part/total:.1f}%"


def stats_block(values, label="Time", unit="s"):
    if not values:
        return f"  {label}: no data\n"
    n = len(values)
    total = sum(values)
    mean = statistics.mean(values)
    median = statistics.median(values)
    stdev = statistics.stdev(values) if n > 1 else 0.0
    mn = min(values)
    mx = max(values)
    p10 = sorted(values)[int(0.10 * n)]
    p90 = sorted(values)[int(0.90 * n)]
    lines = [
        f"  {label}:",
        f"    Count   : {fmt_count(n)}",
        f"    Total   : {fmt_time(total)}",
        f"    Mean    : {fmt_time(mean)}",
        f"    Median  : {fmt_time(median)}",
        f"    Stdev   : {fmt_time(stdev)}",
        f"    Min     : {fmt_time(mn)}",
        f"    P10     : {fmt_time(p10)}",
        f"    P90     : {fmt_time(p90)}",
        f"    Max     : {fmt_time(mx)}",
    ]
    return "\n".join(lines) + "\n"


def write_section(f, title):
    f.write(f"\n{SEP}\n{title}\n{SEP}\n")


def write_subsection(f, title):
    f.write(f"\n{SEP2}\n{title}\n{SEP2}\n")


def main():
    with get_db_session() as session:
        # ── gather document ids for sld-scanned ──────────────────────────────
        docs = (
            session.query(
                Document.id,
                Document.parser_id,
                Document.doc_id,
                func.length(Document.text).label("text_length"),
                Document.title_gen,
            )
            .filter(Document.source_id == SOURCE)
            .all()
        )
        doc_id_set = {d.id for d in docs}
        doc_by_id = {d.id: d for d in docs}
        total_docs = len(docs)

        # parser_id -> list of doc ids
        by_parser = defaultdict(list)
        for d in docs:
            by_parser[d.parser_id].append(d.id)

        # ── parsing logs ─────────────────────────────────────────────────────
        parsing_logs = (
            session.query(ParsingLog)
            .filter(ParsingLog.document_id.in_(doc_id_set))
            .all()
        )

        # ── summary logs ─────────────────────────────────────────────────────
        summary_logs = (
            session.query(SummaryLog)
            .filter(SummaryLog.document_id.in_(doc_id_set))
            .all()
        )

        # ── chunk embedding logs ──────────────────────────────────────────────
        embedding_logs = (
            session.query(ChunkEmbeddingLog)
            .filter(ChunkEmbeddingLog.document_id.in_(doc_id_set))
            .all()
        )

        with open(OUTPUT_FILE, "w") as f:
            f.write(f"Pipeline Statistics Report — source: {SOURCE}\n")
            f.write(f"{'='*80}\n")
            f.write(f"Total documents in source : {fmt_count(total_docs)}\n")
            f.write(f"Parsers                   : {', '.join(sorted(by_parser))}\n")

            docs_with_text = sum(1 for d in docs if d.text_length and d.text_length > 0)
            f.write(f"Docs with text            : {fmt_count(docs_with_text)} ({pct(docs_with_text, total_docs)})\n")
            text_lengths = [d.text_length for d in docs if d.text_length]
            if text_lengths:
                f.write(f"Text length (chars)       : mean={fmt_count(int(statistics.mean(text_lengths)))}  "
                        f"median={fmt_count(int(statistics.median(text_lengths)))}  "
                        f"total={fmt_count(sum(text_lengths))}\n")

            # ================================================================
            write_section(f, "1. PARSING  (text extraction + image description)")
            # ================================================================

            f.write(f"\nTotal parsing log entries : {fmt_count(len(parsing_logs))}\n")

            # group by parser
            parse_by_parser = defaultdict(list)
            for pl in parsing_logs:
                doc = doc_by_id.get(pl.document_id)
                parser = doc.parser_id if doc else "unknown"
                parse_by_parser[parser].append(pl)

            for parser in sorted(parse_by_parser):
                logs = parse_by_parser[parser]
                write_subsection(f, f"Parser: {parser}  ({fmt_count(len(logs))} log entries, "
                                    f"{fmt_count(len(by_parser[parser]))} docs)")

                total_times = [pl.total_time_seconds for pl in logs]
                text_times  = [pl.text_extraction_time_seconds for pl in logs]
                img_times   = [pl.image_description_time_seconds for pl in logs
                               if pl.image_description_time_seconds is not None]
                text_lens   = [pl.text_length for pl in logs if pl.text_length]
                num_docs    = [pl.num_documents for pl in logs]
                hostnames   = set(pl.hostname for pl in logs if pl.hostname)

                f.write(f"\n  Hostnames : {', '.join(sorted(hostnames)) or 'N/A'}\n")
                f.write(f"  Docs extracted / log entry: "
                        f"mean={statistics.mean(num_docs):.1f}  "
                        f"total={fmt_count(sum(num_docs))}\n")

                f.write("\n")
                f.write(stats_block(total_times, "Total parse time"))
                f.write(stats_block(text_times,  "  Text extraction time"))
                if img_times:
                    f.write(stats_block(img_times, "  Image description time"))
                    f.write(f"  Image description coverage: "
                            f"{fmt_count(len(img_times))}/{fmt_count(len(logs))} "
                            f"({pct(len(img_times), len(logs))})\n")
                else:
                    f.write("  Image description time: not recorded\n")

                if text_lens:
                    f.write(stats_block(text_lens, "  Text length (chars)", unit="chars"))

                # throughput
                if total_times and text_lens and len(total_times) == len(text_lens):
                    rates = [l/t for l, t in zip(text_lens, total_times) if t > 0]
                    if rates:
                        f.write(f"  Throughput (chars/s): "
                                f"mean={statistics.mean(rates):,.0f}  "
                                f"median={statistics.median(rates):,.0f}\n")

                # date range
                times = [pl.insertion_time for pl in logs if pl.insertion_time]
                if times:
                    earliest = min(times).astimezone(timezone.utc)
                    latest   = max(times).astimezone(timezone.utc)
                    f.write(f"  Date range: {earliest.strftime('%Y-%m-%d %H:%M')} UTC"
                            f" → {latest.strftime('%Y-%m-%d %H:%M')} UTC\n")

            # per-parser summary across all parsers
            write_subsection(f, "Cross-parser comparison (mean total parse time per doc)")
            for parser in sorted(parse_by_parser):
                logs = parse_by_parser[parser]
                times = [pl.total_time_seconds for pl in logs]
                if times:
                    f.write(f"  {parser:<20}: mean={fmt_time(statistics.mean(times))}  "
                            f"median={fmt_time(statistics.median(times))}  "
                            f"total={fmt_time(sum(times))}  "
                            f"n={fmt_count(len(times))}\n")

            # ================================================================
            write_section(f, "2. SUMMARY / GIST GENERATION")
            # ================================================================

            f.write(f"\nTotal summary log entries : {fmt_count(len(summary_logs))}\n")

            # docs that have summary
            docs_with_summary = (
                session.query(Document.parser_id, Document.id)
                .filter(Document.source_id == SOURCE, Document.summary.isnot(None))
                .all()
            )
            f.write(f"Docs with summary         : {fmt_count(len(docs_with_summary))} "
                    f"({pct(len(docs_with_summary), total_docs)})\n")

            # by parser
            sum_by_parser = defaultdict(int)
            for d in docs_with_summary:
                sum_by_parser[d.parser_id] += 1
            for parser in sorted(sum_by_parser):
                n_parser = len(by_parser[parser])
                f.write(f"  {parser:<20}: {fmt_count(sum_by_parser[parser])} / {fmt_count(n_parser)} "
                        f"({pct(sum_by_parser[parser], n_parser)})\n")

            # group summary logs by model
            sum_by_model = defaultdict(list)
            for sl in summary_logs:
                sum_by_model[sl.model].append(sl)

            for model in sorted(sum_by_model):
                logs = sum_by_model[model]
                write_subsection(f, f"Model: {model}  ({fmt_count(len(logs))} log entries)")

                times = [sl.time_summary for sl in logs]
                hostnames = set(sl.hostname for sl in logs if sl.hostname)
                f.write(f"\n  Hostnames : {', '.join(sorted(hostnames)) or 'N/A'}\n")

                f.write("\n")
                f.write(stats_block(times, "Summary generation time"))

                # meta field: may contain token counts
                input_tokens  = [sl.meta.get("input_tokens")  for sl in logs
                                  if sl.meta and sl.meta.get("input_tokens")]
                output_tokens = [sl.meta.get("output_tokens") for sl in logs
                                  if sl.meta and sl.meta.get("output_tokens")]
                if input_tokens:
                    f.write(stats_block(input_tokens, "  Input tokens", unit="tok"))
                if output_tokens:
                    f.write(stats_block(output_tokens, "  Output tokens", unit="tok"))

                date_times = [sl.insertion_time for sl in logs if sl.insertion_time]
                if date_times:
                    earliest = min(date_times).astimezone(timezone.utc)
                    latest   = max(date_times).astimezone(timezone.utc)
                    f.write(f"  Date range: {earliest.strftime('%Y-%m-%d %H:%M')} UTC"
                            f" → {latest.strftime('%Y-%m-%d %H:%M')} UTC\n")

            # ================================================================
            write_section(f, "3. CHUNK EMBEDDING GENERATION")
            # ================================================================

            f.write(f"\nTotal embedding log entries : {fmt_count(len(embedding_logs))}\n")

            # group by (chunk_strategy, embedding_name)
            emb_by_config = defaultdict(list)
            for el in embedding_logs:
                key = (el.chunk_strategy or "?", el.embedding_name or "?")
                emb_by_config[key].append(el)

            for (strategy, emb_name) in sorted(emb_by_config):
                logs = emb_by_config[(strategy, emb_name)]
                write_subsection(f, f"Strategy: {strategy}  |  Embedding: {emb_name}  "
                                    f"({fmt_count(len(logs))} log entries)")

                total_times = [el.total_time_seconds    for el in logs]
                chunk_times = [el.chunking_time_seconds  for el in logs]
                emb_times   = [el.embedding_time_seconds for el in logs]
                num_chunks  = [el.num_chunks    for el in logs]
                num_embs    = [el.num_embeddings for el in logs]
                hostnames   = set(el.hostname for el in logs if el.hostname)

                f.write(f"\n  Hostnames : {', '.join(sorted(hostnames)) or 'N/A'}\n")
                f.write(f"  Chunks per doc: mean={statistics.mean(num_chunks):.1f}  "
                        f"median={statistics.median(num_chunks):.0f}  "
                        f"total={fmt_count(sum(num_chunks))}\n")
                f.write(f"  Embeddings per doc: mean={statistics.mean(num_embs):.1f}  "
                        f"total={fmt_count(sum(num_embs))}\n")

                f.write("\n")
                f.write(stats_block(total_times, "Total embed time"))
                f.write(stats_block(chunk_times, "  Chunking time"))
                f.write(stats_block(emb_times,   "  Embedding time"))

                # throughput
                rates = [nc/t for nc, t in zip(num_chunks, emb_times) if t > 0]
                if rates:
                    f.write(f"  Embedding throughput (chunks/s): "
                            f"mean={statistics.mean(rates):.2f}  "
                            f"median={statistics.median(rates):.2f}\n")

                date_times = [el.insertion_time for el in logs if el.insertion_time]
                if date_times:
                    earliest = min(date_times).astimezone(timezone.utc)
                    latest   = max(date_times).astimezone(timezone.utc)
                    f.write(f"  Date range: {earliest.strftime('%Y-%m-%d %H:%M')} UTC"
                            f" → {latest.strftime('%Y-%m-%d %H:%M')} UTC\n")

            # cross-config summary
            write_subsection(f, "Cross-config comparison (mean total embed time per doc)")
            for (strategy, emb_name) in sorted(emb_by_config):
                logs = emb_by_config[(strategy, emb_name)]
                times = [el.total_time_seconds for el in logs]
                f.write(f"  {strategy} / {emb_name:<30}: "
                        f"mean={fmt_time(statistics.mean(times))}  "
                        f"total={fmt_time(sum(times))}  "
                        f"n={fmt_count(len(times))}\n")

            # ================================================================
            write_section(f, "4. OVERALL PIPELINE COST SUMMARY (sld-scanned)")
            # ================================================================

            total_parse_time = sum(pl.total_time_seconds for pl in parsing_logs)
            total_summary_time = sum(sl.time_summary for sl in summary_logs)
            total_embed_time = sum(el.total_time_seconds for el in embedding_logs)
            grand_total = total_parse_time + total_summary_time + total_embed_time

            f.write(f"\n  {'Stage':<35} {'Total time':>12}  {'% of total':>10}\n")
            f.write(f"  {'-'*60}\n")
            f.write(f"  {'Parsing (all parsers)':<35} {fmt_time(total_parse_time):>12}  "
                    f"{pct(total_parse_time, grand_total):>10}\n")
            f.write(f"  {'Summary/gist generation':<35} {fmt_time(total_summary_time):>12}  "
                    f"{pct(total_summary_time, grand_total):>10}\n")
            f.write(f"  {'Chunk embedding':<35} {fmt_time(total_embed_time):>12}  "
                    f"{pct(total_embed_time, grand_total):>10}\n")
            f.write(f"  {'-'*60}\n")
            f.write(f"  {'Grand total':<35} {fmt_time(grand_total):>12}  {'100.0%':>10}\n")

            f.write(f"\n")

    print(f"Report written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
