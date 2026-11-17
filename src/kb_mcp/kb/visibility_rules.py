"""Configured visibility rules: which documents a sweep takes out of search, by name.

A deployment describes its standing rules in its privacy configuration
(kb_mcp.privacy.config: ``PRIVACY_CONFIG_FILE``, or the release's
``share/kb-mcp/privacy.toml`` from ``deploy/mu2e-privacy.toml``) instead of
code, so that the words that mark, say, a monthly cost report stay with the
deployment and a new pattern needs no release. `kb tools visibility sweep
--rule <name>` runs one of them (see docs/guides/privacy.md).

Each ``[visibility.rules.<name>]`` table may use any of these, all optional:

``name_patterns``
    file names (``doc_id``) selected wherever they are;
``entry_titles``
    DocDB entry titles (``meta.title`` of the raw document) selected as a
    whole;
``[[visibility.rules.<name>.packages]]`` with ``titles`` and ``file_patterns``
    inside an entry whose title matches, only the files whose name matches
    (generic words like "budget" are safe there, not corpus-wide);
``exclude_patterns``
    file names never selected by this rule, whatever else matches;
``hide_get``
    also hide from ``kb_get`` (default: out of search only);
``reason``
    stored with every decision.

Patterns are SQL ``LIKE`` patterns, matched case-insensitively: ``%`` is any
text, ``_`` any one character, ``\\`` escapes either.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_RULE_KEYS = {"name_patterns", "entry_titles", "packages", "exclude_patterns", "hide_get", "reason", "description"}
_PACKAGE_KEYS = {"titles", "file_patterns"}


@dataclass(frozen=True)
class NameRule:
    """One configured rule; see the module docstring for the fields."""

    name: str
    name_patterns: Tuple[str, ...] = ()
    entry_titles: Tuple[str, ...] = ()
    packages: Tuple[Tuple[Tuple[str, ...], Tuple[str, ...]], ...] = ()
    exclude_patterns: Tuple[str, ...] = ()
    hide_get: bool = False
    reason: str = ""
    description: str = ""

    def select(self, session, *, source_id: Optional[str] = None, hide_get: Optional[bool] = None) -> List[Dict[str, Any]]:
        """Raw documents this rule selects, as sweep decisions."""
        from sqlalchemy import and_, func, not_, or_
        from .db_models import RawDocument

        title = func.coalesce(RawDocument.meta["title"].astext, "")

        def any_like(column, patterns):
            return or_(*[column.ilike(p, escape="\\") for p in patterns])

        selectors = []
        if self.name_patterns:
            selectors.append(any_like(RawDocument.doc_id, self.name_patterns))
        if self.entry_titles:
            selectors.append(any_like(title, self.entry_titles))
        for titles, file_patterns in self.packages:
            selectors.append(and_(any_like(title, titles), any_like(RawDocument.doc_id, file_patterns)))
        if not selectors:
            return []

        query = session.query(RawDocument.id, RawDocument.doc_id).filter(or_(*selectors))
        if self.exclude_patterns:
            query = query.filter(not_(any_like(func.coalesce(RawDocument.doc_id, ""), self.exclude_patterns)))
        if source_id:
            query = query.filter(RawDocument.source_id == source_id)

        hide = self.hide_get if hide_get is None else (hide_get or self.hide_get)
        reason = self.reason or f"visibility rule {self.name}"
        return [
            {
                "raw_document_id": raw_id,
                "visible_search": False,
                "visible_get": not hide,
                "reason": reason,
                "meta": {"rule": self.name, "doc_id": doc_id},
            }
            for raw_id, doc_id in query.order_by(RawDocument.doc_id).all()
        ]


def _strings(value, where: str) -> Tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ValueError(f"{where}: expected a list of non-empty strings")
    return tuple(value)


def parse_rules(tables: Dict[str, Any], *, source: str = "<rules>") -> Dict[str, NameRule]:
    """Build the rules from the ``[visibility.rules]`` tables; raises ValueError on mistakes."""
    rules = {}
    if not isinstance(tables, dict):
        raise ValueError(f"{source}: [visibility.rules] must be a table")
    for name, spec in tables.items():
        where = f"{source}: visibility.rules.{name}"
        if not isinstance(spec, dict):
            raise ValueError(f"{where}: must be a table")
        unknown = set(spec) - _RULE_KEYS
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
        packages = []
        for i, pkg in enumerate(spec.get("packages", [])):
            pwhere = f"{where}.packages[{i}]"
            if not isinstance(pkg, dict) or set(pkg) - _PACKAGE_KEYS or not {"titles", "file_patterns"} <= set(pkg):
                raise ValueError(f"{pwhere}: needs exactly 'titles' and 'file_patterns'")
            packages.append((_strings(pkg["titles"], f"{pwhere}.titles"),
                             _strings(pkg["file_patterns"], f"{pwhere}.file_patterns")))
        rule = NameRule(
            name=name,
            name_patterns=_strings(spec.get("name_patterns", []), f"{where}.name_patterns") if spec.get("name_patterns") else (),
            entry_titles=_strings(spec.get("entry_titles", []), f"{where}.entry_titles") if spec.get("entry_titles") else (),
            packages=tuple(packages),
            exclude_patterns=_strings(spec["exclude_patterns"], f"{where}.exclude_patterns") if spec.get("exclude_patterns") else (),
            hide_get=bool(spec.get("hide_get", False)),
            reason=str(spec.get("reason", "")),
            description=str(spec.get("description", "")),
        )
        if not (rule.name_patterns or rule.entry_titles or rule.packages):
            raise ValueError(f"{where}: selects nothing (needs name_patterns, entry_titles or packages)")
        rules[name] = rule
    return rules


def load_rules(path: Optional[Path] = None) -> Dict[str, NameRule]:
    """The configured rules, by name: from `path`, else the privacy configuration in use."""
    from ..privacy.config import load_config, read_config

    config = read_config(path) if path is not None else load_config()
    source = str(path) if path is not None else "privacy configuration"
    return parse_rules(config.get("visibility", {}).get("rules", {}), source=source)
