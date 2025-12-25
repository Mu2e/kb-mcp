#!/bin/bash

# Script to use alcf inference service

# Help function
show_help() {
    cat << EOF
Usage: $0 [OPTIONS]

Configure ALCF (Argonne Leadership Computing Facility) inference service credentials
in our environment file.

Options:
    --env FILE      Path to the .env file (default: .env)
    --cluster NAME  Cluster name: 'sophia' or 'metis' (default: sophia)
    -h, --help      Show this help message and exit

Description:
    This script checks if we have an active ALCF token and updates our .env file 
    with the appropriate OPENAI_API_KEY and OPENAI_BASE_URL settings for the 
    specified cluster. If we don't have an active token, it will authenticate with ALCF 
    through a URL that will be displayed in the terminal.

Examples:
    ```bash
    # Use default .env file and sophia cluster
    $0

    # Specify custom .env file
    $0 --env .env.local

    # Use metis cluster
    $0 --cluster metis

    # Use both options
    $0 --env .env.local --cluster metis

    # Use sophia cluster explicitly
    $0 --cluster sophia
    ```
EOF
}

# Initialize defaults
ENV_FILE=".env"
CLUSTER="sophia"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)      [[ -z "$2" ]] && { echo "Error: --env requires a value"; show_help; exit 1; }
                    ENV_FILE="$2"; shift 2 ;;
        --cluster)  [[ -z "$2" ]] && { echo "Error: --cluster requires a value"; show_help; exit 1; }
                    CLUSTER="$2"; shift 2 ;;
        -h|--help) show_help; exit 0 ;;
        *)          echo "Error: Unknown option '$1'"; show_help; exit 1 ;;
    esac
done

# Lets make sure we have the dependencies installed
# mainly adds globus support
pip install -e ".[alcf]"

# get the latest script to authenticate with alcf
curl -O https://raw.githubusercontent.com/argonne-lcf/inference-endpoints/refs/heads/main/inference_auth_token.py

# authenticate with alcf
#python inference_auth_token.py authenticate

TOKEN=$(python inference_auth_token.py get_access_token)

# Validate cluster argument
if [[ "$CLUSTER" != "sophia" ]] && [[ "$CLUSTER" != "metis" ]]; then
    echo "Error: Invalid cluster '$CLUSTER'. Must be 'sophia' or 'metis'."
    echo ""
    show_help
    exit 1
fi

# Set base URL based on cluster
if [ "$CLUSTER" == "metis" ]; then
    BASE_URL="https://inference-api.alcf.anl.gov/resource_server/metis"
else
    BASE_URL="https://inference-api.alcf.anl.gov/resource_server/sophia"
fi

# If .env file doesn't exist, complain
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found."
    echo "   Please create it first. You can start from the example file: .env.example"
    exit 1
fi

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')


CURRENT_BASE_URL=$(grep "^OPENAI_BASE_URL=" "$ENV_FILE" | cut -d '=' -f2-)
if [ "$CURRENT_BASE_URL" == "$BASE_URL" ]; then
    echo "OPENAI_BASE_URL is already set to $BASE_URL, only updating OPENAI_API_KEY"
    awk -v token="$TOKEN" '
        /^OPENAI_API_KEY=/ {
            print "OPENAI_API_KEY=" token;
            next;
        }
        { print }
    ' "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
    exit 0
fi

# Process both OPENAI_API_KEY and OPENAI_BASE_URL
awk -v token="$TOKEN" -v base_url="$BASE_URL" -v timestamp="$TIMESTAMP" '
    /^OPENAI_API_KEY=/ {
        print "# " $0 "[Replaced " timestamp "] ";
        print "OPENAI_API_KEY=" token;
        key_found=1;
        next;
    }
    /^OPENAI_BASE_URL=/ {
        print "# " $0 " [Replaced " timestamp "] ";
        print "OPENAI_BASE_URL=" base_url;
        url_found=1;
        next;
    }
    { print }
    END {
        if (!key_found) {
            print "";
            print "# ALCF Configuration - Added " timestamp;
            print "OPENAI_API_KEY=" token;
        }
        if (!url_found) {
            if (key_found) print "";
            print "OPENAI_BASE_URL=" base_url;
        }
    }
' "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"

echo "✓ Updated ALCF configuration in $ENV_FILE"
echo "  Cluster: $CLUSTER"
echo "  Base URL: $BASE_URL"
echo ""
echo "Current ALCF configuration:"
grep "^OPENAI_API_KEY=\|^OPENAI_BASE_URL=" "$ENV_FILE"





################################# List available models #################################
# Fetch and display available models
echo "Please verify OPENAI_MODEL is set in $ENV_FILE"
echo ""
echo "Available models on $CLUSTER:"

MODELS_JSON=$(curl -sS -X GET "https://inference-api.alcf.anl.gov/resource_server/list-endpoints" \
    -H "Authorization: Bearer ${TOKEN}")


if [ $? -eq 0 ] && [ -n "$MODELS_JSON" ]; then
    if [ "$CLUSTER" == "metis" ]; then
        echo "$MODELS_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
models = data.get('clusters', {}).get('metis', {}).get('frameworks', {}).get('api', {}).get('models', [])
for model in models:
    print(f'  {model}')
" 2>/dev/null || echo "Error parsing models"
    else
        echo "$MODELS_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
models = data.get('clusters', {}).get('sophia', {}).get('frameworks', {}).get('vllm', {}).get('models', [])
for model in models:
    print(f'  {model}')
" 2>/dev/null || echo "Error parsing models"
    fi
else
    echo "Could not fetch models list from ALCF"
fi

echo ""