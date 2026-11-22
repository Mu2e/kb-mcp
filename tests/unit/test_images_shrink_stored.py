"""`kb tools images shrink`: stored originals replaced by the copy new images get."""

import io
import json
import os

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from kb_mcp.kb import images  # noqa: E402
from kb_mcp.parser import boilerplate  # noqa: E402


def _png(size, noise=True):
    img = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)) if noise else Image.new("RGB", size, (30, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def storage_limits(monkeypatch):
    monkeypatch.setenv("PARSE_IMAGE_DESCRIPTION_MAX_DIM", "1280")
    monkeypatch.setenv("PARSE_IMAGE_DESCRIPTION_MAX_BYTES", "800000")
    monkeypatch.delenv("PARSE_IMAGE_STORE_ORIGINAL", raising=False)


def test_a_large_original_is_replaced_and_keeps_its_hash():
    data = _png((2400, 1800))
    out, meta = images.shrunk_image(data, {"description": "a plot", "format": "png"})
    assert len(out) < 0.9 * len(data) and len(out) <= 800_000
    img = Image.open(io.BytesIO(out)); img.load()
    assert img.format == "JPEG" and max(img.size) <= 1280
    assert meta["image_hash"] == boilerplate.image_hash(data)      # the original's: registry key
    assert meta["format"] == "jpeg" and meta["original_bytes"] == len(data)
    assert meta["description"] == "a plot"


def test_an_existing_hash_is_kept():
    _, meta = images.shrunk_image(_png((2400, 1800)), {"image_hash": "abc"})
    assert meta["image_hash"] == "abc"


def test_small_images_are_left_alone():
    assert images.shrunk_image(_png((300, 200)), {}) is None
    assert images.shrunk_image(b"not an image", {}) is None


def test_store_original_setting_disables_it(monkeypatch):
    monkeypatch.setenv("PARSE_IMAGE_STORE_ORIGINAL", "true")
    assert images.shrunk_image(_png((2400, 1800)), {}) is None


def test_the_command_is_registered_as_a_dry_run_by_default():
    import argparse
    from kb_mcp.kb.cli import tools_commands

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    tools_commands.setup_commands(sub)
    args = parser.parse_args(["tools", "images", "shrink", "--max-minutes", "30"])
    assert args.func is tools_commands.cmd_images_shrink
    assert args.apply is False and args.min_bytes == 800_000 and args.max_minutes == 30


# -- the batch loop, through a recording session ---------------------------------

from contextlib import contextmanager  # noqa: E402
from types import SimpleNamespace  # noqa: E402


class _Session:
    def __init__(self, rows, dialect="postgresql"):
        self.rows, self.sql, self.params = rows, [], []
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect))

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sql.append(sql); self.params.append(params or {})
        if sql.lstrip().startswith("SELECT id FROM"):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [r[0] for r in self.rows]))
        if 'SELECT id, "binary", meta' in sql:
            return SimpleNamespace(fetchall=lambda: list(self.rows))
        return SimpleNamespace()


@pytest.fixture
def session_with(monkeypatch):
    def make(rows, dialect="postgresql"):
        s = _Session(rows, dialect)

        @contextmanager
        def fake(session=None):
            yield s
        monkeypatch.setattr(images, "get_db_session", fake)
        return s
    return make


def test_apply_locks_the_batch_and_merges_only_its_keys(session_with):
    big = _png((2400, 1800))
    s = session_with([("img-1", big, {"boilerplate": {"label": "kept"}, "image_hash": "h"}),
                      ("img-2", _png((300, 200)), None)])
    r = images.shrink_stored_images(apply=True, pause=0)
    assert r["shrunk"] == 1 and r["left"] == 1
    (batch,) = [q for q in s.sql if 'SELECT id, "binary", meta' in q]
    assert "FOR UPDATE SKIP LOCKED" in batch
    updates = [(q, p) for q, p in zip(s.sql, s.params) if q.lstrip().startswith("UPDATE")]
    shrink_sql, shrink_params = next((q, p) for q, p in updates if '"binary" = :b' in q)
    assert "coalesce(meta, '{}'::jsonb) || cast(:m AS jsonb)" in shrink_sql
    ours = json.loads(shrink_params["m"])
    assert set(ours) == {"format", "original_bytes"}            # boilerplate and the existing hash untouched
    assert any("storage_checked" in q for q, _ in updates)       # the small one is marked


def test_dry_run_neither_locks_nor_writes(session_with):
    s = session_with([("img-1", _png((2400, 1800)), {})])
    r = images.shrink_stored_images(apply=False, pause=0)
    assert r["shrunk"] == 1
    assert not any("FOR UPDATE" in q for q in s.sql)
    assert not any(q.lstrip().startswith("UPDATE") for q in s.sql)


def test_null_meta_images_are_selected(session_with):
    s = session_with([])
    images.shrink_stored_images(apply=False, pause=0)
    selection = next(q for q in s.sql if q.lstrip().startswith("SELECT id FROM"))
    assert "NOT (coalesce(meta, '{}'::jsonb) ? 'original_bytes')" in selection
    assert "NOT (coalesce(meta, '{}'::jsonb) ? 'storage_checked')" in selection


def test_sqlite_is_refused_before_any_sql(session_with):
    s = session_with([], dialect="sqlite")
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        images.shrink_stored_images(apply=True)
    assert s.sql == []


def test_store_original_refuses_instead_of_marking_everything(monkeypatch, session_with):
    monkeypatch.setenv("PARSE_IMAGE_STORE_ORIGINAL", "true")
    s = session_with([("img-1", _png((2400, 1800)), {})])
    with pytest.raises(RuntimeError, match="PARSE_IMAGE_STORE_ORIGINAL"):
        images.shrink_stored_images(apply=True)
    assert s.sql == []
