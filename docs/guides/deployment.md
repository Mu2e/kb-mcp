## Docker Deployment

Container runs HTTP (no HTTPS) - meant for reverse proxy or Cloud Run deployment. For HTTPS in Docker, see comments in Dockerfile to enable it. 

### Build

```bash
docker build -t kb-mcp .
```

### Run

**With PostgreSQL database/No web authentification:**

```bash
docker run -d -p 8443:8443 \     
  -e DISABLE_WEB_AUTH=true \
  -e DB_HOST=ep-patient-butterfly-aenc585m-pooler.c-2.us-east-2.aws.neon.tech \
  -e DB_HOST=your_db_host \
  -e DB_USER=your_db_user \
  -e DB_PASSWORD=your_db_password \
  -e DB_NAME=kb_mcp \
  -e DB_PORT=5432 \
  -e DB_SCHEMA=public \
  -v kb-mcp-data:/app/data \
  --name kb-mcp \
  kb-mcp
```

**With SQLite (development only)/with github authentification:**

```bash
docker run -d -p 8443:8443 \
  -e GITHUB_CLIENT_ID=your_client_id \
  -e GITHUB_CLIENT_SECRET=your_client_secret \
  -e GITHUB_REQUIRED_REPO=owner/repo \
  -e SQLITE_DB_PATH=/app/data/kb.db \
  -v kb-mcp-data:/app/data \
  --name kb-mcp \
  kb-mcp
```

**Notes:**

- GitHub OAuth environment variables only needed if using OAuth authentication. For API key only deployments, these can be omitted.
- Database environment variables:
  - **Required for PostgreSQL**: `DB_HOST`, `DB_USER`, `DB_PASSWORD`
  - **Optional for PostgreSQL**: `DB_NAME` (default: `kb_mcp`), `DB_PORT` (default: `5432`), `DB_SCHEMA` (default: `public`)
  - **For SQLite**: Use `SQLITE_DB_PATH` instead (default: `data/kb.db`). Use a volume mount to persist data.
  - **Important**: `DB_HOST` is required for PostgreSQL to prevent accidentally connecting to localhost in containerized deployments.

### Test

Point your browser to [http://localhost:8443](http://localhost:8443)
```bash
curl http://localhost:8443/status
```

### Stop

```bash
docker stop kb-mcp
docker rm kb-mcp
```


## Google Cloud Run Deployment

Deploy kb-mcp server to Google Cloud Run.

### Prerequisites

- Google Cloud account with billing enabled
- gcloud CLI installed and authenticated
- Project created in Google Cloud Console

### Postgresql
So far I tested this with postgresql on [neon.com](https://neon.com/)


### Setup

0. **Set/Check Active Project**

```bash
gcloud config set project <YOUR_PROJECT_ID>
gcloud config get-value project
```


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

2. **Store secrets in Secret Manager**:

```bash
# Enable Secret Manager API
gcloud services enable secretmanager.googleapis.com

# Create GitHub OAuth secrets
echo -n "YOUR_GITHUB_CLIENT_ID" | gcloud secrets create github-client-id --data-file=-
echo -n "YOUR_GITHUB_CLIENT_SECRET" | gcloud secrets create github-client-secret --data-file=-

# Create database secrets (required)
echo -n "YOUR_DB_HOST" | gcloud secrets create db-host --data-file=-
echo -n "YOUR_DB_USER" | gcloud secrets create db-user --data-file=-
echo -n "YOUR_DB_PASSWORD" | gcloud secrets create db-password --data-file=-

# Create optional database secrets (if not using defaults)
echo -n "YOUR_DB_NAME" | gcloud secrets create db-name --data-file=-
echo -n "YOUR_DB_SCHEMA" | gcloud secrets create db-schema --data-file=-

# Grant Cloud Run access to secrets (replace YOUR_PROJECT_ID)
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format="value(projectNumber)")
# Required secrets
for secret in github-client-id github-client-secret db-host db-user db-password; do
    gcloud secrets add-iam-policy-binding ${secret} \
      --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
      --role="roles/secretmanager.secretAccessor"
done
# Optional secrets (if created)
for secret in db-name db-schema; do
    if gcloud secrets describe ${secret} --project=YOUR_PROJECT_ID &>/dev/null; then
        gcloud secrets add-iam-policy-binding ${secret} \
          --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
          --role="roles/secretmanager.secretAccessor"
    fi
done
```

### Deploy

**Basic deployment (default service name `kb-mcp`):**
```bash
./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID
```

**With custom domain:**
```bash
./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID https://sld.example.com
```

**With custom service name:**
```bash
./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID --service-name sld-kb
```

**With custom domain and service name:**
```bash
./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID https://sld.example.com --service-name sld-kb
```

**With GitHub repository restriction:**
```bash
./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID --github-repo owner/repo
```

**With Firestore:**
```bash
./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID [BASE_URL] --firestore
```

**With custom service name and Firestore:**
```bash
./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID [BASE_URL] --service-name sld-kb --firestore
```

**With all options:**
```bash
./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID https://sld.example.com --service-name sld-kb --github-repo owner/repo --firestore
```

This will:

1. Build the Docker image (in the cloud)
2. Push to Google Container Registry
3. Deploy to Cloud Run with secrets
4. Auto-configure BASE_URL (uses custom domain if provided, otherwise auto-generated URL)
5. Output the service URL

**Note**: 

- The default service name is `kb-mcp`. Use `--service-name` to adjust it to your service name.
- By default, access is restricted to users with access to `HEP-KE/kb-mcp`. Use `--github-repo owner/repo` to change this, or `--github-repo ""` to allow all authenticated GitHub users.
- The deployment uses file-based storage with Cloud Storage mount by default (`SESSION_STORE_FIRESTORE=false`). To use Firestore instead, use the `--firestore` flag.

### Custom Domain (Optional)

To use a custom domain instead of the auto-generated Cloud Run URL:

1. **Deploy first** without custom domain:
   ```bash
   ./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID
   ```
   
   Or with a custom service name:
   ```bash
   ./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID --service-name sld-kb
   ```

2. **Map custom domain** to the deployed service:
   ```bash
   gcloud beta run domain-mappings create \
     --service kb-mcp \
     --domain sld.example.com \
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
   ./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID https://sld.example.com
   ```
   
   Or with custom service name:
   ```bash
   ./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID https://sld.example.com --service-name sld-kb
   ```

### Finish Setup

Update your GitHub OAuth App callback URL ([at github.com](https://github.com/settings/developers)) to match your deployment URL:
```
https://YOUR-SERVICE-URL/oauth/github/callback
```

Or if using custom domain (in this example: `sld.scorrodi.dev`):
```
https://mcp.scorrodi.dev/oauth/github/callback
```

**Note**: This single callback URL handles both MCP OAuth (for Claude Desktop, Cline) and admin web interface login. The server automatically routes based on the OAuth state parameter.

## Storage Options

### File-based Storage (Default)

The deployment uses file-based storage with Cloud Storage mount by default. Sessions and API keys are stored in JSON files on the mounted Cloud Storage volume at `./data`.

**Configuration:**
- `SESSION_STORE_FIRESTORE=false`

#### Firestore (Optional)

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
   
   Or with custom service name:
   ```bash
   ./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID [BASE_URL] --service-name sld-kb --firestore
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

## Database Migration

To migrate your knowledge base PostgreSQL database (for example from local to cloud or between remotes).

### Export from Local Database

```bash
pg_dump -h localhost -U kb_user kb_database \
  --no-owner --no-acl \
  -F c \
  -f kb_backup.dump
```

**Options:**
- `--no-owner` - Don't include ownership commands (cloud DBs have different users)
- `--no-acl` - Don't include access control (permissions differ in cloud)
- `-F c` - Custom format (compressed, supports parallel restore)

### Import to Cloud Database

```bash
pg_restore \
  --no-owner --no-acl \
  --clean --if-exists \
  -d "postgresql://user:password@host/database?sslmode=require" \
  kb_backup.dump
```

**Options:**
- `--clean` - Drop existing objects before restoring
- `--if-exists` - Don't error if objects don't exist