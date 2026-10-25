"""Database engine/session wiring for the FastAPI app.

Kept separate from app.py and routes/ so both can import it without
circular imports, and so tests can override the `get_db` dependency
to point at a temporary/in-memory database instead of the real one.

The engine is created lazily (on app start or first real use), never at
import time, so importing this module has no side effects on disk.
"""

import os
from functools import lru_cache

import sqlalchemy
from sqlalchemy import Engine
from sqlalchemy.exc import ArgumentError
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from rulesmith.models import get_session_factory, init_db

DEFAULT_DATABASE_URL = "sqlite:///./data/rulesmith.db"


def get_database_url() -> str:
    """The configured SQLAlchemy URL; unset or empty means the SQLite default."""
    return os.environ.get("RULESMITH_DATABASE_URL") or DEFAULT_DATABASE_URL


def build_engine(url: str) -> Engine:
    """Create an engine for `url` without ever leaking its password.

    SQLite file databases get their parent directory created. Errors are
    re-raised without the original message, which could embed the raw URL.
    """
    try:
        parsed = make_url(url)
    except ArgumentError:
        raise RuntimeError("RULESMITH_DATABASE_URL is not a valid SQLAlchemy URL") from None

    if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
        from pathlib import Path

        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)

    # Looked up on the module at call time, not imported by name: the OpenTelemetry
    # SQLAlchemy instrumentation replaces `sqlalchemy.create_engine`, and a name
    # bound at import time would bypass it (leaving only `connect` spans).
    return sqlalchemy.create_engine(parsed)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """The app's engine, created and its tables ensured on first call."""
    url = get_database_url()
    engine = build_engine(url)
    try:
        init_db(engine)
    except Exception as exc:
        safe_url = make_url(url).render_as_string(hide_password=True)
        raise RuntimeError(
            f"Could not initialise database {safe_url} ({type(exc).__name__})"
        ) from None
    return engine


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker:
    return get_session_factory(get_engine())


def get_db():
    """FastAPI dependency yielding a SQLAlchemy session.

    Tests override this dependency (via app.dependency_overrides) to
    point at a temporary/in-memory database instead of the real one.
    """
    session: Session
    with _session_factory()() as session:
        yield session
