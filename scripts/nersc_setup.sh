#!/bin/bash

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "This script is meant to be sourced, not executed."
    echo "   Please run:"
    echo "   $ source ${BASH_SOURCE[0]}"
    exit 1
fi

DO_UPDATE=false
for arg in "$@"; do
    if [[ "$arg" == "--pull" ]] || [[ "$arg" == "--update" ]]; then
        DO_UPDATE=true
    fi
done

# --- Configuration ---
PROJECT_ID="m5115"
REPO_NAME="kb-mcp"
REPO_URL="git@github.com:HEP-KE/kb-mcp.git"
USER_SECRETS="$HOME/.kb-mcp.env"
SHARED_SECRETS="/global/cfs/cdirs/$PROJECT_ID/secrets/kb-mcp.env" # fallback if user secrets don't exist

# --- Paths ---
# User specific directory in "software" for faster loading
SOFTWARE_BASE="/global/common/software/$PROJECT_ID/$USER"
VENV_PATH="$SOFTWARE_BASE/${REPO_NAME}-venv"
SCRATCH_REPO="$SCRATCH/$REPO_NAME"
DATA_PERSISTENT="$CFS/$PROJECT_ID/$USER/${REPO_NAME}-data"

# Load python
module load python
PYTHON_EXE=$(which python)
echo "Using Python: $PYTHON_EXE"

# --- Check/Create Environment in Software ---
if [ ! -d "$VENV_PATH" ]; then
    echo "Creating new virtual environment in Software filesystem..."
    echo "Path: $VENV_PATH"
    
    mkdir -p "$SOFTWARE_BASE"
    
    # Create venv using the MODULE python
    "$PYTHON_EXE" -m venv "$VENV_PATH" --prompt "kb"
    
    # Activate and Upgrade pip
    source "$VENV_PATH/bin/activate"
    pip install --upgrade pip
else
    # Activate
    source "$VENV_PATH/bin/activate"
fi

# --- Check/Clone Repo in Scratch ---
if [ ! -d "$SCRATCH_REPO" ]; then
    echo "Cloning repository to Scratch..."
    cd $SCRATCH
    git clone "$REPO_URL"

    # --- Check if clone worked ---
    if [ $? -ne 0 ] || [ ! -d "$SCRATCH_REPO" ]; then
        echo "ERROR: Git clone failed."
        echo "   Maybe you need to add your SSH keys to github?"
        return 1
    fi
else
    # Repo exists, check if update requested
    if [ "$DO_UPDATE" = true ]; then
        echo "Pulling changes..."
        cd "$SCRATCH_REPO"
        git pull
        if [ $? -ne 0 ]; then
            echo "Git pull failed. Continuing with local version."
        fi
    fi
fi

# --- Install Package ---
cd "$SCRATCH_REPO"

# --- Setup Local Symlinks ---
# Adding a local .venv
if [ ! -L "$SCRATCH_REPO/.venv" ]; then
    echo "Linking .venv -> $VENV_PATH"
    ln -s "$VENV_PATH" "$SCRATCH_REPO/.venv"
fi

# Create a persistent directory if it doesn't exist
if [ ! -d "$DATA_PERSISTENT" ]; then
    echo "Creating persistent data dir in CFS..."
    mkdir -p "$DATA_PERSISTENT"
fi

# Link it to the "data" folder in the repository
if [ ! -L "$SCRATCH_REPO/data" ]; then
    echo "Linking ./data -> CFS (Persistent)"
    # If a real 'data' folder accidentally exists, warn or move it
    if [ -d "$SCRATCH_REPO/data" ]; then
        echo "WARNING: Found an existing 'data' directory in scratch. Please fix!"
    else
        ln -s "$DATA_PERSISTENT" "$SCRATCH_REPO/data"
    fi
fi

FORCE_INSTALL=$DO_UPDATE

if ! python -m pip show "$REPO_NAME" > /dev/null 2>&1; then
    FORCE_INSTALL=true
fi

if [ "$FORCE_INSTALL" = true ]; then
    echo "Installing/Updating package dependencies..."
    python -m pip install --upgrade pip
    python -m pip install -e ".[docs]"
fi

rm -f "$SCRATCH_REPO/.env"

if [ -f "$USER_SECRETS" ]; then
    echo "Settings: Using override from ($USER_SECRETS)"
    ln -s "$USER_SECRETS" "$SCRATCH_REPO/.env"
elif [ -f "$SHARED_SECRETS" ]; then
    echo "Settings: Using SHARED settings from ($SHARED_SECRETS)"
    ln -s "$SHARED_SECRETS" "$SCRATCH_REPO/.env"
else
    echo "WARNING: No settings (.kb-mcp.env) found!"
    echo "   checked: $USER_SECRETS"
    echo "   checked: $SHARED_SECRETS"
fi

# Setup .env.local for user-specific overrides (e.g., ALCF credentials)
# This file is loaded AFTER .env and overrides shared settings
USER_ENV_LOCAL="$HOME/.kb-mcp.env.local"
rm -f "$SCRATCH_REPO/.env.local"

if [ -f "$USER_ENV_LOCAL" ]; then
    echo "User overrides: Linking .env.local -> $USER_ENV_LOCAL"
    ln -s "$USER_ENV_LOCAL" "$SCRATCH_REPO/.env.local"
else
    echo "User overrides: Creating empty $USER_ENV_LOCAL"
    echo "# User-specific environment overrides (e.g., ALCF credentials)" > "$USER_ENV_LOCAL"
    echo "# This file is loaded AFTER .env and takes precedence" >> "$USER_ENV_LOCAL"
    ln -s "$USER_ENV_LOCAL" "$SCRATCH_REPO/.env.local"
fi

# --- Fix Prompt: Remove the inner (nersc-python) label ---
# syntax: ${VARIABLE//search/replace}
if [[ -n "$PS1" ]]; then
    PS1=${PS1// (nersc-python)/}
fi

# --- Start the database ---
source "$SCRATCH_REPO/scripts/nersc_setup_db.sh"

echo "Environment activated. You are in $SCRATCH_REPO"

CURRENT_HOST=$(hostname -f)
echo "----------------------------------------------------------------"
echo "Type 'kb --help' for an overview of the command line tools."
echo "----------------------------------------------------------------"
echo "If you start the server (kb-server), port froward with:"
echo "  ssh -J $USER@perlmutter.nersc.gov -L 8443:localhost:8443 $CURRENT_HOST"
echo "  and acces it at http://localhost:8443 in your browser"
echo "----------------------------------------------------------------"
