#!/bin/bash
# Install marker-pdf in SCRATCH and export PYTHONPATH

MARKER_LIB_DIR="$SCRATCH/marker_libs"

# Install if not already present
if [ ! -d "$MARKER_LIB_DIR" ]; then
    echo "Installing marker-pdf to $MARKER_LIB_DIR..."
    mkdir -p "$MARKER_LIB_DIR"
    python -m pip install --target "$MARKER_LIB_DIR" "marker-pdf[all]"
    echo "marker-pdf installed"
fi

# Export PYTHONPATH
export PYTHONPATH="$MARKER_LIB_DIR:$PYTHONPATH"
echo "PYTHONPATH updated: $PYTHONPATH"
