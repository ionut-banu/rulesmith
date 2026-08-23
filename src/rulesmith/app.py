from fastapi import FastAPI

from rulesmith.routes.datasets import router as datasets_router
from rulesmith.routes.rules import router as rules_router
from rulesmith.routes.uploads import router as uploads_router

app = FastAPI(title="Rulesmith")
app.include_router(datasets_router)
app.include_router(rules_router)
app.include_router(uploads_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
