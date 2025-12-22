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
Host perlmutter*.nersc.gov saul*.nersc.gov dtn*.nersc.gov
    User <user-name>
    IdentityFile ~/.ssh/nersc
    IdentitiesOnly yes
    ForwardAgent yes
```

## Quick Setup Script
These are instructions on how to use it in per-user develop mode. Later we might want to add a docker that could just be run?

The `nersc_setup.sh` script automates the setup of kb-mcp on NERSC systems:

1. **Download the script:**
   ```bash
   curl -O https://raw.githubusercontent.com/HEP-KE/kb-mcp/sld/scripts/nersc_setup.sh
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
