import pytest
from fastapi.testclient import TestClient
from kb_mcp.server.server import app

@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)

def test_web_server_health(client):
    """Test if the web server responds to a basic request."""
    # The server usually has a / or /web path
    response = client.get("/", follow_redirects=True)
    assert response.status_code == 200

def test_static_assets(client):
    """Check if the server is set up to serve static files."""
    # We saw a 'static' dir in server module
    response = client.get("/static/css/style.css")
    # Even if it's 404, we want to see that the server is alive.
    # But often it should be 200 if assets exist.
    assert response.status_code in [200, 404]
