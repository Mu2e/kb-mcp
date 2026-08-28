#!/bin/bash

HOST="anl-login"

# 1. Check if a ControlMaster SSH connection is already active (using IPv4 -4)
#if ! ssh -4 -O check "$HOST" 2>&1 | grep -q "not running"; then
#    echo "SSH connection to $HOST is already active."
#else
#    echo "Opening SSH connection to $HOST..."
#    ssh -4 -fN "$HOST"
#fi
#
## 2. Check/start argo-proxy serve on the remote machine safely
#ssh -4 "$HOST" "pgrep -x argo-proxy > /dev/null || nohup argo-proxy serve > /dev/null 2>&1 &"
#
## Brief pause to ensure everything initializes
#sleep 2

# 3. Set environment variables and launch Claude
export ANTHROPIC_BASE_URL="http://127.0.0.1:64259"
export ANTHROPIC_API_KEY="sk-ant-dummy-key"
claude

