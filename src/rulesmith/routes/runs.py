"""Route for viewing a single run's detail page."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from rulesmith.db import get_db
from rulesmith.models import Run
from rulesmith.templates_engine import templates

router = APIRouter()


@router.get("/datasets/{dataset_id}/runs/{run_id}")
def get_run(dataset_id: int, run_id: int, request: Request, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if run is None or run.dataset_id != dataset_id:
        raise HTTPException(status_code=404, detail="Run not found")

    rule_results = sorted(run.rule_results, key=lambda r: r.id)
    passed = sum(1 for r in rule_results if r.verdict == "pass")
    failed = sum(1 for r in rule_results if r.verdict == "fail")
    broken = sum(1 for r in rule_results if r.verdict == "broken")

    return templates.TemplateResponse(
        request,
        "runs/detail.html",
        {
            "run": run,
            "dataset": run.dataset,
            "upload": run.upload,
            "rule_results": rule_results,
            "passed": passed,
            "failed": failed,
            "broken": broken,
        },
    )
