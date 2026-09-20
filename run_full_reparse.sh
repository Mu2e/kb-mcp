#!/bin/bash
# Full mu2e-docdb re-parse, logging to shared /exp so it can be watched from
# another host.
#
#   ./run_full_reparse.sh            # all remaining mu2e-docdb documents
#   ./run_full_reparse.sh --dry-run  # show the target list and stop
#
# Run it under nohup/tmux — this takes hours and must survive a dropped ssh:
#   nohup ./run_full_reparse.sh > /dev/null 2>&1 &

set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

# Logs are data, and /exp/mu2e/app is nearly full — keep them on the data
# volume, next to the other persistent artefacts. Honours KB_DATA_DIR the same
# way scripts/setup_mu2e_uv.sh does.
LOG_DIR="${KB_DATA_DIR:-/exp/mu2e/data/users/$USER/kb-mcp-data}/logs"
mkdir -p "$LOG_DIR"
STAMP=$(date +%Y%m%d-%H%M%S)
LOG="$LOG_DIR/reparse-${STAMP}.log"
STATUS="$LOG_DIR/reparse-${STAMP}.status"

# `kb` must come from the venv, not whatever is first on PATH.
command -v kb >/dev/null 2>&1 || {
    echo "ERROR: 'kb' not on PATH — source scripts/setup_mu2e_uv.sh first" >&2
    exit 1
}

export PYTHONUNBUFFERED=1

{
    echo "host      : $(hostname)"
    echo "started   : $(date -Is)"
    echo "kb        : $(command -v kb)"
    echo "git       : $(git rev-parse --short HEAD) ($(git status --porcelain | wc -l) files dirty)"
    python - <<'PY' 2>/dev/null || true
import torch
print(f"device    : {'CUDA ' + torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only'}")
PY
    echo "log       : $LOG"
    echo "---"
} | tee "$STATUS"

start=$(date +%s)
stdbuf -oL -eL kb reparse --source-id mu2e-docdb "$@" >> "$LOG" 2>&1
rc=$?
end=$(date +%s)

{
    echo "---"
    echo "finished  : $(date -Is)"
    echo "elapsed   : $(( (end-start)/60 )) min"
    echo "exit code : $rc"
    # `grep -c` exits 1 on no matches, so let it print its own 0 rather than
    # adding a second one via ||.
    echo "throttled : $(grep -ci 'gave up after retries' "$LOG"; true)"
    echo "placeholder descriptions: $(grep -c 'Image description unavailable' "$LOG"; true)"
    echo "errors    : $(grep -c ' - ERROR - ' "$LOG"; true)"
    tail -3 "$LOG"
} | tee -a "$STATUS"

exit $rc
