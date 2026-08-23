import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from rulesmith.app import app
from rulesmith.db import get_db
from rulesmith.models import Dataset, Rule, RuleResult, Run, Upload, get_session_factory, init_db


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


def _make_dataset(client, name="orders"):
    with client.session_factory() as session:
        dataset = Dataset(name=name)
        session.add(dataset)
        session.commit()
        return dataset.id


def test_new_rule_form_renders(client):
    dataset_id = _make_dataset(client)

    response = client.get(f"/datasets/{dataset_id}/rules/new")

    assert response.status_code == 200
    assert "name=\"name\"" in response.text
    assert "name=\"expression\"" in response.text


def test_new_rule_form_for_nonexistent_dataset_returns_404(client):
    response = client.get("/datasets/999999/rules/new")

    assert response.status_code == 404


def test_create_rule_with_valid_data_redirects_and_lists_rule(client):
    dataset_id = _make_dataset(client)

    response = client.post(
        f"/datasets/{dataset_id}/rules",
        data={"name": "not_null", "expression": "col is not None"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/datasets/{dataset_id}"

    with client.session_factory() as session:
        assert session.query(Rule).count() == 1
        rule = session.query(Rule).first()
        assert rule.name == "not_null"
        assert rule.expression == "col is not None"

    detail_response = client.get(f"/datasets/{dataset_id}")
    assert "not_null" in detail_response.text
    assert "col is not None" in detail_response.text


def test_create_rule_with_empty_name_creates_no_row(client):
    dataset_id = _make_dataset(client)

    response = client.post(
        f"/datasets/{dataset_id}/rules",
        data={"name": "", "expression": "col is not None"},
    )

    assert response.status_code == 422
    assert response.status_code != 500

    with client.session_factory() as session:
        assert session.query(Rule).count() == 0


def test_create_rule_with_whitespace_only_name_creates_no_row(client):
    dataset_id = _make_dataset(client)

    response = client.post(
        f"/datasets/{dataset_id}/rules",
        data={"name": "   ", "expression": "col is not None"},
    )

    assert response.status_code == 422
    with client.session_factory() as session:
        assert session.query(Rule).count() == 0


def test_create_rule_with_empty_name_rerenders_form_with_error_and_input(client):
    dataset_id = _make_dataset(client)

    response = client.post(
        f"/datasets/{dataset_id}/rules",
        data={"name": "   ", "expression": "col is not None"},
    )

    assert response.status_code == 422
    assert "empty" in response.text.lower() or "error" in response.text.lower()
    assert "col is not None" in response.text


def test_create_rule_with_unparseable_expression_creates_no_row(client):
    dataset_id = _make_dataset(client)

    response = client.post(
        f"/datasets/{dataset_id}/rules",
        data={"name": "broken", "expression": "col ==="},
    )

    assert response.status_code == 422
    assert response.status_code != 500

    with client.session_factory() as session:
        assert session.query(Rule).count() == 0


def test_create_rule_with_unparseable_expression_rerenders_with_specific_error(client):
    dataset_id = _make_dataset(client)

    response = client.post(
        f"/datasets/{dataset_id}/rules",
        data={"name": "broken", "expression": "col ==="},
    )

    assert response.status_code == 422
    assert "broken" in response.text
    assert "col ===" in response.text


def test_create_rule_with_whitespace_only_expression_is_rejected(client):
    dataset_id = _make_dataset(client)

    response = client.post(
        f"/datasets/{dataset_id}/rules",
        data={"name": "broken", "expression": "   "},
    )

    assert response.status_code == 422
    with client.session_factory() as session:
        assert session.query(Rule).count() == 0


def test_create_rule_for_nonexistent_dataset_returns_404(client):
    response = client.post(
        "/datasets/999999/rules",
        data={"name": "not_null", "expression": "col is not None"},
    )

    assert response.status_code == 404


def test_very_long_rule_name_and_expression_accepted_and_rendered(client):
    # A long chain of small comparisons, not one huge integer literal:
    # CPython 3.11+ caps integer-literal string conversion at 4300 digits
    # (a language-level DoS guard, unrelated to this app), so an expression
    # with a single 5000-digit integer is correctly rejected as invalid.
    dataset_id = _make_dataset(client)
    long_name = "a" * 5000
    long_expression = " and ".join(["col == 1"] * 600)

    response = client.post(
        f"/datasets/{dataset_id}/rules",
        data={"name": long_name, "expression": long_expression},
        follow_redirects=False,
    )

    assert response.status_code == 303

    detail_response = client.get(f"/datasets/{dataset_id}")
    assert detail_response.status_code == 200
    assert long_name in detail_response.text
    assert long_expression in detail_response.text


def test_rule_name_and_expression_with_script_tag_is_escaped(client):
    dataset_id = _make_dataset(client)
    malicious = "<script>alert(1)</script>"

    response = client.post(
        f"/datasets/{dataset_id}/rules",
        data={"name": malicious, "expression": "col == 1"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    detail_response = client.get(f"/datasets/{dataset_id}")
    assert "<script>alert(1)</script>" not in detail_response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in detail_response.text


def test_rule_name_with_script_tag_in_validation_error_is_escaped(client):
    dataset_id = _make_dataset(client)
    malicious = "<script>alert(1)</script>"

    response = client.post(
        f"/datasets/{dataset_id}/rules",
        data={"name": "", "expression": malicious},
    )

    assert response.status_code == 422
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_edit_rule_form_prefilled(client):
    dataset_id = _make_dataset(client)
    with client.session_factory() as session:
        rule = Rule(dataset_id=dataset_id, name="not_null", expression="col is not None")
        session.add(rule)
        session.commit()
        rule_id = rule.id

    response = client.get(f"/datasets/{dataset_id}/rules/{rule_id}/edit")

    assert response.status_code == 200
    assert "not_null" in response.text
    assert "col is not None" in response.text


def test_edit_rule_for_nonexistent_rule_id_returns_404(client):
    dataset_id = _make_dataset(client)

    response = client.get(f"/datasets/{dataset_id}/rules/999999/edit")

    assert response.status_code == 404


def test_edit_rule_for_rule_belonging_to_different_dataset_returns_404(client):
    dataset_id = _make_dataset(client, "orders")
    other_dataset_id = _make_dataset(client, "shipments")
    with client.session_factory() as session:
        rule = Rule(dataset_id=other_dataset_id, name="not_null", expression="col is not None")
        session.add(rule)
        session.commit()
        rule_id = rule.id

    response = client.get(f"/datasets/{dataset_id}/rules/{rule_id}/edit")

    assert response.status_code == 404


def test_update_rule_with_valid_data_persists_and_redirects(client):
    dataset_id = _make_dataset(client)
    with client.session_factory() as session:
        rule = Rule(dataset_id=dataset_id, name="not_null", expression="col is not None")
        session.add(rule)
        session.commit()
        rule_id = rule.id

    response = client.post(
        f"/datasets/{dataset_id}/rules/{rule_id}",
        data={"name": "renamed", "expression": "col > 0"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/datasets/{dataset_id}"

    with client.session_factory() as session:
        updated = session.get(Rule, rule_id)
        assert updated.name == "renamed"
        assert updated.expression == "col > 0"


def test_update_rule_with_empty_name_creates_no_change(client):
    dataset_id = _make_dataset(client)
    with client.session_factory() as session:
        rule = Rule(dataset_id=dataset_id, name="not_null", expression="col is not None")
        session.add(rule)
        session.commit()
        rule_id = rule.id

    response = client.post(
        f"/datasets/{dataset_id}/rules/{rule_id}",
        data={"name": "   ", "expression": "col > 0"},
    )

    assert response.status_code == 422
    with client.session_factory() as session:
        unchanged = session.get(Rule, rule_id)
        assert unchanged.name == "not_null"
        assert unchanged.expression == "col is not None"


def test_update_rule_with_unparseable_expression_creates_no_change(client):
    dataset_id = _make_dataset(client)
    with client.session_factory() as session:
        rule = Rule(dataset_id=dataset_id, name="not_null", expression="col is not None")
        session.add(rule)
        session.commit()
        rule_id = rule.id

    response = client.post(
        f"/datasets/{dataset_id}/rules/{rule_id}",
        data={"name": "not_null", "expression": "col ==="},
    )

    assert response.status_code == 422
    with client.session_factory() as session:
        unchanged = session.get(Rule, rule_id)
        assert unchanged.expression == "col is not None"


def test_update_rule_for_nonexistent_rule_returns_404(client):
    dataset_id = _make_dataset(client)

    response = client.post(
        f"/datasets/{dataset_id}/rules/999999",
        data={"name": "x", "expression": "1 == 1"},
    )

    assert response.status_code == 404


def test_delete_rule_removes_from_list_and_redirects(client):
    dataset_id = _make_dataset(client)
    with client.session_factory() as session:
        rule = Rule(dataset_id=dataset_id, name="not_null", expression="col is not None")
        session.add(rule)
        session.commit()
        rule_id = rule.id

    response = client.post(
        f"/datasets/{dataset_id}/rules/{rule_id}/delete", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/datasets/{dataset_id}"

    with client.session_factory() as session:
        assert session.get(Rule, rule_id) is None

    detail_response = client.get(f"/datasets/{dataset_id}")
    assert "not_null" not in detail_response.text


def test_delete_rule_for_nonexistent_rule_returns_404(client):
    dataset_id = _make_dataset(client)

    response = client.post(f"/datasets/{dataset_id}/rules/999999/delete")

    assert response.status_code == 404


def test_editing_rule_does_not_alter_existing_rule_results(client):
    dataset_id = _make_dataset(client)
    with client.session_factory() as session:
        upload = Upload(
            dataset_id=dataset_id,
            original_filename="orders.csv",
            stored_path="/data/orders.csv",
            format="csv",
        )
        session.add(upload)
        session.commit()

        run = Run(dataset_id=dataset_id, upload_id=upload.id)
        session.add(run)
        session.commit()

        rule = Rule(dataset_id=dataset_id, name="not_null", expression="col is not None")
        session.add(rule)
        session.commit()
        rule_id = rule.id

        result = RuleResult(
            run_id=run.id,
            rule_id=rule.id,
            rule_name="not_null",
            verdict="pass",
            pass_count=10,
            fail_count=0,
        )
        session.add(result)
        session.commit()
        result_id = result.id

    response = client.post(
        f"/datasets/{dataset_id}/rules/{rule_id}",
        data={"name": "renamed", "expression": "col > 0"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with client.session_factory() as session:
        result = session.get(RuleResult, result_id)
        assert result is not None
        assert result.rule_id == rule_id
        assert result.rule_name == "not_null"
        assert result.verdict == "pass"
        assert result.pass_count == 10
        assert result.fail_count == 0


def test_deleting_rule_does_not_alter_existing_rule_results(client):
    dataset_id = _make_dataset(client)
    with client.session_factory() as session:
        upload = Upload(
            dataset_id=dataset_id,
            original_filename="orders.csv",
            stored_path="/data/orders.csv",
            format="csv",
        )
        session.add(upload)
        session.commit()

        run = Run(dataset_id=dataset_id, upload_id=upload.id)
        session.add(run)
        session.commit()

        rule = Rule(dataset_id=dataset_id, name="not_null", expression="col is not None")
        session.add(rule)
        session.commit()
        rule_id = rule.id

        result = RuleResult(
            run_id=run.id,
            rule_id=rule.id,
            rule_name="not_null",
            verdict="fail",
            pass_count=8,
            fail_count=2,
        )
        session.add(result)
        session.commit()
        result_id = result.id

    response = client.post(
        f"/datasets/{dataset_id}/rules/{rule_id}/delete", follow_redirects=False
    )
    assert response.status_code == 303

    with client.session_factory() as session:
        assert session.get(Rule, rule_id) is None

        result = session.get(RuleResult, result_id)
        assert result is not None
        assert result.rule_id is None
        assert result.rule_name == "not_null"
        assert result.verdict == "fail"
        assert result.pass_count == 8
        assert result.fail_count == 2
