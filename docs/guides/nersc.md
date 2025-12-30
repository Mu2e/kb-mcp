# NERSC

## Login

Simple ssh like in the example below requires to to enter your password **and** authentificator CODE every time:

```bash
ssh <user-name>@perlmutter.nersc.gov
```

### sshproxy

For instructions see [docs.nersc.gov/connect/mfa/#sshproxy](https://docs.nersc.gov/connect/mfa/#sshproxy)
The `sshproxy` allows to generate certificates that can be used for 24h:

1. Get `sshproxy` for your laptop/machine from [portal.nersc.gov/cfs/mfa/](https://portal.nersc.gov/cfs/mfa/)

2. Run:
```bash
sshproxy -u <user-name>
```

3. Add the following to your `.ssh/config`:

```
Host perlmutter*.nersc.gov saul*.nersc.gov dtn*.nersc.gov *.perlmutter.nersc.gov
    User <user-name>
    IdentityFile ~/.ssh/nersc
    IdentitiesOnly yes
    ForwardAgent yes
```

## Quick Setup Script
These are instructions on how to use it in per-user develop mode. For containerized deployment on NERSC, see [NERSC Deployment](deployment.md#nersc-deployment).

The `nersc_setup.sh` script automates the setup of kb-mcp on NERSC systems:

0. **Add SSH key to GitHub**  
Make sure you added your SSH key to your GitHub account. If you need help, see the [GitHub guide on adding SSH keys](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/adding-a-new-ssh-key-to-your-github-account).

1. **Download the script:**
   ```bash
   (t=$(mktemp -d) && git clone --depth 1 --branch sld git@github.com:HEP-KE/kb-mcp.git "$t" && cp "$t/scripts/nersc_setup.sh" . && rm -rf "$t")
   ```

2. **Source the script** (it must be sourced, not executed):
   ```bash
   source nersc_setup.sh
   ```

3. **Update the repository** (optional):
   ```bash
   source nersc_setup.sh --update
   ```

The script will:
- Create a virtual environment in `/global/common/software/` (persistent across sessions)
- Clone the repository to `$SCRATCH/kb-mcp`
- Link persistent data directory from CFS
- Install dependencies
- Load your `.env` from `~/.kb-mcp.env` or shared secrets
- Automatically start the database (see Database Setup below)

**Note:** The script must be sourced (not executed) because it sets up your environment variables.

### Accessing the Web Server

After setup, you can start the web (and mcp) server with `kb-server` and access it from your local machine using SSH port forwarding:

```bash
ssh -J <user-name>@perlmutter.nersc.gov -L 8443:localhost:8443 <user-name>@<login-node-name>.chn.perlmutter.nersc.gov
```

Then access the web interface at `https://localhost:8443` in your browser.

## Interactive Nodes

### GPU node:
```bash
salloc --nodes 1 --qos interactive --time 01:00:00 --constraint gpu --account m5115_g
```
All nodes come with 4 GPUs. If you want to use all on them we have a script to run a command, for example like
```bash
./scripts/run_on_4gpus.sh kb tools parse-all inspire-hep --extract-images --describe-images --parser-name marker
```

### CPU nodes:
```bash
salloc --nodes 1 --qos interactive --time 01:00:00 --constraint cpu --account m5115
```

## Database Setup

The `nersc_setup_db.sh` script is automatically called by `nersc_setup.sh` to set up a PostgreSQL database with the pgvector extension. The database setup handles:

### Automatic Database Management

- **Checks for existing database**: If a database is already running (as configured in the common `kb-mcp.env` file), it will detect and use it
- **Starts new database if needed**: If no database is available, it starts a new PostgreSQL container using `podman-hpc`
- **Login node requirement**: The database can only be started on login nodes (not compute nodes)

### Database Configuration

The script:

- Uses PostgreSQL 16 with pgvector extension (`pgvector/pgvector:pg16`)
- Stores database data in `$SCRATCH/kb-mcp-db/pgdata`
- Runs on port `54321` (external) mapped to container port `5432`
- Generates and stores database credentials in the shared secrets file at `/global/cfs/cdirs/$PROJECT_ID/secrets/kb-mcp.env`
- Automatically enables the `vector` extension for embedding storage

The database is accessible from other NERSC nodes, allowing compute nodes to connect to the database running on a login node. **The database is persistent but only avaiable as long as the login node session is alive**.

**Note:** Currently, the database data is stored in `$SCRATCH`, which is temporary. Consider backing up important data or migrating to CFS for persistence.

## Running vLLM (gpt-oss-120b) locally at NERSC

After completing the setup with `nersc_setup.sh` as described above, you can run a local vLLM server on NERSC compute nodes using the `nersc_launch_llm.sh` script. The script automatically submits a SLURM job to launch vLLM on a worker node with 4 GPUs, waits for the server to become ready (typically 5-10 minutes), and updates your `.env` file with the `OPENAI_BASE_URL` and `OPENAI_API_KEY` so that kb-mcp can use the local LLM instance.

To launch the vLLM server:

```bash
./scripts/nersc_launch_llm.sh
```

The script requires a Hugging Face token (set `HF_TOKEN` in your `~/.kb-mcp.env.local` file) and will output the connection details once the server is ready.

## Files and Folders

`kb-mcp` uses the following files and folders (by default):

- **Repository clone:**  
  - `$SCRATCH/kb-mcp` : Main source code and scripts.
- **Persistent data directory:**  
  - `/global/cfs/cdirs/<PROJECT_ID>/<username>/kb-mcp-data`  
    Linked to `data/` inside the repo clone for long-term storage, even if `$SCRATCH` is purged.
- **Database data directory:**  
  - `$SCRATCH/kb-mcp-db/pgdata`  
    Stores the PostgreSQL data files (persistent as long as your `$SCRATCH` is available).
- **Common secrets / environment file:**  
  - `/global/cfs/cdirs/<PROJECT_ID>/secrets/kb-mcp.env`  
    Shared location for secrets (database credentials, OAuth keys, etc).  
    The database setup script (`nersc_setup_db.sh`) will update this file automatically with current database connection info if it (re)starts, so all users see the current database endpoint.
- **User-specific environment override (optional):**  
  - `~/.kb-mcp.env`  
    If present, overrides or augments settings from the shared environment, allowing customization for your account (e.g., different credentials, custom endpoints).
- **`.env` file in the repo clone:**  
  - `$SCRATCH/kb-mcp/.env`  
    This is a symlink set up by `nersc_setup.sh` to either your `~/.kb-mcp.env` *or* the shared CFS `kb-mcp.env`. This is the file read by the application at startup.

**Note:**  

- Database credentials and most secrets are managed via the shared `.env` in the secrets folder.  
- If you run the database setup (`nersc_setup_db.sh`), it will update the credentials in the shared `.env` so other users (or compute nodes) will have access to the latest settings.
- You can always override by creating or editing your own `~/.kb-mcp.env`. This will take precedence for your user.


