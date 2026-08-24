"""Routes for listing, creating, and viewing datasets."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from rulesmith.db import get_db
from rulesmith.models import Dataset, Run
from rulesmith.templates_engine import templates
from rulesmith.trend import build_chart_points, build_trend_points, polyline_points

router = APIRouter()

MAX_RUNS_SHOWN = 50

TREND_CHART_WIDTH = 600
TREND_CHART_HEIGHT = 160
TREND_CHART_PADDING = 30


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


def build_dataset_detail_context(dataset_id: int, db: Session) -> dict:
    """Build the template context for the dataset detail page.

    Shared by `get_dataset()` and the upload error paths in
    `routes/uploads.py`, which need to re-render the same page (rules,
    run history, trend chart) alongside an upload error message.
    """
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

    # issue #13's run history is ordered newest-first; the trend needs the
    # same run set ordered oldest-to-newest.
    trend_points = build_trend_points(reversed(runs))
    chart_points = build_chart_points(
        trend_points,
        width=TREND_CHART_WIDTH,
        height=TREND_CHART_HEIGHT,
        padding=TREND_CHART_PADDING,
    )

    return {
        "run_rows": run_rows,
        "more_runs_than_shown": total_runs > MAX_RUNS_SHOWN,
        "chart_points": chart_points,
        "chart_polyline": polyline_points(chart_points),
        "chart_width": TREND_CHART_WIDTH,
        "chart_height": TREND_CHART_HEIGHT,
        "chart_padding": TREND_CHART_PADDING,
    }


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: int, request: Request, db: Session = Depends(get_db)):
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    context = build_dataset_detail_context(dataset_id, db)
    context["dataset"] = dataset

    return templates.TemplateResponse(
        request,
        "datasets/detail.html",
        context,
    )
