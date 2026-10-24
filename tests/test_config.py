import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from rulesmith import db as db_module
from rulesmith.app import app
from rulesmith.models import _enable_sqlite_foreign_keys


@pytest.fixture(autouse=True)
def fresh_engine_cache():
    db_module.get_engine.cache_clear()
    db_module._session_factory.cache_clear()
    yield
    db_module.get_engine.cache_clear()
    db_module._session_factory.cache_clear()


@pytest.mark.parametrize("value", [None, ""])
def test_database_url_defaults_to_sqlite_file(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("RULESMITH_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("RULESMITH_DATABASE_URL", value)
    assert db_module.get_database_url() == "sqlite:///./data/rulesmith.db"


def test_database_url_from_env(monkeypatch):
    monkeypatch.setenv("RULESMITH_DATABASE_URL", "postgresql+psycopg://u:p@h/d")
    assert db_module.get_database_url() == "postgresql+psycopg://u:p@h/d"


def test_start_creates_data_dir_and_keeps_data_across_restarts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RULESMITH_DATABASE_URL", raising=False)

    with TestClient(app) as client:
        assert (tmp_path / "data" / "rulesmith.db").exists()
        assert client.post("/datasets", data={"name": "orders"}, follow_redirects=False).status_code in (200, 303)

    db_module.get_engine.cache_clear()
    db_module._session_factory.cache_clear()
    with TestClient(app) as client:
        assert "orders" in client.get("/datasets").text


def test_root_redirects_to_datasets():
    response = TestClient(app).get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/datasets"


def test_upload_dir_env(monkeypatch):
    import importlib

    from rulesmith.routes import uploads

    monkeypatch.setenv("RULESMITH_UPLOAD_DIR", "/somewhere/else")
    importlib.reload(uploads)
    try:
        assert str(uploads.UPLOAD_DIR) == "/somewhere/else"
        monkeypatch.setenv("RULESMITH_UPLOAD_DIR", "")
        importlib.reload(uploads)
        assert str(uploads.UPLOAD_DIR) == "data/uploads"
    finally:
        monkeypatch.delenv("RULESMITH_UPLOAD_DIR", raising=False)
        importlib.reload(uploads)


def test_fk_pragma_listener_only_for_sqlite():
    class NotSqlite:
        def cursor(self):
            raise AssertionError("must not run SQL on non-SQLite connections")

    _enable_sqlite_foreign_keys(NotSqlite(), None)

    conn = sqlite3.connect(":memory:")
    _enable_sqlite_foreign_keys(conn, None)
    assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)


def test_invalid_url_error_has_no_url():
    with pytest.raises(RuntimeError) as exc:
        db_module.build_engine("not-a-url-secret")
    assert "secret" not in str(exc.value)


def test_init_failure_hides_password(monkeypatch):
    monkeypatch.setenv("RULESMITH_DATABASE_URL", "postgresql+psycopg://user:s3cret@127.0.0.1:1/db")
    with pytest.raises(RuntimeError) as exc:
        db_module.get_engine()
    assert "s3cret" not in str(exc.value)
    assert exc.value.__cause__ is None
