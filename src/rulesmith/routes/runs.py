"""Routes for viewing a single run's detail page and its failing rows."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from rulesmith.db import get_db
from rulesmith.loader import TableLoadError, load_table
from rulesmith.models import Run, RuleResult
from rulesmith.templates_engine import templates

router = APIRouter()

MAX_FAILING_ROWS = 100


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


@router.get("/datasets/{dataset_id}/runs/{run_id}/rule-results/{rule_result_id}/failing-rows")
def get_failing_rows(
    dataset_id: int,
    run_id: int,
    rule_result_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    run = db.get(Run, run_id)
    if run is None or run.dataset_id != dataset_id:
        raise HTTPException(status_code=404, detail="Run not found")

    rule_result = db.get(RuleResult, rule_result_id)
    if rule_result is None or rule_result.run_id != run.id:
        raise HTTPException(status_code=404, detail="Rule result not found")

    context = {
        "request": request,
        "rule_result": rule_result,
    }

    if rule_result.verdict != "fail":
        context["not_applicable"] = True
        return templates.TemplateResponse(
            request, "runs/failing_rows.html", context, status_code=200
        )

    upload = run.upload
    try:
        df = load_table(upload.stored_path, upload.format)
    except TableLoadError as exc:
        context["error"] = f"Could not load the source file: {exc}"
        return templates.TemplateResponse(
            request, "runs/failing_rows.html", context, status_code=200
        )

    error = None
    indices: list[int] = []
    try:
        parsed = json.loads(rule_result.failing_row_ref or "")
    except (json.JSONDecodeError, TypeError):
        error = "Failing row data is missing or invalid."
    else:
        if not isinstance(parsed, list) or not all(isinstance(i, int) for i in parsed):
            error = "Failing row data is missing or invalid."
        elif any(i < 0 or i >= len(df) for i in parsed):
            error = "Failing row data references rows outside the source file."
        else:
            indices = parsed

    if error:
        context["error"] = error
        return templates.TemplateResponse(
            request, "runs/failing_rows.html", context, status_code=200
        )

    fail_count = rule_result.fail_count if rule_result.fail_count is not None else len(indices)
    truncated_indices = indices[:MAX_FAILING_ROWS]
    rows = df.iloc[truncated_indices]

    context["columns"] = list(rows.columns)
    context["rows"] = rows.to_dict(orient="records")
    context["fail_count"] = fail_count
    context["shown"] = len(truncated_indices)
    context["truncated"] = fail_count > MAX_FAILING_ROWS

    return templates.TemplateResponse(request, "runs/failing_rows.html", context, status_code=200)
