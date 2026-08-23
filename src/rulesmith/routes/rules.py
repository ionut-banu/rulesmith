"""Routes for creating, editing, and deleting a dataset's rules."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from rulesmith.db import get_db
from rulesmith.expression import InvalidExpressionError, referenced_columns
from rulesmith.models import Dataset, Rule
from rulesmith.templates_engine import templates

router = APIRouter()


def _get_dataset_or_404(dataset_id: int, db: Session) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return dataset


def _get_rule_or_404(dataset_id: int, rule_id: int, db: Session) -> Rule:
    rule = db.get(Rule, rule_id)
    if rule is None or rule.dataset_id != dataset_id:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


def _validate(name: str, expression: str) -> str | None:
    """Return an error message if invalid, else None."""
    if not name.strip():
        return "Name cannot be empty."

    try:
        referenced_columns(expression)
    except InvalidExpressionError as exc:
        return f"Expression is invalid: {exc}"

    return None


@router.get("/datasets/{dataset_id}/rules/new")
def new_rule(dataset_id: int, request: Request, db: Session = Depends(get_db)):
    dataset = _get_dataset_or_404(dataset_id, db)

    return templates.TemplateResponse(
        request,
        "rules/form.html",
        {
            "dataset": dataset,
            "rule": None,
            "action": f"/datasets/{dataset.id}/rules",
        },
    )


@router.post("/datasets/{dataset_id}/rules")
def create_rule(
    dataset_id: int,
    request: Request,
    name: str = Form(""),
    expression: str = Form(""),
    db: Session = Depends(get_db),
):
    dataset = _get_dataset_or_404(dataset_id, db)

    error = _validate(name, expression)
    if error:
        return templates.TemplateResponse(
            request,
            "rules/form.html",
            {
                "dataset": dataset,
                "rule": None,
                "action": f"/datasets/{dataset.id}/rules",
                "error": error,
                "name": name,
                "expression": expression,
            },
            status_code=422,
        )

    rule = Rule(dataset_id=dataset.id, name=name.strip(), expression=expression)
    db.add(rule)
    db.commit()

    return RedirectResponse(url=f"/datasets/{dataset.id}", status_code=303)


@router.get("/datasets/{dataset_id}/rules/{rule_id}/edit")
def edit_rule(
    dataset_id: int, rule_id: int, request: Request, db: Session = Depends(get_db)
):
    dataset = _get_dataset_or_404(dataset_id, db)
    rule = _get_rule_or_404(dataset_id, rule_id, db)

    return templates.TemplateResponse(
        request,
        "rules/form.html",
        {
            "dataset": dataset,
            "rule": rule,
            "action": f"/datasets/{dataset.id}/rules/{rule.id}",
            "name": rule.name,
            "expression": rule.expression,
        },
    )


@router.post("/datasets/{dataset_id}/rules/{rule_id}")
def update_rule(
    dataset_id: int,
    rule_id: int,
    request: Request,
    name: str = Form(""),
    expression: str = Form(""),
    db: Session = Depends(get_db),
):
    dataset = _get_dataset_or_404(dataset_id, db)
    rule = _get_rule_or_404(dataset_id, rule_id, db)

    error = _validate(name, expression)
    if error:
        return templates.TemplateResponse(
            request,
            "rules/form.html",
            {
                "dataset": dataset,
                "rule": rule,
                "action": f"/datasets/{dataset.id}/rules/{rule.id}",
                "error": error,
                "name": name,
                "expression": expression,
            },
            status_code=422,
        )

    rule.name = name.strip()
    rule.expression = expression
    db.commit()

    return RedirectResponse(url=f"/datasets/{dataset.id}", status_code=303)


@router.post("/datasets/{dataset_id}/rules/{rule_id}/delete")
def delete_rule(
    dataset_id: int, rule_id: int, request: Request, db: Session = Depends(get_db)
):
    _get_dataset_or_404(dataset_id, db)
    rule = _get_rule_or_404(dataset_id, rule_id, db)

    db.delete(rule)
    db.commit()

    return RedirectResponse(url=f"/datasets/{dataset_id}", status_code=303)
