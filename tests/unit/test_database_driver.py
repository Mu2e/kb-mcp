"""The PostgreSQL engine must use psycopg2 whatever SQLAlchemy's default is.

SQLAlchemy 2.1 changed the DBAPI behind a bare postgresql:// URL to psycopg
(3), which is not installed; a release that resolved 2.1 failed every query.
"""

from kb_mcp.kb import database


def test_engine_uses_psycopg2(monkeypatch):
    cfg = {'user': 'dev', 'password': None, 'host': 'db.example.org', 'port': '5432',
           'name': 'kb', 'hostaddr': None, 'schema': 'public'}
    monkeypatch.setattr('kb_mcp.config.get_database_config', lambda: cfg)
    engine = database.create_engine_with_config()   # does not connect
    assert engine.dialect.driver == "psycopg2"
    # The plain URL is kept for callers that pass it to psycopg2.connect().
    assert database.get_database_url().startswith("postgresql://")
