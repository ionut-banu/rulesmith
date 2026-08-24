import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from rulesmith.app import app
from rulesmith.db import get_db
from rulesmith.models import Dataset, Rule, RuleResult, Run, Upload, get_session_factory, init_db
from rulesmith.routes import uploads as uploads_module

FIXTURES = Path(__file__).parent / "fixtures" / "loader"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    session_factory = get_session_factory(engine)

    def override_get_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(uploads_module, "UPLOAD_DIR", tmp_path / "uploads")

    with TestClient(app) as test_client:
        test_client.session_factory = session_factory
        test_client.upload_dir = tmp_path / "uploads"
        yield test_client
    app.dependency_overrides.clear()


def _make_dataset(client, name="orders"):
    with client.session_factory() as session:
        dataset = Dataset(name=name)
        session.add(dataset)
        session.commit()
        return dataset.id


def _add_rule(client, dataset_id, name, expression):
    with client.session_factory() as session:
        rule = Rule(dataset_id=dataset_id, name=name, expression=expression)
        session.add(rule)
        session.commit()
        return rule.id


def _upload(client, dataset_id, fixture_name, filename=None):
    path = FIXTURES / fixture_name
    filename = filename or fixture_name
    with path.open("rb") as f:
        return client.post(
            f"/datasets/{dataset_id}/uploads",
            files={"file": (filename, f, "application/octet-stream")},
            follow_redirects=False,
        )


def test_valid_upload_creates_upload_run_and_rule_results_with_correct_verdicts(client):
    dataset_id = _make_dataset(client)
    _add_rule(client, dataset_id, "AgeCheck", "age > 18")
    _add_rule(client, dataset_id, "AlwaysBroken", "missing_col > 0")

    response = _upload(client, dataset_id, "valid.csv")

    assert response.status_code == 303

    with client.session_factory() as session:
        uploads = session.query(Upload).all()
        runs = session.query(Run).all()
        results = session.query(RuleResult).order_by(RuleResult.id).all()

        assert len(uploads) == 1
        assert uploads[0].dataset_id == dataset_id
        assert uploads[0].format == "csv"

        assert len(runs) == 1
        assert runs[0].upload_id == uploads[0].id
        assert runs[0].dataset_id == dataset_id

        assert len(results) == 2
        age_check, always_broken = results
        assert age_check.rule_name == "AgeCheck"
        assert age_check.verdict == "pass"  # Alice (30), Bob (25), Carol (40) are all > 18
        assert age_check.pass_count == 3
        assert age_check.fail_count == 0
        assert always_broken.rule_name == "AlwaysBroken"
        assert always_broken.verdict == "broken"
        assert always_broken.broken_reason is not None


def test_valid_upload_redirects_to_run_detail_page(client):
    dataset_id = _make_dataset(client)

    response = _upload(client, dataset_id, "valid.csv")

    assert response.status_code == 303
    with client.session_factory() as session:
        run = session.query(Run).one()
    assert response.headers["location"] == f"/datasets/{dataset_id}/runs/{run.id}"


def test_uploaded_file_is_retained_on_disk(client):
    dataset_id = _make_dataset(client)

    _upload(client, dataset_id, "valid.csv")

    with client.session_factory() as session:
        upload = session.query(Upload).one()

    stored_path = Path(upload.stored_path)
    assert stored_path.exists()
    assert stored_path.read_bytes() == (FIXTURES / "valid.csv").read_bytes()


def test_malformed_file_creates_no_rows_and_no_file(client):
    dataset_id = _make_dataset(client)

    response = _upload(client, dataset_id, "malformed.csv")

    assert response.status_code == 422

    with client.session_factory() as session:
        assert session.query(Upload).count() == 0
        assert session.query(Run).count() == 0
        assert session.query(RuleResult).count() == 0

    upload_dir = client.upload_dir
    if upload_dir.exists():
        assert list(upload_dir.rglob("*")) == []


def test_unrecognized_extension_is_rejected(client):
    dataset_id = _make_dataset(client)

    response = client.post(
        f"/datasets/{dataset_id}/uploads",
        files={"file": ("data.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 422
    with client.session_factory() as session:
        assert session.query(Upload).count() == 0
        assert session.query(Run).count() == 0

    upload_dir = client.upload_dir
    if upload_dir.exists():
        assert list(upload_dir.rglob("*")) == []


def _exact_size_csv(size: int) -> bytes:
    """A valid CSV of exactly `size` bytes."""
    header = b"name,age\n"
    row = b"a,1\n"
    body = bytearray(header)
    while len(body) + len(row) <= size:
        body += row
    remaining = size - len(body)
    if remaining > 0:
        # Pad the final row's name field to make up the exact remainder.
        filler = "a" * (remaining - len(",1\n"))
        body += f"{filler},1\n".encode()
    assert len(body) == size
    return bytes(body)


def test_file_at_exactly_the_size_cap_is_accepted(client):
    dataset_id = _make_dataset(client)
    content = _exact_size_csv(20 * 1024 * 1024)

    response = client.post(
        f"/datasets/{dataset_id}/uploads",
        files={"file": ("exact.csv", content, "text/csv")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with client.session_factory() as session:
        assert session.query(Upload).count() == 1


def test_oversized_file_is_rejected(client):
    dataset_id = _make_dataset(client)
    big_content = b"a" * (20 * 1024 * 1024 + 1)

    response = client.post(
        f"/datasets/{dataset_id}/uploads",
        files={"file": ("big.csv", big_content, "text/csv")},
    )

    assert response.status_code == 413
    with client.session_factory() as session:
        assert session.query(Upload).count() == 0
        assert session.query(Run).count() == 0

    upload_dir = client.upload_dir
    if upload_dir.exists():
        assert list(upload_dir.rglob("*")) == []


def test_upload_for_nonexistent_dataset_returns_404(client):
    response = _upload(client, 999999, "valid.csv")

    assert response.status_code == 404


def test_two_uploads_to_same_dataset_do_not_collide(client):
    dataset_id = _make_dataset(client)

    first = _upload(client, dataset_id, "valid.csv")
    second = _upload(client, dataset_id, "valid.csv")

    assert first.status_code == 303
    assert second.status_code == 303

    with client.session_factory() as session:
        uploads = session.query(Upload).order_by(Upload.id).all()
        runs = session.query(Run).order_by(Run.id).all()

    assert len(uploads) == 2
    assert len(runs) == 2
    assert uploads[0].stored_path != uploads[1].stored_path
    assert Path(uploads[0].stored_path).exists()
    assert Path(uploads[1].stored_path).exists()


def test_json_upload_is_parsed_and_stored(client):
    dataset_id = _make_dataset(client)

    response = _upload(client, dataset_id, "valid.json")

    assert response.status_code == 303
    with client.session_factory() as session:
        upload = session.query(Upload).one()
        assert upload.format == "json"


def test_parquet_upload_is_parsed_and_stored(client):
    dataset_id = _make_dataset(client)

    response = _upload(client, dataset_id, "valid.parquet")

    assert response.status_code == 303
    with client.session_factory() as session:
        upload = session.query(Upload).one()
        assert upload.format == "parquet"


def test_unrecognized_extension_renders_dataset_detail_html(client):
    dataset_id = _make_dataset(client)
    _add_rule(client, dataset_id, "AgeCheck", "age > 18")
    run_id = _upload(client, dataset_id, "valid.csv").headers["location"].rsplit("/", 1)[-1]

    response = client.post(
        f"/datasets/{dataset_id}/uploads",
        files={"file": ("data.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "Unrecognized file extension" in body
    assert "AgeCheck" in body
    assert f"Run {run_id}" in body


def test_oversized_file_renders_dataset_detail_html(client):
    dataset_id = _make_dataset(client)
    _add_rule(client, dataset_id, "AgeCheck", "age > 18")
    run_id = _upload(client, dataset_id, "valid.csv").headers["location"].rsplit("/", 1)[-1]
    big_content = b"a" * (20 * 1024 * 1024 + 1)

    response = client.post(
        f"/datasets/{dataset_id}/uploads",
        files={"file": ("big.csv", big_content, "text/csv")},
    )

    assert response.status_code == 413
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "20 MB upload limit" in body
    assert "AgeCheck" in body
    assert f"Run {run_id}" in body


def test_malformed_file_renders_dataset_detail_html(client):
    dataset_id = _make_dataset(client)
    _add_rule(client, dataset_id, "AgeCheck", "age > 18")
    run_id = _upload(client, dataset_id, "valid.csv").headers["location"].rsplit("/", 1)[-1]

    response = _upload(client, dataset_id, "malformed.csv")

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "Could not parse uploaded file" in body
    assert "AgeCheck" in body
    assert f"Run {run_id}" in body


def test_successful_upload_still_redirects(client):
    dataset_id = _make_dataset(client)

    response = _upload(client, dataset_id, "valid.csv")

    assert response.status_code == 303
    with client.session_factory() as session:
        run = session.query(Run).one()
    assert response.headers["location"] == f"/datasets/{dataset_id}/runs/{run.id}"


def test_nonexistent_dataset_upload_returns_404_unchanged(client):
    response = _upload(client, 999999, "valid.csv")

    assert response.status_code == 404
    assert response.json() == {"detail": "Dataset not found"}


def test_upload_error_html_escapes_crafted_filename(client):
    dataset_id = _make_dataset(client)

    response = client.post(
        f"/datasets/{dataset_id}/uploads",
        files={"file": ("data.<script>", b"hello", "text/plain")},
    )

    assert response.status_code == 422
    body = response.text
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


def test_failing_row_ref_is_json_encoded_list_of_indices(client):
    dataset_id = _make_dataset(client)
    _add_rule(client, dataset_id, "OldEnough", "age >= 30")

    _upload(client, dataset_id, "valid.csv")

    with client.session_factory() as session:
        result = session.query(RuleResult).one()

    assert result.verdict == "fail"
    indices = json.loads(result.failing_row_ref)
    assert isinstance(indices, list)
    assert all(isinstance(i, int) for i in indices)
