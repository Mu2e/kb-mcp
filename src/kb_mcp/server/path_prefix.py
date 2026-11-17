"""Serve the web UI under a path prefix behind a reverse proxy.

The pages use absolute paths (``/static/js/app.js``, ``fetch('/api/get')``,
``href="/web/usage"``). Behind a proxy that maps
``https://host/kb/doc/...`` to this server's ``/...``, the browser resolves
those against the proxy's root and misses the prefix. This middleware:

- takes the prefix from ``X-Forwarded-Prefix`` (set by the proxy), else from
  ``WEB_BASE_PATH``;
- strips it from incoming paths, so ``/kb/doc/web`` is handled as ``/web``
  (and a request without it still works: the ssh tunnel keeps working);
- puts it in front of this app's own absolute paths in HTML and JavaScript
  responses and in redirects.

Only the app's route roots are rewritten (``/web``, ``/api``, ``/static``,
...), and only where they stand right after a quote, backtick, ``=`` or
``(``: a path in document text that happens to look like one is the price of
not touching the 200-odd templates one by one. Without a prefix the
middleware passes everything through untouched.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PREFIX_OK = re.compile(r"^(/[A-Za-z0-9._~-]+)+$")
_REWRITE_TYPES = ("text/html", "application/javascript", "text/javascript")


def normalize_prefix(value: Optional[str]) -> str:
    """``/kb/doc`` from ``kb/doc/``, ``/kb/doc`` ...; empty for none or anything odd."""
    if not value:
        return ""
    value = "/" + value.strip().strip("/")
    return value if value != "/" and _PREFIX_OK.match(value) else ""


def _path_pattern(roots: Iterable[str]) -> "re.Pattern[str]":
    alternatives = "|".join(re.escape(r) for r in sorted(set(roots), key=len, reverse=True))
    # "/web", "/api/get?x", "/static/js/app.js" -- and a bare "/" (href="/")
    return re.compile(r"""(?<=["'`(=])/(?:(?:%s)(?=[/?#"'`)\s])|(?=["'`)]))""" % alternatives)


def rewrite_paths(text: str, prefix: str, pattern: "re.Pattern[str]") -> str:
    if not prefix:
        return text
    return pattern.sub(lambda m: prefix + m.group(0), text)


class PathPrefixMiddleware:
    """ASGI middleware; see the module docstring."""

    def __init__(self, app, base_path: str = "", roots: Iterable[str] = ()):
        self.app = app
        self.base_path = normalize_prefix(base_path)
        self.pattern = _path_pattern(roots)
        self._logged_forwarded = False

    def _log_forwarded_once(self, scope) -> None:
        # Shows once what the proxy sends (X-Forwarded-Prefix or not), without cookies.
        if self._logged_forwarded:
            return
        seen = {n.decode("latin-1"): v.decode("latin-1")[:200] for n, v in scope.get("headers") or ()
                if n.startswith(b"x-forwarded-") or n == b"forwarded"}
        if seen:
            self._logged_forwarded = True
            logger.info("Web UI behind a proxy: %s; prefix in use: %r", seen,
                        normalize_prefix(seen.get("x-forwarded-prefix")) or self.base_path)

    def _prefix(self, scope) -> str:
        for name, value in scope.get("headers") or ():
            if name == b"x-forwarded-prefix":
                return normalize_prefix(value.decode("latin-1"))
        return self.base_path

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        self._log_forwarded_once(scope)
        prefix = self._prefix(scope)
        if not prefix:
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        if path == prefix or path.startswith(prefix + "/"):
            scope = dict(scope, path=path[len(prefix):] or "/")
            raw = scope.get("raw_path")
            if raw is not None and raw.startswith(prefix.encode()):
                scope["raw_path"] = raw[len(prefix):] or b"/"
        # No root_path: Starlette strips it from the path when routing, which
        # double-counts the prefix we already removed (Mount + StaticFiles 404).

        start: dict = {}
        body: List[bytes] = []

        async def send_wrapper(message):
            nonlocal start
            if message["type"] == "http.response.start":
                headers: List[Tuple[bytes, bytes]] = []
                rewrite = False
                for name, value in message.get("headers", []):
                    if name.lower() == b"location":
                        loc = value.decode("latin-1")
                        if loc.startswith("/") and not loc.startswith("//") and not loc.startswith(prefix + "/") and loc != prefix:
                            value = (prefix + loc).encode("latin-1")
                    if name.lower() == b"content-type":
                        rewrite = value.decode("latin-1").split(";")[0].strip().lower() in _REWRITE_TYPES
                    headers.append((name, value))
                start = dict(message, headers=headers)
                start["_rewrite"] = rewrite
                if not rewrite:
                    start.pop("_rewrite")
                    await send(start)
                return
            if message["type"] == "http.response.body" and start.get("_rewrite"):
                body.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                charset = "utf-8"
                text = b"".join(body).decode(charset, errors="replace")
                data = rewrite_paths(text, prefix, self.pattern).encode(charset)
                headers = [(n, v) for n, v in start["headers"] if n.lower() != b"content-length"]
                headers.append((b"content-length", str(len(data)).encode()))
                out = {k: v for k, v in start.items() if k != "_rewrite"}
                out["headers"] = headers
                await send(out)
                await send({"type": "http.response.body", "body": data, "more_body": False})
                return
            await send(message)

        await self.app(scope, receive, send_wrapper)
