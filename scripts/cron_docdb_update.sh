#!/bin/bash
# Run the DocDB update job from this checkout (cron, or by hand): build/activate
# the checkout's environment, then run scripts/kb-docdb-update.sh from the repo
# root. A release runs kb-docdb-update.sh directly from its venv instead, under
# the systemd timer installed by kb-docdb-install-timer.sh.
#
#   crontab: 0 6,18 * * * /exp/mu2e/app/users/<you>/kb-mcp/scripts/cron_docdb_update.sh
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

# Sourced, so it activates the venv in this shell; self-healing if this is a
# fresh node or /tmp got cleaned. Its output goes to the job's own log dir.
LOG_DIR="${KB_DATA_DIR:-/exp/mu2e/data/users/$USER/kb-mcp-data}/logs"
mkdir -p "$LOG_DIR"
SETUP_LOG="$LOG_DIR/docdb-setup-$(date +%Y%m%d-%H%M%S).log"
if ! source scripts/setup_mu2e_uv.sh > "$SETUP_LOG" 2>&1; then
    echo "FATAL: environment setup failed — see $SETUP_LOG" >&2
    tail -20 "$SETUP_LOG" >&2
    exit 1
fi
rm -f "$SETUP_LOG"

exec scripts/kb-docdb-update.sh
