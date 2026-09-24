"""KB_ALCF_HOME relocates the ALCF/Globus login away from $HOME."""

from pathlib import Path

from kb_mcp import alcf_auth


def test_tokens_path_follows_kb_alcf_home(monkeypatch, tmp_path):
    monkeypatch.setenv("KB_ALCF_HOME", str(tmp_path))
    assert alcf_auth._tokens_path().is_relative_to(tmp_path)
    assert alcf_auth.get_token_status()["has_token_file"] is False


def test_tokens_path_defaults_to_home(monkeypatch):
    monkeypatch.delenv("KB_ALCF_HOME", raising=False)
    assert alcf_auth._tokens_path().is_relative_to(Path.home())
