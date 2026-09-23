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
  kb-mcp.sh [--check [--defaults <path>|--no-defaults]] [kb-server options...]

  --check   Report the configuration the service would run with, without
            binding a port or contacting the database. Use this after an
            install, before enabling the unit.

            The deployed configuration is TWO files, combined by the unit:

              EnvironmentFile=<release>/share/kb-mcp/mu2e.env   non-secret
                  policy -- ports, bind addresses, auth mode, embedding model
              Environment=KB_ENV_FILE=<...>/kb-mcp.env          secrets and
                  site topology -- DB_*, the LLM endpoint, ADMIN_PASSWORD

            --check loads both, in that order, with the private file last and
            overriding, exactly as kb_mcp.env does at runtime. Checking with
            only one of them reports code defaults for everything the other
            file sets -- which looks like a healthy result rather than an
            unchecked one.

  --defaults <path>   Non-secret defaults to load first. Defaults to this
                      release's share/kb-mcp/mu2e.env, which is the file the
                      unit names.
  --no-defaults       Skip that layer and report code defaults.

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
  # Newline-separated rather than space-joined: a deploy path containing a
  # space would otherwise arrive as two arguments.
  if [[ $# -gt 0 ]]; then
    KB_MCP_CHECK_ARGS_STR="$(printf '%s\n' "$@")"
  else
    KB_MCP_CHECK_ARGS_STR=""
  fi
  export KB_MCP_CHECK_ARGS_STR
  # The unit loads this as EnvironmentFile before KB_ENV_FILE. The check has
  # to load it too, or it reports code defaults for every value it sets --
  # ports, bind addresses, auth mode, embedding model.
  export KB_MCP_DEFAULTS_FILE="${KB_MCP_DEFAULTS_FILE:-$here/../share/kb-mcp/mu2e.env}"
  exec "$here/python" - <<'PY'
import os
import sys
from pathlib import Path

# Mirror what the entry point does, using the same resolution order.
sys.argv = ["kb-mcp.sh"] + os.environ.get("KB_MCP_CHECK_ARGS_STR", "").splitlines()

from dotenv import dotenv_values, load_dotenv

from kb_mcp.env import env_file_from_argv, resolve_env_file, load_env

failed = False


def fail(message):
    global failed
    failed = True
    print(f"FAIL  {message}")


def note(*lines):
    for line in lines:
        print(f"      {line}")


def _flag(name):
    return name in sys.argv[1:]


def _opt(name):
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == name:
            return args[i + 1] if i + 1 < len(args) else None
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


# --- layer 1: the defaults the unit loads as EnvironmentFile ---------------
# override=True so that an exported PORT in the operator's shell does not make
# the check disagree with the service. The private file is loaded after this
# and also overrides, so the precedence order matches the unit exactly:
# defaults -> unit Environment= -> KB_ENV_FILE.
defaults_explicit = _opt("--defaults")
defaults = defaults_explicit or os.environ.get("KB_MCP_DEFAULTS_FILE")

if _flag("--no-defaults"):
    defaults = None
    print("ok    defaults: <skipped>")
    note("Ports, bind addresses and the embedding model below are code",
         "defaults, not what the unit loads.")
elif defaults and Path(defaults).is_file():
    defaults = Path(defaults).resolve()
    load_dotenv(dotenv_path=str(defaults), override=True)
    print(f"ok    defaults: {defaults}")
elif defaults_explicit:
    fail(f"defaults file not found: {defaults_explicit}")
    sys.exit(1)
else:
    note(f"NOTE: no defaults file at {defaults}.",
         "This release may predate it. Ports, bind addresses and the",
         "embedding model below are code defaults, not the unit's.")
    defaults = None

# --- layer 2: the private file named by KB_ENV_FILE ------------------------
explicit = env_file_from_argv()
env_file = resolve_env_file(explicit)
try:
    load_env(explicit)
except FileNotFoundError as exc:
    print(f"FAIL  env file: {exc}")
    sys.exit(1)
print(f"ok    env file: {env_file or '<none found>'}")

# Being handed the shipped defaults as --env-file passes every check below
# while reporting a configuration that contains no secrets at all, so name it.
if env_file:
    resolved = Path(env_file).resolve() if Path(env_file).exists() else Path(env_file)
    in_release = "share/kb-mcp" in resolved.as_posix()
    if (defaults and resolved == defaults) or in_release:
        fail("--env-file points at the shipped defaults, not the private file.")
        note("The private file is the one the unit names with",
             "Environment=KB_ENV_FILE (mode 600, outside git). It holds DB_*,",
             "the LLM endpoint and ADMIN_PASSWORD; the defaults file holds",
             "none of them, so everything below would be a code default.")

try:
    import kb_mcp.server.server as srv
except Exception as exc:  # noqa: BLE001 - report anything that breaks import
    print(f"FAIL  import kb_mcp.server.server: {exc!r}")
    sys.exit(1)
print(f"ok    import kb_mcp.server.server")
print(f"ok    MCP endpoint: {srv.MCP_HOST}:{srv.PORT}")
print(f"ok    web UI      : {srv.WEB_HOST}:{srv.WEB_PORT}")
if srv.WEB_HOST not in ("127.0.0.1", "localhost", "::1"):
    note("NOTE: the web UI is not bound to loopback.")

from kb_mcp.config import get_server_config, get_embedding_config, get_auth_config

emb = get_embedding_config()
provider = emb.get("provider")
model = emb.get("model")
print(f"ok    embedding provider: {provider} (model: {model or '<unset>'})")
if not model:
    note("NOTE: EMBEDDING_MODEL is unset, so the code default applies. It",
         "must match what the indexed chunks were embedded with -- the",
         "query is embedded at read time and compared in the same vector",
         "space, so a mismatch returns plausible-looking nonsense rather",
         "than an error.")

# The query must be embedded in the same vector space as the stored chunks, so
# a missing sentence-transformers is a hard failure at first search, not a
# warning. It is a core dependency, so this only trips on a broken install.
if provider in ("st", "sentence-transformers", "sentence_transformers"):
    try:
        import sentence_transformers  # noqa: F401
    except ImportError as exc:
        # Missing and broken need different fixes. A transformers version that
        # does not match sentence-transformers raises ModuleNotFoundError from
        # deep inside the import, and reporting that as "not installed" sends
        # the operator off to reinstall a package that is already there.
        import importlib.util

        try:
            installed = importlib.util.find_spec("sentence_transformers") is not None
        except Exception:  # noqa: BLE001 - a broken package can raise here too
            installed = True
        if installed:
            fail(f"sentence-transformers is installed but fails to import: {exc}")
            note("Usually a transformers/sentence-transformers version mismatch",
                 "in this environment. Queries cannot be embedded, so every",
                 "search fails at read time.")
        else:
            fail("sentence-transformers is not installed.")
            note("It is a core dependency -- the default embedding provider --",
                 "so the install is incomplete.")
    else:
        print(f"ok    sentence-transformers present")
        try:
            import torch
            build = torch.version.cuda or "cpu"
            print(f"ok    torch {torch.__version__} (build: {build})")
        except ImportError:
            fail("sentence-transformers present but torch is not importable")

# --- what the private file actually carries --------------------------------
# Names and presence only, never values: this output lands in terminals,
# journals and tickets.
file_values = {}
if env_file and Path(env_file).is_file():
    try:
        file_values = dotenv_values(str(env_file))
    except Exception:  # noqa: BLE001 - a malformed file should not crash here
        note("NOTE: could not parse the env file for a key inventory.")


def present(key):
    value = file_values.get(key)
    if value is None:
        value = os.environ.get(key)
    return bool(value and str(value).strip())


missing = [k for k in ("DB_HOST", "DB_NAME", "DB_USER") if not present(k)]
if missing:
    fail("database: " + ", ".join(missing) + " not set")
    note("The server starts anyway -- the connection and the embedder are",
         "both resolved lazily on the first search, and kb_search reports a",
         'broken database as {"message": "No results found", "results": []}',
         "rather than as an error. This is the check that catches it.")
else:
    print("ok    database: DB_HOST, DB_NAME, DB_USER set")

for key, fallback in (("DB_PORT", "5432"), ("DB_SCHEMA", "public")):
    if not present(key):
        note(f"NOTE: {key} unset -- falling back to {fallback}.")

if present("DB_PASSWORD"):
    note("NOTE: DB_PASSWORD is set. This deployment authenticates with",
         "Kerberos/GSSAPI from the service account, and get_database_url()",
         "only builds a password-less URL when DB_PASSWORD is unset.")

if present("OPENAI_BASE_URL"):
    print("ok    LLM endpoint: OPENAI_BASE_URL set")
else:
    fail("LLM endpoint: OPENAI_BASE_URL not set")
    note("kb_research, summaries and graph extraction need it.")
if not present("OPENAI_API_KEY"):
    note("NOTE: OPENAI_API_KEY unset.")

auth = get_auth_config()
admin_gate = bool(auth['admin_password'] or auth['admin_password_hash'])
if auth['web_require_auth']:
    print("ok    web UI auth: login required (WEB_REQUIRE_AUTH=true)")
elif not auth['web_public_mode']:
    fail("web UI auth: WEB_REQUIRE_AUTH=false with WEB_PUBLIC_MODE=false")
    note("/login mints a session carrying admin rights with no password and",
         "no check, so anyone who reaches the port gets uploads, deletes,",
         "re-chunking and API-key management. Set WEB_PUBLIC_MODE=true.")
elif not admin_gate:
    fail("web UI auth: public mode with no ADMIN_PASSWORD")
    note("With no password configured is_admin_unlocked() returns True and",
         "require_admin() lets every write route through. Uploads, deletes,",
         "re-chunking and API-key management are reachable by anyone who can",
         "reach the port -- on a shared host that is any account, through an",
         "ssh tunnel to loopback.")
else:
    print("ok    web UI auth: public mode, writes behind ADMIN_PASSWORD")
print(f"ok    auth config loaded ({len(auth)} settings)")

# --- paths the unit supplies with Environment= -----------------------------
# Unset is not a failure here: kb-mcp-install-unit.sh sets all of these, and a
# manual check runs without them. It is reported because the consequences are
# silent ones.
data_dir = os.environ.get("DATA_DIR")
if data_dir:
    print(f"ok    DATA_DIR: {data_dir}")
else:
    note("NOTE: DATA_DIR unset -- the unit sets it. Unset, it defaults to the",
         "relative \"data\", and a systemd --user service inherits the",
         "account's home as its working directory, so API keys and session",
         "stores would land in ~/data.")

hf_home = os.environ.get("HF_HOME")
print(f"ok    HF_HOME: {hf_home or '<unset>'}")
home = os.path.expanduser("~")
if hf_home and Path(hf_home).as_posix().startswith(Path(home).as_posix()):
    note("NOTE: this is the account's home cache, not the persistent path the",
         "unit passes with --hf-home. Harmless for a manual check.")

mikey = os.environ.get("MIKEY_KEYS_FILE")
if mikey:
    print(f"ok    MIKEY_KEYS_FILE: {mikey}")
    if not Path(mikey).is_file():
        fail(f"mikey keys file does not exist: {mikey}")
else:
    note("NOTE: MIKEY_KEYS_FILE unset -- the unit sets it from --mikey-keys.",
         "Unset, mikey token auth is off.")

sys.exit(1 if failed else 0)
PY
fi

exec "$here/kb-server" "$@"
