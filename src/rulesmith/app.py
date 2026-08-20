from fastapi import FastAPI

app = FastAPI(title="Rulesmith")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
