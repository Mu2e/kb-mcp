#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  kb-docdb-install-timer.sh --env-file <path> [options]

Renders a systemd --user service + timer that runs this release's
kb-docdb-update.sh (the incremental Mu2e DocDB import) on a schedule, and
registers both, the same way kb-mcp-install-unit.sh does for the servers: the
units are written into this release's share/kb-mcp/ and linked into
~/.config/systemd/user, and ExecStart points at <deploy-root>/current, so an
upgrade or rollback is repointing `current`.

Run it as the account whose DocDB and database credentials the import uses.
The release needs the ingest extras:
  KB_MCP_EXTRAS=ingest,docling,alcf deploy-mu2e.sh <deploy-root> <tag>

Options:
  --env-file <path>     REQUIRED. The pinned env file (KB_ENV_FILE), normally
                        <deploy-root>/config/kb-mcp.env. The job runs from its
                        directory, which also holds .env.local (DocDB login,
                        ALCF token), the optional credentials.local.sh hook and
                        inference_auth_token.py. The env file must not set
                        OPENAI_API_KEY/OPENAI_BASE_URL: it beats .env.local,
                        where the ALCF refresh writes them.
  --data-dir <path>     Logs and caches (KB_DATA_DIR).
                        Default: /exp/mu2e/data/users/$USER/kb-mcp-data
  --hf-home <path>      Hugging Face cache. Default: <data-dir>/huggingface_cache
  --alcf-home <path>    Stands in for $HOME for the ALCF/Globus login, keeping
                        it off the home area. Default: <data-dir>/alcf
  --on-calendar <spec>  systemd OnCalendar. Default: "*-*-* 06,18:00:00"
  --days <n>            Look-back window per run (DAYS). Default: 1
  --defaults <path>     Non-secret settings (EnvironmentFile), shared with the
                        servers so ingest embeds exactly as they search.
                        Default: this release's share/kb-mcp/mu2e.env
  --deploy-root <p>     Auto-detected from <root>/releases/<ref>/.venv/bin.
  --no-enable           Render and link, but do not enable the timer.
  --dry-run             Print both units, then exit.

Requires linger so the timer runs while you are logged out:
  loginctl enable-linger

Requires a home directory on the node: user units can only live under
~/.config/systemd/user. Where there is none (e.g. /nashome not mounted), use
cron from the release instead; see scripts/kb_docdb.crontab.

Example:
  kb-docdb-install-timer.sh --env-file /exp/mu2e/app/users/$USER/mcp/kb/config/kb-mcp.env
USAGE
  exit 2
}

env_file=""
data_dir="/exp/mu2e/data/users/$USER/kb-mcp-data"
hf_home=""
alcf_home=""
on_calendar="*-*-* 06,18:00:00"
days="1"
defaults_file=""
deploy_root=""
do_enable=1
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)    env_file="${2:-}"; shift 2 ;;
    --data-dir)    data_dir="${2:-}"; shift 2 ;;
    --hf-home)     hf_home="${2:-}"; shift 2 ;;
    --alcf-home)   alcf_home="${2:-}"; shift 2 ;;
    --on-calendar) on_calendar="${2:-}"; shift 2 ;;
    --days)        days="${2:-}"; shift 2 ;;
    --defaults)    defaults_file="${2:-}"; shift 2 ;;
    --deploy-root) deploy_root="${2:-}"; shift 2 ;;
    --no-enable)   do_enable=0; shift ;;
    --dry-run)     dry_run=1; shift ;;
    --help|-h)     usage ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage ;;
  esac
done

[[ -n "$env_file" ]] || { echo "ERROR: --env-file is required" >&2; usage; }
if [[ ! -d "$HOME" ]]; then
  echo "ERROR: home directory $HOME does not exist on this node, and systemd user" >&2
  echo "       units can only be registered under ~/.config/systemd/user." >&2
  echo "       Use cron from the release instead; see scripts/kb_docdb.crontab." >&2
  exit 1
fi
[[ -f "$env_file" ]] || { echo "ERROR: env file does not exist: $env_file" >&2; exit 1; }
env_file="$(cd "$(dirname "$env_file")" && pwd -P)/$(basename "$env_file")"
config_dir="$(dirname "$env_file")"
[[ "$days" =~ ^[1-9][0-9]*$ ]] || { echo "ERROR: --days must be a positive integer" >&2; exit 1; }
case "$data_dir" in
  /*) : ;;
  *) echo "ERROR: --data-dir must be an absolute path (got: $data_dir)" >&2; exit 1 ;;
esac
: "${hf_home:=$data_dir/huggingface_cache}"
: "${alcf_home:=$data_dir/alcf}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"   # <release>/.venv/bin
venv_dir="$(dirname "$here")"
job="$here/kb-docdb-update.sh"
[[ -x "$job" ]] || { echo "ERROR: job script not found or not executable: $job" >&2; exit 1; }

# Same rule as kb-mcp-install-unit.sh: run <deploy-root>/current, so a rollback
# is repointing the symlink rather than re-rendering the units.
if [[ -z "$deploy_root" && "$here" == */releases/*/.venv/bin ]]; then
  deploy_root="${here%/releases/*}"
fi
if [[ -n "$deploy_root" ]]; then
  exec_target="$deploy_root/current/.venv/bin/kb-docdb-update.sh"
  [[ -x "$exec_target" ]] || {
    echo "ERROR: $exec_target is not executable. Does 'current' point at a release?" >&2
    exit 1
  }
  if [[ "$(readlink -f "$exec_target")" != "$(readlink -f "$job")" ]]; then
    echo "WARNING: 'current' does not point at the release this script came from." >&2
    echo "         ExecStart will run: $(readlink -f "$exec_target")" >&2
  fi
else
  exec_target="$job"
  echo "NOTE: no deploy-root layout detected; ExecStart pinned to this venv." >&2
fi

: "${defaults_file:=$venv_dir/share/kb-mcp/mu2e.env}"
[[ -f "$defaults_file" ]] || { echo "ERROR: defaults file not found: $defaults_file" >&2; exit 1; }

# Problems that would otherwise surface only in the first run's log.
if grep -qE '^[[:space:]]*(export[[:space:]]+)?OPENAI_(API_KEY|BASE_URL)=' "$env_file"; then
  echo "WARNING: $env_file sets OPENAI_API_KEY/OPENAI_BASE_URL; they would override" >&2
  echo "         the refreshed ALCF token. Keep them in $config_dir/.env.local only." >&2
fi
env_keys() {
  grep -oE '^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=' "$1" 2>/dev/null \
    | sed -E 's/^[[:space:]]*(export[[:space:]]+)?//; s/=$//' | sort -u
}
shared_keys="$(comm -12 <(env_keys "$env_file") <(env_keys "$config_dir/.env.local") | tr '\n' ' ')"
if [[ -n "$shared_keys" ]]; then
  echo "WARNING: set in both $env_file and .env.local; the pinned file wins, so" >&2
  echo "         the .env.local values are ignored: $shared_keys" >&2
fi
for f in "$env_file" "$config_dir/.env.local"; do
  if [[ -e "$f" && -n "$(find "$f" -perm /077)" ]]; then
    echo "WARNING: $f is readable or writable by others; chmod 600 it." >&2
  fi
done
if [[ ! -f "$config_dir/inference_auth_token.py" ]]; then
  echo "NOTE: no $config_dir/inference_auth_token.py: image descriptions will be" >&2
  echo "      placeholders until the ALCF login is set up (see the end of this output)." >&2
fi

share_dir="$venv_dir/share/kb-mcp"
service_path="$share_dir/kb-docdb-update.service"
timer_path="$share_dir/kb-docdb-update.timer"

# Unquoted heredocs so the $variables expand: keep backticks and $(...) out.
service_content="$(cat <<UNIT
# Generated by kb-docdb-install-timer.sh -- do not hand-edit; re-run it instead.
# Triggered by kb-docdb-update.timer. Run once by hand with:
#   systemctl --user start kb-docdb-update.service

[Unit]
Description=kb-mcp incremental Mu2e DocDB import
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
# The env file's directory: .env.local, credentials.local.sh and
# inference_auth_token.py are found there.
WorkingDirectory=$config_dir
# Shared with the servers, so documents are chunked and embedded exactly the
# way the server embeds queries. Plain KEY=VALUE lines, no "export".
EnvironmentFile=$defaults_file
Environment=KB_ENV_FILE=$env_file
Environment=KB_DATA_DIR=$data_dir
Environment=HF_HOME=$hf_home
Environment=KB_ALCF_HOME=$alcf_home
Environment=KB_RUN_TRIGGER=systemd-timer
Environment=DAYS=$days
ExecStart=$exec_target
# A normal run takes minutes to an hour; this only stops a hung one from
# blocking every later run (the timer does not start a unit that is active).
TimeoutStartSec=12h
# Shared interactive node: yield to people.
Nice=10
IOSchedulingClass=idle
UNIT
)"

timer_content="$(cat <<UNIT
# Generated by kb-docdb-install-timer.sh -- do not hand-edit; re-run it instead.

[Unit]
Description=Run the kb-mcp DocDB import on a schedule

[Timer]
OnCalendar=$on_calendar
# Catch up on a run missed while the node was down.
Persistent=true

[Install]
WantedBy=timers.target
UNIT
)"

if [[ "$dry_run" == "1" ]]; then
  echo "### $service_path"; echo "$service_content"; echo
  echo "### $timer_path";   echo "$timer_content"
  exit 0
fi

mkdir -p "$data_dir" "$hf_home" "$alcf_home"
chmod 700 "$alcf_home"
mkdir -p "$share_dir"
printf '%s\n' "$service_content" > "$service_path"
printf '%s\n' "$timer_content" > "$timer_path"
echo "Rendered: $service_path"
echo "Rendered: $timer_path"

if ! systemctl --user show-environment >/dev/null 2>&1; then
  echo "ERROR: systemctl --user is not available for this account; units rendered but not registered." >&2
  exit 1
fi
systemctl --user link --force "$service_path" "$timer_path"
systemctl --user daemon-reload

if [[ "$do_enable" == "1" ]]; then
  systemctl --user enable --now kb-docdb-update.timer
  echo "Timer enabled:"
  systemctl --user list-timers kb-docdb-update.timer --no-pager || true
else
  echo "Not enabled (--no-enable). To enable: systemctl --user enable --now kb-docdb-update.timer"
fi

cat <<EOF

Run once now:  systemctl --user start kb-docdb-update.service
Status:        systemctl --user status kb-docdb-update.service
Logs:          $data_dir/logs/docdb-update-*.log  (and journalctl --user -u kb-docdb-update)
EOF

if [[ ! -f "$alcf_home/.globus/app/58fdd3bc-e1c3-4ce5-80ea-8d6b87cfb944/inference_app/tokens.json" ]]; then
  cat <<EOF

ALCF login (once, interactive, for image descriptions):
  cd $config_dir
  curl -O https://raw.githubusercontent.com/argonne-lcf/inference-endpoints/refs/heads/main/inference_auth_token.py
  HOME=$alcf_home $venv_dir/bin/python inference_auth_token.py authenticate
EOF
fi

if ! loginctl show-user "$(id -un)" -p Linger 2>/dev/null | grep -q "Linger=yes"; then
  echo
  echo "WARNING: linger is not enabled for $(id -un); the timer stops at logout." >&2
  echo "         Fix with: loginctl enable-linger" >&2
fi
