import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from kb_mcp.server.ip_allowlist import IPAllowlistMiddleware, is_allowed, parse_allowed_ips

LIST = "131.225.111.176,2620:6a:0:400:f0:0:111:176 131.225.111.177,2620:6a:0:400:f0:0:111:177"


def test_allowed_and_denied():
    nets = parse_allowed_ips(LIST)
    for ok in ("131.225.111.176", "131.225.111.177", "2620:6a:0:400:f0:0:111:176",
               "2620:6a:0:400:f0:0:111:177", "::ffff:131.225.111.176", "127.0.0.1", "::1"):
        assert is_allowed(ok, nets), ok
    for bad in ("131.225.111.178", "2620:6a:0:400:f0:0:111:178", "10.0.0.1", "", None, "junk"):
        assert not is_allowed(bad, nets), bad


def test_empty_list_is_loopback_only():
    assert is_allowed("127.0.0.1", [])
    assert not is_allowed("131.225.111.176", [])


def test_bad_entry_raises():
    with pytest.raises(ValueError):
        parse_allowed_ips("131.225.111.176,not-an-ip")


def _client(peer):
    async def ok(request):
        return PlainTextResponse("ok")

    app = IPAllowlistMiddleware(Starlette(routes=[Route("/", ok)]), parse_allowed_ips(LIST))
    return TestClient(app, client=(peer, 50000))


def test_middleware():
    assert _client("131.225.111.177").get("/").status_code == 200
    assert _client("8.8.8.8").get("/").status_code == 403
