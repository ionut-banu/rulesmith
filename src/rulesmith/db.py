"""Database engine/session wiring for the FastAPI app.

Kept separate from app.py and routes/ so both can import it without
circular imports, and so tests can override the `get_db` dependency
to point at a temporary/in-memory database instead of the real one.
"""

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

from rulesmith.models import get_session_factory, init_db

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "rulesmith.db"

engine: Engine = create_engine(f"sqlite:///{DB_PATH}")
init_db(engine)
SessionLocal: sessionmaker = get_session_factory(engine)


def get_db():
    """FastAPI dependency yielding a SQLAlchemy session.

    Tests override this dependency (via app.dependency_overrides) to
    point at a temporary/in-memory database instead of the real one.
    """
    with SessionLocal() as session:
        yield session
