# kb-mcp

## Python environment

Use `scripts/setup_mu2e_uv.sh` — do **not** create a venv by hand or rely on the
system python.

```bash
source scripts/setup_mu2e_uv.sh
```

It must be **sourced**, not executed, for the activation to stick. The script
creates (or reuses) a uv venv, installs the project editable, and sets `HF_HOME`.

Three locations, all overridable via environment variables before sourcing:

| What | Default | Override |
|---|---|---|
| venv | `/tmp/$USER/kb-env-uv` | `KB_ENV_DIR` |
| uv package cache | `/tmp/$USER/uv-cache` | `UV_CACHE_DIR` |
| persistent data (model weights) | `/exp/mu2e/data/users/$USER/kb-mcp-data` | `KB_DATA_DIR` |

The venv lives on local scratch and is disposable — delete and re-source to
rebuild. Non-interactive one-offs can call the interpreter directly at
`/tmp/$USER/kb-env-uv/bin/python`.

## Configuration

Runtime settings come from `.env` (see `.env.example` for the documented set).
`src/kb_mcp/config.py` treats an **empty** value as unset and falls back to the
coded default — `FOO=` in `.env` does not mean "empty string".

## Database

PostgreSQL, credentials in `.env` (`DB_HOST`/`DB_PORT`/`DB_USER`/`DB_PASSWORD`/`DB_NAME`).
There is no `psql` on `PATH`; cvmfs ships one:

```bash
PG=$(find /cvmfs/mu2e.opensciencegrid.org/packages/postgresql -maxdepth 4 \
     -path "*almalinux9*" -name psql -printf '%h\n' | head -1)
set -a && source .env && set +a
PGPASSWORD="$DB_PASSWORD" "$PG/psql" -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME"
```

cvmfs only carries up to Postgres 15; when the server is newer, `scripts/dump_db.sh`
runs a matching `pg_dump` from a container instead.

Note `binary` is a reserved word in queries — quote it as `"binary"`.

## Commits

One line, imperative mood, sentence case, no type prefix — matching the existing
log ("Stop claiming auth is disabled in public mode", not "fix(auth): ...").
Describe the effect, not the mechanism.

No AI attribution: no `Co-Authored-By` trailer, no "generated with" footer, no
mention of Claude in the message or body.

Split unrelated work into separate commits rather than one sweep. This repo
often has several people's uncommitted changes in the tree at once, so stage
explicit paths — never `git commit -a` or `git add .` — and check `git status`
for work that is not yours before committing a shared file.
