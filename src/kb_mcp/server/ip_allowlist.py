"""Source-address allowlist for the web UI.

The web UI used to be kept off the network purely by binding to loopback. To
let a few known hosts (e.g. reverse proxies) reach it, it can be bound to a
network interface instead, and this middleware then refuses every peer that is
not on the list. Loopback is always accepted so ssh tunnels and local health
checks keep working.

The check uses the TCP peer address only. X-Forwarded-For is deliberately
ignored: any client could forge it, and uvicorn only honours it from
127.0.0.1 anyway.
"""

import ipaddress
import logging
from typing import Iterable, List, Optional, Union

logger = logging.getLogger(__name__)

IPNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


def parse_allowed_ips(value: str) -> List[IPNetwork]:
    """Parse a comma/whitespace separated list of IPs or CIDR networks.

    Raises ValueError on a bad entry: a typo in an access list must stop the
    server from starting, not silently shrink (or widen) the list.
    """
    networks: List[IPNetwork] = []
    for item in (value or "").replace(",", " ").split():
        networks.append(ipaddress.ip_network(item, strict=False))
    return networks


def _normalize(host: str) -> Optional[ipaddress._BaseAddress]:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return None
    # A dual-stack socket reports IPv4 peers as ::ffff:a.b.c.d.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return addr.ipv4_mapped
    return addr


def is_allowed(host: Optional[str], networks: Iterable[IPNetwork]) -> bool:
    if not host:
        return False
    addr = _normalize(host)
    if addr is None:
        return False
    if addr.is_loopback:
        return True
    return any(addr.version == n.version and addr in n for n in networks)


class IPAllowlistMiddleware:
    """Pure ASGI middleware: 403 for peers outside loopback + the allowlist."""

    def __init__(self, app, networks: Iterable[IPNetwork]):
        self.app = app
        self.networks = list(networks)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        host = client[0] if client else None
        if is_allowed(host, self.networks):
            await self.app(scope, receive, send)
            return
        logger.warning("Web UI: refused connection from %s (not in WEB_ALLOWED_IPS)", host)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = b"Forbidden\n"
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"text/plain"),
                        (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
