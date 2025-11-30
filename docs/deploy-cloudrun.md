# Google Cloud Run Deployment

Deploy your MCP server to Google Cloud Run - a fully managed serverless platform that automatically scales to zero when not in use, keeping costs minimal.

## Prerequisites

- Google Cloud account with billing enabled
- gcloud CLI installed and authenticated
- Project created in Google Cloud Console

## Setup

1. **Create Cloud Storage bucket** for persistent data (API keys):

```bash
# Create bucket (replace YOUR_PROJECT_ID with your project ID)
gsutil mb gs://YOUR_PROJECT_ID-mcp-data
```

Use the following to get your service account
```bash
gcloud iam service-accounts list
``` 
and then grant it permision:
```bash
gsutil iam ch serviceAccount:XXXXXXXXXXXXX-compute@developer.gserviceaccount.com:roles/storage.objectAdmin gs://YOUR_PROJECT_ID-mcp-data
```

2. **Store GitHub OAuth secrets** in Secret Manager:

```bash
# Enable Secret Manager API
gcloud services enable secretmanager.googleapis.com

# Create secrets
echo -n "YOUR_GITHUB_CLIENT_ID" | gcloud secrets create github-client-id --data-file=-
echo -n "YOUR_GITHUB_CLIENT_SECRET" | gcloud secrets create github-client-secret --data-file=-

# Grant Cloud Run access to secrets (replace YOUR_PROJECT_ID)
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format="value(projectNumber)")
gcloud secrets add-iam-policy-binding github-client-id \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
gcloud secrets add-iam-policy-binding github-client-secret \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

## Deploy

Without custom domain (uses auto-generated Cloud Run URL):
```bash
./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID
```

With custom domain (if you've already set up domain mapping):
```bash
./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID https://mcp.example.com
```

This will:
1. Build the Docker image (in the cloud)
2. Push to Google Container Registry
3. Deploy to Cloud Run with secrets
4. Auto-configure BASE_URL (uses custom domain if provided, otherwise auto-generated URL)
5. Output the service URL

**Note**: To enable GitHub repository-based access control, edit `scripts/deploy-cloudrun.sh` and set `GITHUB_REQUIRED_REPO` in the `--set-env-vars` line.

## Custom Domain (Optional)

To use a custom domain instead of the auto-generated Cloud Run URL:

1. **Deploy first** without custom domain:
   ```bash
   ./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID
   ```

2. **Map custom domain** to the deployed service:
   ```bash
   gcloud beta run domain-mappings create \
     --service test-mcp \
     --domain mcp.example.com \
     --region us-central1
   ```

3. **Add DNS records** shown in the output to your domain registrar:
   ```
   Type: CNAME
   Name: mcp
   Value: ghs.googlehosted.com
   ```

   DNS propagation takes 5-60 minutes.

4. **Redeploy with custom domain** to update BASE_URL:
   ```bash
   ./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID https://mcp.example.com
   ```

## Finish Setup

Update your GitHub OAuth App callback URL ([at github.com](https://github.com/settings/developers)) to match your deployment URL:
```
https://YOUR-SERVICE-URL/oauth/github/callback
```

Or if using custom domain (in this example: `mcp.scorrodi.dev`):
```
https://mcp.scorrodi.dev/oauth/github/callback
```

**Note**: This single callback URL handles both MCP OAuth (for Claude Desktop, Cline) and admin web interface login. The server automatically routes based on the OAuth state parameter.
