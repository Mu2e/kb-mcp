#!/bin/bash

if [ $# -eq 0 ]; then
    echo "Usage: $0 <command>"
    exit 1
fi

echo "Launching parallel jobs on 4 GPUs with live terminal output..."
trap "kill 0" EXIT

for i in {0..3}
do
    echo "Starting GPU $i..."
    # 1. 2>&1 merges errors into the standard output
    # 2. | tee gpu_${i}.log sends it to the file
    # 3. | sed adds a prefix [GPU X] for your terminal view
    CUDA_VISIBLE_DEVICES=$i "$@" 2>&1 \
        | tee "gpu_${i}.log" \
        | sed "s/^/[GPU $i] /" &
done

wait
echo "All parallel tasks completed."

