import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from rulesmith.app import app
from rulesmith.db import get_db
from rulesmith.models import Dataset, Run, RuleResult, Upload, get_session_factory, init_db


@pytest.fixture()
def client(tmp_path):
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
        test_client.upload_dir = tmp_path
        yield test_client
    app.dependency_overrides.clear()


def _write_csv(tmp_path, name, rows):
    """Write a CSV with a `name,age` header and the given (name, age) rows."""
    path = tmp_path / name
    lines = ["name,age"]
    lines += [f"{n},{a}" for n, a in rows]
    path.write_text("\n".join(lines) + "\n")
    return path


def _make_run(client, upload_path, upload_format="csv", results=None):
    """Create a Dataset/Upload/Run pointing at `upload_path`, with RuleResults.

    `results` is a list of dicts with keys matching RuleResult fields.
    Returns (dataset_id, run_id, [rule_result_id, ...]).
    """
    with client.session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()

        upload = Upload(
            dataset_id=dataset.id,
            original_filename=upload_path.name,
            stored_path=str(upload_path),
            format=upload_format,
        )
        session.add(upload)
        session.commit()

        run = Run(dataset_id=dataset.id, upload_id=upload.id)
        session.add(run)
        session.commit()

        rule_result_ids = []
        for spec in results or []:
            rr = RuleResult(run_id=run.id, **spec)
            session.add(rr)
            session.commit()
            rule_result_ids.append(rr.id)

        return dataset.id, run.id, rule_result_ids


def _url(dataset_id, run_id, rule_result_id):
    return f"/datasets/{dataset_id}/runs/{run_id}/rule-results/{rule_result_id}/failing-rows"


def test_fail_verdict_shows_the_failing_rows(client, tmp_path):
    path = _write_csv(tmp_path, "data.csv", [("Alice", 30), ("Bob", 17), ("Carol", 15)])
    dataset_id, run_id, [rr_id] = _make_run(
        client,
        path,
        results=[
            {
                "rule_id": None,
                "rule_name": "AgeCheck",
                "verdict": "fail",
                "pass_count": 1,
                "fail_count": 2,
                "failing_row_ref": json.dumps([1, 2]),
            }
        ],
    )

    response = client.get(_url(dataset_id, run_id, rr_id))

    assert response.status_code == 200
    assert "Bob" in response.text
    assert "Carol" in response.text
    assert "Alice" not in response.text


def test_fragment_does_not_extend_base_page(client, tmp_path):
    path = _write_csv(tmp_path, "data.csv", [("Alice", 30), ("Bob", 17)])
    dataset_id, run_id, [rr_id] = _make_run(
        client,
        path,
        results=[
            {
                "rule_id": None,
                "rule_name": "AgeCheck",
                "verdict": "fail",
                "pass_count": 1,
                "fail_count": 1,
                "failing_row_ref": json.dumps([1]),
            }
        ],
    )

    response = client.get(_url(dataset_id, run_id, rr_id))

    assert response.status_code == 200
    assert "<html" not in response.text.lower()
    assert "<body" not in response.text.lower()


@pytest.mark.parametrize("verdict", ["pass", "broken"])
def test_non_fail_verdict_returns_not_applicable_not_error(client, tmp_path, verdict):
    path = _write_csv(tmp_path, "data.csv", [("Alice", 30)])
    extra = (
        {"pass_count": 1, "fail_count": 0}
        if verdict == "pass"
        else {"pass_count": None, "fail_count": None, "broken_reason": "boom"}
    )
    dataset_id, run_id, [rr_id] = _make_run(
        client,
        path,
        results=[{"rule_id": None, "rule_name": "R1", "verdict": verdict, **extra}],
    )

    response = client.get(_url(dataset_id, run_id, rr_id))

    assert response.status_code in (200, 400)
    assert response.status_code != 500
    assert "not applicable" in response.text.lower()


def test_truncation_note_appears_when_over_100_failing_rows(client, tmp_path):
    rows = [(f"Person{i}", 10 + i) for i in range(150)]
    path = _write_csv(tmp_path, "data.csv", rows)
    failing_indices = list(range(150))  # all 150 rows fail
    dataset_id, run_id, [rr_id] = _make_run(
        client,
        path,
        results=[
            {
                "rule_id": None,
                "rule_name": "AllFail",
                "verdict": "fail",
                "pass_count": 0,
                "fail_count": 150,
                "failing_row_ref": json.dumps(failing_indices),
            }
        ],
    )

    response = client.get(_url(dataset_id, run_id, rr_id))

    assert response.status_code == 200
    assert "showing 100 of 150 failing rows" in response.text
    assert "Person0" in response.text
    assert "Person99" in response.text
    assert "Person100" not in response.text
    assert "Person149" not in response.text


def test_no_truncation_note_when_at_or_under_100_failing_rows(client, tmp_path):
    rows = [(f"Person{i}", 10 + i) for i in range(100)]
    path = _write_csv(tmp_path, "data.csv", rows)
    failing_indices = list(range(100))
    dataset_id, run_id, [rr_id] = _make_run(
        client,
        path,
        results=[
            {
                "rule_id": None,
                "rule_name": "AllFail",
                "verdict": "fail",
                "pass_count": 0,
                "fail_count": 100,
                "failing_row_ref": json.dumps(failing_indices),
            }
        ],
    )

    response = client.get(_url(dataset_id, run_id, rr_id))

    assert response.status_code == 200
    assert "showing" not in response.text.lower()
    assert "Person0" in response.text
    assert "Person99" in response.text


def test_nonexistent_rule_result_id_returns_404(client, tmp_path):
    path = _write_csv(tmp_path, "data.csv", [("Alice", 30)])
    dataset_id, run_id, _ = _make_run(client, path, results=[])

    response = client.get(_url(dataset_id, run_id, 999999))

    assert response.status_code == 404


def test_rule_result_belonging_to_different_run_returns_404(client, tmp_path):
    path_a = _write_csv(tmp_path, "a.csv", [("Alice", 30)])
    path_b = _write_csv(tmp_path, "b.csv", [("Bob", 20)])
    dataset_id_a, run_id_a, [rr_id_a] = _make_run(
        client,
        path_a,
        results=[
            {
                "rule_id": None,
                "rule_name": "R1",
                "verdict": "fail",
                "pass_count": 0,
                "fail_count": 1,
                "failing_row_ref": json.dumps([0]),
            }
        ],
    )
    dataset_id_b, run_id_b, _ = _make_run(client, path_b, results=[])

    response = client.get(_url(dataset_id_b, run_id_b, rr_id_a))

    assert response.status_code == 404


def test_rule_result_belonging_to_different_dataset_via_run_id_returns_404(client, tmp_path):
    # rule_result_id and run_id are consistent with each other, but dataset_id
    # in the URL belongs to a different dataset entirely.
    path_a = _write_csv(tmp_path, "a.csv", [("Alice", 30)])
    path_b = _write_csv(tmp_path, "b.csv", [("Bob", 20)])
    dataset_id_a, run_id_a, [rr_id_a] = _make_run(
        client,
        path_a,
        results=[
            {
                "rule_id": None,
                "rule_name": "R1",
                "verdict": "fail",
                "pass_count": 0,
                "fail_count": 1,
                "failing_row_ref": json.dumps([0]),
            }
        ],
    )
    dataset_id_b, _, _ = _make_run(client, path_b, results=[])

    response = client.get(_url(dataset_id_b, run_id_a, rr_id_a))

    assert response.status_code == 404


def test_missing_upload_file_shows_error_not_500(client, tmp_path):
    missing_path = tmp_path / "does_not_exist.csv"
    dataset_id, run_id, [rr_id] = _make_run(
        client,
        missing_path,
        results=[
            {
                "rule_id": None,
                "rule_name": "R1",
                "verdict": "fail",
                "pass_count": 0,
                "fail_count": 1,
                "failing_row_ref": json.dumps([0]),
            }
        ],
    )

    response = client.get(_url(dataset_id, run_id, rr_id))

    assert response.status_code != 500
    assert response.status_code == 200
    assert "error" in response.text.lower() or "could not" in response.text.lower()


def test_malformed_failing_row_ref_shows_error_not_500(client, tmp_path):
    path = _write_csv(tmp_path, "data.csv", [("Alice", 30)])
    dataset_id, run_id, [rr_id] = _make_run(
        client,
        path,
        results=[
            {
                "rule_id": None,
                "rule_name": "R1",
                "verdict": "fail",
                "pass_count": 0,
                "fail_count": 1,
                "failing_row_ref": "not valid json",
            }
        ],
    )

    response = client.get(_url(dataset_id, run_id, rr_id))

    assert response.status_code != 500
    assert response.status_code == 200


def test_missing_failing_row_ref_shows_error_not_500(client, tmp_path):
    path = _write_csv(tmp_path, "data.csv", [("Alice", 30)])
    dataset_id, run_id, [rr_id] = _make_run(
        client,
        path,
        results=[
            {
                "rule_id": None,
                "rule_name": "R1",
                "verdict": "fail",
                "pass_count": 0,
                "fail_count": 1,
                "failing_row_ref": None,
            }
        ],
    )

    response = client.get(_url(dataset_id, run_id, rr_id))

    assert response.status_code != 500
    assert response.status_code == 200


def test_failing_row_ref_not_a_list_of_ints_shows_error_not_500(client, tmp_path):
    path = _write_csv(tmp_path, "data.csv", [("Alice", 30)])
    dataset_id, run_id, [rr_id] = _make_run(
        client,
        path,
        results=[
            {
                "rule_id": None,
                "rule_name": "R1",
                "verdict": "fail",
                "pass_count": 0,
                "fail_count": 1,
                "failing_row_ref": json.dumps(["not", "ints"]),
            }
        ],
    )

    response = client.get(_url(dataset_id, run_id, rr_id))

    assert response.status_code != 500
    assert response.status_code == 200


def test_failing_row_ref_index_out_of_range_shows_error_not_500(client, tmp_path):
    path = _write_csv(tmp_path, "data.csv", [("Alice", 30), ("Bob", 20)])
    dataset_id, run_id, [rr_id] = _make_run(
        client,
        path,
        results=[
            {
                "rule_id": None,
                "rule_name": "R1",
                "verdict": "fail",
                "pass_count": 0,
                "fail_count": 1,
                "failing_row_ref": json.dumps([99]),
            }
        ],
    )

    response = client.get(_url(dataset_id, run_id, rr_id))

    assert response.status_code != 500
    assert response.status_code == 200


def test_html_in_row_values_is_escaped(client, tmp_path):
    path = _write_csv(tmp_path, "data.csv", [("<script>alert(1)</script>", 30)])
    dataset_id, run_id, [rr_id] = _make_run(
        client,
        path,
        results=[
            {
                "rule_id": None,
                "rule_name": "R1",
                "verdict": "fail",
                "pass_count": 0,
                "fail_count": 1,
                "failing_row_ref": json.dumps([0]),
            }
        ],
    )

    response = client.get(_url(dataset_id, run_id, rr_id))

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_expand_control_present_on_run_detail_page_for_failed_rule(client, tmp_path):
    path = _write_csv(tmp_path, "data.csv", [("Alice", 30), ("Bob", 17)])
    dataset_id, run_id, [rr_id] = _make_run(
        client,
        path,
        results=[
            {
                "rule_id": None,
                "rule_name": "AgeCheck",
                "verdict": "fail",
                "pass_count": 1,
                "fail_count": 1,
                "failing_row_ref": json.dumps([1]),
            }
        ],
    )

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}")

    assert response.status_code == 200
    assert _url(dataset_id, run_id, rr_id) in response.text
    assert "hx-get" in response.text
