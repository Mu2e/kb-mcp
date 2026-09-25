import pytest
from starlette.testclient import TestClient

from kb_mcp.server.server import create_web_app


@pytest.fixture
def client():
    """Test client for the web UI (the MCP endpoint is a separate app)."""
    return TestClient(create_web_app())


def test_web_server_health(client):
    """The web UI starts and answers its public status page."""
    response = client.get("/status", follow_redirects=True)
    assert response.status_code == 200


def test_static_assets(client):
    """The web UI serves its stylesheet."""
    response = client.get("/static/css/style.css")
    assert response.status_code == 200
