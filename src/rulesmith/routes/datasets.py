"""Routes for listing, creating, and viewing datasets."""

from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from rulesmith.db import get_db
from rulesmith.models import Dataset
from rulesmith.templates_engine import templates

router = APIRouter()


@router.get("/datasets")
def list_datasets(request: Request, db: Session = Depends(get_db)):
    datasets = db.query(Dataset).order_by(Dataset.id).all()
    return templates.TemplateResponse(
        request,
        "datasets/list.html",
        {"datasets": datasets},
    )


@router.post("/datasets")
async def create_dataset(request: Request, db: Session = Depends(get_db)):
    # Parsed manually (instead of FastAPI's Form(...)) to avoid adding the
    # python-multipart dependency for what is a plain url-encoded POST from
    # an HTML form with no file upload and no custom enctype.
    body = (await request.body()).decode()
    fields = dict(parse_qsl(body, keep_blank_values=True))
    name = fields.get("name", "")

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

    return templates.TemplateResponse(
        request,
        "datasets/detail.html",
        {"dataset": dataset},
    )
