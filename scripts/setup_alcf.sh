#!/bin/bash

# Script to use alcf inference service

# Help function
show_help() {
    cat << EOF
Usage: $0 [OPTIONS]

Configure ALCF (Argonne Leadership Computing Facility) inference service credentials
in a user-specific environment file.

Options:
    --env FILE      Path to the user-specific .env file (default: .env.local)
    --cluster NAME  Cluster name: 'sophia' or 'metis' (default: sophia)
    --list-models   Skip setup and only list available models for the cluster
    -h, --help      Show this help message and exit

Description:
    This script checks if we have an active ALCF token and updates a user-specific
    .env.local file with the appropriate OPENAI_API_KEY and OPENAI_BASE_URL settings
    for the specified cluster. If we don't have an active token, it will authenticate
    with ALCF through a URL that will be displayed in the terminal.

    The .env.local file is loaded AFTER .env and overrides shared settings, allowing
    each user to have their own ALCF credentials without modifying the shared .env
    file (which may be a symlink to shared secrets on NERSC).

    Use --list-models to skip the setup and only display available models.

Examples:
    \`\`\`bash
    # Use default .env.local file and sophia cluster
    $0

    # Specify custom env file
    $0 --env .env.custom

    # Use metis cluster
    $0 --cluster metis

    # Use both options
    $0 --env .env.custom --cluster metis

    # Use sophia cluster explicitly
    $0 --cluster sophia

    # List available models without updating env file
    $0 --list-models

    # List models for metis cluster
    $0 --list-models --cluster metis
    \`\`\`
EOF
}

# Initialize defaults
ENV_FILE=".env.local"
CLUSTER="sophia"
LIST_MODELS=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)      [[ -z "$2" ]] && { echo "Error: --env requires a value"; show_help; exit 1; }
                    ENV_FILE="$2"; shift 2 ;;
        --cluster)  [[ -z "$2" ]] && { echo "Error: --cluster requires a value"; show_help; exit 1; }
                    CLUSTER="$2"; shift 2 ;;
        --list-models) LIST_MODELS=true; shift ;;
        -h|--help) show_help; exit 0 ;;
        *)          echo "Error: Unknown option '$1'"; show_help; exit 1 ;;
    esac
done

# Validate cluster argument
if [[ "$CLUSTER" != "sophia" ]] && [[ "$CLUSTER" != "metis" ]]; then
    echo "Error: Invalid cluster '$CLUSTER'. Must be 'sophia' or 'metis'."
    echo ""
    show_help
    exit 1
fi

# Only do setup if not in list-only mode
if [ "$LIST_MODELS" = false ]; then
    # Lets make sure we have the dependencies installed
    # mainly adds globus support
    pip install -e ".[alcf]"

    # get the latest script to authenticate with alcf
    curl -O https://raw.githubusercontent.com/argonne-lcf/inference-endpoints/refs/heads/main/inference_auth_token.py

    # authenticate with alcf
    #python inference_auth_token.py authenticate

    if ! TOKEN=$(python inference_auth_token.py get_access_token 2>&1); then
        python inference_auth_token.py authenticate || exit 1
        TOKEN=$(python inference_auth_token.py get_access_token) || exit 1
    fi

    # Set base URL based on cluster
    if [ "$CLUSTER" == "metis" ]; then
        BASE_URL="https://inference-api.alcf.anl.gov/resource_server/metis/api/v1"
    else
        BASE_URL="https://inference-api.alcf.anl.gov/resource_server/sophia/vllm/v1"
    fi

    # Create .env.local if it doesn't exist
    if [ ! -f "$ENV_FILE" ]; then
        echo "Creating $ENV_FILE for user-specific settings..."
        touch "$ENV_FILE"
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
else
    # In list-only mode, we still need the token and auth script
    # Check if inference_auth_token.py exists, if not download it
    if [ ! -f "inference_auth_token.py" ]; then
        curl -O https://raw.githubusercontent.com/argonne-lcf/inference-endpoints/refs/heads/main/inference_auth_token.py
    fi
    
    TOKEN=$(python inference_auth_token.py get_access_token)
fi





################################# List available models #################################
# Fetch and display available models
if [ "$LIST_MODELS" = false ]; then
    echo "Please verify OPENAI_MODEL is set in $ENV_FILE"
    echo ""
fi
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