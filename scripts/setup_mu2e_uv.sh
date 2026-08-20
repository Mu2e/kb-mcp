#!/bin/bash
set -e

# This script keeps three concerns in three separate places:
#   - code:        wherever this repo is checked out (detected below)
#   - environment: disposable, fast local scratch (/tmp) — venv + package
#                  cache, safe to delete and rebuild at any time
#   - data:        persistent storage (/exp) — anything expensive to
#                  regenerate (e.g. downloaded model weights), so it
#                  survives /tmp cleanup and isn't tied to one node
#
# Override any of the three locations via KB_ENV_DIR / UV_CACHE_DIR /
# KB_DATA_DIR in the environment before running this script.

# Dynamically set the project directory based on where this script is located
# (this script lives in scripts/, so the project root is one level up)
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
SOURCE_CODE_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON_VERSION="3.11"

# Where to put the venv. Override with: KB_ENV_DIR=/some/path ./setup_mu2e_uv.sh
LOCAL_ENV_DIR="${KB_ENV_DIR:-/tmp/$USER/kb-env-uv}"

# uv's package cache defaults to $HOME/.cache/uv, which can blow past small
# home-directory quotas. Keep it on local scratch next to the venv instead.
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/$USER/uv-cache}"

# Persistent data (model weights, etc.) lives separately from both the code
# and the disposable local-scratch environment, so it survives /tmp cleanup
# and isn't tied to a single node. Override with: KB_DATA_DIR=/some/path
DATA_DIR="${KB_DATA_DIR:-/exp/mu2e/app/users/scorrodi/kb-mcp-data}"

# 1. Make sure uv is available, installing it to ~/.local/bin if not
if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found. Installing to \$HOME/.local/bin..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "Using uv: $(command -v uv) ($(uv --version))"

# 2. Create the venv on local scratch if it doesn't exist yet
if [ ! -d "$LOCAL_ENV_DIR" ]; then
    echo "Local environment not found at $LOCAL_ENV_DIR. Creating..."
    uv venv "$LOCAL_ENV_DIR" --python "$PYTHON_VERSION"

    source "$LOCAL_ENV_DIR/bin/activate"

    echo "Installing project requirements from $SOURCE_CODE_DIR..."
    uv pip install -e "$SOURCE_CODE_DIR"
else
    echo "Local environment found. Activating..."
    source "$LOCAL_ENV_DIR/bin/activate"
fi

# 3. Configure environment
mkdir -p "$DATA_DIR/huggingface_cache"
export HF_HOME="$DATA_DIR/huggingface_cache"

echo "Environment activated."
echo "Project path: $SOURCE_CODE_DIR"
echo "Data path:    $DATA_DIR"
echo "Python location: $(which python)"
