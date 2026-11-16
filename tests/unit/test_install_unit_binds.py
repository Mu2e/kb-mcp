"""kb-mcp-install-unit.sh leaves bind addresses to the settings unless told otherwise.

A --web-host=127.0.0.1 the script wrote by default overrode WEB_HOST=:: from
deploy/mu2e.env and locked the WEB_ALLOWED_IPS hosts out of the 0.2.11 web UI.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "kb-mcp-install-unit.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def exec_start(tmp_path, *args):
    env_file = tmp_path / "kb-mcp.env"
    env_file.write_text("")
    env_file.chmod(0o600)
    out = subprocess.run(["bash", str(SCRIPT), *args, "--defaults", str(ROOT / "deploy" / "mu2e.env"),
                          "--data-dir", str(tmp_path), "--env-file", str(env_file), "--dry-run"],
                         capture_output=True, text=True, check=True).stdout
    (line,) = [l for l in out.splitlines() if l.startswith("ExecStart=")]
    return line


def test_web_unit_takes_web_host_from_the_settings(tmp_path):
    line = exec_start(tmp_path, "--surface", "web")
    assert "--web-host" not in line and "--web-port=8108" in line


def test_mcp_unit_takes_mcp_host_from_the_settings(tmp_path):
    line = exec_start(tmp_path, "--surface", "mcp")
    assert "--host" not in line and "--port=8008" in line


def test_an_explicit_address_still_overrides(tmp_path):
    assert "--web-host=127.0.0.1" in exec_start(tmp_path, "--surface", "web", "--web-host", "127.0.0.1")
    assert "--host=10.0.0.5" in exec_start(tmp_path, "--surface", "mcp", "--host", "10.0.0.5")


def test_the_shipped_settings_bind_as_deployed():
    text = (ROOT / "deploy" / "mu2e.env").read_text()
    assert "\nWEB_HOST=::\n" in text and "\nMCP_HOST=0.0.0.0\n" in text
