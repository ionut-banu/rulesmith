"""The app stylesheet: served from inside the package and linked by every page."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import rulesmith
from rulesmith import app as app_module
from rulesmith.app import app
from rulesmith.db import get_db
from rulesmith.models import Dataset, Rule, RuleResult, Run, Upload, get_session_factory, init_db

STYLESHEET_URL = "/static/app.css"
STYLESHEET_LINK = f'<link rel="stylesheet" href="{STYLESHEET_URL}">'
VIEWPORT_META = '<meta name="viewport" content="width=device-width, initial-scale=1">'


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


def _seed(client):
    """One dataset with a rule and a run holding a fail result. Returns ids."""
    with client.session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()
        rule = Rule(dataset_id=dataset.id, name="not_null", expression="col is not None")
        upload = Upload(
            dataset_id=dataset.id,
            original_filename="orders.csv",
            stored_path="/tmp/whatever.csv",
            format="csv",
        )
        session.add_all([rule, upload])
        session.commit()
        run = Run(dataset_id=dataset.id, upload_id=upload.id)
        session.add(run)
        session.commit()
        result = RuleResult(
            run_id=run.id,
            rule_id=rule.id,
            rule_name="not_null",
            verdict="pass",
            pass_count=3,
            fail_count=0,
        )
        session.add(result)
        session.commit()
        return dataset.id, rule.id, run.id, result.id


def test_stylesheet_is_served_as_css(client):
    response = client.get(STYLESHEET_URL)

    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0].strip() == "text/css"
    assert len(response.text) > 0


def test_static_dir_resolves_from_the_package_not_the_cwd():
    package_dir = Path(rulesmith.__file__).resolve().parent

    assert app_module.STATIC_DIR == package_dir / "static"
    assert (app_module.STATIC_DIR / "app.css").is_file()


def test_stylesheet_is_served_regardless_of_working_directory(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    response = client.get(STYLESHEET_URL)

    assert response.status_code == 200


def test_stylesheet_loads_nothing_from_outside_the_app():
    css = (app_module.STATIC_DIR / "app.css").read_text()

    assert "@import" not in css
    assert "@font-face" not in css
    assert "url(" not in css
    assert "http:" not in css and "https:" not in css


def test_stylesheet_follows_the_os_colour_scheme():
    css = (app_module.STATIC_DIR / "app.css").read_text()

    assert "color-scheme: light dark" in css
    assert "@media (prefers-color-scheme: dark)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_every_full_page_links_one_stylesheet_and_the_viewport_meta(client):
    dataset_id, rule_id, run_id, _ = _seed(client)
    pages = [
        "/datasets",
        f"/datasets/{dataset_id}",
        f"/datasets/{dataset_id}/rules/new",
        f"/datasets/{dataset_id}/rules/{rule_id}/edit",
        f"/datasets/{dataset_id}/runs/{run_id}",
    ]

    for page in pages:
        response = client.get(page)
        assert response.status_code == 200, page
        head = response.text.split("</head>", 1)[0]
        assert head.count(STYLESHEET_LINK) == 1, page
        assert len(re.findall(r'<link rel="stylesheet"', response.text)) == 1, page
        assert VIEWPORT_META in head, page
        assert "<style" not in response.text, page


def test_failing_rows_fragment_stays_bare(client):
    dataset_id, _, run_id, result_id = _seed(client)

    response = client.get(
        f"/datasets/{dataset_id}/runs/{run_id}/rule-results/{result_id}/failing-rows"
    )

    assert response.status_code == 200
    text = response.text.lower()
    for tag in ("<html", "<body", "<link", "<style"):
        assert tag not in text


def test_htmx_script_tag_is_unchanged(client):
    response = client.get("/datasets")

    assert '<script src="https://unpkg.com/htmx.org@1.9.12" defer></script>' in response.text
