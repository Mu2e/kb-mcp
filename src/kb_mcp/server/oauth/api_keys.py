"""API key management for MCP server."""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import TypedDict


class ApiKeyInfo(TypedDict):
    """API key information stored in keys file."""

    username: str
    description: str
    created: str


class ApiKeyManager:
    """Manage API keys for authentication."""

    def __init__(self, keys_file: str | Path):
        """Initialize API key manager.

        Args:
            keys_file: Path to JSON file storing API keys
        """
        self.keys_file = Path(keys_file)
        self.keys_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.keys_file.exists():
            self.keys_file.write_text("{}")

    def _load_keys(self) -> dict[str, ApiKeyInfo]:
        """Load API keys from file."""
        with open(self.keys_file) as f:
            return json.load(f)

    def _save_keys(self, keys: dict[str, ApiKeyInfo]) -> None:
        """Save API keys to file."""
        with open(self.keys_file, "w") as f:
            json.dump(keys, f, indent=2)

    @staticmethod
    def _generate_key() -> str:
        """Generate a new API key.

        Format: sk_<48 random hex chars>
        """
        random_part = secrets.token_hex(24)  # 24 bytes = 48 hex chars
        return f"sk_{random_part}"

    def create_key(self, username: str, description: str = "") -> str:
        """Create a new API key.

        Args:
            username: Username to associate with this key
            description: Optional description of the key's purpose

        Returns:
            The generated API key
        """
        api_key = self._generate_key()

        keys = self._load_keys()
        keys[api_key] = {
            "username": username,
            "description": description,
            "created": datetime.now().isoformat(),
        }
        self._save_keys(keys)

        return api_key

    def verify_key(self, api_key: str) -> str | None:
        """Verify an API key and return the associated username.

        Args:
            api_key: The API key to verify

        Returns:
            Username if valid, None if invalid
        """
        keys = self._load_keys()
        if api_key in keys:
            return keys[api_key]["username"]
        return None

    def list_keys(self) -> dict[str, ApiKeyInfo]:
        """List all API keys with their information.

        Returns:
            Dict mapping API key to its information
        """
        return self._load_keys()

    def revoke_key(self, api_key: str) -> bool:
        """Revoke an API key.

        Args:
            api_key: The API key to revoke

        Returns:
            True if key was found and revoked, False otherwise
        """
        keys = self._load_keys()
        if api_key in keys:
            del keys[api_key]
            self._save_keys(keys)
            return True
        return False

