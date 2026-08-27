#!/bin/bash
# Deliberately no `set -e`: this script is sourced, so a non-zero status from
# any command would exit the *caller's* interactive shell. The steps whose
# failure actually matters are checked explicitly below and reported with
# `kb_setup_fail`, which returns rather than exits for the same reason.

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
# (this script lives in scripts/, so the project root is one level up).
#
# BASH_SOURCE, not $0: this script is meant to be *sourced*, and when sourced
# $0 is the calling shell ("bash", or "-bash" under a login shell) rather than
# this file — which resolved the project to /usr and made `uv pip install`
# fail with "does not appear to be a Python project".
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SOURCE_CODE_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON_VERSION="3.11"

# Report a fatal setup problem without killing the caller's shell (see the
# note on `set -e` above). Sourced: returns. Executed: exits non-zero.
kb_setup_fail() {
    echo "ERROR: $*" >&2
    if [ "${BASH_SOURCE[0]}" != "$0" ]; then
        return 1
    fi
    exit 1
}

if [ ! -f "$SOURCE_CODE_DIR/pyproject.toml" ]; then
    kb_setup_fail "no pyproject.toml under $SOURCE_CODE_DIR — this script must
  live in <repo>/scripts/. Source it as: source scripts/setup_mu2e_uv.sh"
    return 1 2>/dev/null || exit 1
fi

# Where to put the venv. Override with: KB_ENV_DIR=/some/path ./setup_mu2e_uv.sh
LOCAL_ENV_DIR="${KB_ENV_DIR:-/tmp/$USER/kb-env-uv}"

# uv's package cache defaults to $HOME/.cache/uv, which can blow past small
# home-directory quotas. Keep it on local scratch next to the venv instead.
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/$USER/uv-cache}"

# Persistent data (model weights, etc.) lives separately from both the code
# and the disposable local-scratch environment, so it survives /tmp cleanup
# and isn't tied to a single node. Override with: KB_DATA_DIR=/some/path
DATA_DIR="${KB_DATA_DIR:-/exp/mu2e/data/users/$USER/kb-mcp-data}"

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
else
    echo "Local environment found. Activating..."
fi

source "$LOCAL_ENV_DIR/bin/activate"

# Install (or top up) the project every time, not just on first creation:
# a venv built before an extra was added here would otherwise stay stale, and
# a missing parser backend fails silently — parse just returns empty text.
# docling is the default parser for PDF/PPTX/DOCX/HTML, so it is not optional
# in practice; test carries pytest so the suite runs without a second install.
echo "Installing project requirements from $SOURCE_CODE_DIR..."
if ! uv pip install -e "$SOURCE_CODE_DIR[docling,test]"; then
    kb_setup_fail "uv pip install failed — the venv at $LOCAL_ENV_DIR may be
  missing docling, and a missing parser backend fails silently at run time
  (parse returns empty text). Fix the install before parsing anything."
    return 1 2>/dev/null || exit 1
fi

# 3. Configure environment
mkdir -p "$DATA_DIR/huggingface_cache"
export HF_HOME="$DATA_DIR/huggingface_cache"

echo "Environment activated."
echo "Project path: $SOURCE_CODE_DIR"
echo "Data path:    $DATA_DIR"
echo "Python location: $(which python)"
