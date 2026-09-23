"""get_database_url must survive credentials with URL-special characters."""

from sqlalchemy.engine import make_url

from kb_mcp.kb import database


def _url(monkeypatch, **cfg):
    base = {'user': 'dev', 'password': None, 'host': 'db.example.org',
            'port': '5432', 'name': 'kb', 'hostaddr': None}
    base.update(cfg)
    monkeypatch.setattr('kb_mcp.config.get_database_config', lambda: base)
    return make_url(database.get_database_url())


def test_password_with_special_characters_round_trips(monkeypatch):
    pw = 'p@ss:w/rd#50%'
    url = _url(monkeypatch, password=pw)
    assert url.password == pw
    assert url.host == 'db.example.org'
    assert url.database == 'kb'


def test_no_password_keeps_plain_user(monkeypatch):
    url = _url(monkeypatch, user='scorrodi')
    assert url.username == 'scorrodi'
    assert url.password is None
