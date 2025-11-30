#!/bin/bash

# Google Cloud Run Deployment Script

set -e

# Check arguments
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "Usage: ./scripts/deploy-cloudrun.sh PROJECT_ID [BASE_URL]"
    echo "Example: ./scripts/deploy-cloudrun.sh my-gcp-project"
    echo "Example: ./scripts/deploy-cloudrun.sh my-gcp-project https://mcp.example.com"
    echo ""
    echo "BASE_URL: Optional custom domain. If not provided, uses auto-generated Cloud Run URL."
    exit 1
fi

# Configuration
PROJECT_ID="$1"
CUSTOM_BASE_URL="${2:-}"  # Optional second argument
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
echo "Bucket: ${BUCKET_NAME}"
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

# Deploy to Cloud Run (without BASE_URL initially)
echo "Deploying to Cloud Run..."
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
    --set-env-vars="USE_HTTPS=false,HOST=0.0.0.0,GITHUB_REQUIRED_REPO=corrodis/test-mcp" \
    --set-secrets="GITHUB_CLIENT_ID=github-client-id:latest,GITHUB_CLIENT_SECRET=github-client-secret:latest"

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
