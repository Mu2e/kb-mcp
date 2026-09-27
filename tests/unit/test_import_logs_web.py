"""The Imports page may only ever serve log files from configured directories."""

import os

from kb_mcp.server.web.routes.imports import (
    LOG_NAME_RE,
    allowed_log_path,
    clean_log_text,
)


def test_log_inside_allowed_dir_is_served(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    f = logs / "docdb-update-20260924-210501.log"
    f.write_text("ok")
    assert allowed_log_path(str(f), [logs.resolve()]) == f.resolve()


def test_paths_outside_allowed_dirs_are_refused(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("no")
    allowed = [logs.resolve()]

    assert allowed_log_path(str(secret), allowed) is None
    assert allowed_log_path(str(logs / ".." / "secret.txt"), allowed) is None
    link = logs / "docdb-update-20260924-210501.log"
    os.symlink(secret, link)
    assert allowed_log_path(str(link), allowed) is None, "a symlink led outside"
    assert allowed_log_path(str(secret), []) is None, "nothing is served unconfigured"
    assert allowed_log_path(None, allowed) is None


def test_only_job_log_names_match():
    assert LOG_NAME_RE.match("docdb-update-20260924-210501.log")
    for bad in ("../x.log", "docdb-update-2026.log", "kb-mcp.env", "docdb-update-20260924-210501.log/x"):
        assert not LOG_NAME_RE.match(bad)


def test_progress_bars_and_colours_are_cleaned():
    raw = "Embedding:  10%|#\rEmbedding:  50%|#####\rEmbedding: 100%|##########\n\x1b[32m[INFO]\x1b[0m done"
    assert clean_log_text(raw) == "Embedding: 100%|##########\n[INFO] done"


def test_unreadable_directory_outside_allowed_is_not_touched(tmp_path):
    """Old runs point at a log directory the web server cannot read. Asking
    whether a file there exists raised PermissionError and broke the whole
    Imports page; such paths must simply get no log link."""
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "docdb-update-20260101-000000.log").write_text("x")
    allowed = tmp_path / "logs"
    allowed.mkdir()
    locked.chmod(0)
    try:
        assert allowed_log_path(str(locked / "docdb-update-20260101-000000.log"),
                                [allowed.resolve()]) is None
    finally:
        locked.chmod(0o700)


def test_unreadable_file_inside_allowed_dir_gives_no_link(tmp_path):
    logs = tmp_path / "logs"
    sub = logs / "sub"
    sub.mkdir(parents=True)
    (sub / "x.log").write_text("x")
    sub.chmod(0)
    try:
        assert allowed_log_path(str(sub / "x.log"), [logs.resolve()]) is None
    finally:
        sub.chmod(0o700)
