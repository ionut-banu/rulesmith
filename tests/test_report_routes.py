import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from rulesmith.app import app
from rulesmith.db import get_db
from rulesmith.models import Dataset, Run, RuleResult, Upload, get_session_factory, init_db


@pytest.fixture()
def client():
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
    with TestClient(app) as test_client:
        test_client.session_factory = session_factory
        yield test_client
    app.dependency_overrides.clear()


def _make_run(client, dataset_name="orders", filename="upload.csv", results=None):
    with client.session_factory() as session:
        dataset = Dataset(name=dataset_name)
        session.add(dataset)
        session.commit()

        upload = Upload(
            dataset_id=dataset.id,
            original_filename=filename,
            stored_path="/tmp/whatever.csv",
            format="csv",
        )
        session.add(upload)
        session.commit()

        run = Run(dataset_id=dataset.id, upload_id=upload.id)
        session.add(run)
        session.commit()

        for spec in results or []:
            session.add(RuleResult(run_id=run.id, **spec))
        session.commit()

        return dataset.id, run.id


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_download_link_present_on_run_detail_page(client):
    dataset_id, run_id = _make_run(client)

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}")

    assert f'href="/datasets/{dataset_id}/runs/{run_id}/report.csv"' in response.text


def test_report_has_csv_content_type_and_attachment_filename(client):
    dataset_id, run_id = _make_run(client)

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}/report.csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="run-{run_id}-report.csv"'
    )


def test_header_row_is_exact(client):
    dataset_id, run_id = _make_run(client)

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}/report.csv")

    rows = _rows(response.text)
    assert rows[0] == ["rule_name", "verdict", "pass_count", "fail_count", "broken_reason"]


def test_mixed_verdicts_one_row_each_with_correct_fields(client):
    dataset_id, run_id = _make_run(
        client,
        results=[
            {"rule_id": None, "rule_name": "R1", "verdict": "pass", "pass_count": 3, "fail_count": 0},
            {"rule_id": None, "rule_name": "R2", "verdict": "fail", "pass_count": 2, "fail_count": 1},
            {
                "rule_id": None,
                "rule_name": "R3",
                "verdict": "broken",
                "pass_count": None,
                "fail_count": None,
                "broken_reason": "unknown column(s): missing_col",
            },
        ],
    )

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}/report.csv")

    rows = _rows(response.text)
    assert rows[1] == ["R1", "pass", "3", "0", ""]
    assert rows[2] == ["R2", "fail", "2", "1", ""]
    assert rows[3] == ["R3", "broken", "", "", "unknown column(s): missing_col"]


def test_zero_rules_run_downloads_header_only(client):
    dataset_id, run_id = _make_run(client, results=[])

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}/report.csv")

    assert response.status_code == 200
    rows = _rows(response.text)
    assert rows == [["rule_name", "verdict", "pass_count", "fail_count", "broken_reason"]]


def test_nonexistent_run_returns_404_not_csv(client):
    dataset_id, _ = _make_run(client)

    response = client.get(f"/datasets/{dataset_id}/runs/999999/report.csv")

    assert response.status_code == 404


def test_run_belonging_to_different_dataset_returns_404(client):
    dataset_id_a, run_id_a = _make_run(client, dataset_name="a")
    dataset_id_b, _ = _make_run(client, dataset_name="b")

    response = client.get(f"/datasets/{dataset_id_b}/runs/{run_id_a}/report.csv")

    assert response.status_code == 404


def test_rule_name_with_comma_quote_and_newline_is_well_formed_csv(client):
    tricky_name = 'Contains, a "quote" and\na newline'
    dataset_id, run_id = _make_run(
        client,
        results=[
            {"rule_id": None, "rule_name": tricky_name, "verdict": "pass", "pass_count": 1, "fail_count": 0}
        ],
    )

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}/report.csv")

    rows = _rows(response.text)
    assert rows[1][0] == tricky_name


def test_formula_prefixed_fields_are_neutralized(client):
    dataset_id, run_id = _make_run(
        client,
        results=[
            {
                "rule_id": None,
                "rule_name": "=SUM(A1:A9)",
                "verdict": "broken",
                "pass_count": None,
                "fail_count": None,
                "broken_reason": "+cmd|' /C calc'!A1",
            },
            {
                "rule_id": None,
                "rule_name": "@import",
                "verdict": "broken",
                "pass_count": None,
                "fail_count": None,
                "broken_reason": "-1+1",
            },
        ],
    )

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}/report.csv")

    rows = _rows(response.text)
    for row in rows[1:]:
        name, reason = row[0], row[4]
        assert not name.startswith(("=", "+", "-", "@"))
        assert not reason.startswith(("=", "+", "-", "@"))
    # The original content is still recoverable, just prefixed.
    assert rows[1][0].lstrip("'") == "=SUM(A1:A9)"
    assert rows[2][0].lstrip("'") == "@import"
