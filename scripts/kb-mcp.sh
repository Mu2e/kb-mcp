#!/usr/bin/env bash
#
# kb-mcp.sh -- launcher for the deployed Mu2e kb-mcp MCP endpoint.
#
# Installed next to `kb-server` in the venv's bin/ (see pyproject.toml,
# [tool.hatch.build.targets.wheel.shared-scripts]), so the systemd unit's
# ExecStart is a single self-contained line and any server-side environment
# setup has one obvious place to live.
#
# It forwards everything to `kb-server`, so the systemd unit decides which
# surface to run: kb-mcp.service passes --only-mcp, kb-web.service passes
# --only-web. They are separate units so the web UI can be restarted, or can
# fail, without dropping the MCP sessions agents are holding.
#
# Both units share one DATA_DIR, which is shared state rather than per-service
# scratch: api_keys.json, the session stores, and copies of every ingested
# document under sources/ and uploads/. That is safe because the credential
# files are written through an atomic rename (see kb_mcp.secure_file), and
# because the two surfaces touch different session stores -- a --only-mcp
# process mounts no web routes, so it never writes web_sessions.json.
#
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

# Hugging Face model weights (BAAI/bge-small-en-v1.5, ~130 MB) are downloaded on
# first use. HF_HOME must point somewhere persistent and SHARED ACROSS RELEASES,
# or every redeploy re-downloads them into a directory that the next deploy
# abandons. The systemd unit sets it; this default only covers a manual run.
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"

usage() {
  cat >&2 <<'USAGE'
Usage:
  kb-mcp.sh [--check] [kb-server options...]

  --check   Import the server and report resolved configuration without
            binding a port or contacting the database. Use this after an
            install, before enabling the unit.

Everything else is passed straight through to `kb-server`, e.g.:

  kb-mcp.sh --only-mcp --host 0.0.0.0 --port 8008
  kb-mcp.sh --only-web --web-host 127.0.0.1 --web-port 8108
USAGE
  exit 2
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
fi

if [[ "${1:-}" == "--check" ]]; then
  shift
  # Pass remaining args (notably --env-file) through to the check so it
  # inspects the same configuration the service would actually run with.
  KB_MCP_CHECK_ARGS=("$@")
  export KB_MCP_CHECK_ARGS_STR="${KB_MCP_CHECK_ARGS[*]-}"
  exec "$here/python" - <<'PY'
import os
import sys

# Mirror what the entry point does, using the same resolution order.
sys.argv = ["kb-mcp.sh"] + os.environ.get("KB_MCP_CHECK_ARGS_STR", "").split()

from kb_mcp.env import env_file_from_argv, resolve_env_file, load_env

failed = False

# Resolve exactly as the entry point does, including --env-file from the
# arguments forwarded above -- otherwise this reports a different file from
# the one the service would actually use.
explicit = env_file_from_argv()
env_file = resolve_env_file(explicit)
try:
    load_env(explicit)
except FileNotFoundError as exc:
    print(f"FAIL  env file: {exc}")
    sys.exit(1)
print(f"ok    env file: {env_file or '<none found>'}")

try:
    import kb_mcp.server.server as srv
except Exception as exc:  # noqa: BLE001 - report anything that breaks import
    print(f"FAIL  import kb_mcp.server.server: {exc!r}")
    sys.exit(1)
print(f"ok    import kb_mcp.server.server")
print(f"ok    MCP endpoint: {srv.MCP_HOST}:{srv.PORT}")
print(f"ok    web UI      : {srv.WEB_HOST}:{srv.WEB_PORT}")
if srv.WEB_HOST not in ("127.0.0.1", "localhost", "::1"):
    print("      NOTE: the web UI is not bound to loopback.")

from kb_mcp.config import get_server_config, get_embedding_config, get_auth_config

emb = get_embedding_config()
provider = emb.get("provider")
print(f"ok    embedding provider: {provider} (model: {emb.get('model') or 'default'})")

# The query must be embedded in the same vector space as the stored chunks, so
# a missing sentence-transformers is a hard failure at first search, not a
# warning. It is a core dependency, so this only trips on a broken install.
if provider in ("st", "sentence-transformers", "sentence_transformers"):
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        print("FAIL  provider needs sentence-transformers, which is not installed.")
        print("      It is a core dependency -- the install is incomplete.")
        failed = True
    else:
        print(f"ok    sentence-transformers present")
        try:
            import torch
            build = torch.version.cuda or "cpu"
            print(f"ok    torch {torch.__version__} (build: {build})")
        except ImportError:
            print("FAIL  sentence-transformers present but torch is not importable")
            failed = True

print(f"ok    HF_HOME: {os.environ.get('HF_HOME', '<unset>')}")

auth = get_auth_config()
print(f"ok    auth config loaded ({len(auth)} settings)")

sys.exit(1 if failed else 0)
PY
fi

exec "$here/kb-server" "$@"
