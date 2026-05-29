#!/bin/bash
# Run a command in parallel across N workers, passing WORKER_ID=0..N-1 to each.
# Used for CPU-based parsers that don't need CUDA_VISIBLE_DEVICES.
#
# Usage: ./scripts/run_on_n_workers.sh N <command> [args...]

if [ $# -lt 2 ]; then
    echo "Usage: $0 <num_workers> <command> [args...]"
    exit 1
fi

NUM_WORKERS="$1"
shift

echo "Launching $NUM_WORKERS parallel worker(s)..."
trap "kill 0" EXIT

for i in $(seq 0 $((NUM_WORKERS - 1))); do
    WORKER_ID=$i WORKER_ID=$i "$@" 2>&1 \
        | tee "worker_${i}.log" \
        | sed "s/^/[Worker $i] /" &
done

wait
echo "All parallel tasks completed."
