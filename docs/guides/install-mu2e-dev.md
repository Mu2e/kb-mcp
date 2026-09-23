# Mu2e developer setup

How to get a development checkout of kb-mcp running on a Mu2e interactive node
(`mu2egpvm*`, `mu2eaigpvm*`). To only *use* the knowledge base from an MCP
client, see [MCP Clients](mcp-clients.md) instead.

## Before you start

Ask a maintainer for access to a database. There are two:

- **Dev database**: for development, safe to break. Logs in with a username
  and password. Use it for anything that writes (ingest, re-chunking,
  re-embedding).
- **Production database**: the real Mu2e knowledge base. You connect as
  yourself with your Kerberos ticket, and your role decides what you may do.

!!! warning
    Writes to the production database are live for everyone. Do write-heavy
    work on the dev database.

Optional:

- your Fermilab **Services** account (SSO login), for importing from DocDB
- ALCF inference access, for image descriptions during parsing

## 1. Check out and build

```bash
mkdir -p /exp/mu2e/app/users/$USER && cd /exp/mu2e/app/users/$USER
git clone https://github.com/HEP-KE/kb-mcp.git
cd kb-mcp
git switch develop
source scripts/setup_mu2e_uv.sh
```

Source the setup script (don't run it) at the start of every session. The first
run takes several minutes. The venv lives in `/tmp/$USER` on each node and is
rebuilt automatically if it is removed. Persistent data (model weights, logs)
lives in `/exp/mu2e/data/users/$USER/kb-mcp-data`. Add `--scratch` to use
`/scratch/$USER` instead of `/tmp`.

## 2. Configure

`.env` holds the shared settings, and `.env.local` holds your secrets.
`.env.local` overrides `.env`. Both are git-ignored and must stay private:

```bash
install -m 600 .env.mu2e.example .env
install -m 600 /dev/null .env.local
```

In `.env`, fill in the `<placeholders>`:

- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_SCHEMA`: from a maintainer.
  `DB_USER=${USER}` is right for production. For the dev database, use its
  username.
- `PORT`, `WEB_PORT`: two free ports for your local servers. The node is
  shared, and 8000–8009 are reserved.

Leave `EMBEDDING_MODEL` and `CHUNK_STRATEGY` alone. They must match the existing
corpus. [All settings](../reference/env.example.md) are documented in
`.env.example`.

In `.env.local`, add what applies to you:

```bash
DB_PASSWORD=<dev-database-password>        # dev database only
MU2E_DOCDB_USERNAME=<services-username>    # DocDB imports only
MU2E_DOCDB_PASSWORD=<services-password>
```

For image descriptions, run `source scripts/setup_alcf.sh`. It asks for a
Globus login the first time and writes the ALCF token into `.env.local`. The
token is short-lived, so re-run it when image descriptions start failing.
Everything else works without it. The text model runs on `vllm.fnal.gov` and
needs no token.

## 3. Verify

```bash
kinit                          # production database only
kb-import --check-connections
python -m pytest tests/unit -q # no database needed, about a minute
```

`database` and `llm[gpt-oss:120b]` must be `OK`. Until you run `setup_alcf.sh`,
the two `google/gemma-4-31B-it` checks fail with a 404. That is expected, and
only affects image descriptions.

## 4. Run the servers

```bash
kb-server              # or --only-web / --only-mcp
```

Both servers listen on `127.0.0.1` only. To reach the web UI from your laptop:

```bash
ssh -L <web-port>:127.0.0.1:<web-port> <you>@<node>.fnal.gov
# open http://127.0.0.1:<web-port>/web
```

See [CLI Usage](cli.md) for `kb`, `kb-import` and `kb-parse`.

## Troubleshooting

| symptom | fix |
|---|---|
| `No Kerberos credentials available` | production: `kinit`. Dev: set `DB_PASSWORD` in `.env.local` |
| `password authentication failed` | wrong dev `DB_USER` / `DB_PASSWORD` |
| `permission denied` on `CREATE TABLE`, or search finds nothing | your role lacks `USAGE` on the schema; ask a maintainer |
| `permission denied` on insert/update | read-only role; use the dev database |
| `ModuleNotFoundError` after a pull | re-source `scripts/setup_mu2e_uv.sh` |
| "Image description unavailable" | ALCF token expired: `source scripts/setup_alcf.sh` |
| `Address already in use` | pick another `PORT` / `WEB_PORT` |
