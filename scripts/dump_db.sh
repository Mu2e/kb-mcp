#!/bin/bash
# dump_db.sh — dump the kb-mcp PostgreSQL database with pg_dump.
#
# Reads DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME from .env (or the
# environment, if already set). pg_dump must match the server's major
# version or it refuses to run — cvmfs only ships up to Postgres 15, so
# when the server is newer this script runs pg_dump from an official
# "postgres:<major>" container via apptainer instead.
#
# Usage:
#   scripts/dump_db.sh [output_dir]
#
# output_dir defaults to $KB_DB_BACKUP_DIR, or /exp/mu2e/data/users/$USER/kb_db_backup.

set -euo pipefail

SCRIPT_DIR="$(dirname "$(realpath "$0")")"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

: "${DB_HOST:?DB_HOST not set (check .env)}"
: "${DB_PORT:?DB_PORT not set (check .env)}"
: "${DB_USER:?DB_USER not set (check .env)}"
: "${DB_NAME:?DB_NAME not set (check .env)}"
: "${DB_PASSWORD:?DB_PASSWORD not set (check .env)}"

BACKUP_DIR="${1:-${KB_DB_BACKUP_DIR:-/exp/mu2e/data/users/$USER/kb_db_backup}}"
mkdir -p "$BACKUP_DIR"

# Cached container images live here regardless of output_dir, so repeated
# calls (e.g. with different output_dir args) don't re-pull each time.
CACHE_DIR="/exp/mu2e/data/users/$USER/kb_db_backup"
mkdir -p "$CACHE_DIR"

OUT="$BACKUP_DIR/kb_backup_$(date +%Y%m%d_%H%M%S).dump"

CVMFS_PG_BIN=$(find /cvmfs/mu2e.opensciencegrid.org/packages/postgresql -maxdepth 4 \
    -path "*almalinux9*" -name pg_dump 2>/dev/null -printf '%h\n' | head -1)

# Match pg_dump's major version to the server's, otherwise it refuses to run.
SERVER_MAJOR=""
if [ -n "$CVMFS_PG_BIN" ] && [ -x "$CVMFS_PG_BIN/psql" ]; then
    SERVER_MAJOR=$(PGPASSWORD="$DB_PASSWORD" "$CVMFS_PG_BIN/psql" -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -tAc "SHOW server_version_num;" 2>/dev/null | cut -c1-2 || true)
fi
SERVER_MAJOR="${SERVER_MAJOR:-17}"

run_dump() {
    if [ -n "$CVMFS_PG_BIN" ] && "$CVMFS_PG_BIN/pg_dump" --version 2>/dev/null | grep -q " $SERVER_MAJOR\."; then
        echo "Using cvmfs pg_dump: $CVMFS_PG_BIN/pg_dump"
        PGPASSWORD="$DB_PASSWORD" "$CVMFS_PG_BIN/pg_dump" \
            -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" \
            --no-owner --no-acl -F c -f "$OUT"
    else
        echo "No matching local pg_dump for server major version $SERVER_MAJOR — using postgres:$SERVER_MAJOR via apptainer"
        SIF="$CACHE_DIR/postgres_${SERVER_MAJOR}.sif"
        if [ ! -f "$SIF" ]; then
            echo "Pulling postgres:$SERVER_MAJOR image..."
            apptainer pull "$SIF" "docker://postgres:$SERVER_MAJOR"
        fi
        apptainer exec --bind "$BACKUP_DIR" --env PGPASSWORD="$DB_PASSWORD" "$SIF" \
            pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" \
            --no-owner --no-acl -F c -f "$OUT"
    fi
}

run_dump

echo "Dump written to $OUT ($(du -h "$OUT" | cut -f1))"
