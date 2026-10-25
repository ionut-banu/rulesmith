import asyncio

import pytest

from rulesmith import failure_switch
from rulesmith.app import app, lifespan


@pytest.mark.parametrize("name", ["prod", "production", "PROD"])
def test_set_in_prod_fails_startup(monkeypatch, name):
    monkeypatch.setenv("RULESMITH_FAIL_UPLOADS", "1")
    monkeypatch.setenv("RULESMITH_ENV", name)
    with pytest.raises(RuntimeError, match="RULESMITH_FAIL_UPLOADS"):
        failure_switch.check_startup()


def test_app_lifespan_refuses_to_start_in_prod(monkeypatch):
    monkeypatch.setenv("RULESMITH_FAIL_UPLOADS", "1")
    monkeypatch.setenv("RULESMITH_ENV", "prod")

    async def start():
        async with lifespan(app):
            pass

    with pytest.raises(RuntimeError, match="RULESMITH_FAIL_UPLOADS"):
        asyncio.run(start())


@pytest.mark.parametrize("value", [None, ""])
def test_prod_without_switch_starts(monkeypatch, value):
    monkeypatch.setenv("RULESMITH_ENV", "prod")
    if value is None:
        monkeypatch.delenv("RULESMITH_FAIL_UPLOADS", raising=False)
    else:
        monkeypatch.setenv("RULESMITH_FAIL_UPLOADS", value)
    failure_switch.check_startup()


def test_switch_with_local_env_starts(monkeypatch):
    monkeypatch.setenv("RULESMITH_FAIL_UPLOADS", "1")
    monkeypatch.setenv("RULESMITH_ENV", "local")
    failure_switch.check_startup()
