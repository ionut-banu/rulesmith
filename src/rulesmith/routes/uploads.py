"""Route for uploading a file to a dataset, running its rules, and storing the result."""

import json
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from rulesmith.db import get_db
from rulesmith.loader import TableLoadError, load_table
from rulesmith.models import Dataset, Run, RuleResult, Upload
from rulesmith.rules import RuleInput
from rulesmith.rules import run as run_rules

router = APIRouter()

# Module-level so tests can point it at a temp directory via monkeypatch,
# the same way `get_db` is overridden for a temp database.
UPLOAD_DIR = Path("./data/uploads")

MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 MB

_EXTENSION_TO_FORMAT = {".csv": "csv", ".json": "json", ".parquet": "parquet"}


@router.post("/datasets/{dataset_id}/uploads")
async def upload_file(
    dataset_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    original_filename = file.filename or ""
    ext = Path(original_filename).suffix.lower()
    file_format = _EXTENSION_TO_FORMAT.get(ext)
    if file_format is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unrecognized file extension: {ext!r}. Expected .csv, .json, or .parquet.",
        )

    content_length = request.headers.get("content-length")
    if content_length is not None and int(content_length) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds the 20 MB upload limit.")

    # Read with a cap so an oversized file is never fully buffered, let
    # alone written to disk, regardless of what Content-Length claimed.
    contents = await file.read(MAX_UPLOAD_SIZE + 1)
    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds the 20 MB upload limit.")

    # Validate the file parses before anything is persisted. The temp file
    # lives outside UPLOAD_DIR, so a failed upload never leaves a trace in
    # the retained-uploads directory.
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        load_table(tmp_path, file_format)
    except TableLoadError as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Could not parse uploaded file: {exc}") from exc

    dataset_dir = UPLOAD_DIR / str(dataset_id)
    upload_uuid = uuid.uuid4().hex
    final_path = dataset_dir / f"{upload_uuid}{ext}"

    try:
        dataset_dir.mkdir(parents=True, exist_ok=True)
        tmp_path.replace(final_path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Could not store uploaded file: {exc}") from exc

    upload = Upload(
        dataset_id=dataset.id,
        original_filename=original_filename,
        stored_path=str(final_path),
        format=file_format,
    )
    db.add(upload)
    db.commit()
    db.refresh(upload)

    run = Run(dataset_id=dataset.id, upload_id=upload.id)
    db.add(run)
    db.commit()
    db.refresh(run)

    df = load_table(final_path, file_format)
    rule_inputs = [RuleInput(name=rule.name, expression=rule.expression) for rule in dataset.rules]
    results = run_rules(df, rule_inputs)

    for rule, result in zip(dataset.rules, results):
        db.add(
            RuleResult(
                run_id=run.id,
                rule_id=rule.id,
                rule_name=result.rule_name,
                verdict=result.verdict,
                pass_count=result.pass_count,
                fail_count=result.fail_count,
                broken_reason=result.broken_reason,
                failing_row_ref=json.dumps(result.failing_row_indices),
            )
        )
    db.commit()

    return RedirectResponse(url=f"/datasets/{dataset.id}/runs/{run.id}", status_code=303)
