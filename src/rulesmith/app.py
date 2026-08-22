from fastapi import FastAPI

from rulesmith.routes.datasets import router as datasets_router

app = FastAPI(title="Rulesmith")
app.include_router(datasets_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
