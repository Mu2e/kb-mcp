# Mu2e Deployment

Deploys kb-mcp on a Mu2e interactive node as two persistent `systemd --user`
services, following the pattern of the other Mu2e MCP servers (see
[Mu2e/aitools](https://github.com/Mu2e/aitools), `mcp/registry/README.md`).

No container: the package is installed from a pinned git ref into a versioned
venv. This page is self-contained — everything needed to deploy, verify,
upgrade and debug is here.

| | |
|---|---|
| account | `mu2eai` |
| host | `mu2eaigpvm01` |
| deploy root | `/exp/mu2e/app/users/mu2eai/mcp/kb` |
| data dir | `/exp/mu2e/data/users/mu2eai/kb` |
| private config | `<deploy-root>/config/kb-mcp.env` (mode 600) |
| MCP endpoint | `0.0.0.0:8008`, bearer token required |
| web UI | `127.0.0.1:8108`, loopback only |

Run everything as the service account.

> New host or account? Do [First-time setup](#first-time-setup) first,
> then come back here.

## Deploying a release

The repeatable loop: identical for a first install and every upgrade.

### 0. Cut the release

```bash
git push mu2e develop && git tag v0.2.2 && git push mu2e v0.2.2
```

Tag the **tip**, not the version-bump commit, if anything landed after it.

### 1. Install

```bash
mu2einit && slc uv

REF=v0.2.2
curl -fsSL https://raw.githubusercontent.com/Mu2e/kb-mcp/$REF/scripts/deploy-mu2e.sh \
     -o /tmp/deploy-mu2e.sh
bash /tmp/deploy-mu2e.sh /exp/mu2e/app/users/mu2eai/mcp/kb $REF
```

Same command for a first install and for every upgrade. `deploy-mu2e.sh` is the
one piece that cannot come from the deployment — it is what creates the venv
everything else lives in — so fetch it, pinned to the ref being installed.
`$REF` is used for both the URL and the argument, so the installer always
matches the release it installs.

Creates `<deploy-root>/releases/<ref>/.venv` and points `<deploy-root>/current`
at it. `uv` fetches the ref from GitHub, so **the tag must be pushed first**. No
source tree is copied: `kb-mcp.sh`, `kb-mcp-install-unit.sh` and
`kb-mcp-smoke-test` are installed into `<release>/.venv/bin/`, and the rest of
this page only uses those.

> A copy of the installer also ships at `<release>/.venv/bin/deploy-mu2e.sh`,
> for a host that cannot reach GitHub. It is the *previous* release's
> installer, so prefer the fetch above.

CPU-only torch is installed first, on purpose: `sentence-transformers` is a
core dependency and its torch would otherwise resolve to the CUDA build —
several GB, useless for embedding one query at a time. Expect ~1.3 GB per
release, so prune old ones rather than letting them accumulate.

`KB_MCP_EXTRAS=ingest` adds the parser stack, for a host that also ingests. A
query-only server does not need it. (With extras, torchvision is installed
from the same CPU index — from PyPI it is built against CUDA torch and
`import sentence_transformers` then dies with a misleading
`Could not import module 'PreTrainedModel'`.)

### 2. Check before restarting into it

```bash
/exp/mu2e/app/users/mu2eai/mcp/kb/current/.venv/bin/kb-mcp.sh --check \
  --env-file /exp/mu2e/app/users/mu2eai/mcp/kb/config/kb-mcp.env
```

`--check` loads **both** configuration layers in the unit's order — this
release's `mu2e.env`, then the private file overriding it. Checking either
alone reports a confident, healthy-looking configuration that will never run:

| checked with | reports |
|---|---|
| `mu2e.env` alone | right ports and model, no database, no LLM endpoint, no `ADMIN_PASSWORD` |
| the private file alone | right secrets, but `127.0.0.1:8443` and an unset model — *code* defaults |
| both (the default) | what the service will actually do |

It fails on: `--env-file` pointing at the shipped defaults; missing `DB_HOST`,
`DB_NAME`, `DB_USER` or `OPENAI_BASE_URL`; public mode with no
`ADMIN_PASSWORD`; no valid Kerberos ticket. `DATA_DIR`, `HF_HOME` and
`MIKEY_KEYS_FILE` are reported but not failed on — the unit supplies them.

It cannot see the service's Kerberos cache, only this shell's, and says so.

### 3. Install the units

Once per surface, with the **same** `--data-dir`:

```bash
U=/exp/mu2e/app/users/mu2eai/mcp/kb/current/.venv/bin/kb-mcp-install-unit.sh
D=/exp/mu2e/data/users/mu2eai/kb
E=/exp/mu2e/app/users/mu2eai/mcp/kb/config/kb-mcp.env
K=/path/to/mikey/keys   # the SHARED keystore -- see the note below

$U --surface mcp --data-dir "$D" --port 8008 \
   --env-file "$E" --hf-home "$D/cache/huggingface" \
   --mikey-keys "$K" --krb5-ccname "$KRB5CCNAME"

$U --surface web --data-dir "$D" --web-port 8108 \
   --env-file "$E" --hf-home "$D/cache/huggingface" \
   --krb5-ccname "$KRB5CCNAME"

loginctl enable-linger      # once per account, or the services die at logout
```

`--dry-run` prints the unit without writing anything.

- **`--data-dir` is required and absolute**, under `/exp/mu2e/data`. It holds
  API keys, session stores and copies of every ingested document. `DATA_DIR`
  otherwise defaults to the relative `"data"`, and a `systemd --user` service
  inherits the account's home as its working directory — state would land in
  `~/data/` on the NAS home area. The unit pins `WorkingDirectory` for the same
  reason.
- **Both surfaces share one `--data-dir`.** It is shared state, not per-service
  scratch; separate directories would put web uploads where ingestion cannot
  see them. Safe because credential files are written through atomic renames
  and the two surfaces touch different session stores.
- **`--mikey-keys` is the one shared keys file** the other Mu2e MCP servers
  read — check an existing unit rather than guessing. mikey has no default path
  precisely so servers cannot drift into private key namespaces. Passing an
  empty value disables mikey auth silently, so confirm it landed:
  `systemctl --user show kb-mcp -p Environment | tr ' ' '\n' | grep MIKEY`.
- **`--hf-home` must be outside the release.** The embedding model
  (`BAAI/bge-small-en-v1.5`, ~130 MB) downloads on first use; without this,
  every redeploy re-downloads it.

The unit is rendered into this release's `share/kb-mcp/` and registered with
`systemctl --user link`, so `~/.config` holds only a symlink. `ExecStart` points
at `<deploy-root>/current`.

Re-running the installer is not optional. Two things follow `current`, and only
one follows it by itself:

| | points at | updates |
|---|---|---|
| `ExecStart` | `<deploy-root>/current/.venv/bin/kb-mcp.sh` | on restart, automatically |
| the unit file | `~/.config/systemd/user/…` → `releases/<ref>/…` | only when the installer re-runs |

A restart alone runs new code under the **old release's unit**, with whatever
`Environment=` and `EnvironmentFile=` that release rendered — and pruning the
old release then leaves a dangling symlink and a service that will not start.

The installer enables and restarts each unit. Confirm the running processes are
the release you just deployed — `systemctl --user status` shows the resolved
path:

```bash
systemctl --user status kb-mcp.service kb-web.service | grep releases/
```

If that still shows the previous release, restart explicitly:

```bash
systemctl --user restart kb-mcp.service kb-web.service
```

### 4. Verify

```bash
systemctl --user status kb-mcp.service kb-web.service
systemctl --user show kb-mcp -p ExecStart -p Environment   # authoritative
```

The MCP endpoint has **no `/status`** — that route belongs to the web UI, which
`--only-mcp` does not start. Use the smoke test, which ships with the package:

```bash
/exp/mu2e/app/users/mu2eai/mcp/kb/current/.venv/bin/kb-mcp-smoke-test \
  http://127.0.0.1:8008 --token mikey_xxx --query "tracker alignment"
```

`--query` matters: the server starts happily against an unreachable database,
because the connection and the embedder both resolve lazily on the first
search, and `kb_search` reports a broken database as
`{"message": "No results found", "results": []}` rather than as an error. The
smoke test therefore treats an empty result as failure (`--allow-empty` if the
knowledge base really is empty).

The first query after a restart loads the embedding model and takes roughly a
minute; later ones take a few seconds. The default `--timeout` (120 s) covers
that — if it does trip, the smoke test says it timed out rather than reporting
a stream error, and the server is usually still working.

Auth, from another host:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://mu2eaigpvm01:8008/mcp     # 401
curl ... -H 'Authorization: Bearer <bad token>' ...                       # 401
```

Web UI over `ssh -L 8108:localhost:8108 mu2eaigpvm01`:

| path | expect | meaning |
|---|---|---|
| `/status` | 200 | serving |
| `/web` | 200 | browsing open, database reachable |
| `/web` | 500 | database unreachable — it lists documents, so it fails loudly |
| `/login` | 303 → `/admin/login` | public mode active (302 to `/login?redirect=` means it is **not**) |

Permissions, once:

```bash
stat -c '%A %U %n' "$D" "$D/api_keys.json"
# drwx--S--- mu2eai     (setgid inherited from /exp/mu2e/data; still 0700)
# -rw------- mu2eai
```

### 5. Prune

Prune last, keeping the release you would roll back to:

```bash
ls -l /exp/mu2e/app/users/mu2eai/mcp/kb/current
rm -rf /exp/mu2e/app/users/mu2eai/mcp/kb/releases/<old-ref>
```

Rolling back to a release still on disk *is* repointing `current` and
restarting — no re-render.

## Scheduled DocDB import

The incremental DocDB import runs from a release, twice a day, from cron. It
runs as a user, not the service account: DocDB has no service login, so login
is only possible as a user.

Same layout as the servers: deploy root `/exp/mu2e/app/users/<you>/mcp/kb`,
data (logs, model cache, ALCF login) in `/exp/mu2e/data/users/<you>/kb-mcp-data`.
Files in `<deploy-root>/config/`, all mode 600:

| file | holds | from |
|---|---|---|
| `kb-mcp.env` | database, LLM routing, parser settings | `.env.mu2e.example`, keeping only the database, LLM and parser keys |
| `.env.local` | `MU2E_DOCDB_USERNAME`, `MU2E_DOCDB_PASSWORD` | by hand; each run adds the ALCF token |
| `inference_auth_token.py` | ALCF login helper | downloaded, see below |
| `credentials.local.sh` | optional, sourced before each run | your own |

`kb-mcp.env` wins over `.env.local`, so no key may be in both; every run warns
if one is.

Install the release with the ingest extras:

```bash
REF=v0.2.3
ROOT=/exp/mu2e/app/users/$USER/mcp/kb
curl -fsSL https://raw.githubusercontent.com/Mu2e/kb-mcp/$REF/scripts/deploy-mu2e.sh \
     -o /tmp/deploy-mu2e.sh
KB_MCP_EXTRAS=ingest,docling,alcf bash /tmp/deploy-mu2e.sh $ROOT $REF
```

`REF` can be any pushed git ref, not only a tag: a commit (`REF=7a9340b`) is
handy for trying a change before tagging it. The release directory is named
after the ref.

Create the config files above (`mkdir -m 700 $ROOT/config`; `install -m 600`
for each). The ALCF login is kept under the data dir rather than `$HOME`.
Either reuse an existing one by copying
`~/.globus/app/58fdd3bc-e1c3-4ce5-80ea-8d6b87cfb944/inference_app/tokens.json`
to the same path under `/exp/mu2e/data/users/$USER/kb-mcp-data/alcf/`, or log in
once:

```bash
cd $ROOT/config
curl -O https://raw.githubusercontent.com/argonne-lcf/inference-endpoints/refs/heads/main/inference_auth_token.py
HOME=/exp/mu2e/data/users/$USER/kb-mcp-data/alcf $ROOT/current/.venv/bin/python inference_auth_token.py authenticate
```

Check, then do one run by hand:

```bash
$ROOT/current/.venv/bin/kb-mcp.sh --check --env-file $ROOT/config/kb-mcp.env
KB_ENV_FILE=$ROOT/config/kb-mcp.env $ROOT/current/.venv/bin/kb-import --check-connections
KB_ENV_FILE=$ROOT/config/kb-mcp.env $ROOT/current/.venv/bin/kb-docdb-update.sh; echo rc=$?
```

`kb-mcp.sh --check` is the server's check: its web-UI and mikey findings do not
apply here, and `OPENAI_BASE_URL` is only set once a run has refreshed the ALCF
token. A manual run with `DAYS=<n>` catches up after missed days.

Then install the crontab on the node that should run it:
`scripts/kb_docdb.crontab` from the repository, with your paths. Its `HOME=`
line points cron at the data directory, since cron changes to `$HOME` before
each job and the account may have no home directory on the node (mu2eaigpvm01
does not mount `/nashome`). `crontab <file>` replaces the node's whole crontab.

After the first scheduled run, check the newest log:

```bash
ls -t /exp/mu2e/data/users/$USER/kb-mcp-data/logs/docdb-update-*.log | head -1
```

A failed run exits non-zero and its log ends with a `FAILURE :` line; every
run also appears in `kb logs imports`. Upgrading is the normal release loop:
the crontab runs `<deploy-root>/current`, so deploying a new release is enough.

## Reference

### Web UI access model

There is no OAuth provider, so `WEB_REQUIRE_AUTH=false` — and that alone is not
a complete configuration: `WEB_PUBLIC_MODE=true` must go with it.

With both off, `/web` redirects to `/login`, and `/login` mints a session
carrying `has_admin=True` with no password and no check — anyone reaching the
port gets uploads, deletes, re-chunking and API-key management. Public mode
instead serves the browsable pages with no session and sends `/login` to
`/admin/login`, leaving the write pages behind `ADMIN_PASSWORD`.

Binding to loopback does **not** substitute for that on a shared node: any
account on the host can reach `127.0.0.1:8108` through an ssh tunnel. Loopback
keeps the UI off the network; `ADMIN_PASSWORD` is what protects writes.

If no admin password is set, the write pages are open — `is_admin_unlocked()`
returns `True` when nothing is configured to check against.

### Troubleshooting

Logs are in the journal; there is no log file. `-o cat` drops the syslog prefix
so tracebacks read as tracebacks:

```bash
journalctl --user -u kb-web.service -f -o cat
journalctl --user -u kb-mcp.service --no-pager -n 200 -o cat
```

`kb-web` is usually the better one to read: it fails on every page load, so the
traceback is fresh and repeated.

| symptom | cause |
|---|---|
| `/web` 500 **and** `kb_search` returns 0 results | the database. Same fault, loud on one surface and silent on the other. |
| `... GSSAPI ... Ticket expired` | `KRB5CCNAME` — the service is reading a different cache than your shell. See [Kerberos credentials](#kerberos-credentials). |
| `permission denied for table <x>` / `CREATE TABLE` in the traceback | missing `USAGE`. See [Database grants](#database-grants). |
| `permission denied for table logs_search` | missing `INSERT`. See [Database grants](#database-grants). |
| `DetachedInstanceError` on `doc.text` | a *consequence* — a failed write in the same transaction. The real error is logged immediately before it. |
| `could not translate host name` / `Connection refused` | `DB_HOST`, not Kerberos. |
| ports read `127.0.0.1:8443` / `:8444` | the defaults layer did not load; those are code defaults. |
| `Could not import module 'PreTrainedModel'` | torchvision built against CUDA torch; reinstall from the CPU index. |

### Notes

- Port 8008 by Mu2e convention; the aitools registry reserves 8000–8009 for MCP
  servers, and the entry belongs in `mcp/registry/config/ports.json`.
- **Two separate units** so the web UI can be restarted, or can fail, without
  dropping MCP sessions agents are holding. The cost is memory: each process
  loads its own copy of the embedding model, ~530 MB resident once it has
  served a query (124 MB before — the model loads lazily).
- Web port 8108 is 8008 + 100, so it is readable off the MCP port and stays
  outside the reserved range.
- **`KB_ENV_FILE` rather than `EnvironmentFile=` for secrets**: setting it also
  stops `kb_mcp.config` searching for a `.env` of its own. That search walked up
  from the installed module's directory, so a `.env` left anywhere above the
  package — in the deploy root beside `config/`, say — was loaded with
  `override=True` and beat the unit for every key the private file did not set.
- `HIDE_GRAPH=true` in `deploy/mu2e.env` drops the three graph MCP tools, stops
  `kb_get` fetching associated nodes, and removes the graph from the web UI.

## First-time setup

Once per host and account. Not part of a routine deploy.

### Private configuration file

Configuration lives in three places, deliberately:

| where | holds | changed by |
|---|---|---|
| `deploy/mu2e.env` (in git, ships to `<release>/.venv/share/kb-mcp/mu2e.env`) | policy: ports, bind addresses, auth mode, embedding model, `HIDE_GRAPH`, log levels | commit + new release |
| the systemd unit | paths: `DATA_DIR`, `HF_HOME`, `MIKEY_KEYS_FILE`, `KRB5CCNAME` | re-running `kb-mcp-install-unit.sh` |
| `KB_ENV_FILE` (mode 600, outside git) | secrets **and site topology**: database coordinates, LLM endpoint | editing that file |

They load in that order, and `KB_ENV_FILE` is loaded last with `override=True`,
so a secret always beats a default. Nothing in the running service reads from a
working checkout.

Create the private file **as the service account**:

```bash
f=/exp/mu2e/app/users/mu2eai/mcp/kb/config/kb-mcp.env
[ -e "$f" ] || install -D -m 600 /dev/null "$f"
```

`install -D -m 600 /dev/null` creates the parent directories and a file that is
mode 600 *from the moment it exists*, unlike `touch` + `chmod`. The `[ -e ]`
guard matters because `install` truncates — without it, re-running this wipes
the credentials.

Contents — these keys and no others:

```bash
# Database. No DB_PASSWORD: Kerberos/GSSAPI supplies the credential, so
# DB_USER must be the service account's own principal.
DB_HOST=
DB_PORT=
DB_NAME=
DB_USER=
DB_SCHEMA=            # not necessarily "public"
# DB_HOSTADDR=        # only when tunnelling: libpq derives the GSSAPI
                      # principal from DB_HOST, not from the TCP target

OPENAI_BASE_URL=
OPENAI_API_KEY=

# Guards the web UI's write pages (upload, delete, re-chunk, API keys).
# Browsing stays open in public mode, so this is what protects writes.
ADMIN_PASSWORD=
```

Do not put inline `# comments` after values here if this file is ever handed to
systemd as an `EnvironmentFile=` — python-dotenv strips them, systemd does not,
and the value silently becomes `value   # comment`.

`EMBEDDING_MODEL` is set in `deploy/mu2e.env` and **must match what the indexed
chunks were embedded with** — the query is embedded at read time and compared in
the same vector space, so a mismatch returns plausible-looking nonsense rather
than an error. Check it against the `embedding_configs` rows before first start.

### Database grants

The service connects as `mu2eai` with GSSAPI and needs read access to the
schema holding the knowledge base. Run as the schema owner:

Substitute your own `DB_SCHEMA` and `DB_USER` for `<schema>` and `<role>`:

```sql
GRANT USAGE ON SCHEMA <schema> TO <role>;
GRANT SELECT ON ALL TABLES IN SCHEMA <schema> TO <role>;
ALTER DEFAULT PRIVILEGES IN SCHEMA <schema> GRANT SELECT ON TABLES TO <role>;
GRANT INSERT ON <schema>.logs_search TO <role>;
```

Why each one:

- **`USAGE`** — without it the tables are *invisible*, not merely unreadable.
  SQLAlchemy's reflection then finds nothing, concludes the schema is empty and
  tries to `CREATE TABLE`, which fails with `permission denied`. `/web` returns
  500 and `kb_search` returns nothing.
- **`ALTER DEFAULT PRIVILEGES`** — without it, any table added to the schema
  later is invisible again, reproducing the same failure.
- **`INSERT ON logs_search`** — every search writes one row. The insert is
  *not* optional in practice: it is flushed in the caller's transaction, so a
  denial poisons the session and the search returns
  `DetachedInstanceError` after having found its results.

`mu2eai` should **not** have `CREATE`. Verify:

```sql
SELECT has_schema_privilege('<role>','<schema>','USAGE'),   -- want true
       has_schema_privilege('<role>','<schema>','CREATE');  -- want false
```

### Kerberos credentials

The database connection has no password: it authenticates with GSSAPI from the
service account's ticket. A `systemd --user` service does not inherit your
shell's `KRB5CCNAME`, so it is passed explicitly when the units are installed
(`--krb5-ccname "$KRB5CCNAME"`, step 3).

Confirm the service sees the same cache you do:

```bash
klist                                     # your shell
systemd-run --user --pipe --wait klist    # the service
```

If they differ, the service is reading a stale ticket and will fail on its
first query with `Ticket expired`. Whatever renews the cache — typically a cron
job holding a keytab — has to keep running.
