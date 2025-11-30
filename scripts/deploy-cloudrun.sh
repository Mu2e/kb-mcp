#!/bin/bash

# Google Cloud Run Deployment Script

set -e

# Parse arguments
USE_FIRESTORE=false
PROJECT_ID=""
CUSTOM_BASE_URL=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --firestore)
            USE_FIRESTORE=true
            shift
            ;;
        *)
            if [ -z "$PROJECT_ID" ]; then
                PROJECT_ID="$1"
            elif [ -z "$CUSTOM_BASE_URL" ]; then
                CUSTOM_BASE_URL="$1"
            fi
            shift
            ;;
    esac
done

# Check required arguments
if [ -z "$PROJECT_ID" ]; then
    echo "Usage: ./scripts/deploy-cloudrun.sh PROJECT_ID [BASE_URL] [--firestore]"
    echo "Example: ./scripts/deploy-cloudrun.sh my-gcp-project"
    echo "Example: ./scripts/deploy-cloudrun.sh my-gcp-project https://mcp.example.com"
    echo "Example: ./scripts/deploy-cloudrun.sh my-gcp-project https://mcp.example.com --firestore"
    echo ""
    echo "Arguments:"
    echo "  PROJECT_ID: Your Google Cloud project ID (required)"
    echo "  BASE_URL:   Optional custom domain. If not provided, uses auto-generated Cloud Run URL."
    echo "  --firestore: Use Firestore instead of file-based storage"
    exit 1
fi
SERVICE_NAME="test-mcp"
REGION="us-central1"
BUCKET_NAME="${PROJECT_ID}-mcp-data"  # Cloud Storage bucket for persistent data
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "================================================"
echo "Deploying to Google Cloud Run"
echo "================================================"
echo "Project: ${PROJECT_ID}"
echo "Service: ${SERVICE_NAME}"
echo "Region: ${REGION}"
echo "Storage: $([ "$USE_FIRESTORE" = true ] && echo "Firestore" || echo "File-based (Cloud Storage)")"
if [ "$USE_FIRESTORE" = false ]; then
    echo "Bucket: ${BUCKET_NAME}"
fi
if [ -n "${CUSTOM_BASE_URL}" ]; then
    echo "Custom BASE_URL: ${CUSTOM_BASE_URL}"
fi
echo "================================================"

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "Error: gcloud CLI is not installed"
    echo "Install from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Build and push the container image
echo "Building container image..."
gcloud builds submit --tag ${IMAGE_NAME}

# Set environment variables
if [ "$USE_FIRESTORE" = true ]; then
    SESSION_STORE="SESSION_STORE_FIRESTORE=true"
else
    SESSION_STORE="SESSION_STORE_FIRESTORE=false"
fi

# Deploy to Cloud Run (without BASE_URL initially)
echo "Deploying to Cloud Run..."
if [ "$USE_FIRESTORE" = true ]; then
    # Deploy with Firestore (no Cloud Storage volume needed)
    gcloud run deploy ${SERVICE_NAME} \
        --image ${IMAGE_NAME} \
        --platform managed \
        --region ${REGION} \
        --allow-unauthenticated \
        --port 8443 \
        --memory 512Mi \
        --cpu 1 \
        --min-instances 0 \
        --max-instances 1 \
        --execution-environment gen2 \
        --set-env-vars="USE_HTTPS=false,HOST=0.0.0.0,GITHUB_REQUIRED_REPO=corrodis/test-mcp,${SESSION_STORE}" \
        --set-secrets="GITHUB_CLIENT_ID=github-client-id:latest,GITHUB_CLIENT_SECRET=github-client-secret:latest"
else
    # Deploy with file-based storage (Cloud Storage volume)
    gcloud run deploy ${SERVICE_NAME} \
        --image ${IMAGE_NAME} \
        --platform managed \
        --region ${REGION} \
        --allow-unauthenticated \
        --port 8443 \
        --memory 512Mi \
        --cpu 1 \
        --min-instances 0 \
        --max-instances 1 \
        --execution-environment gen2 \
        --add-volume name=data,type=cloud-storage,bucket=${BUCKET_NAME} \
        --add-volume-mount volume=data,mount-path=/app/data \
        --set-env-vars="USE_HTTPS=false,HOST=0.0.0.0,GITHUB_REQUIRED_REPO=corrodis/test-mcp,${SESSION_STORE}" \
        --set-secrets="GITHUB_CLIENT_ID=github-client-id:latest,GITHUB_CLIENT_SECRET=github-client-secret:latest"
fi

# Determine BASE_URL to use
if [ -n "${CUSTOM_BASE_URL}" ]; then
    BASE_URL="${CUSTOM_BASE_URL}"
else
    # Get the auto-generated service URL and use it as BASE_URL
    BASE_URL=$(gcloud run services describe ${SERVICE_NAME} --region ${REGION} --format 'value(status.url)')
fi

# Update deployment with BASE_URL
echo "Setting BASE_URL to ${BASE_URL}..."
gcloud run services update ${SERVICE_NAME} \
    --region ${REGION} \
    --update-env-vars="BASE_URL=${BASE_URL}"

echo "================================================"
echo "Deployment complete!"
echo "Service URL: ${BASE_URL}"
echo "================================================"
echo ""
echo "Update your GitHub OAuth App callback URL to:"
echo "  ${BASE_URL}/oauth/github/callback"
