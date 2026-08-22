import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from rulesmith.app import app
from rulesmith.db import get_db
from rulesmith.models import Dataset, get_session_factory, init_db


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


def test_list_datasets_empty_state(client):
    response = client.get("/datasets")

    assert response.status_code == 200
    assert "No datasets yet" in response.text


def test_list_datasets_shows_name_and_link(client):
    with client.session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()
        dataset_id = dataset.id

    response = client.get("/datasets")

    assert response.status_code == 200
    assert "orders" in response.text
    assert f'href="/datasets/{dataset_id}"' in response.text


def test_list_datasets_includes_create_form(client):
    response = client.get("/datasets")

    assert response.status_code == 200
    assert '<form method="post" action="/datasets">' in response.text
    assert 'name="name"' in response.text


def test_create_dataset_with_valid_name_redirects_to_detail(client):
    response = client.post("/datasets", data={"name": "orders"}, follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/datasets/")

    with client.session_factory() as session:
        assert session.query(Dataset).count() == 1
        assert session.query(Dataset).first().name == "orders"


def test_create_dataset_follow_redirect_shows_detail_page(client):
    response = client.post("/datasets", data={"name": "orders"})

    assert response.status_code == 200
    assert "orders" in response.text


def test_create_dataset_with_empty_name_creates_no_row(client):
    response = client.post("/datasets", data={"name": ""})

    assert response.status_code == 422
    assert response.status_code != 500

    with client.session_factory() as session:
        assert session.query(Dataset).count() == 0


def test_create_dataset_with_whitespace_only_name_creates_no_row(client):
    response = client.post("/datasets", data={"name": "   "})

    assert response.status_code == 422

    with client.session_factory() as session:
        assert session.query(Dataset).count() == 0


def test_create_dataset_with_empty_name_rerenders_list_with_error(client):
    response = client.post("/datasets", data={"name": "   "})

    assert response.status_code == 422
    assert "Datasets" in response.text
    assert "error" in response.text.lower() or "empty" in response.text.lower()


def test_two_datasets_may_share_the_same_name(client):
    first = client.post("/datasets", data={"name": "orders"}, follow_redirects=False)
    second = client.post("/datasets", data={"name": "orders"}, follow_redirects=False)

    assert first.status_code == 303
    assert second.status_code == 303
    assert first.headers["location"] != second.headers["location"]

    with client.session_factory() as session:
        assert session.query(Dataset).count() == 2


def test_dataset_detail_renders_name_and_created_at(client):
    with client.session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()
        dataset_id = dataset.id
        created_at = dataset.created_at

    response = client.get(f"/datasets/{dataset_id}")

    assert response.status_code == 200
    assert "orders" in response.text
    assert str(created_at.year) in response.text


def test_dataset_detail_renders_placeholder_rules_and_runs_sections(client):
    with client.session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()
        dataset_id = dataset.id

    response = client.get(f"/datasets/{dataset_id}")

    assert response.status_code == 200
    assert "Rules" in response.text
    assert "Runs" in response.text


def test_dataset_detail_for_nonexistent_id_returns_404(client):
    response = client.get("/datasets/999999")

    assert response.status_code == 404


def test_dataset_name_with_script_tag_is_escaped_not_executed(client):
    malicious_name = "<script>alert(1)</script>"

    create_response = client.post(
        "/datasets", data={"name": malicious_name}, follow_redirects=False
    )
    dataset_id = create_response.headers["location"].rsplit("/", 1)[-1]

    detail_response = client.get(f"/datasets/{dataset_id}")

    assert "<script>alert(1)</script>" not in detail_response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in detail_response.text

    list_response = client.get("/datasets")
    assert "<script>alert(1)</script>" not in list_response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in list_response.text


def test_very_long_dataset_name_is_accepted_and_rendered(client):
    long_name = "a" * 5000

    create_response = client.post(
        "/datasets", data={"name": long_name}, follow_redirects=False
    )

    assert create_response.status_code == 303

    dataset_id = create_response.headers["location"].rsplit("/", 1)[-1]
    detail_response = client.get(f"/datasets/{dataset_id}")

    assert detail_response.status_code == 200
    assert long_name in detail_response.text
