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

# 1. Make sure uv is available.
#
# Deliberately NOT $HOME/.local/bin. $HOME here is a Fermilab NAS home area
# (/nashome), which is not to be used for this: it is quota-limited, shared,
# and not something an unattended job should depend on being mounted and
# healthy on whichever node it lands. Everything this project needs at run
# time lives on /exp instead, so uv goes beside the other persistent,
# node-independent data. Override with KB_BIN_DIR=/some/path.
KB_BIN_DIR="${KB_BIN_DIR:-$DATA_DIR/bin}"
mkdir -p "$KB_BIN_DIR"
export PATH="$KB_BIN_DIR:$PATH"

# Test $KB_BIN_DIR specifically rather than `command -v uv`: an interactive
# shell usually has a uv on PATH from $HOME, and finding it would silently
# keep the $HOME dependency alive. The copy we want has to exist *here*.
if [ ! -x "$KB_BIN_DIR/uv" ]; then
    # Prefer copying a uv we already have over re-downloading it.
    kb_existing_uv="$(command -v uv 2>/dev/null)"
    if [ -n "$kb_existing_uv" ] && cp "$kb_existing_uv" "$KB_BIN_DIR/uv" 2>/dev/null; then
        chmod +x "$KB_BIN_DIR/uv"
        echo "Copied existing uv into $KB_BIN_DIR (cron-readable)."
    else
        echo "uv not found in $KB_BIN_DIR. Installing..."
        curl -LsSf https://astral.sh/uv/install.sh \
            | env UV_INSTALL_DIR="$KB_BIN_DIR" INSTALLER_NO_MODIFY_PATH=1 sh
    fi
    unset kb_existing_uv
fi

if ! command -v uv >/dev/null 2>&1; then
    kb_setup_fail "uv is still not on PATH after attempting to install it into
  $KB_BIN_DIR — check network access and that the directory is writable."
    return 1 2>/dev/null || exit 1
fi

# uv's *managed* interpreters default to $HOME/.local/share/uv/python, which
# puts a multi-hundred-MB interpreter tree on the NAS home area and makes
# every venv depend on it. Point them at /exp so even the fallback path
# keeps off $HOME.
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$DATA_DIR/uv-python}"

echo "Using uv: $(command -v uv) ($(uv --version))"

# 2. Create the venv on local scratch if it doesn't exist yet
#
# A venv created before the $HOME changes above still symlinks its
# interpreter into $HOME (uv's managed-Python default), which quietly keeps
# the NAS-home dependency no matter what PATH says. Probe the interpreter and
# rebuild, rather than leaving a venv that only *looks* relocated.
if [ -d "$LOCAL_ENV_DIR" ] && ! "$LOCAL_ENV_DIR/bin/python" -c '' 2>/dev/null; then
    echo "Existing venv interpreter is unusable here (likely symlinked into"
    echo "\$HOME); rebuilding $LOCAL_ENV_DIR..."
    rm -rf "$LOCAL_ENV_DIR"
fi

# A managed interpreter is required here, not merely preferred: under cron's
# minimal PATH the only system Python is /usr/bin/python3 (3.9.x), which is
# below this project's requires-python (>=3.10), and there is no system
# python3.11 at all. So uv downloads CPython PYTHON_VERSION once into
# UV_PYTHON_INSTALL_DIR (on /exp, set above) and every later run reuses it.
# --python-preference system is kept so that a matching system interpreter,
# if one ever appears, wins over the download; it is not what happens today.
if [ ! -d "$LOCAL_ENV_DIR" ]; then
    echo "Local environment not found at $LOCAL_ENV_DIR. Creating..."
    if ! uv venv "$LOCAL_ENV_DIR" --python "$PYTHON_VERSION" --python-preference system; then
        kb_setup_fail "could not create the venv at $LOCAL_ENV_DIR"
        return 1 2>/dev/null || exit 1
    fi
else
    echo "Local environment found. Activating..."
fi

source "$LOCAL_ENV_DIR/bin/activate"

# Install (or top up) the project every time, not just on first creation:
# a venv built before an extra was added here would otherwise stay stale, and
# a missing parser backend fails silently — parse just returns empty text.
# docling is the default parser for PDF/PPTX/DOCX/HTML, so it is not optional
# in practice; test carries pytest so the suite runs without a second install.
# alcf carries globus_sdk, which inference_auth_token.py imports: without it
# the unattended ALCF token refresh in cron_docdb_update.sh dies with
# ModuleNotFoundError and every image description silently degrades to
# placeholder text.
echo "Installing project requirements from $SOURCE_CODE_DIR..."
if ! uv pip install -e "$SOURCE_CODE_DIR[docling,test,alcf]"; then
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
