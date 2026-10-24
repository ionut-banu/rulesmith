from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from rulesmith.db import get_db, get_engine
from rulesmith.routes.datasets import router as datasets_router
from rulesmith.routes.rules import router as rules_router
from rulesmith.routes.runs import router as runs_router
from rulesmith.routes.uploads import router as uploads_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the database (and ./data for the SQLite default) on start.
    # Skipped when tests override `get_db`, so they never touch the real one.
    if get_db not in app.dependency_overrides:
        get_engine()
    yield


app = FastAPI(title="Rulesmith", lifespan=lifespan)
app.include_router(datasets_router)
app.include_router(rules_router)
app.include_router(runs_router)
app.include_router(uploads_router)


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/datasets", status_code=303)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
