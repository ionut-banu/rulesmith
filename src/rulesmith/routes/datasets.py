"""Routes for listing, creating, and viewing datasets."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from rulesmith.db import get_db
from rulesmith.models import Dataset, Run
from rulesmith.templates_engine import templates

router = APIRouter()

MAX_RUNS_SHOWN = 50


@router.get("/datasets")
def list_datasets(request: Request, db: Session = Depends(get_db)):
    datasets = db.query(Dataset).order_by(Dataset.id).all()
    return templates.TemplateResponse(
        request,
        "datasets/list.html",
        {"datasets": datasets},
    )


@router.post("/datasets")
def create_dataset(
    request: Request, name: str = Form(""), db: Session = Depends(get_db)
):
    stripped = name.strip()
    if not stripped:
        datasets = db.query(Dataset).order_by(Dataset.id).all()
        return templates.TemplateResponse(
            request,
            "datasets/list.html",
            {
                "datasets": datasets,
                "error": "Name cannot be empty.",
                "name": name,
            },
            status_code=422,
        )

    dataset = Dataset(name=stripped)
    db.add(dataset)
    db.commit()
    db.refresh(dataset)

    return RedirectResponse(url=f"/datasets/{dataset.id}", status_code=303)


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: int, request: Request, db: Session = Depends(get_db)):
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    total_runs = (
        db.query(Run).filter(Run.dataset_id == dataset_id).count()
    )
    runs = (
        db.query(Run)
        .filter(Run.dataset_id == dataset_id)
        .order_by(Run.created_at.desc())
        .limit(MAX_RUNS_SHOWN)
        .all()
    )

    run_rows = []
    for run in runs:
        rule_results = run.rule_results
        passed = sum(1 for r in rule_results if r.verdict == "pass")
        failed = sum(1 for r in rule_results if r.verdict == "fail")
        broken = sum(1 for r in rule_results if r.verdict == "broken")
        run_rows.append(
            {
                "run": run,
                "passed": passed,
                "failed": failed,
                "broken": broken,
                "has_results": len(rule_results) > 0,
            }
        )

    return templates.TemplateResponse(
        request,
        "datasets/detail.html",
        {
            "dataset": dataset,
            "run_rows": run_rows,
            "more_runs_than_shown": total_runs > MAX_RUNS_SHOWN,
        },
    )
