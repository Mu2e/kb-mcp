#!/bin/bash
# NERSC Deployment Script (Podman-HPC)

set -e

# --- Configuration ---
# Defaults
SERVICE_NAME="kb-mcp"
PROJECT_ID="m5115"
IMAGE_TAG=""
PORT=8443
USE_HTTPS=true
REBUILD=false

# Paths
REPO_ROOT=$(pwd)
SECRETS_DIR="/global/cfs/cdirs/${PROJECT_ID}/secrets"
ENV_FILE="${REPO_ROOT}/.env"
DATA_HOST_DIR="/global/cfs/cdirs/${PROJECT_ID}/${USER}/${SERVICE_NAME}-data"

# --- Argument Parsing ---
while [[ $# -gt 0 ]]; do
    case $1 in
        --tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        --rebuild)
            REBUILD=true
            shift
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
        *)
            echo "Unknown argument: $1"
            echo "Usage: ./deploy-nersc.sh [--tag TAG] [--rebuild] [--github-repo OWNER/REPO | --globus-group UUID] [--no-https]"
            exit 1
            ;;
    esac
done

if [ -z "$IMAGE_TAG" ]; then
    # Use latest git commit hash as tag if available, otherwise fallback to "latest"
    if git rev-parse --git-dir > /dev/null 2>&1; then
        IMAGE_TAG="$(git rev-parse --short HEAD)"
        echo "No image tag specified. Using latest git commit hash: $IMAGE_TAG"
    else
        IMAGE_TAG="latest"
        echo "No image tag specified and not in a git repo. Using tag: $IMAGE_TAG"
    fi
fi

IMAGE_NAME="${SERVICE_NAME}:${IMAGE_TAG}"
echo "================================================"
echo "Image:   ${IMAGE_NAME}"
echo "Port:    ${PORT}"
echo "HTTPS:   ${USE_HTTPS}"
echo "Secrets: ${SECRETS_DIR}"

# Check for .env (Database credentials)
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found."
    echo "   Please run 'source scripts/nersc_setup_db.sh' first to generate it."
    exit 1
fi

# --- 2. Certificate Management (Auto-Generate) ---
if [ "$USE_HTTPS" = true ]; then
    CERT_FILE="${SECRETS_DIR}/cert.pem"
    KEY_FILE="${SECRETS_DIR}/key.pem"

    echo "Checking certificates..."
    
    # Ensure secrets dir exists
    mkdir -p "$SECRETS_DIR"
    
    if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
        echo "   Generating self-signed certificates for 'localhost'..."
        openssl req -x509 -newkey rsa:4096 \
            -keyout "$KEY_FILE" \
            -out "$CERT_FILE" \
            -days 365 -nodes \
            -subj "/CN=localhost" 2>/dev/null
        
        # Lock them down
        chmod 640 "$CERT_FILE" "$KEY_FILE"
        echo "   Certificates generated at $SECRETS_DIR"
    else
        echo "   Using existing certificates."
    fi
fi

# Only rebuild if requested or image missing
if [ "$REBUILD" = true ] || ! podman-hpc images | grep -q "${IMAGE_NAME}"; then
    echo "Building Container Image '${IMAGE_NAME}'..."
    
    podman-hpc build -t "${IMAGE_NAME}" .
    
    echo "Migrating to Podman-HPC..."
    podman-hpc migrate "${IMAGE_NAME}"
    
    echo "Build complete."
else
    echo "Skipping build (use --rebuild to force)."
fi

echo "Deploying Service..."

# Stop existing container if running
podman-hpc rm -f "${SERVICE_NAME}" >/dev/null 2>&1 || true

# Construct Environment Variables
# Note that we load the .env file to set the environment variables. These OVERRIDE the values in .env.
ENV_VARS="--env-file ${ENV_FILE}"
ENV_VARS="${ENV_VARS} -e HOST=0.0.0.0"
ENV_VARS="${ENV_VARS} -e PORT=${PORT}"
ENV_VARS="${ENV_VARS} -e USE_HTTPS=${USE_HTTPS}"

if [ -n "$GITHUB_REQUIRED_REPO" ]; then
    ENV_VARS="${ENV_VARS} -e GITHUB_REQUIRED_REPO=${GITHUB_REQUIRED_REPO}"
fi
if [ -n "$GLOBUS_REQUIRED_GROUP" ]; then
    ENV_VARS="${ENV_VARS} -e GLOBUS_REQUIRED_GROUP=${GLOBUS_REQUIRED_GROUP}"
fi

# Check for OAuth Secrets in the .env file
# We grep the file to make sure the user added them to the shared secrets
MISSING_SECRETS=false
if [ -n "$GITHUB_REQUIRED_REPO" ]; then
    if ! grep -q "GITHUB_CLIENT_ID" "$ENV_FILE"; then
        echo "Missing GITHUB_CLIENT_ID in $ENV_FILE"
        MISSING_SECRETS=true
    fi
    if ! grep -q "GITHUB_CLIENT_SECRET" "$ENV_FILE"; then
        echo "Missing GITHUB_CLIENT_SECRET in $ENV_FILE"
        MISSING_SECRETS=true
    fi
fi

if [ -n "$GLOBUS_REQUIRED_GROUP" ]; then
    if ! grep -q "GLOBUS_CLIENT_ID" "$ENV_FILE"; then
        echo "Missing GLOBUS_CLIENT_ID in $ENV_FILE"
        MISSING_SECRETS=true
    fi
    if ! grep -q "GLOBUS_CLIENT_SECRET" "$ENV_FILE"; then
        echo "Missing GLOBUS_CLIENT_SECRET in $ENV_FILE"
        MISSING_SECRETS=true
    fi
fi

if [ "$MISSING_SECRETS" = true ]; then
    echo ""
    echo "    Please add these keys to your shared secrets file:"
    echo "   $SECRETS_DIR/kb-mcp.env"
    echo "   Then run 'source scripts/nersc_setup_db.sh' to refresh your .env"
    exit 1
fi

# Construct Volume Mounts
VOLUMES="-v ${DATA_HOST_DIR}:/app/data"
# Mount Certificates if HTTPS is on
if [ "$USE_HTTPS" = true ]; then
    VOLUMES="${VOLUMES} -v ${SECRETS_DIR}/cert.pem:/app/certs/cert.pem"
    VOLUMES="${VOLUMES} -v ${SECRETS_DIR}/key.pem:/app/certs/key.pem"
fi

# Run Command
# --net=host : Critical for talking to the DB on localhost
# --userns=keep-id : Critical for writing files to scratch
podman-hpc run -d --rm \
    --name "${SERVICE_NAME}" \
    --net=host \
    --userns=keep-id \
    ${ENV_VARS} \
    ${VOLUMES} \
    "${IMAGE_NAME}"

echo "================================================"
echo "Deployment complete!"
echo "Service is running on $(hostname):${PORT}"
echo ""
echo "To access from your laptop:"
echo "1. Run this SSH tunnel on your machine:"
echo "   ssh -J $USER@perlmutter.nersc.gov -L 8443:localhost:8443 ${USER}@$(hostname)"
echo ""
echo "2. Open in browser:"
if [ "$USE_HTTPS" = true ]; then
    echo "   https://localhost:${PORT}"
else
    echo "   http://localhost:${PORT}"
fi
echo "================================================"