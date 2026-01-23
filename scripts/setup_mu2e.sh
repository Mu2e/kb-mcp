#!/bin/bash
# Enable conda for this shell
source /exp/mu2e/app/users/scorrodi/kb-mcp/miniforge/bin/activate

# Activate the specific environment
conda activate /exp/mu2e/app/users/scorrodi/kb-mcp/env

conda config --env --set env_prompt '(kb-mcp) '

export HF_HOME=$(pwd)/.huggingface_cache

echo "Environment activated. Python is located at: $(which python)"
