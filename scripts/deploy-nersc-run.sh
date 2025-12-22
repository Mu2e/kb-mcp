#!/bin/bash
# scripts/deploy-nersc-run.sh
# Purpose: Configures certificates/secrets and runs the container.

set -e

# --- Configuration ---
SERVICE_NAME="kb-mcp"
PROJECT_ID="m5115"
IMAGE_TAG="latest"
PORT=8443
USE_HTTPS=true
LOCAL_MODE=false

# Paths
REPO_ROOT=$(pwd)
SECRETS_DIR="/global/cfs/cdirs/${PROJECT_ID}/secrets"
ENV_FILE="${REPO_ROOT}/.env"
DATA_HOST_DIR="/global/cfs/cdirs/${PROJECT_ID}/${USER}/${SERVICE_NAME}-data"

# --- Argument Parsing ---
while [[ $# -gt 0 ]]; do
    case $1 in
        --env-file)
            ENV_FILE="$2"
            shift 2
            ;;
        --tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        --github-repo)
            GITHUB_REQUIRED_REPO="$2"
            shift 2
            ;;
        --globus-group)
            GLOBUS_REQUIRED_GROUP="$2"
            shift 2
            ;;
        --no-https)
            USE_HTTPS=false
            shift
            ;;
        --local)
            LOCAL_MODE=true
            shift
            ;;
        *)
            shift
            ;;
    esac
done

# --- Image Resolution ---
if [ -z "$IMAGE_TAG" ]; then
    if git rev-parse --git-dir > /dev/null 2>&1; then
        IMAGE_TAG="$(git rev-parse --short HEAD)"
    else
        IMAGE_TAG="latest"
    fi
fi
IMAGE_NAME="${SERVICE_NAME}:${IMAGE_TAG}"

echo "================================================"
echo "Deploying Service: ${IMAGE_NAME}"
echo "================================================"
echo "Port:    ${PORT}"
echo "HTTPS:   ${USE_HTTPS}"
echo "Local:   ${LOCAL_MODE} (bind to localhost, disable auth)"
echo "Secrets: ${SECRETS_DIR}"
if [ "${GITHUB_REQUIRED_REPO}" != "" ]; then
    echo "GitHub Repo: ${GITHUB_REQUIRED_REPO}"
fi
if [ "${GLOBUS_REQUIRED_GROUP}" != "" ]; then
    echo "Globus Group: ${GLOBUS_REQUIRED_GROUP}"
fi

# Check for .env (Database credentials)
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found."
    echo "   Either set the --env-file argument or run 'source scripts/nersc_setup_db.sh' first to generate it."
    exit 1
fi

# --- Certificate Management ---
# Create Data Directory
mkdir -p "$DATA_HOST_DIR"

# Construct Volume Mounts
VOLUMES="-v ${DATA_HOST_DIR}:/app/data"

if [ "$USE_HTTPS" = true ]; then
    CERT_FILE="${SECRETS_DIR}/cert.pem"
    KEY_FILE="${SECRETS_DIR}/key.pem"

    # Ensure secrets dir exists
    mkdir -p "$SECRETS_DIR"
    
    if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
        echo "   Generating self-signed certificates..."
        openssl req -x509 -newkey rsa:4096 \
            -keyout "$KEY_FILE" \
            -out "$CERT_FILE" \
            -days 365 -nodes \
            -subj "/CN=localhost" 2>/dev/null
        chmod 640 "$CERT_FILE" "$KEY_FILE"
    fi
    # Add Certs to Volumes
    VOLUMES="${VOLUMES} -v ${CERT_FILE}:/app/certs/cert.pem -v ${KEY_FILE}:/app/certs/key.pem"
fi

# --- Secrets Validation ---
# Skip OAuth validation if local mode (auth disabled)
if [ "$LOCAL_MODE" = false ]; then
    MISSING_SECRETS=false
    if [ -n "$GITHUB_REQUIRED_REPO" ]; then
        if ! grep -q "GITHUB_CLIENT_ID" "$ENV_FILE"; then echo "Missing GITHUB_CLIENT_ID"; MISSING_SECRETS=true; fi
        if ! grep -q "GITHUB_CLIENT_SECRET" "$ENV_FILE"; then echo "Missing GITHUB_CLIENT_SECRET"; MISSING_SECRETS=true; fi
    fi
    if [ -n "$GLOBUS_REQUIRED_GROUP" ]; then
        if ! grep -q "GLOBUS_CLIENT_ID" "$ENV_FILE"; then echo "Missing GLOBUS_CLIENT_ID"; MISSING_SECRETS=true; fi
        if ! grep -q "GLOBUS_CLIENT_SECRET" "$ENV_FILE"; then echo "Missing GLOBUS_CLIENT_SECRET"; MISSING_SECRETS=true; fi
    fi

    if [ "$MISSING_SECRETS" = true ]; then
        echo ""
        echo "Error: Missing OAuth keys in $ENV_FILE"
        echo "Please add them to $SECRETS_DIR/kb-mcp.env and run 'source scripts/nersc_setup_db.sh'"
        exit 1
    fi
fi

# --- Execution ---
echo "Starting Container..."

# Stop existing container
podman-hpc rm -f "${SERVICE_NAME}" >/dev/null 2>&1 || true

# Construct Environment Variables
ENV_VARS="--env-file ${ENV_FILE}"
if [ "$LOCAL_MODE" = true ]; then
    ENV_VARS="${ENV_VARS} -e HOST=127.0.0.1"
    ENV_VARS="${ENV_VARS} -e DISABLE_AUTH=true"
else
    ENV_VARS="${ENV_VARS} -e HOST=0.0.0.0"
fi
ENV_VARS="${ENV_VARS} -e PORT=${PORT}"
if [ "$USE_HTTPS" = true ]; then
    ENV_VARS="${ENV_VARS} -e BASE_URL=https://localhost:${PORT}"
else
    ENV_VARS="${ENV_VARS} -e BASE_URL=http://localhost:${PORT}"
fi
ENV_VARS="${ENV_VARS} -e USE_HTTPS=${USE_HTTPS}"

if [ -n "$GITHUB_REQUIRED_REPO" ]; then
    ENV_VARS="${ENV_VARS} -e GITHUB_REQUIRED_REPO=${GITHUB_REQUIRED_REPO}"
fi
if [ -n "$GLOBUS_REQUIRED_GROUP" ]; then
    ENV_VARS="${ENV_VARS} -e GLOBUS_REQUIRED_GROUP=${GLOBUS_REQUIRED_GROUP}"
fi

# Run Command
podman-hpc run \
    --name "${SERVICE_NAME}" \
    --net=host \
    --userns=keep-id \
    ${ENV_VARS} \
    ${VOLUMES} \
    "${IMAGE_NAME}"

echo "================================================"
echo "Deployment complete!"
if [ "$LOCAL_MODE" = true ]; then
    echo "Service is running on localhost:${PORT} (local mode, auth disabled)"
else
    echo "Service is running on $(hostname):${PORT}"
fi
echo ""
echo "To access from your laptop:"
echo "1. Run this SSH tunnel on your machine:"
echo "   ssh -J $USER@perlmutter.nersc.gov -L 8443:localhost:8443 ${USER}@$(hostname -f)"
echo ""
echo "2. Open in browser:"
if [ "$USE_HTTPS" = true ]; then
    echo "   https://localhost:${PORT}"
else
    echo "   http://localhost:${PORT}"
fi
if [ "$LOCAL_MODE" = true ]; then
    echo ""
    echo "Note: Authentication is disabled (local mode)."
fi
echo "================================================"