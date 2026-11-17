"""The web UI behind a reverse proxy under a path (https://host/kb/doc/...)."""
import pytest
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from kb_mcp.server.path_prefix import PathPrefixMiddleware, normalize_prefix

ROOTS = ("web", "api", "static", "admin", "login", "logout", "files", "status", "oauth")
PAGE = """<html><head><link href="/static/css/style.css"><script src="/static/js/app.js"></script></head>
<body><a href="/">home</a> <a href="/web/usage">usage</a> <a href="/web?doc_type=text">docs</a>
<form action="/login"></form><p>see /web for docs, and https://example.org/web/x</p>
<a href="/webcam">not ours</a> <a href="//cdn.example.org/web/x.js">cdn</a></body></html>"""
JS = "fetch('/api/get?' + q); const u = `/api/document/${id}/chunks`; location.href = \"/web\";"


def app():
    async def page(request):
        return HTMLResponse(PAGE)

    async def js(request):
        return Response(JS, media_type="application/javascript")

    async def api(request):
        return JSONResponse({"path": request.url.path, "text": "a '/web' in a document"})

    async def root(request):
        return RedirectResponse("/web?doc_type=text")

    return Starlette(routes=[Route("/", root), Route("/web", page), Route("/web/usage", page),
                             Route("/static/js/app.js", js), Route("/api/get", api)])


def client(base_path=""):
    return TestClient(PathPrefixMiddleware(app(), base_path, ROOTS), base_url="http://kb")


def test_without_a_prefix_nothing_changes():
    c = client()
    assert c.get("/web").text == PAGE
    assert c.get("/static/js/app.js").text == JS
    r = c.get("/", follow_redirects=False)
    assert r.headers["location"] == "/web?doc_type=text"


@pytest.mark.parametrize("how", ["header", "setting"])
def test_links_scripts_and_redirects_get_the_prefix(how):
    c = client("" if how == "header" else "/kb/doc")
    h = {"X-Forwarded-Prefix": "/kb/doc"} if how == "header" else {}
    html = c.get("/web", headers=h).text            # the proxy stripped /kb/doc
    assert 'src="/kb/doc/static/js/app.js"' in html and 'href="/kb/doc/static/css/style.css"' in html
    assert 'href="/kb/doc/"' in html and 'href="/kb/doc/web/usage"' in html and 'href="/kb/doc/web?doc_type=text"' in html
    assert 'action="/kb/doc/login"' in html
    assert 'href="/webcam"' in html and 'href="//cdn.example.org/web/x.js"' in html
    assert "see /web for docs" in html and "https://example.org/web/x" in html   # text and other hosts stay
    js = c.get("/static/js/app.js", headers=h).text
    assert "fetch('/kb/doc/api/get?'" in js and "`/kb/doc/api/document/${id}/chunks`" in js and '"/kb/doc/web"' in js
    assert c.get("/", headers=h, follow_redirects=False).headers["location"] == "/kb/doc/web?doc_type=text"
    assert c.get("/api/get", headers=h).json()["text"] == "a '/web' in a document"   # JSON is data, left alone


def test_requests_with_and_without_the_prefix_both_work():
    c = client("/kb/doc")
    assert c.get("/kb/doc/web").status_code == 200            # a proxy that passes the full path
    assert c.get("/web").status_code == 200                   # the ssh tunnel
    assert c.get("/kb/doc/api/get").json()["path"] == "/api/get"
    assert c.get("/kb/doc").status_code == 200                # the prefix alone is the root -> redirect followed


def test_content_length_matches_the_rewritten_body():
    r = client("/kb/doc").get("/web")
    assert int(r.headers["content-length"]) == len(r.content)


@pytest.mark.parametrize("value, expected", [
    ("/kb/doc", "/kb/doc"), ("kb/doc/", "/kb/doc"), ("/", ""), ("", ""), (None, ""),
    ("/kb doc", ""), ("/kb/<script>", ""), ("/a/../b", "/a/../b"),
])
def test_prefix_normalisation(value, expected):
    assert normalize_prefix(value) == expected


def test_what_the_proxy_sends_is_logged_once_without_cookies(caplog):
    import logging
    c = client("/kb/doc")
    with caplog.at_level(logging.INFO, logger="kb_mcp.server.path_prefix"):
        for _ in range(3):
            c.get("/web", headers={"X-Forwarded-For": "1.2.3.4", "X-Forwarded-Host": "mu2e-exp.fnal.gov", "Cookie": "secret=1"})
    lines = [r.getMessage() for r in caplog.records if "behind a proxy" in r.getMessage()]
    assert len(lines) == 1 and "x-forwarded-host" in lines[0] and "secret" not in lines[0]
    assert "'/kb/doc'" in lines[0]


def test_static_files_under_the_prefix(tmp_path):
    """A mounted StaticFiles app must find its files (root_path would double the prefix)."""
    from starlette.routing import Mount
    from starlette.staticfiles import StaticFiles
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "app.js").write_text("fetch('/api/get')")
    inner = Starlette(routes=[Mount("/static", StaticFiles(directory=str(tmp_path)))])
    c = TestClient(PathPrefixMiddleware(inner, "/kb/doc", ROOTS), base_url="http://kb")
    for path in ("/kb/doc/static/js/app.js", "/static/js/app.js"):
        r = c.get(path)
        assert r.status_code == 200 and "fetch('/kb/doc/api/get')" in r.text
