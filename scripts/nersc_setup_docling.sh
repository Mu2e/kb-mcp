#!/bin/bash
# Set up docling for GPU parsing on Perlmutter compute nodes.
#
# The entire docling_libs directory is copied to node-local /tmp (tmpfs, 176GB)
# so that RapidOCR and other model files are loaded from fast local storage.
# RapidOCR resolves model paths relative to its own package directory, so moving
# the whole package is the only reliable way to avoid slow Lustre random reads.
#
# Usage: source scripts/nersc_setup_docling.sh [--download]
#   --download   Force re-download of HF/docling models into /tmp

DOCLING_LIB_DIR="$SCRATCH/docling_libs"

DO_DOWNLOAD=false
for arg in "$@"; do
    if [[ "$arg" == "--download" ]]; then
        DO_DOWNLOAD=true
    fi
done

# Install library to SCRATCH if not already present (only needs to happen once)
if [ ! -d "$DOCLING_LIB_DIR" ]; then
    echo "Installing docling to $DOCLING_LIB_DIR..."
    mkdir -p "$DOCLING_LIB_DIR"
    python -m pip install --target "$DOCLING_LIB_DIR" "docling"
    echo "docling installed"
fi

LOCAL_BASE="/tmp/docling_$USER"
LOCAL_LIBS="$LOCAL_BASE/docling_libs"
LOCAL_HF="$LOCAL_BASE/huggingface"
LOCAL_DOCLING="$LOCAL_BASE/docling_cache"
LOCAL_TORCH="$LOCAL_BASE/torch_cache"

# Copy entire docling_libs to /tmp so RapidOCR finds its models locally.
if [ ! -d "$LOCAL_LIBS" ]; then
    echo "Copying docling_libs to local tmpfs ($LOCAL_LIBS)..."
    mkdir -p "$LOCAL_BASE"
    cp -r "$DOCLING_LIB_DIR" "$LOCAL_LIBS"
    echo "Done copying docling_libs."
else
    echo "docling_libs already in local tmpfs."
fi

# Point all env vars at /tmp — models download/load from fast local storage
export PYTHONPATH="$LOCAL_LIBS:$PYTHONPATH"
export DOCLING_CACHE_DIR="$LOCAL_DOCLING"
export HF_HOME="$LOCAL_HF"
export TORCH_HOME="$LOCAL_TORCH"

echo "PYTHONPATH updated: $PYTHONPATH"
echo "DOCLING_CACHE_DIR: $DOCLING_CACHE_DIR"
echo "HF_HOME: $HF_HOME"

# Download HF/docling models into /tmp if not already present (or forced)
if [ "$DO_DOWNLOAD" = true ] || [ ! -d "$LOCAL_DOCLING" ]; then
    echo "Downloading Docling models to local tmpfs (this may take a few minutes)..."
    mkdir -p "$LOCAL_DOCLING" "$LOCAL_HF" "$LOCAL_TORCH"
    python - <<'EOF'
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions

print("Initializing converter and downloading models...")
pipeline_options = PdfPipelineOptions()
pipeline_options.do_ocr = True
pipeline_options.do_table_structure = True
pipeline_options.generate_picture_images = True

converter = DocumentConverter(
    format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
)
print("Models downloaded and cached successfully.")
EOF
else
    echo "Models already present in local tmpfs ($LOCAL_BASE)."
fi
