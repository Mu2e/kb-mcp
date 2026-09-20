#!/usr/bin/env python3
"""Generic Postgres-to-Postgres sync tool for kb-mcp (dev <-> prod, or any pair).

Two modes:

  --bootstrap   One-time (but safe to re-run) setup of a brand-new, empty
                target database: creates the pgvector extension, all static
                tables (via kb_mcp.kb.database.init_db()), and reconciles
                the graph_node_types/graph_verbs ontology tables against the
                source (see reconcile_graph_ontology below for why that pair
                needs special handling).

  (default)     Sync new rows from source to target, one table at a time,
                in dependency order. Deliberately simple: a row is copied if
                its primary key doesn't already exist on the target; an
                existing target row is NEVER updated or deleted. This makes
                the tool direction-agnostic (there's no conflict to
                resolve) and safe to run repeatedly/on a schedule. Chasing
                in-place edits/re-parses on the source, or bidirectional
                reconciliation, is explicitly out of scope for now - add it
                if it turns out to actually be needed.

Connections are configured via env vars, not CLI flags, so real credentials
never appear in shell history or `ps`:

    FROM_DB_URL     a full postgresql:// connection string
    FROM_DB_SCHEMA  target schema on the source side (default: public)

    TO_DB_URL       a full postgresql:// connection string
    TO_DB_SCHEMA    target schema on the target side (default: public)

Deliberately named differently from the app's own DB_* vars (read by
src/kb_mcp/config.py) so `.env`/`.env.local` autoloading - which happens on
import of anything under kb_mcp - can never accidentally leak one side's
settings into the other. SCHEMA is kept as its own var rather than folded
into the URL, since it's an app-level policy choice (which search_path to
target) rather than a raw connection detail.

A URL with no password (e.g. "postgresql://scorrodi@ifdb11:5475/mu2e_docdb_prd")
is what lets libpq negotiate GSSAPI (Kerberos) instead of password auth,
the same branch kb_mcp.kb.database.get_database_url() uses - provided a
valid ticket exists in the environment (kinit) and the server's
pg_hba.conf accepts it.

hostaddr (Kerberos-through-a-tunnel gotcha, confirmed necessary against
the real prod database 2026-09-20): when connecting through an SSH
local-forward (e.g. `ssh -L 15475:ifdb11:5475 jumphost`, then connecting to
127.0.0.1:15475), libpq derives the Kerberos service-principal hostname
from whatever `host` value it's given - which would be "127.0.0.1" or
"localhost", not the real server name, and GSSAPI auth fails looking for a
service principal that doesn't exist. The fix is to pass the REAL hostname
as `host` (for Kerberos/TLS identity) and the tunnel's local endpoint as
`hostaddr` (the actual TCP target) - libpq connection URIs accept arbitrary
keyword=value query params, so this is just:

    postgresql://scorrodi@ifdb11:5475/mu2e_docdb_prd?hostaddr=127.0.0.1

Only add ?hostaddr=... when tunneling; omit it for a directly-reachable
database.

Usage:
    python scripts/sync_databases.py --check-only from
    python scripts/sync_databases.py --check-only to
    python scripts/sync_databases.py --dry-run
    python scripts/sync_databases.py --bootstrap
    python scripts/sync_databases.py
    python scripts/sync_databases.py --only-tables documents,chunks
    python scripts/sync_databases.py --verify-only
"""

import argparse
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import psycopg2
from tqdm import tqdm

# Tables with no FK dependency on documents/chunks - load first, any order
# within the group.
PRE_TABLES = ["sources", "parsers", "documents_raw", "embedding_configs", "chunk_strategies"]

# The documents -> document_parser_outputs -> chunks chain, in this order
# (each FKs onto the previous).
DOCUMENT_CHAIN = ["documents", "document_parser_outputs", "chunks"]

# Ontology tables: seeded independently by init_db()'s seed_graph_defaults()
# on a fresh target, so they need the delete-then-replace treatment during
# --bootstrap (see reconcile_graph_ontology) rather than plain insert-only
# sync. Once bootstrap has aligned their ids across both sides, they sync
# like any other table (included in POST_TABLES from then on is NOT
# necessary - reconcile_graph_ontology's own logic makes plain sync_table
# calls in POST_TABLES safe even on repeat runs, since ids stay aligned).
ONTOLOGY_TABLES = ["graph_node_types", "graph_verbs"]

# FKs onto documents/chunks/graph ontology.
POST_TABLES = [
    "graph_node_types",
    "graph_verbs",
    "graph_nodes",
    "graph_relations",
    "graph_relations_evidence",
    "graph_node_map",
    "graph_extraction_logs",
    "privacy_filters",
    "llm_usage",
    "parser_comparisons",
    "parser_categories",
    "logs_import_runs",
    "logs_parsing",
    "logs_chunk_embedding",
    "logs_summary",
    "logs_search",
    "eval_generation",
    "eval_dataset",
    "eval_audit",
    "eval_runs",
    "eval_results",
    "eval_retrieved_documents",
]

# Primary key column(s) per static table, for ON CONFLICT (...) DO NOTHING.
# Dynamic embeddings_<model> tables always use "id" (see
# create_embedding_table() in kb_mcp/kb/embedding/db_models.py).
PRIMARY_KEYS = {
    "sources": "id",
    "parsers": "name",
    "documents_raw": "id",
    "embedding_configs": "short_name",
    "chunk_strategies": "strategy",
    "documents": "id",
    "document_parser_outputs": "document_id",
    "chunks": "id",
    "graph_node_types": "id",
    "graph_verbs": "id",
    "graph_nodes": "id",
    "graph_relations": "id",
    "graph_relations_evidence": "id",
    "graph_node_map": "node_id, document_id",  # composite PK
    "graph_extraction_logs": "id",
    "privacy_filters": "id",
    "llm_usage": "id",
    "parser_comparisons": "id",
    "parser_categories": "id",
    "logs_import_runs": "id",
    "logs_parsing": "id",
    "logs_chunk_embedding": "id",
    "logs_summary": "id",
    "logs_search": "id",
    "eval_generation": "id",
    "eval_dataset": "id",
    "eval_audit": "id",
    "eval_runs": "id",
    "eval_results": "id",
    "eval_retrieved_documents": "id",
}

# Highest-risk FKs for --verify-only's anti-join spot check: (table, column, ref_table, ref_column).
FK_SPOT_CHECKS = [
    ("chunks", "document_id", "documents", "id"),
    ("graph_nodes", "type_id", "graph_node_types", "id"),
    ("graph_relations", "verb_id", "graph_verbs", "id"),
]


def _db_url(prefix: str) -> str:
    url = os.environ.get(f"{prefix}_DB_URL")
    if not url:
        raise SystemExit(f"{prefix}_DB_URL is not set")
    return url


def _schema(prefix: str) -> str:
    return os.environ.get(f"{prefix}_DB_SCHEMA", "public")


def get_conn(prefix: str):
    """Connect using {prefix}_DB_URL and set search_path from {prefix}_DB_SCHEMA.

    Mirrors kb_mcp.kb.database.create_engine_with_config()'s search_path
    logic: the configured schema, always with "public" appended (pgvector's
    `vector` type only lives in public on every deployment seen so far -
    confirmed true for prod too, 2026-09-20).
    """
    conn = psycopg2.connect(_db_url(prefix))
    schema = _schema(prefix)
    schema_parts = [s.strip() for s in schema.split(",") if s.strip()]
    if "public" not in schema_parts:
        schema_parts.append("public")
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {','.join(schema_parts)}")
    conn.commit()
    return conn


def kerberos_smoke_test(prefix: str) -> None:
    """Connect and report identity - the first-class check for an
    auth path (GSSAPI-via-psycopg2/libpq) that had never been exercised in
    this codebase before 2026-09-20. A failure here almost always means
    pg_hba.conf on the server doesn't have an entry for this connecting
    IP/user/database, or there's no valid ticket (`klist`) in this shell -
    not a bug in this script.
    """
    conn = get_conn(prefix)
    with conn.cursor() as cur:
        cur.execute("SELECT current_user, current_schema(), version()")
        user, schema, version = cur.fetchone()
    conn.close()
    print(f"  {prefix}: connected OK - user={user} schema={schema}")
    print(f"  {prefix}: {version.splitlines()[0]}")


def table_exists(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = ANY(current_schemas(false)) AND tablename = %s)",
            (table,),
        )
        return cur.fetchone()[0]


def row_count(conn, table: str) -> int:
    if not table_exists(conn, table):
        return 0
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        return cur.fetchone()[0]


def get_columns(conn, table: str) -> list:
    """Authoritative column list for a table, in a stable order, read from
    the database itself rather than hardcoded - both sides run the same
    schema (same git checkout), so introspection is simpler and can't drift
    out of sync the way a hand-maintained per-table column list could.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ANY(current_schemas(false)) AND table_name = %s "
            "ORDER BY ordinal_position",
            (table,),
        )
        return [row[0] for row in cur.fetchall()]


class _ProgressWrapper:
    """Thin file-like wrapper around a stream that updates a tqdm row-count
    bar as write()/read() get called. psycopg2's copy_expert() drives these
    methods directly, many times, as data streams over the wire - COPY's
    text format is one row per line, so counting newlines in each chunk
    gives an exact, live per-row progress bar with no threads needed.
    """

    def __init__(self, stream, total: int, desc: str):
        self._stream = stream
        self._pbar = tqdm(total=total, desc=desc, unit="row", leave=False)

    def write(self, data):
        self._pbar.update(data.count(b"\n"))
        return self._stream.write(data)

    def read(self, *args):
        data = self._stream.read(*args)
        self._pbar.update(data.count(b"\n"))
        return data

    def close_bar(self) -> None:
        self._pbar.close()


def sync_table(src_conn, dst_conn, table: str, pk: str) -> tuple:
    """Copy rows present on src but not on dst (by primary key) into dst.
    Never updates or deletes an existing dst row - see module docstring.

    Same load_direct() idiom as scripts/import_wzhou2_snapshot.py: a
    staging table (so a plain COPY, which has no conflict handling of its
    own, can still be followed by an ON CONFLICT DO NOTHING insert), one
    transaction per table.

    Shows a tqdm progress bar for the two phases that can take real time on
    a large table (reading from source, writing to target) - psycopg2's
    copy_expert() calls the given file-like object's write()/read() many
    times as data streams over the wire, so a thin io.BytesIO subclass that
    counts newlines (= rows, since COPY's text format is one row per line)
    on each call gets live per-row progress for free, no threads needed.

    Returns (inserted, total_on_src).
    """
    if not table_exists(src_conn, table):
        print(f"  {table}: does not exist on source, skipping")
        return (0, 0)
    if not table_exists(dst_conn, table):
        raise RuntimeError(
            f"{table} does not exist on target - run --bootstrap first "
            f"(or, for a dynamic embeddings_* table, sync embedding_configs first)"
        )

    total = row_count(src_conn, table)
    if total == 0:
        print(f"  {table}: 0 rows on source, skipping")
        return (0, 0)

    dst_total = row_count(dst_conn, table)
    if dst_total >= total:
        print(f"  {table}: already in sync ({dst_total} rows on target), skipping")
        return (0, total)

    columns = get_columns(src_conn, table)
    col_list = ", ".join(f'"{c}"' for c in columns)

    raw_buf = io.BytesIO()
    read_progress = _ProgressWrapper(raw_buf, total=total, desc=f"  {table} (read)")
    with src_conn.cursor() as cur:
        cur.copy_expert(f'COPY (SELECT {col_list} FROM "{table}") TO STDOUT', read_progress)
    read_progress.close_bar()
    raw_buf.seek(0)

    write_progress = _ProgressWrapper(raw_buf, total=total, desc=f"  {table} (write)")
    with dst_conn.cursor() as cur:
        cur.execute(f'CREATE TEMP TABLE "staging_{table}" (LIKE "{table}" INCLUDING ALL)')
        cur.copy_expert(f'COPY "staging_{table}" ({col_list}) FROM STDIN', write_progress)
        write_progress.close_bar()
        cur.execute(f'SELECT COUNT(*) FROM "staging_{table}"')
        staged = cur.fetchone()[0]
        cur.execute(
            f'INSERT INTO "{table}" ({col_list}) '
            f'SELECT {col_list} FROM "staging_{table}" '
            f"ON CONFLICT ({pk}) DO NOTHING"
        )
        inserted = cur.rowcount
        cur.execute(f'DROP TABLE "staging_{table}"')
    dst_conn.commit()
    print(f"  {table}: {inserted}/{staged} rows inserted ({staged - inserted} already present)")
    return (inserted, staged)


def reconcile_graph_ontology(src_conn, dst_conn) -> None:
    """graph_node_types / graph_verbs: init_db()'s seed_graph_defaults()
    auto-seeds these two tables on a fresh target with DIFFERENT UUIDs than
    the source's real rows for the same labels/names (both columns are
    unique=True) - a plain insert-only sync would collide. Same
    load_replace() idiom as scripts/import_wzhou2_snapshot.py: delete the
    (auto-seeded) existing rows and replace with the source's real rows, so
    both sides carry identical ids from here on - every later sync of
    graph_nodes/graph_relations (which FK onto these) then just works via
    the normal insert-only path, no further special-casing ever needed.

    Idempotent: if dst's ids already exactly match src's, skip - re-running
    --bootstrap on an already-reconciled target must not touch these
    tables again. Aborts loudly if dst's graph_nodes/graph_relations are
    already non-empty, since that means something already depends on the
    current (possibly auto-seeded) ids and a blind delete+replace could
    silently orphan or fail confusingly rather than cleanly.
    """
    for dependent in ("graph_nodes", "graph_relations"):
        if row_count(dst_conn, dependent) > 0:
            raise RuntimeError(
                f"{dependent} on target is non-empty - refusing to touch "
                f"graph_node_types/graph_verbs automatically. Investigate "
                f"by hand before re-running --bootstrap."
            )

    for table in ONTOLOGY_TABLES:
        pk = PRIMARY_KEYS[table]
        with dst_conn.cursor() as cur:
            cur.execute(f'SELECT array_agg("{pk}" ORDER BY "{pk}") FROM "{table}"')
            dst_ids = cur.fetchone()[0] or []
        with src_conn.cursor() as cur:
            cur.execute(f'SELECT array_agg("{pk}" ORDER BY "{pk}") FROM "{table}"')
            src_ids = cur.fetchone()[0] or []

        if sorted(dst_ids) == sorted(src_ids):
            print(f"  {table}: already matches source ontology, skipping replace")
            continue

        columns = get_columns(src_conn, table)
        col_list = ", ".join(f'"{c}"' for c in columns)
        buf = io.BytesIO()
        with src_conn.cursor() as cur:
            cur.copy_expert(f'COPY (SELECT {col_list} FROM "{table}") TO STDOUT', buf)
        buf.seek(0)

        with dst_conn.cursor() as cur:
            cur.execute(f'DELETE FROM "{table}"')
            deleted = cur.rowcount
            cur.copy_expert(f'COPY "{table}" ({col_list}) FROM STDIN', buf)
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            total = cur.fetchone()[0]
        dst_conn.commit()
        print(f"  {table}: replaced {deleted} existing rows with {total} from source")


def discover_embedding_tables(conn) -> list:
    """Read embedding_configs -> [(short_name, dimension), ...]."""
    if not table_exists(conn, "embedding_configs"):
        return []
    with conn.cursor() as cur:
        cur.execute("SELECT short_name, dimension FROM embedding_configs")
        return cur.fetchall()


def ensure_embedding_table(short_name: str, dimension: int) -> str:
    """Create the embeddings_<model> table (+ its declared indexes) on the
    target if it doesn't exist yet, via the app's own
    create_embedding_table(), so the DDL can never drift from what the app
    itself would create. Returns the table name.

    Uses TO_DB_URL directly with SQLAlchemy's create_engine() - a libpq URI
    with a ?hostaddr=... query param works there exactly as it does for a
    plain psycopg2.connect(url), so no separate connect_args are needed for
    that. search_path is a separate matter, though: a freshly-opened engine
    has no idea about {prefix}_DB_SCHEMA, and defaults to Postgres's own
    default search_path (not necessarily even "public") - without setting
    it explicitly here, CREATE TABLE ... REFERENCES chunks(id) fails with
    "relation chunks does not exist" on any non-default target schema,
    since the new table's FK can't find chunks outside the search_path.
    Passed the same way create_engine_with_config() does it in
    kb_mcp/kb/database.py: a libpq startup option, so every connection this
    engine opens (including the ones .create() uses internally) has the
    right search_path from the moment it connects.
    """
    from sqlalchemy import create_engine
    from kb_mcp.kb.embedding.db_models import create_embedding_table, get_embedding_table_name

    table_name = get_embedding_table_name(short_name)
    schema = _schema("TO")
    schema_parts = [s.strip() for s in schema.split(",") if s.strip()]
    if "public" not in schema_parts:
        schema_parts.append("public")
    search_path = ",".join(schema_parts)
    engine = create_engine(_db_url("TO"), connect_args={"options": f"-c search_path={search_path}"})
    try:
        table = create_embedding_table(short_name, dimension)
        table.create(bind=engine, checkfirst=True)
    finally:
        engine.dispose()
    return table_name


def ensure_ivfflat_index(dst_conn, table_name: str) -> None:
    index_name = f"{table_name}_vector_idx"
    with dst_conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE INDEX IF NOT EXISTS "{index_name}"
            ON "{table_name}"
            USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 250)
            """
        )
    dst_conn.commit()


def _parse_db_url(url: str) -> dict:
    """Break a postgresql:// URL (with an optional ?hostaddr=... query
    param) into the discrete components kb_mcp.config.get_database_config()
    expects (DB_HOST/PORT/NAME/USER/PASSWORD/HOSTADDR).
    """
    from urllib.parse import urlparse, parse_qs

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    return {
        "HOST": parsed.hostname,
        "PORT": str(parsed.port) if parsed.port else None,
        "NAME": parsed.path.lstrip("/"),
        "USER": parsed.username,
        "PASSWORD": parsed.password,
        "HOSTADDR": query.get("hostaddr", [None])[0],
    }


def bootstrap_target_schema() -> None:
    """Create the pgvector extension, all static tables, and the fulltext
    trigger on the target by calling the app's own init_db() - reusing real
    app code instead of reimplementing DDL. init_db()/get_engine() are
    module-level singletons keyed on whatever DB_* env vars were active the
    first time they're called in this process, so: temporarily point DB_*
    at the target's TO_DB_URL/TO_DB_SCHEMA, call init_db(), then restore the
    environment AND reset the cached engine/schema-ready flag so nothing
    later in this same process could accidentally reuse a stale
    target-pointed engine.
    """
    import kb_mcp.kb.database as database

    parts = _parse_db_url(_db_url("TO"))
    parts["SCHEMA"] = _schema("TO")

    db_vars = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_SCHEMA", "DB_HOSTADDR"]
    saved = {var: os.environ.get(var) for var in db_vars}

    try:
        for suffix, value in parts.items():
            if value is not None:
                os.environ[f"DB_{suffix}"] = value
            else:
                os.environ.pop(f"DB_{suffix}", None)

        database._engine = None
        database._SessionLocal = None
        database._schema_ready = False
        database.init_db(create_tables=True)
    finally:
        for var, value in saved.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
        database._engine = None
        database._SessionLocal = None
        database._schema_ready = False


def dry_run_report(src_conn, dst_conn, tables: list) -> None:
    print(f"{'table':<32} {'source':>10} {'target':>10}  action")
    print("-" * 70)
    for table in tables:
        src_n = row_count(src_conn, table)
        dst_n = row_count(dst_conn, table)
        if dst_n >= src_n and src_n > 0:
            action = "in sync"
        elif src_n == 0:
            action = "empty on source"
        else:
            action = f"would insert up to {src_n - dst_n}"
        print(f"{table:<32} {src_n:>10} {dst_n:>10}  {action}")


def verify(src_conn, dst_conn, tables: list) -> None:
    print("-- row counts --")
    for table in tables:
        src_n = row_count(src_conn, table)
        dst_n = row_count(dst_conn, table)
        flag = "" if dst_n >= src_n else "  <-- target has fewer rows"
        print(f"  {table}: source={src_n} target={dst_n}{flag}")

    print("-- FK integrity spot checks (target) --")
    for table, column, ref_table, ref_column in FK_SPOT_CHECKS:
        if not table_exists(dst_conn, table) or not table_exists(dst_conn, ref_table):
            continue
        with dst_conn.cursor() as cur:
            cur.execute(
                f'SELECT COUNT(*) FROM "{table}" t '
                f'WHERE t."{column}" IS NOT NULL '
                f'AND NOT EXISTS (SELECT 1 FROM "{ref_table}" r WHERE r."{ref_column}" = t."{column}")'
            )
            dangling = cur.fetchone()[0]
        status = "OK" if dangling == 0 else f"{dangling} DANGLING ROWS"
        print(f"  {table}.{column} -> {ref_table}.{ref_column}: {status}")

    print("-- ontology id-set equality --")
    for table in ONTOLOGY_TABLES:
        pk = PRIMARY_KEYS[table]
        with src_conn.cursor() as cur:
            cur.execute(f'SELECT array_agg("{pk}" ORDER BY "{pk}") FROM "{table}"')
            src_ids = sorted(cur.fetchone()[0] or [])
        with dst_conn.cursor() as cur:
            cur.execute(f'SELECT array_agg("{pk}" ORDER BY "{pk}") FROM "{table}"')
            dst_ids = sorted(cur.fetchone()[0] or [])
        print(f"  {table}: {'MATCH' if src_ids == dst_ids else 'MISMATCH'}")


def all_tables_in_order(src_conn) -> tuple:
    """Returns (tables, embedding_configs) - embedding_configs is
    [(short_name, dimension), ...], needed separately so the sync loop can
    create each embeddings_<model> table on the target before syncing its
    data (see main()).
    """
    from kb_mcp.kb.embedding.db_models import get_embedding_table_name

    embedding_configs = discover_embedding_tables(src_conn)
    tables = list(PRE_TABLES) + list(DOCUMENT_CHAIN)
    tables += [get_embedding_table_name(short_name) for short_name, _dim in embedding_configs]
    tables += POST_TABLES
    return tables, embedding_configs


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bootstrap", action="store_true", help="Create schema/tables on a fresh target, once")
    parser.add_argument("--dry-run", action="store_true", help="Report row counts on both sides, write nothing")
    parser.add_argument("--check-only", choices=["from", "to"], help="Just the connectivity/Kerberos smoke test")
    parser.add_argument("--only-tables", help="Comma-separated table names to restrict to")
    parser.add_argument("--skip-tables", help="Comma-separated table names to skip")
    parser.add_argument("--verify-only", action="store_true", help="Skip syncing, just run verification checks")
    args = parser.parse_args()

    if args.check_only:
        kerberos_smoke_test("FROM" if args.check_only == "from" else "TO")
        return

    print("-- connectivity check --")
    kerberos_smoke_test("FROM")
    kerberos_smoke_test("TO")
    print()

    src_conn = get_conn("FROM")
    dst_conn = get_conn("TO")

    tables, embedding_configs = all_tables_in_order(src_conn)
    if args.only_tables:
        wanted = set(args.only_tables.split(","))
        tables = [t for t in tables if t in wanted]
    if args.skip_tables:
        skip = set(args.skip_tables.split(","))
        tables = [t for t in tables if t not in skip]

    if args.dry_run:
        print("-- dry run: row counts --")
        dry_run_report(src_conn, dst_conn, tables)
        return

    if args.verify_only:
        verify(src_conn, dst_conn, tables)
        return

    if args.bootstrap:
        print("-- bootstrapping target schema (init_db) --")
        bootstrap_target_schema()
        # get_conn's search_path was set before the target schema/tables
        # existed for a truly fresh database; harmless to just re-set it.
        dst_conn.close()
        dst_conn = get_conn("TO")
        print("-- reconciling graph ontology --")
        reconcile_graph_ontology(src_conn, dst_conn)
        print()

    from kb_mcp.kb.embedding.db_models import get_embedding_table_name

    # table name -> (short_name, dimension), for the dynamic embeddings_*
    # tables only - short_name is kept (not reverse-derived from the table
    # name) since sanitize_table_name() isn't necessarily reversible.
    embedding_table_info = {
        get_embedding_table_name(short_name): (short_name, dim) for short_name, dim in embedding_configs
    }

    print("-- syncing --")
    for table in tables:
        if table in embedding_table_info and not table_exists(dst_conn, table):
            # A dynamic embeddings_<model> table new since the target was
            # last bootstrapped/synced - create it via the app's own
            # create_embedding_table(), same as embedder_base.py would.
            short_name, dimension = embedding_table_info[table]
            print(f"  {table}: creating on target (new embedding config)")
            ensure_embedding_table(short_name, dimension)

        pk = PRIMARY_KEYS.get(table, "id")
        sync_table(src_conn, dst_conn, table, pk)

        if table in embedding_table_info:
            ensure_ivfflat_index(dst_conn, table)

    src_conn.close()
    dst_conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
