"""Engine options set by `rulesmith.db.build_engine`."""

import pytest

from rulesmith import db as db_module


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///:memory:",
        # Neon-style URL; create_engine does not connect, so no server is needed.
        "postgresql+psycopg://user:pw@example.invalid:5432/rulesmith?sslmode=require",
    ],
)
def test_engine_pre_pings_pooled_connections(url):
    # Neon closes connections when it autosuspends an idle compute; pre-ping
    # replaces them on checkout instead of handing a dead one to a request.
    engine = db_module.build_engine(url)
    try:
        assert engine.pool._pre_ping is True
    finally:
        engine.dispose()
