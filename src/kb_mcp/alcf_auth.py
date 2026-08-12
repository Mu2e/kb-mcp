"""Helpers to refresh ALCF inference-service credentials from the web UI.

This performs the same non-interactive steps as `scripts/setup_alcf.sh`
(silent access-token refresh via the stored Globus refresh token). It cannot
perform a full interactive re-authentication (Globus login flow requires a
browser and a local callback server on the machine running the script), so
if no valid refresh token is on disk this raises with instructions to run
the script manually.
"""

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import get_env_local_path

TOKENS_PATH = Path.home() / ".globus" / "app" / "58fdd3bc-e1c3-4ce5-80ea-8d6b87cfb944" / "inference_app" / "tokens.json"

CLUSTER_BASE_URLS = {
    "sophia": "https://inference-api.alcf.anl.gov/resource_server/sophia/vllm/v1",
    "metis": "https://inference-api.alcf.anl.gov/resource_server/metis/api/v1",
}


class AlcfAuthError(Exception):
    """Raised when the ALCF token cannot be refreshed non-interactively."""


def _auth_script_path() -> Path:
    # setup_alcf.sh downloads this to the repo root / current working directory.
    candidates = [
        Path.cwd() / "inference_auth_token.py",
        Path(__file__).resolve().parents[2] / "inference_auth_token.py",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise AlcfAuthError(
        "inference_auth_token.py not found. Run `./scripts/setup_alcf.sh` once from a "
        "terminal on the server to download it and complete the initial login."
    )


def get_token_status() -> dict:
    """Report whether a stored ALCF refresh token exists, without refreshing it."""
    return {
        "has_token_file": TOKENS_PATH.is_file(),
        "current_base_url": os.getenv("OPENAI_BASE_URL"),
        "current_api_key_set": bool(os.getenv("OPENAI_API_KEY")),
    }


def refresh_alcf_token(cluster: str = "sophia") -> dict:
    """Refresh the ALCF access token and update `.env.local` and the live process env.

    Only performs a silent refresh using the existing stored Globus refresh
    token (same as `inference_auth_token.py get_access_token`). If no token
    is stored, or the refresh token itself has expired, this raises
    `AlcfAuthError` telling the user to run `./scripts/setup_alcf.sh` in a
    terminal to complete an interactive login.
    """
    if cluster not in CLUSTER_BASE_URLS:
        raise AlcfAuthError(f"Unknown cluster '{cluster}'. Must be one of {list(CLUSTER_BASE_URLS)}.")

    if not TOKENS_PATH.is_file():
        raise AlcfAuthError(
            "No stored ALCF login found. Run `./scripts/setup_alcf.sh` in a terminal on "
            "the server to authenticate interactively (this requires opening a browser)."
        )

    script_path = _auth_script_path()

    result = subprocess.run(
        [sys.executable, str(script_path), "get_access_token"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise AlcfAuthError(
            "Silent token refresh failed, full re-authentication is likely required. "
            "Run `./scripts/setup_alcf.sh` in a terminal on the server to log in again.\n"
            f"Details: {result.stderr.strip()}"
        )

    token = result.stdout.strip()
    if not token:
        raise AlcfAuthError("Token refresh returned an empty token.")

    base_url = CLUSTER_BASE_URLS[cluster]

    _update_env_local(token, base_url)

    # Update the live process env so the change takes effect without a restart.
    os.environ["OPENAI_API_KEY"] = token
    os.environ["OPENAI_BASE_URL"] = base_url

    return {"cluster": cluster, "base_url": base_url}


def _update_env_local(token: str, base_url: str) -> None:
    env_local_path = get_env_local_path()
    if not env_local_path:
        raise AlcfAuthError("Could not resolve path to .env.local (no .env file found).")

    path = Path(env_local_path)
    if path.is_symlink():
        path = path.resolve()

    lines = path.read_text().splitlines() if path.is_file() else []

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    key_found = False
    url_found = False
    new_lines = []
    for line in lines:
        if re.match(r"^OPENAI_API_KEY=", line):
            new_lines.append(f"# {line} [Replaced {timestamp}]")
            new_lines.append(f"OPENAI_API_KEY={token}")
            key_found = True
        elif re.match(r"^OPENAI_BASE_URL=", line):
            new_lines.append(f"# {line} [Replaced {timestamp}]")
            new_lines.append(f"OPENAI_BASE_URL={base_url}")
            url_found = True
        else:
            new_lines.append(line)

    if not key_found:
        new_lines += ["", f"# ALCF Configuration - Added {timestamp}", f"OPENAI_API_KEY={token}"]
    if not url_found:
        new_lines.append(f"OPENAI_BASE_URL={base_url}")

    path.write_text("\n".join(new_lines) + "\n")
