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
    (root / ".env").write_text("PORT=8443\nMCP_HOST=127.0.0.1\n")
    return root


def _run(staged, env_extra):
    env = {
        k: v
        for k, v in os.environ.items()
        # Drop anything that would reach the probe by another route.
        if k not in {"PORT", "MCP_HOST", "KB_ENV_FILE"}
    }
    env["PYTHONPATH"] = str(staged / "pkg")
    env.update(env_extra)
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
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
