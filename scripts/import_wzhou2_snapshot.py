#!/usr/bin/env python3
"""One-off import: merge wzhou2's kb_mcp database snapshot into our database.

Source: a pg_dump custom-format snapshot of wzhou2's own kb_mcp database
(/exp/mu2e/data/users/wzhou2/snapshots/kb_mcp-*.dump), predating our
document_parser_outputs split and the chunks-provenance-into-meta
consolidation. This script:

  1. Extracts each table's data from the dump as plain-text COPY blocks
     (via `pg_restore --data-only --table=X -f ...`, no live source server
     needed).
  2. Loads tables with no schema difference directly via COPY, in FK order.
  3. Loads `documents` and `chunks` into staging tables first (matching the
     dump's older column shape), then INSERT ... SELECT into the real
     tables, translating docling_json -> document_parser_outputs and
     page_start/page_end/bbox/body_self_refs -> chunks.meta along the way.

Idempotent: every insert uses ON CONFLICT DO NOTHING keyed on primary key,
so a failed/interrupted run can be safely re-run from the top. Rows whose
content_hash already exists under the same source_id are also skipped for
`documents`, to avoid re-importing content we already have from a
different, non-colliding doc_id (see the 3 known content_hash collisions
found during inspection).

Exception: graph_node_types / graph_verbs were seeded independently on
both sides (same labels/names, different ids). Per decision, the incoming
ontology is treated as canonical - existing rows are deleted and replaced
with the dump's, rather than merged. Safe only because our graph_nodes /
graph_relations / graph_node_map / graph_relations_evidence /
graph_extraction_logs were all empty at the time this was written (checked
before running); if that's no longer true when you run this, the DELETE
will fail on the FK constraint rather than silently orphaning rows.

Run:
    python scripts/import_wzhou2_snapshot.py /path/to/kb_mcp-TIMESTAMP.dump
"""

import argparse
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import psycopg2

from kb_mcp.kb.database import get_database_url

# Tables with identical schema on both sides: straight COPY.
# Split into pre/post groups around documents/chunks (handled specially,
# see load_documents/load_chunks) because several of these FK onto
# documents.id or chunks.id and must load after those tables are populated.
PRE_DOCUMENTS_TABLES = [
    "parsers",
    "chunk_strategies",
    "sources",
    "embedding_configs",
    "documents_raw",  # documents.raw_document_id FKs onto this - must load first
    "eval_generation",  # no FK onto documents/chunks; safe either side, kept early
    "eval_runs",
]

# graph_node_types / graph_verbs: seeded independently on both sides (same
# labels, different ids). Per decision, wzhou2's ontology becomes canonical:
# delete ours, replace with theirs (see load_replace). Must run before
# graph_nodes/graph_relations, which FK onto these.
REPLACE_TABLES = [
    "graph_node_types",
    "graph_verbs",
]

# FKs onto documents.id and/or chunks.id - must load after those tables.
POST_DOCUMENTS_TABLES = [
    "embeddings_st_minilml6v2",  # FK: chunk_id -> chunks.id
    "graph_nodes",
    "graph_relations",
    "graph_relations_evidence",  # FK: document_id -> documents.id
    "graph_node_map",  # FK: document_id -> documents.id
    "graph_extraction_logs",  # FK: document_id -> documents.id
    "logs_parsing",  # FK: document_id -> documents.id
    "logs_summary",  # FK: document_id -> documents.id
    "logs_search",
    "logs_chunk_embedding",  # FK: document_id -> documents.id
    "eval_dataset",  # FK: source_document_id -> documents.id
    "eval_results",
    "eval_retrieved_documents",  # FK: document_id -> documents.id
    "eval_audit",
]

# (column, ref_table) per table in POST_DOCUMENTS_TABLES whose FK target
# can legitimately be missing (a document/chunk load_documents/load_chunks
# skipped as a content_hash duplicate) - see load_direct's fk_filter arg.
# Composite-key or empty-in-this-dump tables are omitted; add here if a
# future dump actually populates them and hits the same class of FK error.
FK_FILTERS = {
    "embeddings_st_minilml6v2": ("chunk_id", "chunks"),
    "logs_parsing": ("document_id", "documents"),
    "logs_summary": ("document_id", "documents"),
    "logs_chunk_embedding": ("document_id", "documents"),
    "graph_relations_evidence": ("document_id", "documents"),
    "graph_node_map": ("document_id", "documents"),
    "graph_extraction_logs": ("document_id", "documents"),
    "eval_dataset": ("source_document_id", "documents"),
    "eval_retrieved_documents": ("document_id", "documents"),
}

# Primary key column(s) per table, for ON CONFLICT DO NOTHING.
PRIMARY_KEYS = {
    "parsers": "name",
    "chunk_strategies": "strategy",
    "sources": "id",
    "embedding_configs": "short_name",
    "documents_raw": "id",
    "embeddings_st_minilml6v2": "id",
    "graph_node_types": "id",
    "graph_verbs": "id",
    "graph_nodes": "id",
    "graph_relations": "id",
    "graph_relations_evidence": "id",
    "graph_node_map": "node_id, document_id",  # composite PK
    "graph_extraction_logs": "id",
    "logs_parsing": "id",
    "logs_summary": "id",
    "logs_search": "id",
    "logs_chunk_embedding": "id",
    "eval_generation": "id",
    "eval_dataset": "id",
    "eval_runs": "id",
    "eval_results": "id",
    "eval_retrieved_documents": "id",
    "eval_audit": "id",
}

DOCUMENTS_DUMP_COLUMNS = [
    # "binary" is quoted: it's not a Postgres reserved word, but it is a
    # non-reserved keyword that the parser can't disambiguate from a type
    # name in several contexts (bare CREATE TABLE column position, bare
    # SELECT/INSERT column lists) - quoting sidesteps that everywhere.
    "id", "source_id", "raw_document_id", "parser_id", "doc_id", "uri",
    "source_type", "doc_type", "text", '"binary"', "meta", "creating_time",
    "update_time", "insert_time", "parent_id", "title", "title_gen",
    "summary", "gist", "content_hash", "docling_json",
]

CHUNKS_DUMP_COLUMNS = [
    "id", "document_id", "text", "chunk_index", "char_start_index",
    "char_end_index", "token_length", "section_path", "page_start",
    "page_end", "bbox", "body_self_refs", "chunk_strategy", "meta",
    "created_time", "text_search_vector",
]


def extract_table(dump_path: str, table: str, out_dir: str) -> str:
    """pg_restore --data-only for one table -> plain-SQL file. Returns path."""
    out_path = os.path.join(out_dir, f"{table}.sql")
    subprocess.run(
        [
            "pg_restore", "--data-only", f"--table={table}",
            "-f", out_path, dump_path,
        ],
        check=True,
    )
    return out_path


def copy_block_file(sql_path: str, table: str, out_dir: str) -> str:
    """Slice the `COPY ... FROM stdin; ... \\.` block out of a pg_restore
    plain-SQL file into its own file, ready to feed to copy_expert.
    Returns the path, or None if the table had zero rows (no COPY block).
    """
    block_path = os.path.join(out_dir, f"{table}.copy")
    in_block = False
    wrote_any = False
    with open(sql_path, "r", errors="replace") as src, open(block_path, "w") as dst:
        for line in src:
            if line.startswith(f"COPY public.{table} "):
                in_block = True
                wrote_any = True
                continue
            if in_block:
                if line.strip() == "\\.":
                    break
                dst.write(line)
    return block_path if wrote_any else None


def load_direct(conn, dump_path: str, table: str, out_dir: str, fk_filter=None) -> int:
    """Extract + COPY a schema-identical table straight into the target DB,
    via a staging table so ON CONFLICT DO NOTHING can be applied (COPY
    itself has no conflict handling).

    fk_filter, if given, is (column, ref_table) - rows whose `column` value
    isn't present in `ref_table.id` are dropped before insert. Needed for
    tables FK'ing onto documents/chunks, since load_documents/load_chunks
    can legitimately skip rows (content_hash dedup), and a plain copy would
    otherwise try to insert e.g. an embedding for a chunk that was never
    inserted, violating the FK.
    """
    sql_path = extract_table(dump_path, table, out_dir)
    block_path = copy_block_file(sql_path, table, out_dir)
    if block_path is None:
        print(f"  {table}: 0 rows in dump, skipping")
        return 0

    with conn.cursor() as cur:
        cur.execute(f"CREATE TEMP TABLE staging_{table} (LIKE {table} INCLUDING ALL)")
        with open(block_path, "r") as f:
            cur.copy_expert(f"COPY staging_{table} FROM STDIN", f)

        cur.execute(f"SELECT COUNT(*) FROM staging_{table}")
        total = cur.fetchone()[0]

        dropped = 0
        if fk_filter is not None:
            column, ref_table = fk_filter
            cur.execute(
                f"DELETE FROM staging_{table} s "
                f"WHERE s.{column} IS NOT NULL "
                f"AND NOT EXISTS (SELECT 1 FROM {ref_table} r WHERE r.id = s.{column})"
            )
            dropped = cur.rowcount

        pk = PRIMARY_KEYS[table]
        cur.execute(
            f"INSERT INTO {table} SELECT * FROM staging_{table} "
            f"ON CONFLICT ({pk}) DO NOTHING"
        )
        inserted = cur.rowcount
        cur.execute(f"DROP TABLE staging_{table}")
    conn.commit()
    already_present = total - dropped - inserted
    if dropped:
        print(f"  {table}: {dropped} rows dropped (dangling FK on {fk_filter[0]})")
    print(f"  {table}: {inserted}/{total} rows inserted ({already_present} already present)")
    return inserted


def load_replace(conn, dump_path: str, table: str, out_dir: str) -> int:
    """For graph_node_types / graph_verbs: our existing rows and wzhou2's
    were seeded independently, so they share labels/names but not ids.
    Per decision: treat the incoming (wzhou2) ontology as canonical -
    delete our existing rows and replace with theirs, rather than trying
    to merge by id. Safe only because nothing in our DB references the old
    ids yet (checked before running this: graph_nodes/graph_relations/etc.
    are all empty) - if that's no longer true, this will fail loudly on
    the FK constraint rather than silently orphaning rows.

    Idempotent re-run guard: if the table's ids already exactly match the
    dump's (i.e. a prior run already replaced them), skip - re-deleting
    would hit ON DELETE RESTRICT from graph_nodes/graph_relations once
    those are populated by a later step, even though the ontology itself
    is unchanged.
    """
    sql_path = extract_table(dump_path, table, out_dir)
    block_path = copy_block_file(sql_path, table, out_dir)
    if block_path is None:
        print(f"  {table}: 0 rows in dump, skipping")
        return 0

    with conn.cursor() as cur:
        cur.execute(f"CREATE TEMP TABLE staging_{table} (LIKE {table} INCLUDING ALL)")
        with open(block_path, "r") as f:
            cur.copy_expert(f"COPY staging_{table} FROM STDIN", f)

        pk = PRIMARY_KEYS[table]
        cur.execute(
            f"SELECT (SELECT array_agg({pk} ORDER BY {pk}) FROM {table}) "
            f"= (SELECT array_agg({pk} ORDER BY {pk}) FROM staging_{table})"
        )
        already_matches = cur.fetchone()[0]
        if already_matches:
            cur.execute(f"DROP TABLE staging_{table}")
            conn.commit()
            print(f"  {table}: already matches incoming ontology, skipping replace")
            return 0

        cur.execute(f"DELETE FROM {table}")
        deleted = cur.rowcount
        cur.execute(f"INSERT INTO {table} SELECT * FROM staging_{table}")
        cur.execute(f"DROP TABLE staging_{table}")
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        total = cur.fetchone()[0]
    conn.commit()
    print(f"  {table}: replaced {deleted} existing rows with {total} incoming rows")
    return total


def load_documents(conn, dump_path: str, out_dir: str) -> int:
    """documents: docling_json -> a linked document_parser_outputs row.
    Also skips rows whose content_hash already exists for the same
    source_id (see module docstring: 3 known collisions on inspection).
    """
    sql_path = extract_table(dump_path, "documents", out_dir)
    block_path = copy_block_file(sql_path, "documents", out_dir)
    if block_path is None:
        print("  documents: 0 rows in dump, skipping")
        return 0

    with conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE staging_documents ("
            "id varchar(36), source_id varchar(256), raw_document_id varchar(36), "
            "parser_id varchar(128), doc_id varchar(512), uri varchar(2048), "
            "source_type varchar(128), doc_type varchar(64), text text, "
            '"binary" bytea, meta jsonb, creating_time timestamptz, '
            "update_time timestamptz, insert_time timestamptz, parent_id varchar(36), "
            "title text, title_gen text, summary text, gist text, "
            "content_hash varchar(64), docling_json jsonb)"
        )
        with open(block_path, "r") as f:
            cols = ", ".join(DOCUMENTS_DUMP_COLUMNS)
            cur.copy_expert(f"COPY staging_documents ({cols}) FROM STDIN", f)

        cur.execute("SELECT COUNT(*) FROM staging_documents")
        total = cur.fetchone()[0]

        # Skip rows that collide with an existing (source_id, content_hash)
        # pair already in `documents` - same content, different doc_id.
        cur.execute(
            """
            DELETE FROM staging_documents s
            USING documents d
            WHERE s.source_id = d.source_id
              AND s.content_hash = d.content_hash
              AND s.content_hash IS NOT NULL
            """
        )
        skipped_dupes = cur.rowcount

        # A child's parent_id may point at a row that got dropped above (a
        # dedup'd duplicate) or that was already missing/dangling in the
        # source dump. documents_parent_id_fkey isn't deferrable, so null
        # these out rather than letting the whole batch insert fail.
        cur.execute(
            """
            UPDATE staging_documents s
            SET parent_id = NULL
            WHERE s.parent_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM staging_documents p WHERE p.id = s.parent_id)
              AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = s.parent_id)
            """
        )
        orphaned_parents = cur.rowcount

        cur.execute(
            """
            INSERT INTO documents (
                id, source_id, raw_document_id, parser_id, doc_id, uri,
                source_type, doc_type, text, "binary", meta, creating_time,
                update_time, insert_time, parent_id, title, title_gen,
                summary, gist, content_hash
            )
            SELECT
                id, source_id, raw_document_id, parser_id, doc_id, uri,
                source_type, doc_type, text, "binary", meta, creating_time,
                update_time, insert_time, parent_id, title, title_gen,
                summary, gist, content_hash
            FROM staging_documents
            ON CONFLICT (id) DO NOTHING
            """
        )
        inserted = cur.rowcount

        cur.execute(
            """
            INSERT INTO document_parser_outputs (document_id, output)
            SELECT id, docling_json FROM staging_documents
            WHERE docling_json IS NOT NULL
            ON CONFLICT (document_id) DO NOTHING
            """
        )
        parser_outputs_inserted = cur.rowcount

        cur.execute("DROP TABLE staging_documents")
    conn.commit()
    print(
        f"  documents: {inserted}/{total} rows inserted "
        f"({skipped_dupes} skipped as content_hash duplicates, "
        f"{orphaned_parents} had parent_id nulled out (missing parent), "
        f"{parser_outputs_inserted} parser_output rows created)"
    )
    return inserted


def load_chunks(conn, dump_path: str, out_dir: str) -> int:
    """chunks: page_start/page_end/bbox/body_self_refs -> meta keys."""
    sql_path = extract_table(dump_path, "chunks", out_dir)
    block_path = copy_block_file(sql_path, "chunks", out_dir)
    if block_path is None:
        print("  chunks: 0 rows in dump, skipping")
        return 0

    with conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE staging_chunks ("
            "id varchar(36), document_id varchar(36), text text, "
            "chunk_index integer, char_start_index integer, char_end_index integer, "
            "token_length integer, section_path text, page_start integer, "
            "page_end integer, bbox jsonb, body_self_refs jsonb, "
            "chunk_strategy varchar(128), meta jsonb, created_time timestamptz, "
            "text_search_vector tsvector)"
        )
        with open(block_path, "r") as f:
            cols = ", ".join(CHUNKS_DUMP_COLUMNS)
            cur.copy_expert(f"COPY staging_chunks ({cols}) FROM STDIN", f)

        cur.execute("SELECT COUNT(*) FROM staging_chunks")
        total = cur.fetchone()[0]

        # Only keep chunks whose parent document actually made it into
        # `documents` (skips chunks belonging to documents we dropped as
        # content_hash duplicates above).
        cur.execute(
            """
            INSERT INTO chunks (
                id, document_id, text, chunk_index, char_start_index,
                char_end_index, token_length, section_path, chunk_strategy,
                meta, created_time
            )
            SELECT
                s.id, s.document_id, s.text, s.chunk_index, s.char_start_index,
                s.char_end_index, s.token_length, s.section_path, s.chunk_strategy,
                COALESCE(s.meta, '{}'::jsonb)
                    || jsonb_strip_nulls(jsonb_build_object(
                        'page_start', s.page_start,
                        'page_end', s.page_end,
                        'bbox', s.bbox,
                        'body_self_refs', s.body_self_refs
                    )),
                s.created_time
            FROM staging_chunks s
            WHERE EXISTS (SELECT 1 FROM documents d WHERE d.id = s.document_id)
            ON CONFLICT (id) DO NOTHING
            """
        )
        inserted = cur.rowcount
        cur.execute("DROP TABLE staging_chunks")
    conn.commit()
    print(f"  chunks: {inserted}/{total} rows inserted")
    return inserted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump_path", help="Path to the pg_dump custom-format snapshot")
    parser.add_argument(
        "--work-dir",
        default=None,
        help="Scratch dir for extracted SQL (default: a temp dir, cleaned up after)",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.dump_path):
        print(f"Dump not found: {args.dump_path}")
        sys.exit(1)

    database_url = get_database_url()
    if not database_url.startswith("postgresql"):
        print("This import targets PostgreSQL only.")
        sys.exit(1)

    conn = psycopg2.connect(database_url)
    try:
        work_dir_ctx = (
            tempfile.TemporaryDirectory()
            if args.work_dir is None
            else None
        )
        out_dir = args.work_dir or work_dir_ctx.name
        os.makedirs(out_dir, exist_ok=True)

        print(f"Extracting into: {out_dir}")
        print()

        print("-- Tables with no FK dependency on documents/chunks --")
        for table in PRE_DOCUMENTS_TABLES:
            load_direct(conn, args.dump_path, table, out_dir)

        print()
        print("-- documents / chunks (schema-translated) --")
        load_documents(conn, args.dump_path, out_dir)
        load_chunks(conn, args.dump_path, out_dir)

        print()
        print("-- Ontology tables (incoming replaces existing, see load_replace) --")
        for table in REPLACE_TABLES:
            load_replace(conn, args.dump_path, table, out_dir)

        print()
        print("-- Tables that FK onto documents.id / chunks.id / graph_node_types.id / graph_verbs.id --")
        for table in POST_DOCUMENTS_TABLES:
            load_direct(conn, args.dump_path, table, out_dir, fk_filter=FK_FILTERS.get(table))

        if work_dir_ctx is not None:
            work_dir_ctx.cleanup()

        print()
        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
