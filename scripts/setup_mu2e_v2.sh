#!/bin/bash

# Dynamically set the project directory based on where this script is located
SOURCE_CODE_DIR="$(dirname "$(realpath "$0")")"
LOCAL_ENV_DIR="/tmp/scorrodi/kb-env"
MINIFORGE_INSTALLER="/exp/mu2e/app/users/scorrodi/Miniforge3-Linux-x86_64.sh"

# 1. Check if environment exists on local scratch
if [ ! -d "$LOCAL_ENV_DIR" ]; then
    echo "Local environment not found at $LOCAL_ENV_DIR. Installing..."
    
    # Create directory and install base miniforge
    mkdir -p "$LOCAL_ENV_DIR"
    bash "$MINIFORGE_INSTALLER" -b -p "$LOCAL_ENV_DIR"
    
    # Activate the newly created base environment
    source "$LOCAL_ENV_DIR/bin/activate"
    
    # Install your project in editable mode
    echo "Installing project requirements from $SOURCE_CODE_DIR..."
    pip install -e "$SOURCE_CODE_DIR"
else
    echo "Local environment found. Activating..."
    source "$LOCAL_ENV_DIR/bin/activate"
fi

# 2. Configure environment
conda config --env --set env_prompt '(kb-mcp) '
export HF_HOME="$SOURCE_CODE_DIR/.huggingface_cache"

echo "Environment activated."
echo "Project path: $SOURCE_CODE_DIR"
echo "Python location: $(which python)"
