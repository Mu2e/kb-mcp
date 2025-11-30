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

**File-based storage (default):**
```bash
./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID
```

**With custom domain:**
```bash
./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID https://mcp.example.com
```

**With Firestore:**
```bash
./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID [BASE_URL] --firestore
```

This will:
1. Build the Docker image (in the cloud)
2. Push to Google Container Registry
3. Deploy to Cloud Run with secrets
4. Auto-configure BASE_URL (uses custom domain if provided, otherwise auto-generated URL)
5. Output the service URL

**Note**: 
- To enable GitHub repository-based access control, edit `scripts/deploy-cloudrun.sh` and set `GITHUB_REQUIRED_REPO` in the `--set-env-vars` line.
- The deployment uses file-based storage with Cloud Storage mount by default (`SESSION_STORE_FIRESTORE=false`). To use Firestore instead, change  to `SESSION_STORE_FIRESTORE=true`.

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

## Storage Options

### File-based Storage (Default)

The deployment uses file-based storage with Cloud Storage mount by default. Sessions and API keys are stored in JSON files on the mounted Cloud Storage volume at `./data`.

**Configuration:**
- `SESSION_STORE_FIRESTORE=false`

### Firestore (Optional)

To use Firestore instead of file-based storage (better for scaling to multiple instances):

1. **Enable Firestore API:**
   ```bash
   gcloud services enable firestore.googleapis.com
   ```

2. **Create Firestore database** (if not already created):
   ```bash
   gcloud firestore databases create --location=us-central1
   ```

3. **Grant Firestore permissions to Cloud Run service account:**
   ```bash
   # Get your project number
   PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format="value(projectNumber)")
   
   # Grant Firestore permissions
   gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
     --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
     --role="roles/datastore.user"
   ```

4. **Deploy with Firestore enabled:**
   ```bash
   ./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID [BASE_URL] --firestore
   ```

5. **Set up TTL (Time-To-Live) policies for automatic expiration:**

   Firestore can automatically delete expired documents using TTL policies. This is cleaner than manual cleanup.

   **In Firestore Console:**
   1. Go to [Firestore Console](https://console.cloud.google.com/firestore)
   2. Select your database
   3. Click on a collection (e.g., `oauth_sessions` or `web_sessions`)
   4. Click "Indexes" tab → "TTL Policies" tab
   5. Click "Create TTL Policy"
   6. Configure:
      - **Collection ID**: `oauth_sessions` (or `web_sessions`)
      - **TTL field**: `expires_at`
      - **TTL field type**: `Timestamp`
   7. Click "Create"

   **Repeat for both collections:**
   - `oauth_sessions` - expires documents with `expires_at` field (auth codes, access tokens, sessions)
   - `web_sessions` - expires documents with `expires_at` field (web browser sessions)

   **Note:** TTL policies take effect within 24-48 hours after creation. Documents are deleted when their `expires_at` timestamp is in the past.
   
   **Orphaned Documents:** When `auth_codes` or `access_tokens` expire and are deleted by TTL, related `github_tokens` and `token_users` documents become orphaned. This is harmless (they won't be accessed), but if you want to clean them up, you can create a Cloud Function that periodically queries for orphaned entries. For disk storage, cleanup automatically removes orphaned entries.

**Note:** No Docker changes needed! Firestore uses Application Default Credentials (ADC) which works automatically in Cloud Run. The `google-cloud-firestore` package is already included in the Docker image.
