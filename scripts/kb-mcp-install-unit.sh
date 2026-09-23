#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  kb-mcp-install-unit.sh --surface <mcp|web> --data-dir <path> \
                         --env-file <path> [options]

Renders a systemd --user unit for this release and registers it.

kb-mcp serves two surfaces, and each gets its own unit so that the web UI can
be restarted -- or can crash -- without dropping the MCP sessions agents are
holding:

  --surface mcp   kb-mcp.service   MCP endpoint, 0.0.0.0:8008 by default
  --surface web   kb-web.service   web UI, 127.0.0.1:8108 by default

Run this once per surface. Both must be given the SAME --data-dir: it holds
api_keys.json, the session stores, and copies of every ingested source
document under sources/ and uploads/, so it is shared state, not per-service
scratch. Each process loads its own copy of the embedding model, roughly
530 MB resident once it has served a query.

The unit is written into THIS release's own share/kb-mcp/kb-mcp.service and
registered with `systemctl --user link --force`, which puts a *symlink* in
~/.config/systemd/user pointing back here. The real unit content therefore
stays with the installed code: nothing to hand-edit, nothing to copy, and
rolling back is repointing <deploy-root>/current and restarting.

Options:
  --surface <s>       "mcp" or "web" (default: mcp). Selects which unit is
                      rendered, its name, and which surface it starts.
  --port <port>       MCP endpoint port (default: 8008). Mu2e convention
                      reserves 8000-8009 for MCP servers; kb is 8008.
  --env-file <path>   Env file with database credentials and settings
                      (required). Passed as KB_ENV_FILE so it wins over any
                      stray .env -- see "Why KB_ENV_FILE" below.
  --hf-home <path>    Persistent Hugging Face cache shared across releases.
                      Strongly recommended: without it each redeploy
                      re-downloads ~130 MB of model weights.
  --data-dir <path>   REQUIRED, absolute. Writable state: API keys, session
                      stores, and ingested documents. Belongs under
                      /exp/mu2e/data, not beside the code in /exp/mu2e/app --
                      it cannot be derived from the deploy root, which is why
                      there is no default. See "Why DATA_DIR" below.
  --mikey-keys <path> Shared mikey key file for bearer-token auth. Optional;
                      without it mikey auth stays off.
  --defaults <path>   Non-secret settings file, as systemd EnvironmentFile.
                      Defaults to this release's own
                      share/kb-mcp/mu2e.env (shipped with the package).
  --host <addr>       MCP bind address (default: 0.0.0.0).
  --web-port <port>   Web UI port (default: 8108 -- 8008 + 100, deliberately
                      outside the 8000-8009 MCP range).
  --web-host <addr>   Web UI bind address (default: 127.0.0.1). Keep it on
                      loopback and reach it over an ssh tunnel; that is what
                      makes running the UI without per-user login safe.
  --deploy-root <p>   Deploy root holding releases/ and the `current` symlink.
                      Auto-detected from this script's location; override only
                      for a non-standard layout. ExecStart is written against
                      <deploy-root>/current so that rolling back is repointing
                      that symlink and restarting -- no re-render needed.
  --krb5-ccname <s>   Kerberos credential cache for the service, as a
                      KRB5CCNAME spec (e.g. FILE:/tmp/krb5cc_1234_auto).
                      Defaults to $KRB5CCNAME from the installing shell.
                      Required in practice whenever the database connection
                      authenticates with GSSAPI -- see "Why KRB5CCNAME" below.
  --no-krb5-ccname    Do not set it (a deployment using DB_PASSWORD).
  --description <s>   Override the unit Description.
  --no-enable         Render and link, but do not enable/start.
  --dry-run           Print the unit that would be written, then exit.

Why DATA_DIR is set explicitly:
  DATA_DIR defaults to the relative path "data", and a `systemd --user`
  service inherits the user's HOME as its working directory (systemd.exec:
  "the respective user's home directory if run as user"). Left alone, the
  service would write api_keys.json and the session stores into
  ~/data/ on the NAS home area -- silently, and on a filesystem that an
  unattended service should not depend on. WorkingDirectory= is pinned for
  the same reason.

Why KB_ENV_FILE rather than EnvironmentFile= for secrets:
  Naming the file through KB_ENV_FILE pins exactly which file is
  authoritative, and since 0.2.1 it also switches kb_mcp.config out of
  discovery entirely: with KB_ENV_FILE set, no .env is searched for. That
  search walked up from the installed module's own directory -- not the
  working directory -- so any .env above the package, including one left in
  the deploy root beside config/, was loaded with override=True and beat
  everything the unit set with Environment=/EnvironmentFile=. Secrets live in
  that file, not in the unit: keep it mode 600 and owned by the service
  account.

Why KRB5CCNAME has to be set explicitly:
  With no DB_PASSWORD the database connection authenticates with GSSAPI from
  the service account's ticket, and a `systemd --user` service does NOT inherit
  the KRB5CCNAME of the shell that installed it. It falls back to libkrb5's
  default cache, /tmp/krb5cc_<uid> -- while the renewed ticket usually lives
  somewhere else, such as /tmp/krb5cc_<uid>_auto. Both caches exist and both
  hold the same principal, so the failure is not "no credentials cache" but
  "Ticket expired", from whatever stale ticket the default cache still holds.

  The service then starts cleanly, serves, authenticates MCP clients, and
  fails only on the first database call -- where kb_search reports it as
  {"message": "No results found"} rather than as an error. Compare:

    klist                                   # the installing shell
    systemd-run --user --pipe --wait klist  # what the service actually sees

Requires linger so the service survives logout (once per account, permanent):
  loginctl enable-linger

Example (both surfaces, one shared data directory):
  kb-mcp-install-unit.sh --surface mcp --data-dir /exp/mu2e/data/users/mu2eai/kb \
    --env-file /exp/mu2e/app/users/mu2eai/mcp/kb/config/kb-mcp.env \
    --hf-home  /exp/mu2e/data/users/mu2eai/kb/cache/huggingface

  kb-mcp-install-unit.sh --surface web --data-dir /exp/mu2e/data/users/mu2eai/kb \
    --env-file /exp/mu2e/app/users/mu2eai/mcp/kb/config/kb-mcp.env \
    --hf-home  /exp/mu2e/app/users/mu2eai/mcp/kb/cache/huggingface
USAGE
  exit 2
}

port="8008"
env_file=""
hf_home=""
data_dir=""
surface="mcp"
mikey_keys=""
defaults_file=""
host="0.0.0.0"
web_port="8108"
web_host="127.0.0.1"
deploy_root=""
description=""
do_enable=1
dry_run=0
# Default to the credential cache this shell is using. That is correct exactly
# when the installer is run from a session whose ticket works, which is the
# normal case -- and it is the only way the value can be discovered, since the
# service cannot find it on its own. Empty is allowed: a deployment with a
# database password needs no ticket at all.
krb5_ccname="${KRB5CCNAME-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)        port="${2:-}"; shift 2 ;;
    --env-file)    env_file="${2:-}"; shift 2 ;;
    --hf-home)     hf_home="${2:-}"; shift 2 ;;
    --data-dir)    data_dir="${2:-}"; shift 2 ;;
    --surface)     surface="${2:-}"; shift 2 ;;
    --mikey-keys)  mikey_keys="${2:-}"; shift 2 ;;
    --defaults)    defaults_file="${2:-}"; shift 2 ;;
    --host)        host="${2:-}"; shift 2 ;;
    --web-port)    web_port="${2:-}"; shift 2 ;;
    --web-host)    web_host="${2:-}"; shift 2 ;;
    --deploy-root) deploy_root="${2:-}"; shift 2 ;;
    --description) description="${2:-}"; shift 2 ;;
    --krb5-ccname) krb5_ccname="${2:-}"; shift 2 ;;
    --no-krb5-ccname) krb5_ccname=""; shift ;;
    --no-enable)   do_enable=0; shift ;;
    --dry-run)     dry_run=1; shift ;;
    --help|-h)     usage ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage ;;
  esac
done

case "$surface" in
  mcp|web) : ;;
  *) echo "ERROR: --surface must be 'mcp' or 'web' (got: $surface)" >&2; usage ;;
esac
[[ -n "$env_file" ]] || { echo "ERROR: --env-file is required" >&2; usage; }
[[ -n "$data_dir" ]] || {
  echo "ERROR: --data-dir is required, and both surfaces must share one." >&2
  echo "       It belongs under /exp/mu2e/data, not beside the code." >&2
  usage
}

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"   # <release>/.venv/bin
venv_dir="$(dirname "$here")"                              # <release>/.venv
launcher="$here/kb-mcp.sh"

if [[ ! -x "$launcher" ]]; then
  echo "ERROR: launcher not found or not executable: $launcher" >&2
  exit 1
fi

# Prefer <deploy-root>/current/.venv/bin/kb-mcp.sh over this release's resolved
# path, matching the other Mu2e MCP units: a rollback is then repointing
# `current` and restarting, rather than re-rendering and re-linking the unit.
# Auto-detect the deploy root from the standard <root>/releases/<ref>/.venv/bin
# layout; fall back to the resolved launcher when the layout does not match
# (e.g. a plain dev venv), which still produces a working unit.
if [[ -z "$deploy_root" && "$here" == */releases/*/.venv/bin ]]; then
  deploy_root="${here%/releases/*}"
fi

if [[ -n "$deploy_root" ]]; then
  exec_target="$deploy_root/current/.venv/bin/kb-mcp.sh"
  if [[ ! -x "$exec_target" ]]; then
    echo "ERROR: $exec_target is not executable." >&2
    echo "       Has the install finished, and does 'current' point at a release?" >&2
    exit 1
  fi
  # A unit rendered from a release that is not the current one would silently
  # launch a different release than the one being installed.
  if [[ "$(readlink -f "$exec_target")" != "$(readlink -f "$launcher")" ]]; then
    echo "WARNING: 'current' does not point at the release this script came from." >&2
    echo "         ExecStart will launch: $(readlink -f "$exec_target")" >&2
  fi
else
  exec_target="$launcher"
  echo "NOTE: no deploy-root layout detected; ExecStart pinned to this venv." >&2
fi

# Writable state lives beside releases/, not inside one, so a redeploy does
# not strand the API keys and session stores in an old release directory.
case "$data_dir" in
  /*) : ;;
  *) echo "ERROR: --data-dir must be an absolute path (got: $data_dir)" >&2; exit 1 ;;
esac
# 0700: this directory holds api_keys.json and the session stores, which are
# bearer credentials, plus copies of every ingested source document. On a
# shared filesystem the default 0755 would expose all of it.
#
# Not under --dry-run: a flag documented as "print the unit, then exit" has no
# business creating directories.
if [[ "$dry_run" != "1" ]]; then
  mkdir -p "$data_dir"
  chmod 700 "$data_dir"
fi

# Non-secret settings ship with the package; fall back to naming the file
# even if this release predates it, so the error is explicit.
if [[ -z "$defaults_file" ]]; then
  defaults_file="$venv_dir/share/kb-mcp/mu2e.env"
fi
if [[ ! -f "$defaults_file" ]]; then
  echo "ERROR: defaults file not found: $defaults_file" >&2
  echo "       Pass --defaults, or install a release that ships it." >&2
  exit 1
fi

if [[ -n "$mikey_keys" && ! -f "$mikey_keys" ]]; then
  echo "ERROR: mikey keys file not found: $mikey_keys" >&2
  exit 1
fi

# Resolve the env file now: being pointed at a file that does not exist is a
# deployment failure that is much cheaper to catch here than in the journal.
if [[ ! -f "$env_file" ]]; then
  echo "ERROR: env file does not exist: $env_file" >&2
  exit 1
fi
env_file="$(cd "$(dirname "$env_file")" && pwd -P)/$(basename "$env_file")"

if [[ -n "$hf_home" ]]; then
  if [[ "$dry_run" != "1" ]]; then
    mkdir -p "$hf_home"
  fi
  if [[ -d "$hf_home" ]]; then
    hf_home="$(cd "$hf_home" && pwd -P)"
  else
    # Dry run against a path that does not exist yet. readlink -m canonicalises
    # without creating anything, so the printed unit still matches what a real
    # run would write.
    hf_home="$(readlink -m "$hf_home")"
  fi
  hf_line="Environment=HF_HOME=$hf_home"
else
  hf_line="# Environment=HF_HOME=...   # NOT SET: each redeploy re-downloads model weights"
fi

if [[ -n "$mikey_keys" ]]; then
  mikey_keys="$(cd "$(dirname "$mikey_keys")" && pwd -P)/$(basename "$mikey_keys")"
  mikey_line="Environment=MIKEY_KEYS_FILE=$mikey_keys"
else
  mikey_line="# Environment=MIKEY_KEYS_FILE=...   # NOT SET: mikey token auth is off"
fi

if [[ -n "$krb5_ccname" ]]; then
  krb5_line="Environment=KRB5CCNAME=$krb5_ccname"
  echo "Kerberos cache for the service: $krb5_ccname" >&2
  # Warn rather than fail: the cache lives in /tmp and is recreated by whatever
  # renews the ticket, so it can legitimately be absent at install time. A
  # typo, however, is silent until the first database call.
  case "$krb5_ccname" in
    FILE:*)
      krb5_path="${krb5_ccname#FILE:}"
      if [[ ! -e "$krb5_path" ]]; then
        echo "WARNING: $krb5_path does not exist yet." >&2
        echo "         If that is not where the renewed ticket lands, the" >&2
        echo "         service will fail its first database call with" >&2
        echo "         'Ticket expired'." >&2
      fi
      ;;
  esac
else
  krb5_line="# Environment=KRB5CCNAME=...   # NOT SET: GSSAPI will use /tmp/krb5cc_<uid>"
  echo "NOTE: KRB5CCNAME is not set for this unit." >&2
  echo "      If the database connection uses GSSAPI (no DB_PASSWORD), the" >&2
  echo "      service will read libkrb5's default cache rather than the one" >&2
  echo "      your shell uses, and fail with 'Ticket expired'. Pass" >&2
  echo "      --krb5-ccname, or --no-krb5-ccname to silence this." >&2
fi

if [[ "$surface" == "mcp" ]]; then
  unit_name="kb-mcp"
  surface_flag="--only-mcp"
  bind_args="--host=$host --port=$port"
  bind_desc="$host:$port"
  : "${description:=kb-mcp (Mu2e knowledge base MCP endpoint, Postgres-backed)}"
else
  unit_name="kb-web"
  surface_flag="--only-web"
  bind_args="--web-host=$web_host --web-port=$web_port"
  bind_desc="$web_host:$web_port"
  : "${description:=kb-web (Mu2e knowledge base web UI, Postgres-backed)}"
fi

share_dir="$venv_dir/share/kb-mcp"
unit_path="$share_dir/$unit_name.service"

# NOTE: this heredoc is deliberately unquoted so the $variables below expand.
# That also makes backticks and $(...) run as commands, so keep both out of
# the unit text -- a backquoted `export KEY=VALUE` in a comment here silently
# rendered as an empty string before this note existed.
unit_content="$(cat <<UNIT
# Generated by kb-mcp-install-unit.sh -- do not hand-edit.
# Re-run that script to regenerate (safe to re-run at any time).
#
# Registered with: systemctl --user link --force $unit_path
# so ~/.config/systemd/user/kb-mcp.service is a symlink back to this file and
# the unit content stays versioned with this release.

[Unit]
Description=$description
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# Pinned so nothing resolves a relative path against the account's home
# directory, which is what a systemd --user service would otherwise use.
WorkingDirectory=$data_dir

# Non-secret settings, versioned with this release. systemd parses this as
# plain KEY=VALUE lines -- an "export " prefix will NOT work.
EnvironmentFile=$defaults_file

# Writable state: API keys and the web/OAuth session stores. DATA_DIR
# defaults to the relative "data", which would land in the account's home.
Environment=DATA_DIR=$data_dir

# Names the authoritative secrets file. Setting it also stops kb_mcp.config
# searching for a .env of its own, which it did by walking up from the
# installed module's directory -- a file left anywhere above the package would
# otherwise beat the EnvironmentFile above for every key this file does not
# itself set. It is loaded last, so a secret beats any default from
# EnvironmentFile above.
Environment=KB_ENV_FILE=$env_file
$mikey_line
$hf_line
# Kerberos credential cache. A systemd --user service does not inherit the
# installing shell's KRB5CCNAME; without this it reads libkrb5's default
# /tmp/krb5cc_<uid>, which commonly holds a stale ticket while the renewed one
# lives in a differently-named cache. The symptom is a clean startup and
# "Ticket expired" on the first database call.
$krb5_line
# Fail fast instead of hanging if the model cache is cold and the Hub is
# unreachable. Comment out for the first start, which must populate the cache.
#Environment=HF_HUB_OFFLINE=1
ExecStart=$exec_target $surface_flag $bind_args
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT
)"

if [[ "$dry_run" == "1" ]]; then
  echo "$unit_content"
  exit 0
fi

mkdir -p "$share_dir"
printf '%s\n' "$unit_content" > "$unit_path"
echo "Rendered: $unit_path"

if ! systemctl --user show-environment >/dev/null 2>&1; then
  echo "ERROR: systemctl --user is not available for this account." >&2
  echo "       The unit was rendered but not registered." >&2
  exit 1
fi

systemctl --user link --force "$unit_path"
systemctl --user daemon-reload
echo "Linked:   ~/.config/systemd/user/$unit_name.service -> $unit_path"

if [[ "$do_enable" == "1" ]]; then
  systemctl --user enable --now "$unit_name.service"
  echo "Enabled and started $unit_name.service on $bind_desc"
  echo
  echo "  systemctl --user status $unit_name"
  echo "  journalctl --user -u $unit_name -f"
  # Note: no /status route exists on an --only-mcp service; that endpoint is
  # registered on the web app. Use the smoke test to verify the MCP surface.
  echo "  <venv>/bin/python scripts/smoke_test_http.py http://localhost:$port"
else
  echo "Not enabled (--no-enable). To start:"
  echo "  systemctl --user enable --now $unit_name.service"
fi

if ! loginctl show-user "$(id -un)" -p Linger 2>/dev/null | grep -q "Linger=yes"; then
  echo
  echo "WARNING: linger is not enabled for $(id -un); the service will stop at logout." >&2
  echo "         Fix with: loginctl enable-linger" >&2
fi
