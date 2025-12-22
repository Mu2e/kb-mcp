#!/bin/bash

# Google Cloud Run Deployment Script

set -e

# Parse arguments
USE_FIRESTORE=false
CUSTOM_BASE_URL=""
SERVICE_NAME="kb-mcp"
GITHUB_REQUIRED_REPO=""
GLOBUS_REQUIRED_GROUP=""
OAUTH_PROVIDER=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --firestore)
            USE_FIRESTORE=true
            shift
            ;;
        --service-name)
            SERVICE_NAME="$2"
            shift 2
            ;;
        --github-repo)
            if [ "$2" = "" ] || [ "$2" = "''" ] || [ "$2" = '""' ]; then
                GITHUB_REQUIRED_REPO=""
            else
                GITHUB_REQUIRED_REPO="$2"
                OAUTH_PROVIDER="github"
            fi
            shift 2
            ;;
        --globus-group)
            if [ "$2" = "" ] || [ "$2" = "''" ] || [ "$2" = '""' ]; then
                GLOBUS_REQUIRED_GROUP=""
            else
                GLOBUS_REQUIRED_GROUP="$2"
                OAUTH_PROVIDER="globus"
            fi
            shift 2
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
    echo "Usage: ./scripts/deploy-cloudrun.sh PROJECT_ID [BASE_URL] [--service-name SERVICE_NAME] [--github-repo OWNER/REPO | --globus-group GROUP_UUID] [--firestore]"
    echo ""
    echo "Examples:"
    echo "  ./scripts/deploy-cloudrun.sh my-gcp-project"
    echo "  ./scripts/deploy-cloudrun.sh my-gcp-project https://lsd.example.com"
    echo "  ./scripts/deploy-cloudrun.sh my-gcp-project --github-repo myorg/private-repo"
    echo "  ./scripts/deploy-cloudrun.sh my-gcp-project --globus-group 6207971f-dd5b-11f0-945c-0e587dcf26df"
    echo "  ./scripts/deploy-cloudrun.sh my-gcp-project https://lsd.example.com --firestore"
    echo ""
    echo "Arguments:"
    echo "  PROJECT_ID: Your Google Cloud project ID (required)"
    echo "  BASE_URL:   Optional custom domain. If not provided, uses auto-generated Cloud Run URL."
    echo "  --service-name: Cloud Run service name (default: kb-mcp)"
    echo "  --github-repo: Use GitHub OAuth and restrict access to users with access to this repo (format: owner/repo)."
    echo "                 Use --github-repo \"\" to allow all authenticated GitHub users."
    echo "  --globus-group: Use Globus OAuth and restrict access to users in this Globus group (UUID)."
    echo "                  Use --globus-group \"\" to allow all authenticated Globus users."
    echo "  --firestore: Use Firestore instead of file-based storage"
    echo ""
    echo "Note: OAuth is optional. If neither --github-repo nor --globus-group is provided,"
    echo "      only API key authentication will be available (no web interface)."
    exit 1
fi

# Validate OAuth provider selection
if [ -n "$GITHUB_REQUIRED_REPO" ] && [ -n "$GLOBUS_REQUIRED_GROUP" ]; then
    echo "Error: Cannot specify both --github-repo and --globus-group."
    echo "       Only one OAuth provider can be configured at a time."
    exit 1
fi
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
if [ "$OAUTH_PROVIDER" = "github" ]; then
    echo "OAuth: GitHub"
    if [ -n "$GITHUB_REQUIRED_REPO" ]; then
        echo "  Required repo: ${GITHUB_REQUIRED_REPO}"
    else
        echo "  All authenticated users allowed"
    fi
elif [ "$OAUTH_PROVIDER" = "globus" ]; then
    echo "OAuth: Globus"
    if [ -n "$GLOBUS_REQUIRED_GROUP" ]; then
        echo "  Required group: ${GLOBUS_REQUIRED_GROUP}"
    else
        echo "  All authenticated users allowed"
    fi
else
    echo "OAuth: None (API keys only)"
fi
echo "================================================"

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "Error: gcloud CLI is not installed"
    echo "Install from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Check for required secrets
echo "Checking for required secrets..."
REQUIRED_SECRETS=("db-host" "db-user" "db-password")
MISSING_SECRETS=()

# Check database secrets (always required)
for secret in "${REQUIRED_SECRETS[@]}"; do
    if ! gcloud secrets describe ${secret} --project=${PROJECT_ID} &>/dev/null; then
        MISSING_SECRETS+=("${secret}")
    fi
done

# Check OAuth secrets based on provider
if [ "$OAUTH_PROVIDER" = "github" ]; then
    if ! gcloud secrets describe github-client-id --project=${PROJECT_ID} &>/dev/null; then
        MISSING_SECRETS+=("github-client-id")
    fi
    if ! gcloud secrets describe github-client-secret --project=${PROJECT_ID} &>/dev/null; then
        MISSING_SECRETS+=("github-client-secret")
    fi
elif [ "$OAUTH_PROVIDER" = "globus" ]; then
    if ! gcloud secrets describe globus-client-id --project=${PROJECT_ID} &>/dev/null; then
        MISSING_SECRETS+=("globus-client-id")
    fi
    if ! gcloud secrets describe globus-client-secret --project=${PROJECT_ID} &>/dev/null; then
        MISSING_SECRETS+=("globus-client-secret")
    fi
fi

if [ ${#MISSING_SECRETS[@]} -ne 0 ]; then
    echo "Error: Missing required secrets:"
    for secret in "${MISSING_SECRETS[@]}"; do
        echo "  - ${secret}"
    done
    echo ""
    echo "Please create these secrets in Secret Manager before deploying."
    echo "See deployment.md for instructions."
    exit 1
fi

echo "All required secrets found."

# Build and push the container image
echo "Building container image..."
gcloud builds submit --tag ${IMAGE_NAME}

# Set environment variables
if [ "$USE_FIRESTORE" = true ]; then
    SESSION_STORE="SESSION_STORE_FIRESTORE=true"
else
    SESSION_STORE="SESSION_STORE_FIRESTORE=false"
fi

# Build environment variables string
ENV_VARS="USE_HTTPS=false,HOST=0.0.0.0,${SESSION_STORE},DB_PORT=5432"
if [ -n "$GITHUB_REQUIRED_REPO" ]; then
    ENV_VARS="${ENV_VARS},GITHUB_REQUIRED_REPO=${GITHUB_REQUIRED_REPO}"
fi
if [ -n "$GLOBUS_REQUIRED_GROUP" ]; then
    ENV_VARS="${ENV_VARS},GLOBUS_REQUIRED_GROUP=${GLOBUS_REQUIRED_GROUP}"
fi

# Build secrets list (database secrets are always required)
SECRETS_LIST="DB_HOST=db-host:latest,DB_USER=db-user:latest,DB_PASSWORD=db-password:latest"

# Add OAuth secrets based on provider
if [ "$OAUTH_PROVIDER" = "github" ]; then
    SECRETS_LIST="${SECRETS_LIST},GITHUB_CLIENT_ID=github-client-id:latest,GITHUB_CLIENT_SECRET=github-client-secret:latest"
elif [ "$OAUTH_PROVIDER" = "globus" ]; then
    SECRETS_LIST="${SECRETS_LIST},GLOBUS_CLIENT_ID=globus-client-id:latest,GLOBUS_CLIENT_SECRET=globus-client-secret:latest"
fi

# Check if optional database secrets exist and add them
if gcloud secrets describe db-name --project=${PROJECT_ID} &>/dev/null; then
    SECRETS_LIST="${SECRETS_LIST},DB_NAME=db-name:latest"
    echo "Found optional secret: db-name"
fi
if gcloud secrets describe db-schema --project=${PROJECT_ID} &>/dev/null; then
    SECRETS_LIST="${SECRETS_LIST},DB_SCHEMA=db-schema:latest"
    echo "Found optional secret: db-schema"
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
        --set-env-vars="${ENV_VARS}" \
        --set-secrets="${SECRETS_LIST}"
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
        --set-env-vars="${ENV_VARS}" \
        --set-secrets="${SECRETS_LIST}"
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
if [ "$OAUTH_PROVIDER" = "github" ]; then
    echo "Update your GitHub OAuth App callback URL to:"
    echo "  ${BASE_URL}/oauth/callback"
    echo ""
    echo "GitHub Developer Settings: https://github.com/settings/developers"
elif [ "$OAUTH_PROVIDER" = "globus" ]; then
    echo "Update your Globus OAuth App callback URL to:"
    echo "  ${BASE_URL}/oauth/callback"
    echo ""
    echo "Globus Developer Console: https://developers.globus.org/"
else
    echo "No OAuth configured. Only API key authentication is available."
    echo "To enable OAuth, redeploy with --github-repo or --globus-group."
fi
