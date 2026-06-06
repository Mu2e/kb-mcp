#!/bin/bash
# Set up Nougat for GPU parsing on Perlmutter compute nodes.
#
# Installs pdf2image and Pillow to SCRATCH (transformers is already in the venv).
# On first run with --download, downloads the facebook/nougat-base HuggingFace
# model (~1.5GB) to HF_HOME. Subsequent runs reuse the cached model.
#
# Usage: source scripts/nersc_setup_nougat.sh [--download]
#   --download   Force re-download of the HuggingFace model

NOUGAT_LIB_DIR="$SCRATCH/nougat_libs"

DO_DOWNLOAD=false
for arg in "$@"; do
    if [[ "$arg" == "--download" ]]; then
        DO_DOWNLOAD=true
    fi
done

# Install dependencies to SCRATCH if not already present.
# We do NOT install nougat-ocr itself — it requires pyarrow<4.0 which fails
# to build on Python 3.11. Instead we use transformers directly, which provides
# NougatProcessor and AutoModelForVision2Seq natively since transformers 4.37.
# pymupdf is used for PDF rasterization (no system poppler dependency needed).
if [ ! -d "$NOUGAT_LIB_DIR" ]; then
    echo "Installing nougat dependencies to $NOUGAT_LIB_DIR..."
    mkdir -p "$NOUGAT_LIB_DIR"
    python -m pip install --target "$NOUGAT_LIB_DIR" "pymupdf" "Pillow" "nltk" "python-Levenshtein"
    echo "nougat dependencies installed"
fi

export PYTHONPATH="$NOUGAT_LIB_DIR:$PYTHONPATH"
echo "PYTHONPATH updated: $PYTHONPATH"

# HuggingFace model cache — reuse the same HF_HOME as other parsers if set,
# otherwise default to SCRATCH so models survive across sessions.
if [ -z "$HF_HOME" ]; then
    export HF_HOME="$SCRATCH/huggingface"
    echo "HF_HOME: $HF_HOME"
fi

# Download model if requested or not yet cached
MODEL_CACHE_DIR="$HF_HOME/hub/models--facebook--nougat-base"
if [ "$DO_DOWNLOAD" = true ] || [ ! -d "$MODEL_CACHE_DIR" ]; then
    echo "Downloading facebook/nougat-base model (this may take a few minutes)..."
    python - <<'EOF'
from transformers import AutoModelForImageTextToText, NougatProcessor
print("Downloading NougatProcessor...")
processor = NougatProcessor.from_pretrained("facebook/nougat-base")
print("Downloading Nougat model weights...")
model = AutoModelForImageTextToText.from_pretrained("facebook/nougat-base")
print("Model downloaded and cached successfully.")
EOF
else
    echo "Nougat model already cached at $MODEL_CACHE_DIR"
fi