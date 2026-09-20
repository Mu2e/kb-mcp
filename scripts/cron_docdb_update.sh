#!/bin/bash
# Incremental Mu2e DocDB update, meant to run unattended from crontab.
#
#   crontab -e
#   0 6,18 * * * /exp/mu2e/app/users/scorrodi/kb-mcp/scripts/cron_docdb_update.sh
#
# --days 1 on a 12h cadence gives 2x overlap, so one missed or failed run
# doesn't lose a day's worth of documents. --skip-existing makes re-fetching
# an already-seen document a no-op, so the overlap is cheap.
#
# Safe to re-run, and safe if the previous run is still going (flock below
# skips this tick rather than piling up a second run).
#
# For a one-off catch-up over a longer window, override the look-back:
#   DAYS=35 scripts/cron_docdb_update.sh
# (same logging, lock, and ALCF refresh as the scheduled run).

set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

DAYS="${DAYS:-1}"
if ! [[ "$DAYS" =~ ^[1-9][0-9]*$ ]]; then
    echo "DAYS must be a positive integer, got '$DAYS'" >&2
    exit 2
fi

LOG_DIR="${KB_DATA_DIR:-/exp/mu2e/data/users/$USER/kb-mcp-data}/logs"
mkdir -p "$LOG_DIR"
STAMP=$(date +%Y%m%d-%H%M%S)
LOG="$LOG_DIR/docdb-update-${STAMP}.log"

# One run at a time. A cron tick that fires while the previous run is still
# going (slow parse, network hiccup) skips rather than stacking a second run
# on top of it.
LOCK="$LOG_DIR/docdb-update.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "$(date -Is): another docdb-update run still holds $LOCK — skipping this tick" >&2
    exit 0
fi

{
    echo "host      : $(hostname)"
    echo "started   : $(date -Is)"
    echo "git       : $(git rev-parse --short HEAD 2>/dev/null) ($(git status --porcelain 2>/dev/null | wc -l) files dirty)"
    echo "log       : $LOG"
    echo "days      : $DAYS"
    echo "---"
} > "$LOG"

# 1. Environment. Sourced (not just run) so it can activate the venv in
# *this* shell; self-healing if this is a fresh node or /tmp scratch got
# cleared since the last run — see scripts/setup_mu2e_uv.sh for why venv,
# uv cache, and data each live in their own place.
if ! source scripts/setup_mu2e_uv.sh >> "$LOG" 2>&1; then
    echo "FATAL: environment setup failed — see $LOG" >&2
    tail -20 "$LOG" >&2
    exit 1
fi

# 2. ALCF token: a non-interactive refresh ONLY. kb-import reads the token
# from OPENAI_API_KEY in .env.local, not from the Globus token store, so
# merely checking `inference_auth_token.py get_access_token` isn't enough —
# the fresh token has to be written back. refresh_alcf_token() does exactly
# that, and raises instead of prompting if the stored Globus login itself is
# dead. scripts/setup_alcf.sh is NOT used here: on failure it falls back to
# `inference_auth_token.py authenticate` (an interactive browser login that
# would hang a cron job), and it also reinstalls deps and re-downloads the
# auth script from GitHub on every call. A failed refresh degrades image
# descriptions to placeholders rather than blocking the run (docdb text
# extraction doesn't depend on it), so this warns and continues.
{
    echo "--- ALCF token refresh ---"
    if python3 -c "from kb_mcp.alcf_auth import refresh_alcf_token; refresh_alcf_token('sophia')"; then
        echo "ALCF token refreshed into .env.local"
    else
        echo "WARNING: ALCF token refresh failed — image descriptions will be"
        echo "         degraded (placeholder text) this run. Fix by hand with:"
        echo "         source scripts/setup_alcf.sh"
    fi
    echo "--- connection checks (informational; does not block the run) ---"
    kb-import --check-connections || true
    echo "---"
} >> "$LOG" 2>&1

# 3. The actual incremental import.
#
# --no-embed-images / --no-embed-tables: for now (2026-09-19) this job only
# embeds text documents. Images (~13k unchunked) and tables (~900 unchunked,
# leftovers of interrupted sweeps) are deliberately left for a separate pass.
#
# --delay 1: explicit rather than relying on kb-import's own 0.5s default,
# since this runs unattended twice a day and a slower, more polite pace to
# DocDB matters more here than shaving a few minutes off the run.
#
# --max-embed-chars: the corpus's p99 document is ~193K chars, but a long
# tail of financial spreadsheets runs up to 13M chars (11k+ chunks each) and
# can dominate a run's time budget on its own. 500K sits comfortably above
# every normal document while excluding those ~45 outliers, which get
# chunked/embedded separately (a manual/less-frequent pass), not by this
# twice-daily job. Affects auto-embed only, not fetching or parsing.
#
# NOTE: auto-embed and auto-summarize (both on by default) sweep the *whole*
# mu2e-docdb backlog for anything still missing chunks/a summary, not just
# the --days 1 window — see chunk_and_embed_all()/summarize_all() in
# kb/tools.py. That's intentional: it's how the historical backlog (see
# memory: DocDB coverage is ~1%) gets caught up over repeated runs, bounded
# by --max-embed-chars above. Fetching/parsing (the --days-scoped part) is
# unaffected. A prior bug made this sweep re-chunk the same giant
# non-Docling spreadsheets on *every* run forever (chunk_and_embed_all's
# section-strategy backlog query only recognized `section_*` chunks as
# "already done", never the `tokens_*` fallback those documents actually
# get) — fixed by accepting the fallback strategy name too (see the
# accepted_names comment in chunk_and_embed_all).
start=$(date +%s)
export PYTHONUNBUFFERED=1
# Recorded on this run's row in the import-run log (`kb logs imports`), so a
# run can be traced back to how it was started and to this log file.
export KB_RUN_TRIGGER="cron-script"
export KB_RUN_LOG="$LOG"
stdbuf -oL -eL kb-import docdb --days "$DAYS" --skip-existing --delay 1 --max-embed-chars 500000 \
    --no-embed-images --no-embed-tables >> "$LOG" 2>&1
rc=$?
end=$(date +%s)

{
    echo "---"
    echo "finished  : $(date -Is)"
    echo "elapsed   : $(( (end-start)/60 )) min"
    echo "exit code : $rc"
    tail -5 "$LOG"
} >> "$LOG" 2>&1

exit $rc
