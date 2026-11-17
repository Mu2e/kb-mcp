"""The deployment's privacy configuration: what is specific to one site.

The privacy code is generic; the names, address ranges, endpoints and rules
that belong to one deployment come from a TOML file, so another site needs no
code change and Mu2e needs no release to adjust a rule. The file is found in
this order:

1. ``PRIVACY_CONFIG_FILE`` in the environment (empty: none, the generic
   defaults below);
2. ``<venv>/share/kb-mcp/privacy.toml``, shipped with each release from
   ``deploy/mu2e-privacy.toml``.

Sections (all optional; see deploy/mu2e-privacy.toml for Mu2e's):

``[scan]``
    ``organization`` -- who the knowledge base belongs to, in the scan prompt
    ("Mu2e, a particle physics experiment at Fermilab");
    ``systems_owner`` -- whose systems an attacker would target ("Mu2e or
    Fermilab"); ``allowed_hosts`` -- LLM endpoints the scan may send text to
    (none by default: then every endpoint needs ``--allow-endpoint``);
    ``internal_names`` -- names in restriction notices such as "<name>
    internal only".
``[redaction]``
    ``network_prefixes`` -- IPv4 prefixes redacted with the private ranges
    (e.g. the site's own public range, "131.225").
``[visibility.rules.<name>]``
    the sweep rules, see kb.visibility_rules.

A file that cannot be read or has unknown keys is an error, not a silent
fallback: the defaults redact and allow less than Mu2e's file.
"""

from __future__ import annotations

import logging
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib

logger = logging.getLogger(__name__)

CONFIG_FILE_ENV = "PRIVACY_CONFIG_FILE"
SHIPPED_CONFIG_FILE = Path(sys.prefix) / "share" / "kb-mcp" / "privacy.toml"

_SECTIONS = {
    "scan": {"organization", "systems_owner", "allowed_hosts", "internal_names"},
    "redaction": {"network_prefixes"},
    "visibility": {"rules"},
}


def config_file() -> Optional[Path]:
    """The privacy configuration file in use, or None for the generic defaults."""
    configured = os.environ.get(CONFIG_FILE_ENV)
    if configured is not None:
        return Path(configured).expanduser() if configured.strip() else None
    return SHIPPED_CONFIG_FILE if SHIPPED_CONFIG_FILE.is_file() else None


def parse_config(data: Dict[str, Any], *, source: str = "<privacy config>") -> Dict[str, Any]:
    """Check a parsed TOML document's sections and keys; returns it unchanged."""
    unknown = set(data) - set(_SECTIONS)
    if unknown:
        raise ValueError(f"{source}: unknown section(s) {', '.join(sorted(unknown))}")
    for section, keys in _SECTIONS.items():
        table = data.get(section, {})
        if not isinstance(table, dict):
            raise ValueError(f"{source}: [{section}] must be a table")
        bad = set(table) - keys
        if bad:
            raise ValueError(f"{source}: [{section}] unknown key(s) {', '.join(sorted(bad))}")
    for key in ("allowed_hosts", "internal_names"):
        _string_list(data.get("scan", {}).get(key, []), f"{source}: scan.{key}")
    _string_list(data.get("redaction", {}).get("network_prefixes", []), f"{source}: redaction.network_prefixes")
    for key in ("organization", "systems_owner"):
        value = data.get("scan", {}).get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{source}: scan.{key} must be a non-empty string")
    return data


def _string_list(value, where: str):
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ValueError(f"{where}: expected a list of non-empty strings")
    return value


def read_config(path: Path) -> Dict[str, Any]:
    """One configuration file, checked."""
    with open(path, "rb") as f:
        return parse_config(tomllib.load(f), source=str(path))


@lru_cache(maxsize=None)
def _load(path: Optional[str]) -> Dict[str, Any]:
    if path is None:
        logger.info("no privacy configuration file; generic defaults")
        return {}
    return read_config(Path(path))


def load_config() -> Dict[str, Any]:
    """The configuration in use (cached per file path)."""
    path = config_file()
    return _load(str(path) if path else None)


def section(name: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One section of the configuration in use (or of `config`), empty if absent."""
    return dict((load_config() if config is None else config).get(name, {}))
