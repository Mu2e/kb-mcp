#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  kb-mcp-install-unit.sh --port <port> --env-file <path> [options]

Renders a systemd --user unit for this release and registers it.

The unit is written into THIS release's own share/kb-mcp/kb-mcp.service and
registered with `systemctl --user link --force`, which puts a *symlink* in
~/.config/systemd/user pointing back here. The real unit content therefore
stays with the installed code: nothing to hand-edit, nothing to copy, and
rolling back is repointing <deploy-root>/current and restarting.

Options:
  --port <port>       Port for the MCP endpoint (required). Mu2e convention
                      reserves 8000-8009; kb is 8008.
  --env-file <path>   Env file with database credentials and settings
                      (required). Passed as KB_ENV_FILE so it wins over any
                      stray .env -- see "Why KB_ENV_FILE" below.
  --hf-home <path>    Persistent Hugging Face cache shared across releases.
                      Strongly recommended: without it each redeploy
                      re-downloads ~130 MB of model weights.
  --data-dir <path>   Writable state: API keys, web/OAuth session stores.
                      Defaults to <deploy-root>/data, i.e. beside releases/
                      so it survives a redeploy. MUST be absolute -- see
                      "Why DATA_DIR" below.
  --mikey-keys <path> Shared mikey key file for bearer-token auth. Optional;
                      without it mikey auth stays off.
  --defaults <path>   Non-secret settings file, as systemd EnvironmentFile.
                      Defaults to this release's own
                      share/kb-mcp/mu2e.env (shipped with the package).
  --host <addr>       Bind address (default: 0.0.0.0).
  --deploy-root <p>   Deploy root holding releases/ and the `current` symlink.
                      Auto-detected from this script's location; override only
                      for a non-standard layout. ExecStart is written against
                      <deploy-root>/current so that rolling back is repointing
                      that symlink and restarting -- no re-render needed.
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
  kb_mcp.config calls load_dotenv(override=True), so values from a .env file
  beat variables already in the environment. If the service ever resolves a
  stray .env (find_dotenv walks up from the working directory), that file
  would silently override anything set with Environment=/EnvironmentFile=.
  Naming the file through KB_ENV_FILE pins exactly which file is
  authoritative. Secrets live in that file, not in the unit: keep it mode 600
  and owned by the service account.

Requires linger so the service survives logout (once per account, permanent):
  loginctl enable-linger

Example:
  kb-mcp-install-unit.sh --port 8008 \
    --env-file /exp/mu2e/app/users/mu2eai/mcp/kb/config/kb-mcp.env \
    --hf-home  /exp/mu2e/app/users/mu2eai/mcp/kb/cache/huggingface
USAGE
  exit 2
}

port=""
env_file=""
hf_home=""
data_dir=""
mikey_keys=""
defaults_file=""
host="0.0.0.0"
deploy_root=""
description="kb-mcp (Mu2e knowledge base MCP server, Postgres-backed)"
do_enable=1
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)        port="${2:-}"; shift 2 ;;
    --env-file)    env_file="${2:-}"; shift 2 ;;
    --hf-home)     hf_home="${2:-}"; shift 2 ;;
    --data-dir)    data_dir="${2:-}"; shift 2 ;;
    --mikey-keys)  mikey_keys="${2:-}"; shift 2 ;;
    --defaults)    defaults_file="${2:-}"; shift 2 ;;
    --host)        host="${2:-}"; shift 2 ;;
    --deploy-root) deploy_root="${2:-}"; shift 2 ;;
    --description) description="${2:-}"; shift 2 ;;
    --no-enable)   do_enable=0; shift ;;
    --dry-run)     dry_run=1; shift ;;
    --help|-h)     usage ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage ;;
  esac
done

[[ -n "$port" ]]     || { echo "ERROR: --port is required" >&2; usage; }
[[ -n "$env_file" ]] || { echo "ERROR: --env-file is required" >&2; usage; }

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
if [[ -z "$data_dir" ]]; then
  if [[ -n "$deploy_root" ]]; then
    data_dir="$deploy_root/data"
  else
    echo "ERROR: --data-dir is required when no deploy-root layout is detected." >&2
    exit 1
  fi
fi
case "$data_dir" in
  /*) : ;;
  *) echo "ERROR: --data-dir must be an absolute path (got: $data_dir)" >&2; exit 1 ;;
esac
mkdir -p "$data_dir"

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
  mkdir -p "$hf_home"
  hf_home="$(cd "$hf_home" && pwd -P)"
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

share_dir="$venv_dir/share/kb-mcp"
unit_path="$share_dir/kb-mcp.service"

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

# Names the authoritative secrets file. kb_mcp.config loads it with
# override=True, so this must be set explicitly rather than relying on a .env
# being found relative to the working directory. It is loaded last, so a
# secret beats any default from EnvironmentFile above.
Environment=KB_ENV_FILE=$env_file
$mikey_line
$hf_line
# Fail fast instead of hanging if the model cache is cold and the Hub is
# unreachable. Comment out for the first start, which must populate the cache.
#Environment=HF_HUB_OFFLINE=1
ExecStart=$exec_target --host=$host --port=$port
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
echo "Linked:   ~/.config/systemd/user/kb-mcp.service -> $unit_path"

if [[ "$do_enable" == "1" ]]; then
  systemctl --user enable --now kb-mcp.service
  echo "Enabled and started kb-mcp.service"
  echo
  echo "  systemctl --user status kb-mcp"
  echo "  journalctl --user -u kb-mcp -f"
  # Note: no /status route exists on an --only-mcp service; that endpoint is
  # registered on the web app. Use the smoke test to verify the MCP surface.
  echo "  <venv>/bin/python scripts/smoke_test_http.py http://localhost:$port"
else
  echo "Not enabled (--no-enable). To start:"
  echo "  systemctl --user enable --now kb-mcp.service"
fi

if ! loginctl show-user "$(id -un)" -p Linger 2>/dev/null | grep -q "Linger=yes"; then
  echo
  echo "WARNING: linger is not enabled for $(id -un); the service will stop at logout." >&2
  echo "         Fix with: loginctl enable-linger" >&2
fi
