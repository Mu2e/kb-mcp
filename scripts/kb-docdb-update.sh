#!/bin/bash
# Incremental Mu2e DocDB update, meant to run unattended from cron. Installed into a release's .venv/bin; in a checkout it is run through
# scripts/cron_docdb_update.sh, which builds the environment first.
#
# Configuration comes from the same places as every other kb-mcp command:
#   release:  KB_ENV_FILE=<deploy-root>/config/kb-mcp.env (required), plus the
#             .env.local and optional credentials.local.sh beside it. The job
#             runs from that directory.
#   checkout: the checkout's .env / .env.local, run from the repo root.
#
# --days 1 on a 12h cadence gives 2x overlap, so one missed or failed run
# doesn't lose a day's worth of documents. --skip-existing makes re-fetching
# an already-seen document a no-op, so the overlap is cheap.
#
# Safe to re-run, and safe if the previous run is still going (flock below
# skips this tick rather than piling up a second run).
#
# For a one-off catch-up over a longer window, override the look-back:
#   DAYS=35 kb-docdb-update.sh
#
# Other environment: KB_DATA_DIR (logs; default /exp/mu2e/data/users/$USER/
# kb-mcp-data), KB_ALCF_HOME (where the ALCF/Globus login is stored instead of
# $HOME), KB_RUN_TRIGGER (recorded in the import-run log).

set -uo pipefail

# A release: this script sits in the venv's bin/ next to python, so that venv
# is the environment. A checkout: the wrapper has already activated one.
SELF_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd -P)"
if [ -x "$SELF_DIR/python" ]; then
    export PATH="$SELF_DIR:$PATH"
    # A release has no checkout .env to discover, so without a pinned file the
    # run would start with no configuration at all and only fail at the
    # database check.
    if [ -z "${KB_ENV_FILE:-}" ]; then
        echo "KB_ENV_FILE is required when running from a release (set it in the crontab)" >&2
        exit 2
    fi
fi

# With a pinned env file, run from its directory: the .env.local and the
# credentials hook live there, and so does inference_auth_token.py for the
# ALCF refresh. Otherwise the caller's directory (the repo root) is used.
if [ -n "${KB_ENV_FILE:-}" ]; then
    if [ ! -f "$KB_ENV_FILE" ]; then
        echo "KB_ENV_FILE=$KB_ENV_FILE does not exist" >&2
        exit 2
    fi
    cd "$(dirname "$KB_ENV_FILE")"
fi

DAYS="${DAYS:-1}"
if ! [[ "$DAYS" =~ ^[1-9][0-9]*$ ]]; then
    echo "DAYS must be a positive integer, got '$DAYS'" >&2
    exit 2
fi

KB_DATA="${KB_DATA_DIR:-/exp/mu2e/data/users/$USER/kb-mcp-data}"
# A release has no setup script to point caches away from $HOME, so do it here
# (so cron and a by-hand run behave the same).
if [ -n "${KB_ENV_FILE:-}" ]; then
    export HF_HOME="${HF_HOME:-$KB_DATA/huggingface_cache}"
    export KB_ALCF_HOME="${KB_ALCF_HOME:-$KB_DATA/alcf}"
fi
LOG_DIR="$KB_DATA/logs"
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
    echo "kb-mcp    : $(python3 -c 'import kb_mcp; print(kb_mcp.__version__)' 2>&1) ($(command -v kb-import))"
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "git       : $(git rev-parse --short HEAD) ($(git status --porcelain | wc -l) files dirty)"
    fi
    echo "env file  : ${KB_ENV_FILE:-(discovered .env in $PWD)}"
    echo "log       : $LOG"
    echo "days      : $DAYS"
    echo "---"
} > "$LOG"

# 1. Sanity check. A pinned env file beats its .env.local sibling, and the ALCF
# refresh below writes OPENAI_API_KEY / OPENAI_BASE_URL into .env.local. If the
# pinned file sets them too, every refreshed token is silently ignored.
# More generally, any key set in both files takes the pinned file's value, even
# an empty one, which is easy to miss.
env_keys() {
    grep -oE '^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=' "$1" 2>/dev/null \
        | sed -E 's/^[[:space:]]*(export[[:space:]]+)?//; s/=$//' | sort -u
}
if [ -n "${KB_ENV_FILE:-}" ]; then
    if grep -qE '^[[:space:]]*(export[[:space:]]+)?OPENAI_(API_KEY|BASE_URL)=' "$KB_ENV_FILE"; then
        echo "WARNING: $KB_ENV_FILE sets OPENAI_API_KEY/OPENAI_BASE_URL; they override the refreshed ALCF token in .env.local. Move them to .env.local." >> "$LOG"
    fi
    shared_keys="$(comm -12 <(env_keys "$KB_ENV_FILE") <(env_keys "$(dirname "$KB_ENV_FILE")/.env.local") | tr '\n' ' ')"
    if [ -n "$shared_keys" ]; then
        echo "WARNING: set in both $KB_ENV_FILE and .env.local; the .env.local values are ignored: $shared_keys" >> "$LOG"
    fi
fi

# 1b. Site credentials (optional). Whatever an unattended run needs before it
# can reach the database — obtaining a ticket, exporting a variable — is
# site-specific and deliberately kept out of this repo, in a hook that lives
# beside .env.local (the same place, resolved the same way, as the rest of the
# user's private settings; git-ignored via *.local.sh). The hook is sourced (so
# it can export into this run) with its output in the log, and gets
# KB_ENV_LOCAL (the resolved .env.local path) and KB_DATA; a line starting
# "CREDENTIALS FAILED" marks the run failed. No hook: nothing happens here,
# and a missing credential shows up as a failed database check below.
#
# Refused unless owned by this user and not group/other-writable: it runs with
# this account's credentials, so anyone able to edit it would own them.
KB_ENV_LOCAL="$(python3 -c 'from kb_mcp.config import get_env_local_path; print(get_env_local_path() or "")' 2>>"$LOG")"
CRED_HOOK="${KB_CRED_HOOK:-${KB_ENV_LOCAL:+$(dirname "$KB_ENV_LOCAL")/credentials.local.sh}}"
if [ -n "$CRED_HOOK" ] && [ -f "$CRED_HOOK" ]; then
    if [ ! -O "$CRED_HOOK" ] || [ -n "$(find "$CRED_HOOK" -perm /022)" ]; then
        echo "CREDENTIALS FAILED: refusing to source $CRED_HOOK (not owned by $USER, or group/other-writable)" >> "$LOG"
    else
        # shellcheck source=/dev/null
        source "$CRED_HOOK" >> "$LOG" 2>&1
    fi
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
export KB_RUN_TRIGGER="${KB_RUN_TRIGGER:-cron-script}"
export KB_RUN_LOG="$LOG"
stdbuf -oL -eL kb-import docdb --days "$DAYS" --skip-existing --delay 1 --max-embed-chars 500000 \
    --no-embed-images --no-embed-tables >> "$LOG" 2>&1
rc=$?
end=$(date +%s)

# 4. Failure detection.
#
# kb-import cannot report failure through its exit status: auto-summarize and
# auto-embed catch their own exceptions and only log them (see the
# `Error during auto-...` handlers in imports/base.py), after which
# imports/cli.py sets `exit_code = 0` unconditionally. So a run whose database
# is unreachable still exits 0 having imported nothing — observed for real on
# the 2026-09-20 18:00 tick, which logged a failed DB check, errors from both
# auto steps, "Successfully processed 0 document(s)", and exit code 0.
#
# With MAILTO="" in the crontab that failure mode is completely silent, so
# until the CLI propagates failures itself, detect them here by scanning this
# run's own log. Checked in severity order; a failed *database* check is fatal
# because nothing can be stored without it, whereas the other connection
# checks are informational (a dead ALCF endpoint only degrades image
# descriptions, which the token-refresh step above already warns about).
failure_reason=""
if grep -qaE "^CREDENTIALS FAILED" "$LOG"; then
    failure_reason="$(grep -aE "^CREDENTIALS FAILED" "$LOG" | head -1)"
elif grep -qaE "ERROR - Error during auto-(summarize|embed)" "$LOG"; then
    failure_reason="auto-summarize/auto-embed raised — see the traceback in the log"
elif grep -qaE "^[[:space:]]*FAIL[[:space:]]+database" "$LOG"; then
    failure_reason="database connection check failed"
fi

if [ -n "$failure_reason" ] && [ "$rc" -eq 0 ]; then
    # Distinguish "the tool failed" (rc from kb-import) from "the tool claimed
    # success but the log says otherwise" (rc 1 from here).
    rc=1
fi

# Taken before the footer below is appended, which would otherwise show up in
# its own tail.
last_lines="$(tail -5 "$LOG")"
{
    echo "---"
    echo "finished  : $(date -Is)"
    echo "elapsed   : $(( (end-start)/60 )) min"
    if [ -n "$failure_reason" ]; then
        echo "FAILURE   : $failure_reason"
    fi
    echo "exit code : $rc"
    echo "--- last lines before this footer:"
    echo "$last_lines"
} >> "$LOG" 2>&1

# Also on stderr: invisible under MAILTO="", but makes a manual run and any
# future mail-enabled or wrapper-driven invocation say why it failed.
if [ -n "$failure_reason" ]; then
    echo "$(date -Is): docdb update FAILED — $failure_reason (log: $LOG)" >&2
fi

exit $rc
