"""Test-only failure switch used to trip the upload-failure alert locally.

`RULESMITH_FAIL_UPLOADS=1` (any non-empty value) makes otherwise-valid uploads
fail with a 500. It is off when unset or empty and must never be set in a
deployed environment: the app refuses to start with it in prod/production.
"""

import os

FAIL_UPLOADS_ENV = "RULESMITH_FAIL_UPLOADS"
_PROD_ENVS = {"prod", "production"}


def fail_uploads_enabled() -> bool:
    return bool(os.environ.get(FAIL_UPLOADS_ENV))


def check_startup() -> None:
    """Raise if the switch is on in a production environment."""
    env = (os.environ.get("RULESMITH_ENV") or "local").strip().lower()
    if fail_uploads_enabled() and env in _PROD_ENVS:
        raise RuntimeError(
            f"{FAIL_UPLOADS_ENV} is set but RULESMITH_ENV is {env!r}; this test-only "
            f"switch must never be enabled in production. Unset {FAIL_UPLOADS_ENV}."
        )
