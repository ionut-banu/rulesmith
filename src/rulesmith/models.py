"""SQLAlchemy models and engine/session setup for rulesmith.

Plain SQLAlchemy code with no FastAPI imports, so it can be used and
tested without a running app.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    relationship,
    sessionmaker,
)
from sqlalchemy.orm import (
    mapped_column as mc,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mc(Integer, primary_key=True)
    name: Mapped[str] = mc(String, nullable=False)
    created_at: Mapped[datetime] = mc(DateTime, nullable=False, default=_utcnow)

    rules: Mapped[list["Rule"]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    uploads: Mapped[list["Upload"]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    runs: Mapped[list["Run"]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mc(Integer, primary_key=True)
    dataset_id: Mapped[int] = mc(
        Integer, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mc(String, nullable=False)
    expression: Mapped[str] = mc(String, nullable=False)
    created_at: Mapped[datetime] = mc(DateTime, nullable=False, default=_utcnow)

    dataset: Mapped["Dataset"] = relationship(back_populates="rules")
    rule_results: Mapped[list["RuleResult"]] = relationship(
        back_populates="rule",
        passive_deletes=True,
    )


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[int] = mc(Integer, primary_key=True)
    dataset_id: Mapped[int] = mc(
        Integer, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    original_filename: Mapped[str] = mc(String, nullable=False)
    stored_path: Mapped[str] = mc(String, nullable=False)
    format: Mapped[str] = mc(String, nullable=False)
    uploaded_at: Mapped[datetime] = mc(DateTime, nullable=False, default=_utcnow)

    dataset: Mapped["Dataset"] = relationship(back_populates="uploads")
    run: Mapped["Run | None"] = relationship(
        back_populates="upload",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mc(Integer, primary_key=True)
    dataset_id: Mapped[int] = mc(
        Integer, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    upload_id: Mapped[int] = mc(
        Integer,
        ForeignKey("uploads.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    created_at: Mapped[datetime] = mc(DateTime, nullable=False, default=_utcnow)

    dataset: Mapped["Dataset"] = relationship(back_populates="runs")
    upload: Mapped["Upload"] = relationship(back_populates="run")
    rule_results: Mapped[list["RuleResult"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RuleResult(Base):
    __tablename__ = "rule_results"

    id: Mapped[int] = mc(Integer, primary_key=True)
    run_id: Mapped[int] = mc(
        Integer, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[int | None] = mc(
        Integer, ForeignKey("rules.id", ondelete="SET NULL"), nullable=True
    )
    rule_name: Mapped[str] = mc(String, nullable=False)
    verdict: Mapped[str] = mc(String, nullable=False)
    pass_count: Mapped[int | None] = mc(Integer, nullable=True)
    fail_count: Mapped[int | None] = mc(Integer, nullable=True)
    broken_reason: Mapped[str | None] = mc(String, nullable=True)
    failing_row_ref: Mapped[str | None] = mc(Text, nullable=True)

    run: Mapped["Run"] = relationship(back_populates="rule_results")
    rule: Mapped["Rule | None"] = relationship(back_populates="rule_results")


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_session_factory(engine: Engine) -> sessionmaker:
    """Return a session factory bound to the given engine."""
    return sessionmaker(bind=engine)


def init_db(engine: Engine) -> None:
    """Create all tables. Safe to call more than once."""
    Base.metadata.create_all(engine)
