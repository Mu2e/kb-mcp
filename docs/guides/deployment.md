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

**With SQLite (development only)/with OAuth authentication:**

```bash
# GitHub OAuth
docker run -d -p 8443:8443 \
  -e GITHUB_CLIENT_ID=your_client_id \
  -e GITHUB_CLIENT_SECRET=your_client_secret \
  -e GITHUB_REQUIRED_REPO=owner/repo \
  -e SQLITE_DB_PATH=/app/data/kb.db \
  -v kb-mcp-data:/app/data \
  --name kb-mcp \
  kb-mcp

# Or Globus OAuth
docker run -d -p 8443:8443 \
  -e GLOBUS_CLIENT_ID=your_client_id \
  -e GLOBUS_CLIENT_SECRET=your_client_secret \
  -e GLOBUS_REQUIRED_GROUP=group-uuid \
  -e SQLITE_DB_PATH=/app/data/kb.db \
  -v kb-mcp-data:/app/data \
  --name kb-mcp \
  kb-mcp
```

**Notes:**

- OAuth environment variables only needed if using OAuth authentication. For API key only deployments, these can be omitted.
- Configure only one OAuth provider (GitHub or Globus), not both.
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


## NERSC Deployment

Deploy kb-mcp server on NERSC systems using `podman-hpc` containers. This deployment method uses containerized services.

### Build Container Image

Build the container image using `podman-hpc`:

```bash
./scripts/deploy-nersc-build.sh
```

**Options:**
- `--tag <tag>`: Specify a custom image tag (default: uses "latest" or git has if its set to "")

The script will:
1. Build the container image using `podman-hpc build`
2. Migrate the image to NERSC storage using `podman-hpc migrate`

### Run Container

Deploy and run the container (binding to 0.0.0.0 and hence HTTPS enabled). Currently run from the REPO root dir.

```bash
./scripts/deploy-nersc-run.sh
```

**Options:**
- `--env-file <path>`: Path to environment file (default: `.env` in current directory)
- `--tag <tag>`: Image tag to run (default: uses git commit hash or "latest")
- `--github-repo <owner/repo>`: Require GitHub OAuth users to have access to this repository
- `--globus-group <group-uuid>`: Require Globus OAuth users to be in this group
- `--no-https`: Disable HTTPS (use HTTP instead)
- `--local`: Bind to localhost (127.0.0.1) and disable authentication (for development with SSH port forwarding)

**Requirements:**
- The `.env` file must exist and contain database credentials (see [NERSC Setup](nersc.md#database-setup))
- If using OAuth (and not using `--local`), the corresponding OAuth credentials must be in the `.env` file

The script will:
1. Validate environment file and required secrets (OAuth validation skipped in `--local` mode)
2. Generate self-signed certificates (if HTTPS enabled)
3. Create persistent data directory on CFS
4. Start the container with proper volume mounts and environment variables
5. Display SSH port forwarding instructions for accessing from your local machine

**Local Development Mode:**

For local development with SSH port forwarding, use the `--local` flag:

```bash
./scripts/deploy-nersc-run.sh --local
```

This will:
- Bind the server to `127.0.0.1` (localhost only) instead of `0.0.0.0`
- Disable authentication (`DISABLE_AUTH=true`)
- Skip OAuth credentials validation
- Allow access via SSH port forwarding without authentication

**Access from Local Machine:**

After deployment, the script will output SSH port forwarding instructions. Use:

```bash
ssh -J <user-name>@perlmutter.nersc.gov -L 8443:localhost:8443 <user-name>@<login-node-name>.chn.perlmutter.nersc.gov
```

Then access the web interface at `https://localhost:8443` (or `http://localhost:8443` if `--no-https` was used).

**Note:** The container binds to `0.0.0.0` to allow access from other nodes, but you'll need SSH port forwarding to access it from outside NERSC. For development, see [NERSC Setup](nersc.md) for per-user development mode using `nersc_setup.sh`.


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

# Create OAuth secrets (choose GitHub or Globus)
# GitHub: Get credentials from https://github.com/settings/developers
echo -n "YOUR_GITHUB_CLIENT_ID" | gcloud secrets create github-client-id --data-file=-
echo -n "YOUR_GITHUB_CLIENT_SECRET" | gcloud secrets create github-client-secret --data-file=-
# Or Globus: Get credentials from https://developers.globus.org/
echo -n "YOUR_GLOBUS_CLIENT_ID" | gcloud secrets create globus-client-id --data-file=-
echo -n "YOUR_GLOBUS_CLIENT_SECRET" | gcloud secrets create globus-client-secret --data-file=-

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
# Or for Globus:
# for secret in globus-client-id globus-client-secret db-host db-user db-password; do
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

**With OAuth repository/group restriction:**
```bash
# GitHub
./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID --github-repo owner/repo
# Or Globus
./scripts/deploy-cloudrun.sh YOUR_PROJECT_ID --globus-group group-uuid
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
- By default, access is restricted to users with access to `HEP-KE/kb-mcp` (GitHub) or a configured Globus group. Use `--github-repo owner/repo` or `--globus-group group-uuid` to change this, or set to empty string to allow all authenticated users.
- The deployment uses file-based storage with Cloud Storage mount by default (`SESSION_STORE_FIRESTORE=false`). To use Firestore instead, use the `--firestore` flag.
- **Host Header Warning**: If your MCP server connection fails and you see a warning `Invalid Host header: sld.scorrodi.dev` in Cloud Run logs. This is cuased by a new security feature introduced in mcp version 1.23 that doesn't work inside containers (where the server runs on localhost but gets reqeusts from a different URL). Avoid version 1.23 (in our pyroject.toml) since it does **not** allow to switch this feature off. 

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

Update your OAuth App callback URL to match your deployment URL:

- **GitHub**: [GitHub Developer Settings](https://github.com/settings/developers) → `https://YOUR-SERVICE-URL/oauth/callback`
- **Globus**: [Globus Developer Console](https://developers.globus.org/) → `https://YOUR-SERVICE-URL/oauth/callback`

Or if using custom domain (in this example: `sld.scorrodi.dev`):
```
https://mcp.scorrodi.dev/oauth/callback
```

**Note**: This single callback URL handles both MCP OAuth (for Claude Desktop, Cline) and admin web interface login. The server automatically routes based on the OAuth state parameter.

## Mu2e Deployment (systemd --user)

Deploys the MCP endpoint on a Mu2e interactive node as a persistent
`systemd --user` service, following the pattern used by the other Mu2e MCP
servers (see [Mu2e/aitools](https://github.com/Mu2e/aitools) `mcp/registry/README.md`).
Unlike the Docker and Cloud Run deployments above there is no container: the
package is installed straight from a pinned git ref into a versioned venv.

Run everything as the account that will own the service (e.g. `mu2eai`), and
get `uv` first:

```bash
mu2einit && slc uv
```

### Install

```bash
./scripts/deploy-mu2e.sh /exp/mu2e/app/users/mu2eai/mcp/kb v0.2.0
```

This creates `<deploy-root>/releases/<ref>/.venv`, points
`<deploy-root>/current` at it, and installs `kb-mcp` from the pinned ref. No
source tree is copied -- `uv` fetches the ref itself, so a checkout is only
needed for this one script.

CPU-only torch is installed first, on purpose. `sentence-transformers` is a
core dependency (it is the default embedding provider), and its torch
dependency would otherwise resolve to the CUDA build from PyPI -- several GB,
and useless here, since embedding one query at a time is CPU work. Expect
roughly 1.3 GB per release, so prune old releases rather than letting them
accumulate.

Pass `KB_MCP_EXTRAS=ingest` if this host should also run ingestion; a
query-only server does not need the parser stack.

### Configure

Configuration is split across three places, deliberately:

| where | holds | changed by |
|---|---|---|
| `deploy/mu2e.env` (in git, shipped to `<release>/.venv/share/kb-mcp/mu2e.env`) | host-independent policy: ports, bind addresses, auth mode, embedding provider, log levels | commit + new release |
| the systemd unit | deployment paths: `DATA_DIR`, `HF_HOME`, `MIKEY_KEYS_FILE` | re-running `kb-mcp-install-unit.sh` |
| `KB_ENV_FILE` (mode 600, outside git) | secrets **and site topology** -- database host/name/user, LLM endpoint | editing that file |

They load in that order and `kb_mcp.env` reads `KB_ENV_FILE` last with
`override=True`, so a secret always beats a default. Nothing in the running
service reads from a working checkout, where an edit would take effect with no
review gate.

**There is no database password.** The connection authenticates with
Kerberos/GSSAPI from the service account's ambient credentials --
`get_database_url()` builds a password-less URL when `DB_PASSWORD` is unset --
so no database credential exists in any file or environment variable, the same
arrangement the `memory` MCP server uses. If you connect through a tunnel, set
`DB_HOSTADDR` too: libpq derives the GSSAPI principal from `DB_HOST`, not from
the TCP target.

The database coordinates and the LLM endpoint are *not* secrets, but they do
describe internal infrastructure, and this repository is public -- so they live
in this file rather than in `deploy/mu2e.env`. Create it **as the service
account**; a mode-600 file owned by anyone else is one the service cannot
read:

```bash
f=/exp/mu2e/app/users/mu2eai/mcp/kb/config/kb-mcp.env
[ -e "$f" ] || install -D -m 600 /dev/null "$f"
```

`install -D -m 600 /dev/null` creates the parent directories and an empty file
that is mode 600 *from the moment it exists*, rather than `touch` + `chmod`,
which leaves it world-readable under a 022 umask until the chmod lands. The
`[ -e ]` guard matters because `install` truncates an existing file -- without
it, re-running this step wipes the credentials.

Then fill it in -- these keys and no others; everything else has a value in
`deploy/mu2e.env` already:

```bash
# Database. No DB_PASSWORD: Kerberos/GSSAPI supplies the credential.
DB_HOST=
DB_PORT=
DB_NAME=
DB_USER=
DB_SCHEMA=public
# DB_HOSTADDR=          # only when connecting through a tunnel

# LLM endpoint
OPENAI_BASE_URL=
OPENAI_API_KEY=

# Web UI write/admin pages (uploads, delete, re-chunk, key management).
# Browsing and search stay open; the UI is loopback-only regardless.
ADMIN_PASSWORD=
```

`EMBEDDING_MODEL` is set in `deploy/mu2e.env` and **must match what the
indexed chunks were embedded with** -- the query is embedded at read time and
compared in the same vector space, so a mismatch returns plausible-looking
nonsense rather than an error. Check it against the `EmbeddingConfig` rows in
the database before first start.

Check the install before enabling anything. This reports the resolved env
file, the bind target, the embedding provider, and whether the local embedder
is actually installed:

```bash
<deploy-root>/current/.venv/bin/kb-mcp.sh --check \
  --env-file /exp/mu2e/app/users/mu2eai/mcp/kb/config/kb-mcp.env
```

### Enable the service

```bash
<deploy-root>/current/.venv/bin/kb-mcp-install-unit.sh \
  --port 8008 \
  --env-file   /exp/mu2e/app/users/mu2eai/mcp/kb/config/kb-mcp.env \
  --hf-home    /exp/mu2e/app/users/mu2eai/mcp/kb/cache/huggingface \
  --mikey-keys <the shared mikey keys file>
```

`--mikey-keys` must point at the **one shared mikey keys file the other Mu2e
MCP servers already read** -- the same path their units set `MIKEY_KEYS_FILE`
to, in the `mcp/mikey/` directory of the service account. Do not give kb-mcp
its own keys file: mikey has no default path precisely so that each server
cannot drift into a private key namespace, and a separate file would mean
tokens minted for the other servers silently fail here while operators
maintain two stores. Check an existing unit for the exact path rather than
guessing; it is mode 600 and owned by the service account.

`--data-dir` is required and must be absolute. It belongs under
`/exp/mu2e/data`, not beside the code in `/exp/mu2e/app`: it holds the API
keys, the session stores, and copies of every ingested document. There is no
default because it cannot be derived from the deploy root.

The unit also pins `WorkingDirectory` to it. `DATA_DIR` otherwise defaults to
the *relative* `"data"`, and a `systemd --user` service inherits the account's
home as its working directory, so the service would quietly write its state
into `~/data/` on the NAS home area.

Run the installer **once per surface**, with the same `--data-dir`:

```bash
U=<deploy-root>/current/.venv/bin/kb-mcp-install-unit.sh
D=/exp/mu2e/data/users/mu2eai/kb

$U --surface mcp --data-dir "$D" --port 8008 \
   --env-file   /exp/mu2e/app/users/mu2eai/mcp/kb/config/kb-mcp.env \
   --hf-home    "$D/cache/huggingface" \
   --mikey-keys <the shared mikey keys file>

$U --surface web --data-dir "$D" --web-port 8108 \
   --env-file   /exp/mu2e/app/users/mu2eai/mcp/kb/config/kb-mcp.env \
   --hf-home    "$D/cache/huggingface"
```

That gives `kb-mcp.service` on `0.0.0.0:8008` and `kb-web.service` on
`127.0.0.1:8108`.

This renders the unit into *this release's* `share/kb-mcp/kb-mcp.service` and
registers it with `systemctl --user link`, so `~/.config` holds only a symlink
and the unit content stays versioned with the code. `ExecStart` points at
`<deploy-root>/current`, so rolling back is repointing that symlink and
restarting -- no re-render.

Set `--hf-home` to something persistent and shared across releases: the
embedding model (`BAAI/bge-small-en-v1.5`, ~130 MB) downloads on first use,
and without this every redeploy re-downloads it.

Linger must be enabled once per account so the service survives logout:

```bash
loginctl enable-linger
```

### File permissions

`DATA_DIR` holds bearer credentials -- `api_keys.json` and the session stores
-- alongside copies of every ingested source document, so on a shared
filesystem it must not be readable by other accounts.

`kb-mcp-install-unit.sh` creates it `0700`, and the code writes both
credential files `0600` through an atomic temporary-file rename, so they are
never briefly visible at a permissive mode. A file left `0644` by an older
release is tightened on startup, with a warning naming it.

Worth confirming once after the first start, since a directory created by hand
beforehand keeps whatever mode it had:

```bash
stat -c '%A %U %n' <data-dir> <data-dir>/api_keys.json
# drwx------ mu2eai ...
# -rw------- mu2eai ...
```

The shared mikey keys file is owned and mode-600 by the same account; check it
the same way if tokens are being managed there.

### Verify

There is no `/status` route on this service -- that endpoint belongs to the
web UI, which `--only-mcp` does not start. Use the smoke test:

```bash
<deploy-root>/current/.venv/bin/python scripts/smoke_test_http.py \
  http://127.0.0.1:8008 --query "tracker alignment"
```

`--query` matters: the server starts happily with an unreachable database,
because the connection and the embedder are both resolved lazily on the first
search. Note that `kb_search` reports a broken database as
`{"message": "No results found", "results": []}` rather than as an error, so
the smoke test treats an empty result as a failure (pass `--allow-empty` if
the knowledge base really is empty).

### Notes

- Port 8008 by convention; Mu2e reserves 8000-8009 for MCP servers, and the
  entry belongs in `mcp/registry/config/ports.json` in the aitools repo.
- The two surfaces run as **separate units**, `kb-mcp.service` on
  `0.0.0.0:8008` and `kb-web.service` on `127.0.0.1:8108` (8008 + 100, so the
  web port is readable off the MCP port and stays outside the reserved
  8000-8009 range). Reach the UI with `ssh -L 8108:localhost:8108 <host>`.

  Separate units so the web UI can be restarted, or can fail, without
  dropping the MCP sessions agents are holding. The cost is memory: each
  process loads its own copy of the embedding model, about 530 MB resident
  once it has served a query (124 MB before that -- the model loads lazily).

  They share one `DATA_DIR`, which is shared state rather than per-service
  scratch: `api_keys.json`, the session stores, and every ingested document
  under `sources/` and `uploads/`. Giving them separate data directories
  would put web uploads where ingestion cannot see them. It is safe to share
  because the credential files are written through an atomic rename, and
  because the surfaces touch different session stores -- an `--only-mcp`
  process mounts no web routes, so it never writes `web_sessions.json`.
- `KB_ENV_FILE` rather than `EnvironmentFile=`: `kb_mcp.config` calls
  `load_dotenv(override=True)`, so a stray `.env` found relative to the
  working directory would otherwise silently beat the unit's settings.

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

**Quick dump:** `scripts/dump_db.sh [output_dir]` reads connection info from `.env` and writes a
`pg_dump -F c` backup. `pg_dump` must match the server's major version or it refuses to run; the
script checks the server version and, if the version available via cvmfs
(`/cvmfs/mu2e.opensciencegrid.org/packages/postgresql`) doesn't match, falls back to running
`pg_dump` from an official `postgres:<major>` image via `apptainer` (image cached under the backup
dir after the first pull). Output defaults to `/exp/mu2e/data/users/$USER/kb_db_backup`.

### Export from Local Database

```bash
pg_dump -h localhost -U kb_user kb_database \
  --no-owner --no-acl \
  -F c \
  -f kb_backup.dump
```
Or in NERSC podman container:
```bash
podman-hpc exec kb-mcp-postgres-scorrodi pg_dump -U postgres postgres --no-owner --no-acl -F c > kb_backup.dump
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