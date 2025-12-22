#!/bin/bash
# scripts/deploy-nersc-build.sh
# Purpose: Builds the container image and migrates it to NERSC storage.

set -e

SERVICE_NAME="kb-mcp"
IMAGE_TAG="latest"

# --- Argument Parsing ---
while [[ $# -gt 0 ]]; do
    case $1 in
        --tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        *)
            # Ignore other arguments (like --globus-group) so this script can be safe to call with generic args
            shift
            ;;
    esac
done

# --- Tag Logic ---
if [ -z "$IMAGE_TAG" ]; then
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
echo " Building Container Image: ${IMAGE_NAME}"
echo "================================================"

# 1. Build (Pure Podman-HPC)
echo "Building Container Image..."
podman-hpc build -t "${IMAGE_NAME}" .

# 2. Migrate
echo "Migrating to Podman-HPC..."
podman-hpc migrate "${IMAGE_NAME}"

echo "Build complete."