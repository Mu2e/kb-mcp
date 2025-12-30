#!/bin/bash

# This script launches an vLLM server on NERSC Perlmutter using SLURM and Shifter 
# to run openai/gpt-oss-120. The start up takes ~10minutes. 

# --- Configuration ---
JOB_NAME="kb-mcp-llm"
IMAGE="vllm/vllm-openai:v0.11.0"
MODEL="openai/gpt-oss-120b"
HF_HOME=$SCRATCH"/huggingface"
ENV_FILE="$HOME/.kb-mcp.env.local"
LOG_FILE="logs/${JOB_NAME}.log"

CONFIG_FILE="$HOME/.kb-mcp-llm.config.json"

. $ENV_FILE 2>/dev/null

if [ -z "$HF_TOKEN" ]; then
    echo "HF_TOKEN is not set. Please set it before running the script.
         You can obtain a token from https://huggingface.co/settings/tokens
         Please store it in your .kb-mcp.local file as HF_TOKEN=<your_token>"
    exit 1
fi

# --- Function: Run the Server (Compute Node Logic) ---
run_server() {
    local API_KEY=$1
    echo "Starting vLLM on $(hostname) with API Key: $API_KEY"
    
    # Export env vars for Shifter
    export HF_HOME=$HF_HOME
    export HF_TOKEN=$HF_TOKEN

    # Direct Shifter run (Foreground)
    shifter --image=$IMAGE --module=gpu,nccl-plugin \
        --env HF_HOME=$HF_HOME \
        --env HF_TOKEN=$HF_TOKEN \
        -- \
        vllm serve $MODEL \
        --api-key "$API_KEY" \
        --tensor-parallel-size 4 \
        --host 0.0.0.0 \
        --port 8000 \
        --gpu-memory-utilization 0.90
}

# --- Function: Submit the Job (Login Node Logic) ---
submit_job() {
    local API_KEY=$1
    echo "Submitting a slurm job to launch vLLM with API Key $API_KEY"
    
    # We submit THIS script recursively to the scheduler!
    # The --wrap command tells SLURM to run this script again once on the node.
    # We pass the API key so the worker knows it.
    JOB_ID=$(sbatch --parsable --job-name="$JOB_NAME" \
        --output="$LOG_FILE" --error="$LOG_FILE" \
        -A m5115_g -C gpu -t 01:00:00 \
        -N 1 --ntasks-per-node=1 --gpus-per-node=4 \
        --wrap="$0 --worker --api-key $API_KEY")

    if [ -z "$JOB_ID" ]; then
        echo "Submission failed."
        exit 1
    fi
    
    echo "Job Submitted! ID: $JOB_ID"
    echo "Watching logs in $LOG_FILE..."
    
    while [ ! -f "$LOG_FILE" ]; do
        sleep 1
    done

    # Wait/Watch Logic
    NODE=""
    tail -f "$LOG_FILE" &
    TAIL_PID=$!
    
    # Poll for node allocation
    while [ -z "$NODE" ]; do
        NODE=$(squeue -j "$JOB_ID" -h -o "%N")
        if [ -z "$(squeue -j "$JOB_ID" -h)" ]; then
            kill $TAIL_PID 2>/dev/null
            echo "Job died."
            exit 1
        fi
        sleep 2
    done
    
    echo "Job is starting on node: $NODE, waiting for vLLM to be ready... (this may take 5 to 10 minutes)"
    # Poll for Readiness
    URL="http://$NODE:8000/v1"
    while true; do
        curl -s -f -o /dev/null -H "Authorization: Bearer $API_KEY" "$URL/models" 2>/dev/null
        if [ $? -eq 0 ]; then break; fi
        
        if [ -z "$(squeue -j "$JOB_ID" -h)" ]; then
           kill $TAIL_PID 2>/dev/null; echo "Job died."; exit 1
        fi
        sleep 15
    done
    
    kill $TAIL_PID 2>/dev/null
    wait $TAIL_PID 2>/dev/null
    
    # Write Config
    cat > "$CONFIG_FILE" <<EOF
{
  "base_url": "$URL",
  "api_key": "$API_KEY",
  "model": "$MODEL",
  "job_id": "$JOB_ID",
  "node": "$NODE"
}
EOF
    echo -e "LLM IS READY! Config saved to $CONFIG_FILE"
    cat <<EOF
    You can test it with:

    curl -X POST "$URL/chat/completions" \\
    -H "Content-Type: application/json" \\
    -H "Authorization: Bearer $API_KEY" \\
    -d '{
        "model": "$MODEL",
        "messages": [
        {"role": "user", "content": "Hello, world!"}
        ]
    }'
EOF

    # Update ENV_FILE with OPENAI_API_KEY and OPENAI_BASE_URL
    echo ""
    echo "Updating $ENV_FILE with OPENAI_API_KEY and OPENAI_BASE_URL..."

    # Resolve symlink if ENV_FILE is one
    if [ -L "$ENV_FILE" ]; then
        REAL_ENV_FILE=$(readlink -f "$ENV_FILE" 2>/dev/null || readlink "$ENV_FILE")
        echo "Note: $ENV_FILE is a symlink to $REAL_ENV_FILE"
        echo "      Updating the target file to preserve symlink"
        TARGET_FILE="$REAL_ENV_FILE"
    else
        TARGET_FILE="$ENV_FILE"
    fi

    # Create target file if it doesn't exist
    if [ ! -f "$TARGET_FILE" ]; then
        echo "Creating $TARGET_FILE for user-specific settings..."
        touch "$TARGET_FILE"
    fi

    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

    # Process both OPENAI_API_KEY and OPENAI_BASE_URL
    awk -v token="$API_KEY" -v base_url="$URL" -v timestamp="$TIMESTAMP" '
        /^OPENAI_API_KEY=/ {
            print "# " $0 " [Replaced " timestamp "]";
            print "OPENAI_API_KEY=" token;
            key_found=1;
            next;
        }
        /^OPENAI_BASE_URL=/ {
            print "# " $0 " [Replaced " timestamp "]";
            print "OPENAI_BASE_URL=" base_url;
            url_found=1;
            next;
        }
        { print }
        END {
            if (!key_found) {
                print "";
                print "# NERSC vLLM Configuration - Added " timestamp;
                print "OPENAI_API_KEY=" token;
            }
            if (!url_found) {
                if (key_found) print "";
                print "OPENAI_BASE_URL=" base_url;
            }
        }
    ' "$TARGET_FILE" > "$TARGET_FILE.tmp" && mv "$TARGET_FILE.tmp" "$TARGET_FILE"

    echo "✓ Updated NERSC vLLM configuration in $TARGET_FILE"
    if [ "$TARGET_FILE" != "$ENV_FILE" ]; then
        echo "  (via symlink $ENV_FILE)"
    fi
    echo "  Base URL: $URL"
    echo ""
    echo "Current configuration:"
    grep "^OPENAI_API_KEY=\|^OPENAI_BASE_URL=" "$TARGET_FILE"

}

# --- Main Entry Point ---

# Generate Key (if not passed)
API_KEY=$(openssl rand -hex 16)

# Check arguments
if [[ "$1" == "--worker" ]]; then
    # We are running inside the SLURM job (Recursive call)
    # Grab the key passed from the submitter
    shift; shift # skip --worker and --api-key
    run_server "$1"
    exit 0
fi

# Detect Environment
if [[ -n "$SLURM_JOB_ID" ]]; then
    # We are ALREADY on a compute node (Interactive Session)
    # Just run the server directly.
    echo "Detected Interactive Session (Node: $(hostname))"
    run_server "$API_KEY"
else
    # We are on a Login Node -> Submit a job
    submit_job "$API_KEY"
fi
