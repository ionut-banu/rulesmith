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
    """Create a Dataset/Upload/Run and attach the given RuleResult specs.

    `results` is a list of dicts with keys matching RuleResult fields.
    Returns (dataset_id, run_id).
    """
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


def test_run_page_shows_timestamp_and_upload_filename(client):
    dataset_id, run_id = _make_run(client, filename="orders_2026-08-23.csv")

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}")

    assert response.status_code == 200
    assert "orders_2026-08-23.csv" in response.text


def test_all_pass_run_shows_clean_summary(client):
    dataset_id, run_id = _make_run(
        client,
        results=[
            {"rule_id": None, "rule_name": "R1", "verdict": "pass", "pass_count": 3, "fail_count": 0},
            {"rule_id": None, "rule_name": "R2", "verdict": "pass", "pass_count": 3, "fail_count": 0},
        ],
    )

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}")

    assert response.status_code == 200
    assert "2 passed, 0 failed, 0 broken" in response.text
    assert "clean" in response.text.lower()


def test_run_with_fail_shows_counts_and_is_not_a_clean_pass(client):
    dataset_id, run_id = _make_run(
        client,
        results=[
            {"rule_id": None, "rule_name": "R1", "verdict": "pass", "pass_count": 3, "fail_count": 0},
            {"rule_id": None, "rule_name": "R2", "verdict": "fail", "pass_count": 2, "fail_count": 1},
        ],
    )

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}")

    assert response.status_code == 200
    assert "1 passed, 1 failed, 0 broken" in response.text
    assert "run-summary--clean" not in response.text


def test_run_with_broken_shows_reason_and_is_not_a_clean_pass(client):
    dataset_id, run_id = _make_run(
        client,
        results=[
            {"rule_id": None, "rule_name": "R1", "verdict": "pass", "pass_count": 3, "fail_count": 0},
            {
                "rule_id": None,
                "rule_name": "R2",
                "verdict": "broken",
                "pass_count": None,
                "fail_count": None,
                "broken_reason": "unknown column(s): missing_col",
            },
        ],
    )

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}")

    assert response.status_code == 200
    assert "unknown column(s): missing_col" in response.text
    assert "1 passed, 0 failed, 1 broken" in response.text
    assert "run-summary--clean" not in response.text


def test_broken_verdict_is_visually_distinct_from_pass(client):
    dataset_id, run_id = _make_run(
        client,
        results=[
            {"rule_id": None, "rule_name": "R1", "verdict": "pass", "pass_count": 1, "fail_count": 0},
            {
                "rule_id": None,
                "rule_name": "R2",
                "verdict": "broken",
                "pass_count": None,
                "fail_count": None,
                "broken_reason": "boom",
            },
        ],
    )

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}")

    assert "BROKEN" in response.text
    assert "PASS" in response.text
    # The two verdicts must not share the same label/class.
    assert "verdict-pass" in response.text
    assert "verdict-broken" in response.text


def test_zero_rules_run_is_distinct_from_a_clean_pass(client):
    dataset_id, run_id = _make_run(client, results=[])

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}")

    assert response.status_code == 200
    assert "no rules were evaluated" in response.text.lower()
    assert "run-summary--clean" not in response.text


def test_rule_name_and_broken_reason_with_script_tag_are_escaped(client):
    dataset_id, run_id = _make_run(
        client,
        results=[
            {
                "rule_id": None,
                "rule_name": "<script>alert(1)</script>",
                "verdict": "broken",
                "pass_count": None,
                "fail_count": None,
                "broken_reason": "<script>alert(2)</script>",
            }
        ],
    )

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}")

    assert "<script>alert(1)</script>" not in response.text
    assert "<script>alert(2)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_very_long_broken_reason_does_not_break_the_page(client):
    long_reason = "x" * 5000
    dataset_id, run_id = _make_run(
        client,
        results=[
            {
                "rule_id": None,
                "rule_name": "R1",
                "verdict": "broken",
                "pass_count": None,
                "fail_count": None,
                "broken_reason": long_reason,
            }
        ],
    )

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}")

    assert response.status_code == 200
    assert long_reason in response.text


def test_nonexistent_run_id_returns_404(client):
    dataset_id, _ = _make_run(client)

    response = client.get(f"/datasets/{dataset_id}/runs/999999")

    assert response.status_code == 404


def test_run_belonging_to_a_different_dataset_returns_404(client):
    dataset_id_a, run_id_a = _make_run(client, dataset_name="a")
    dataset_id_b, _ = _make_run(client, dataset_name="b")

    response = client.get(f"/datasets/{dataset_id_b}/runs/{run_id_a}")

    assert response.status_code == 404


def test_back_to_dataset_link_present(client):
    dataset_id, run_id = _make_run(client, dataset_name="orders")

    response = client.get(f"/datasets/{dataset_id}/runs/{run_id}")

    assert f'href="/datasets/{dataset_id}"' in response.text
