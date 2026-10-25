"""Route for uploading a file to a dataset, running its rules, and storing the result."""

import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from opentelemetry import trace
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from rulesmith import telemetry
from rulesmith.db import get_db
from rulesmith.loader import TableLoadError, load_table
from rulesmith.models import Dataset, Run, RuleResult, Upload
from rulesmith.routes.datasets import build_dataset_detail_context
from rulesmith.rules import RuleInput
from rulesmith.rules import run as run_rules
from rulesmith.templates_engine import templates

router = APIRouter()
logger = logging.getLogger(__name__)

# Module-level so tests can point it at a temp directory via monkeypatch,
# the same way `get_db` is overridden for a temp database.
UPLOAD_DIR = Path(os.environ.get("RULESMITH_UPLOAD_DIR") or "./data/uploads")

MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 MB

_EXTENSION_TO_FORMAT = {".csv": "csv", ".json": "json", ".parquet": "parquet"}


def _render_upload_error(request: Request, dataset: Dataset, db: Session, error: str, status_code: int):
    """Re-render the dataset detail page with an upload error message.

    Uses the same rules/runs/trend context as `GET /datasets/{id}` so an
    upload failure lands on a normal page rather than a raw JSON error.
    """
    context = build_dataset_detail_context(dataset.id, db)
    context["dataset"] = dataset
    context["error"] = error
    return templates.TemplateResponse(
        request,
        "datasets/detail.html",
        context,
        status_code=status_code,
    )


@router.post("/datasets/{dataset_id}/uploads")
async def upload_file(
    dataset_id: int,
    request: Request,
    # Optional so a request without a `file` part reaches the handler (and is
    # counted as rejected) instead of failing FastAPI validation uncounted.
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    """Telemetry wrapper: one check metric and one log record per request.

    Only ids, counts, the format and the outcome are recorded -- never the
    file name, contents or rule expressions, and exceptions by type only.
    """
    start = time.perf_counter()
    info: dict = {}
    outcome = "error"
    error_type = None
    try:
        response = await _process_upload(dataset_id, request, file, db, info)
        outcome = "completed" if response.status_code == 303 else "rejected"
        return response
    except HTTPException as exc:
        outcome = "rejected" if exc.status_code == 404 else "error"
        if outcome == "error":
            error_type = type(exc).__name__
        raise
    except BaseException as exc:
        error_type = type(exc).__name__
        raise
    finally:
        if outcome == "error":
            telemetry.mark_error(trace.get_current_span())
        telemetry.record_check(outcome, time.perf_counter() - start)
        fields = {"dataset_id": dataset_id, "outcome": outcome, **info}
        if error_type:
            fields["error_type"] = error_type
        logger.info("upload checked", extra=fields)


async def _process_upload(
    dataset_id: int, request: Request, file: UploadFile | None, db: Session, info: dict
):
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    if file is None:
        return _render_upload_error(request, dataset, db, "No file was uploaded.", 422)

    original_filename = file.filename or ""
    ext = Path(original_filename).suffix.lower()
    file_format = _EXTENSION_TO_FORMAT.get(ext)
    if file_format is None:
        return _render_upload_error(
            request,
            dataset,
            db,
            f"Unrecognized file extension: {ext!r}. Expected .csv, .json, or .parquet.",
            422,
        )
    info["format"] = file_format

    with telemetry.span("load_file"):
        # Read with a cap so an oversized file is never fully buffered, let
        # alone written to disk. Enforcing on the actual bytes read (rather
        # than the request's Content-Length) matters because Content-Length
        # reflects the whole multipart body, including boundary/header
        # framing overhead -- comparing that total against the file-size
        # limit would reject a file of exactly the allowed size.
        contents = await file.read(MAX_UPLOAD_SIZE + 1)
        if len(contents) > MAX_UPLOAD_SIZE:
            return _render_upload_error(
                request, dataset, db, "File exceeds the 20 MB upload limit.", 413
            )

        # Validate the file parses before anything is persisted. The temp file
        # lives outside UPLOAD_DIR, so a failed upload never leaves a trace in
        # the retained-uploads directory.
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(contents)
            tmp_path = Path(tmp.name)

        try:
            df = load_table(tmp_path, file_format)
        except TableLoadError as exc:
            tmp_path.unlink(missing_ok=True)
            return _render_upload_error(
                request, dataset, db, f"Could not parse uploaded file: {exc}", 422
            )
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
    info["row_count"] = len(df)

    try:
        with telemetry.span("evaluate_rules"):
            rule_inputs = [
                RuleInput(name=rule.name, expression=rule.expression) for rule in dataset.rules
            ]
            results = run_rules(df, rule_inputs)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    with telemetry.span("persist_results"):
        dataset_dir = UPLOAD_DIR / str(dataset_id)
        upload_uuid = uuid.uuid4().hex
        final_path = dataset_dir / f"{upload_uuid}{ext}"

        try:
            dataset_dir.mkdir(parents=True, exist_ok=True)
            # shutil.move, not Path.replace: the temp dir and the upload dir can be
            # different filesystems (e.g. a Docker volume), where rename fails.
            shutil.move(tmp_path, final_path)
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=500, detail=f"Could not store uploaded file: {exc}"
            ) from exc

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
        info["run_id"] = run.id

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

    counts = {"pass": 0, "fail": 0, "broken": 0}
    for result in results:
        counts[result.verdict] += 1
    telemetry.record_verdicts(counts)
    info.update(
        pass_count=counts["pass"], fail_count=counts["fail"], broken_count=counts["broken"]
    )

    return RedirectResponse(url=f"/datasets/{dataset.id}/runs/{run.id}", status_code=303)
