#!/bin/bash
# Pilot a real `kb reparse` over a handful of documents, with the log turned
# up enough to see each pipeline stage (parse -> image descriptions -> summary
# -> chunk/embed) and any throttling the retry path reports.
#
# Usage:  ./run_pilot.sh [n_docs]      (default 3)
#
# Safe to re-run: reparse is idempotent per document.

set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

N="${1:-3}"
STAMP=$(date +%Y%m%d-%H%M%S)
LOG="pilot-${STAMP}.log"

# The documents: small, 3 images each, so every stage runs but nothing takes
# long. Extend this list to widen the pilot.
DOCS=(
  "56410"
  "56362"
  "12815-Drier_Tower_PV_EN02507_LEFT"
  "56422/Mu2e Near Critical Activities March 2026"
  "56278/Variance Report - DRRF"
  "42556"
)

args=()
for d in "${DOCS[@]:0:$N}"; do
  args+=(--doc-id "$d")
done

echo "=== pilot: $N document(s), log -> $LOG ==="
printf '  %s\n' "${DOCS[@]:0:$N}"
echo

# INFO on our own modules so stage boundaries and the throttle warnings show
# up; the noisy third-party loaders stay filtered in the tee'd view below.
export LOG_LEVEL=INFO
export PYTHONUNBUFFERED=1

start=$(date +%s)
# Unbuffered + tee: full detail lands in $LOG, while the terminal shows a
# filtered view. `stdbuf` keeps the pipeline from batching output into chunks.
stdbuf -oL -eL kb reparse --source-id mu2e-docdb "${args[@]}" 2>&1 \
  | tee "$LOG" \
  | stdbuf -oL grep -Ev "Loading weights|RapidOCR|^\[INFO\].*(rapidocr|device_config|download_file)|tie_word_embeddings|UserWarning|F\.conv2d|HF_TOKEN|pad_token_id"
rc=${PIPESTATUS[0]}
end=$(date +%s)

echo
echo "=== finished in $((end - start))s (exit $rc) ==="

# The failure modes that matter here are the quiet ones: throttling that the
# retry path gave up on, and placeholder text written in place of a real
# description. Surface both explicitly rather than trusting a scan of stdout.
echo "--- throttling / retry ---"
grep -iE "rate limit|throttl|gave up after retries|RateLimited" "$LOG" || echo "  none"
echo "--- image descriptions unavailable ---"
grep -c "Image description unavailable" "$LOG" 2>/dev/null || echo "  0"
echo "--- errors ---"
grep -iE "^\[ERROR\]|Error:|Traceback" "$LOG" | head -20 || echo "  none"
echo
echo "full log: $LOG"
