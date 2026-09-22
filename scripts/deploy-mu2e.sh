#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  deploy-mu2e.sh <deploy-root> <ref> [repo-url]

Installs kb-mcp into <deploy-root>/releases/<ref>/.venv using uv, pinned to the
given git ref (tag/branch/commit), following the Mu2e aitools MCP deployment
pattern (see Mu2e/aitools mcp/registry/README.md):

  uv venv <deploy-root>/releases/<ref>/.venv
  uv pip install --index-url <torch-index> torch        # CPU-only build
  uv pip install "kb-mcp[<extras>] @ git+<repo-url>@<ref>"   # extras optional

No source tree is copied -- uv fetches the pinned ref itself. The install
produces everything needed to run the server:

  <...>/.venv/bin/kb-server                  (python entry point)
  <...>/.venv/bin/kb-mcp.sh                  (wrapper: --check, execs kb-server
                                               --only-mcp)
  <...>/.venv/bin/kb-mcp-install-unit.sh     (renders + links the systemd --user
                                               unit)

<deploy-root>/current is symlinked to the new release. This script does not
touch systemd -- run the printed kb-mcp-install-unit.sh command when ready.

Why torch is installed separately and first:
  sentence-transformers is a core dependency (it is the default embedding
  provider), and its torch dependency resolves to the CUDA build on PyPI --
  multi-GB, and useless here: embedding one query at a time is CPU work, and
  embedders.py picks the device via torch.cuda.is_available(). Installing the
  +cpu build from the PyTorch index first leaves torch already satisfied, so
  the main install does not pull CUDA wheels. Newer uv has --torch-backend=cpu
  for this, but the uv from `slc uv` may predate it, so do it the portable way.

Sizes to expect: ~1.3 GB per release venv (torch alone is ~730 MB). Releases
accumulate under releases/, so prune old ones rather than letting them pile up.

Environment overrides:
  KB_MCP_EXTRAS       extras to install (default: none; e.g. "ingest" to also
                      install the parser stack for ingestion on this host)
  KB_MCP_TORCH_INDEX  PyTorch index (default: CPU wheels)
  KB_MCP_SKIP_TORCH   set to 1 to skip the separate torch step entirely
                      (only sensible if torch is already present)

Examples:
  ./scripts/deploy-mu2e.sh /exp/mu2e/app/home/mu2eai/mcp/deploy/kb v0.2.0
  KB_MCP_EXTRAS=ingest ./scripts/deploy-mu2e.sh /path/to/deploy/kb main

Notes:
  - Run as the account that will own the systemd --user service (e.g. mu2eai).
  - Requires `uv` on PATH: `mu2einit && slc uv` on Mu2e machines.
  - The knowledge base itself is NOT created or migrated here; this installs
    and runs a server against an existing database.
USAGE
  exit 2
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
fi

if [[ $# -lt 2 || $# -gt 3 ]]; then
  usage
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found on PATH (try: mu2einit && slc uv)" >&2
  exit 2
fi

deploy_root="$1"
ref="$2"
repo_url="${3:-https://github.com/Mu2e/kb-mcp}"

extras="${KB_MCP_EXTRAS-}"
torch_index="${KB_MCP_TORCH_INDEX:-https://download.pytorch.org/whl/cpu}"

release_dir="$deploy_root/releases/$ref"
current_link="$deploy_root/current"
venv_dir="$release_dir/.venv"

if [[ -n "$extras" ]]; then
  spec="kb-mcp[$extras] @ git+${repo_url}@${ref}"
else
  spec="kb-mcp @ git+${repo_url}@${ref}"
fi

echo "[1/4] Creating venv: $venv_dir"
mkdir -p "$release_dir"
uv venv "$venv_dir"

if [[ "${KB_MCP_SKIP_TORCH:-0}" == "1" ]]; then
  echo "[2/4] Skipping separate torch install"
else
  echo "[2/4] Installing CPU-only torch from $torch_index"
  uv pip install --python "$venv_dir/bin/python" --index-url "$torch_index" torch
fi

echo "[3/4] Installing ${spec}"
uv pip install --python "$venv_dir/bin/python" "$spec"

echo "[4/4] Pointing $current_link at this release"
ln -sfn "$release_dir" "$current_link"

# Fail loudly here rather than at first search: a CUDA torch in the venv means
# the resolution above went wrong and the release is several GB larger than it
# should be.
if [[ "${KB_MCP_SKIP_TORCH:-0}" != "1" ]]; then
  cuda_ver="$("$venv_dir/bin/python" -c 'import torch; print(torch.version.cuda or "")' 2>/dev/null || true)"
  if [[ -n "$cuda_ver" ]]; then
    echo "WARNING: torch was installed with CUDA $cuda_ver, not the +cpu build." >&2
    echo "         The release will be several GB larger than necessary." >&2
  fi
fi

echo
echo "Done."
echo "Current release: $current_link -> $release_dir"
echo "Release size:    $(du -sh "$release_dir" 2>/dev/null | cut -f1)"
echo
echo "Next steps:"
echo "  1. $venv_dir/bin/kb-mcp.sh --check --env-file /path/to/kb-mcp.env"
echo "  2. $venv_dir/bin/kb-mcp-install-unit.sh \\"
echo "       --port 8008 --env-file /path/to/kb-mcp.env --hf-home /path/to/persistent/hf"
echo "     (pass --no-enable to render + link without starting)"
