"""Verification of mikey-issued API keys (github.com/Mu2e/aitools, mcp/mikey).

Lets one key issued to a collaboration member authenticate against every Mu2e
MCP server, this one included, instead of each server minting its own.

Format-compatible with mikey's KeyStore rather than importing it: mikey
requires mcp>=2.0.0 while this server pins mcp<1.23.0, so the package cannot
be installed alongside us. Its on-disk format (sha256 hex under "hash") is
the contract instead -- if mikey ever changes it, this must follow.

Read-only by design: keys are issued and revoked with the `mikey` CLI in the
account that owns the file. This server never creates or writes it, and never
creates it if missing -- an absent file means misconfiguration, not an empty
keyring, and silently minting one would accept no keys while looking healthy.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

KEY_PREFIX = "mikey_"


class MikeyKeyStore:
    """Verifies bearer tokens against a mikey keys file."""

    def __init__(self, keys_file: str | Path):
        self.keys_file = Path(keys_file)

    @staticmethod
    def is_mikey_token(token: str) -> bool:
        return token.startswith(KEY_PREFIX)

    @staticmethod
    def fingerprint(token: str) -> str:
        """mikey's key id: first 8 chars of the token's hash.

        Safe to log -- it is what `mikey list` prints and `mikey revoke`
        takes, and it identifies a key without exposing any of its secret.
        """
        return _digest(token)[:8]

    def _load(self) -> dict[str, dict]:
        """Read the keys file fresh on every call, so a `mikey revoke` takes
        effect immediately rather than at the next server restart."""
        try:
            records = json.loads(self.keys_file.read_text())
        except FileNotFoundError:
            logger.error("mikey keys file not found: %s", self.keys_file)
            return {}
        except PermissionError:
            # mikey writes the file 0600, so this usually means kb-mcp runs as
            # a different user than the account that owns it.
            logger.error("mikey keys file not readable: %s", self.keys_file)
            return {}
        except (OSError, json.JSONDecodeError) as e:
            logger.error("mikey keys file unusable (%s): %s", self.keys_file, e)
            return {}
        if not isinstance(records, dict):
            logger.error("mikey keys file is not a JSON object: %s", self.keys_file)
            return {}
        return records

    def verify_key(self, token: str) -> str | None:
        """Return the username the token was issued to, or None if invalid."""
        if not self.is_mikey_token(token):
            return None
        digest = _digest(token)
        for record in self._load().values():
            if isinstance(record, dict) and record.get("hash") == digest:
                return record.get("username")
        return None

    def valid_hashes(self) -> set[str]:
        """Every currently-issued key's hash, for pruning caches of revoked keys."""
        return {
            record["hash"]
            for record in self._load().values()
            if isinstance(record, dict) and "hash" in record
        }


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
