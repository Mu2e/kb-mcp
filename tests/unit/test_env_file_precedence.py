"""A pinned KB_ENV_FILE must not leave the rest of the config up for grabs.

The deployed configuration arrives in two parts: the systemd unit puts the
non-secret policy in the process environment (EnvironmentFile= and
Environment=), and KB_ENV_FILE names the private file holding the secrets.
kb_mcp.config also used to *discover* a .env by walking up from its own
directory and load it with override=True, which beat the unit for every key
the private file did not itself set.

These run in subprocesses with a copied package tree, because the discovery
walks up from the module's location rather than the working directory -- the
behaviour cannot be exercised in-process from inside the real checkout.
"""

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "src" / "kb_mcp"

PROBE = textwrap.dedent(
    """
    from kb_mcp.config import get_server_config
    c = get_server_config()
    print(f"port={c['port']} host={c['mcp_host']}")
    """
)


@pytest.fixture(scope="module")
def staged_package(tmp_path_factory):
    """A copy of the package with a stray .env sitting above it."""
    root = tmp_path_factory.mktemp("stray")
    shutil.copytree(
        PACKAGE,
        root / "pkg" / "kb_mcp",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    # The stray file: what a leftover checkout .env, or one dropped in the
    # deploy root, looks like from the installed package's point of view.
    (root / ".env").write_text("PORT=8443\nMCP_HOST=127.0.0.1\nKB_STRAY_MARKER=leaked\nKB_OVERRIDE_PROBE=from-env\n")
    (root / ".env.local").write_text("KB_OVERRIDE_PROBE=from-local\n")
    return root


def _run(staged, env_extra, probe=PROBE):
    env = {
        k: v
        for k, v in os.environ.items()
        # Drop anything that would reach the probe by another route.
        if k not in {"PORT", "MCP_HOST", "KB_ENV_FILE"}
    }
    env["PYTHONPATH"] = str(staged / "pkg")
    env.update(env_extra)
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(staged),
        env=env,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_pinned_env_file_leaves_the_unit_settings_alone(staged_package, tmp_path):
    """The deployment case: KB_ENV_FILE set, and a stray .env above the package.

    PORT and MCP_HOST here stand in for the unit's EnvironmentFile. The
    private file deliberately does not mention them, which is the whole point:
    it is where the secrets live, not the ports.
    """
    private = tmp_path / "kb-mcp.env"
    private.write_text("DB_HOST=db.example.internal\n")

    out = _run(
        staged_package,
        {"KB_ENV_FILE": str(private), "PORT": "8008", "MCP_HOST": "0.0.0.0"},
    )
    assert out == "port=8008 host=0.0.0.0", (
        "a stray .env beat the settings the unit supplied"
    )


def test_pinned_env_file_still_wins_over_the_environment(staged_package, tmp_path):
    """Secrets in the pinned file still override the ambient environment."""
    private = tmp_path / "kb-mcp.env"
    private.write_text("PORT=9001\n")

    out = _run(staged_package, {"KB_ENV_FILE": str(private), "PORT": "8008"})
    assert out.startswith("port=9001")


def test_without_a_pinned_file_discovery_still_works(staged_package):
    """The checkout convenience is unchanged: no KB_ENV_FILE, .env is found."""
    out = _run(staged_package, {"PORT": "8008", "MCP_HOST": "0.0.0.0"})
    assert out == "port=8443 host=127.0.0.1"


# Modules that used to call a bare load_dotenv() at import time, which found
# the stray .env and filled in every key the pinned file left unset.
LEAK_PROBE = textwrap.dedent(
    """
    import os
    import kb_mcp.kb
    import kb_mcp.imports.cli
    import kb_mcp.imports.inspire
    import kb_mcp.parser.parser_azure
    print(os.environ.get("KB_STRAY_MARKER"))
    """
)


def test_pinned_env_file_is_not_topped_up_by_module_imports(staged_package, tmp_path):
    """Importing the package must not load the stray .env behind KB_ENV_FILE."""
    private = tmp_path / "kb-mcp.env"
    private.write_text("DB_HOST=db.example.internal\n")

    out = _run(staged_package, {"KB_ENV_FILE": str(private)}, probe=LEAK_PROBE)
    assert out.splitlines()[-1] == "None", "a module import loaded the stray .env"


# The start of every entry point that uses kb_mcp.env (kb-server,
# kb-server-stdio, kb-agent), followed by the config import.
ENTRY_POINT_PROBE = textwrap.dedent(
    """
    import os
    from kb_mcp.env import load_env
    load_env()
    import kb_mcp.config
    print(os.environ.get("KB_OVERRIDE_PROBE"))
    """
)


def test_entry_point_keeps_env_local_overrides_for_a_discovered_env(staged_package):
    """A discovered .env must not be promoted to pinned, which would beat .env.local."""
    out = _run(staged_package, {}, probe=ENTRY_POINT_PROBE)
    assert out.splitlines()[-1] == "from-local"
