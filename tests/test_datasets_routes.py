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


def test_dataset_detail_shows_upload_form(client):
    with client.session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()
        dataset_id = dataset.id

    response = client.get(f"/datasets/{dataset_id}")

    assert response.status_code == 200
    assert (
        f'<form method="post" action="/datasets/{dataset_id}/uploads" '
        'enctype="multipart/form-data">' in response.text
    )
    assert '<input type="file" id="file" name="file">' in response.text


def test_dataset_detail_upload_form_present_with_no_rules_or_runs(client):
    with client.session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()
        dataset_id = dataset.id

    response = client.get(f"/datasets/{dataset_id}")

    assert response.status_code == 200
    assert "No rules yet." in response.text
    assert "No runs yet." in response.text
    assert 'id="upload"' in response.text
    assert '<input type="file" id="file" name="file">' in response.text


def test_dataset_detail_upload_form_submits_valid_file_and_redirects_to_run(client):
    dataset_id = _make_dataset(client.session_factory)

    response = client.post(
        f"/datasets/{dataset_id}/uploads",
        files={"file": ("data.csv", b"a,b\n1,2\n", "text/csv")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(
        f"/datasets/{dataset_id}/runs/"
    )


def test_dataset_detail_upload_form_submits_invalid_file_shows_error_on_same_page(
    client,
):
    dataset_id = _make_dataset(client.session_factory)

    response = client.post(
        f"/datasets/{dataset_id}/uploads",
        files={"file": ("data.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert '<p class="error">' in response.text
    assert 'id="upload"' in response.text


def _make_dataset(session_factory, name="orders"):
    with session_factory() as session:
        dataset = Dataset(name=name)
        session.add(dataset)
        session.commit()
        return dataset.id


def _make_run(session_factory, dataset_id, filename="upload.csv", results=None, created_at=None):
    """Create an Upload/Run (and attached RuleResults) for an existing dataset.

    `results` is a list of dicts with keys matching RuleResult fields.
    Returns the run id.
    """
    with session_factory() as session:
        upload = Upload(
            dataset_id=dataset_id,
            original_filename=filename,
            stored_path="/tmp/whatever.csv",
            format="csv",
        )
        session.add(upload)
        session.commit()

        run = Run(dataset_id=dataset_id, upload_id=upload.id)
        if created_at is not None:
            run.created_at = created_at
        session.add(run)
        session.commit()

        for spec in results or []:
            session.add(RuleResult(run_id=run.id, **spec))
        session.commit()

        return run.id


def test_dataset_with_zero_runs_shows_empty_state(client):
    dataset_id = _make_dataset(client.session_factory)

    response = client.get(f"/datasets/{dataset_id}")

    assert response.status_code == 200
    assert "No runs yet." in response.text


def test_dataset_runs_listed_newest_first_with_summaries_and_links(client):
    from datetime import datetime, timedelta, timezone

    dataset_id = _make_dataset(client.session_factory)
    base = datetime.now(timezone.utc)

    run1_id = _make_run(
        client.session_factory,
        dataset_id,
        filename="first.csv",
        results=[
            {"rule_id": None, "rule_name": "R1", "verdict": "pass", "pass_count": 3, "fail_count": 0},
        ],
        created_at=base - timedelta(hours=2),
    )
    run2_id = _make_run(
        client.session_factory,
        dataset_id,
        filename="second.csv",
        results=[
            {"rule_id": None, "rule_name": "R1", "verdict": "pass", "pass_count": 2, "fail_count": 0},
            {"rule_id": None, "rule_name": "R2", "verdict": "fail", "pass_count": 1, "fail_count": 1},
        ],
        created_at=base,
    )

    response = client.get(f"/datasets/{dataset_id}")

    assert response.status_code == 200
    text = response.text
    assert "first.csv" in text
    assert "second.csv" in text
    assert f'href="/datasets/{dataset_id}/runs/{run1_id}"' in text
    assert f'href="/datasets/{dataset_id}/runs/{run2_id}"' in text
    # Newest first: run2 entry appears before run1 entry.
    assert text.index(f"runs/{run2_id}") < text.index(f"runs/{run1_id}")
    assert "1 passed, 0 failed, 0 broken" in text
    assert "1 passed, 1 failed, 0 broken" in text


def test_dataset_run_with_zero_rules_shows_no_rules_evaluated_headline(client):
    dataset_id = _make_dataset(client.session_factory)
    _make_run(client.session_factory, dataset_id, results=[])

    response = client.get(f"/datasets/{dataset_id}")

    assert response.status_code == 200
    assert "no rules were evaluated" in response.text.lower()
    assert "0 passed, 0 failed, 0 broken" not in response.text


def test_dataset_with_51_runs_shows_only_50_and_more_runs_note(client):
    from datetime import datetime, timedelta, timezone

    dataset_id = _make_dataset(client.session_factory)
    base = datetime.now(timezone.utc)

    run_ids = []
    for i in range(51):
        run_ids.append(
            _make_run(
                client.session_factory,
                dataset_id,
                filename=f"file{i}.csv",
                results=[],
                created_at=base - timedelta(minutes=51 - i),
            )
        )

    response = client.get(f"/datasets/{dataset_id}")

    assert response.status_code == 200
    text = response.text
    # The oldest run (run_ids[0]) should not be shown; the newest should be.
    assert f"runs/{run_ids[0]}\"" not in text
    assert f"runs/{run_ids[-1]}\"" in text
    assert "more runs than shown" in text.lower()


def test_dataset_with_exactly_50_runs_shows_all_and_no_more_runs_note(client):
    from datetime import datetime, timedelta, timezone

    dataset_id = _make_dataset(client.session_factory)
    base = datetime.now(timezone.utc)

    run_ids = []
    for i in range(50):
        run_ids.append(
            _make_run(
                client.session_factory,
                dataset_id,
                filename=f"file{i}.csv",
                results=[],
                created_at=base - timedelta(minutes=50 - i),
            )
        )

    response = client.get(f"/datasets/{dataset_id}")

    assert response.status_code == 200
    text = response.text
    for run_id in run_ids:
        assert f"runs/{run_id}\"" in text
    assert "more runs than shown" not in text.lower()


def test_dataset_run_list_upload_filename_with_script_tag_is_escaped(client):
    dataset_id = _make_dataset(client.session_factory)
    _make_run(client.session_factory, dataset_id, filename="<script>alert(1)</script>", results=[])

    response = client.get(f"/datasets/{dataset_id}")

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_dataset_run_entry_links_to_run_detail_page(client):
    dataset_id = _make_dataset(client.session_factory)
    run_id = _make_run(client.session_factory, dataset_id, results=[])

    response = client.get(f"/datasets/{dataset_id}")
    assert f'href="/datasets/{dataset_id}/runs/{run_id}"' in response.text

    detail_response = client.get(f"/datasets/{dataset_id}/runs/{run_id}")
    assert detail_response.status_code == 200


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


def test_trend_with_zero_runs_shows_empty_state(client):
    dataset_id = _make_dataset(client.session_factory)

    response = client.get(f"/datasets/{dataset_id}")

    assert response.status_code == 200
    assert "No trend data yet." in response.text
    assert "trend-point" not in response.text


def test_trend_is_labeled_as_row_level_not_rule_level(client):
    dataset_id = _make_dataset(client.session_factory)
    _make_run(
        client.session_factory,
        dataset_id,
        results=[{"rule_id": None, "rule_name": "R1", "verdict": "pass", "pass_count": 10, "fail_count": 0}],
    )

    response = client.get(f"/datasets/{dataset_id}")

    assert response.status_code == 200
    assert "Row pass rate trend" in response.text
    assert "row pass rate" in response.text.lower()
    assert "share of rules passing" in response.text.lower() or "not a share of rules passing" in response.text.lower()


def test_trend_single_eligible_run_renders_one_point(client):
    dataset_id = _make_dataset(client.session_factory)
    run_id = _make_run(
        client.session_factory,
        dataset_id,
        results=[{"rule_id": None, "rule_name": "R1", "verdict": "pass", "pass_count": 8, "fail_count": 2}],
    )

    response = client.get(f"/datasets/{dataset_id}")

    assert response.status_code == 200
    assert response.text.count("trend-point") == 1
    assert f"Run {run_id}" in response.text
    assert "80.0% row pass rate" in response.text
    # A single point should not error and should not require a line.
    assert "trend-line" not in response.text


def test_trend_plots_row_level_rate_ordered_oldest_to_newest(client):
    from datetime import datetime, timedelta, timezone

    dataset_id = _make_dataset(client.session_factory)
    base = datetime.now(timezone.utc)

    older_run_id = _make_run(
        client.session_factory,
        dataset_id,
        filename="older.csv",
        results=[{"rule_id": None, "rule_name": "R1", "verdict": "pass", "pass_count": 5, "fail_count": 5}],
        created_at=base - timedelta(hours=2),
    )
    newer_run_id = _make_run(
        client.session_factory,
        dataset_id,
        filename="newer.csv",
        results=[{"rule_id": None, "rule_name": "R1", "verdict": "pass", "pass_count": 9, "fail_count": 1}],
        created_at=base,
    )

    response = client.get(f"/datasets/{dataset_id}")
    text = response.text

    assert response.status_code == 200
    assert text.count("trend-point") == 2
    older_pos = text.index(f"Run {older_run_id}")
    newer_pos = text.index(f"Run {newer_run_id}")
    assert older_pos < newer_pos
    assert "50.0% row pass rate" in text
    assert "90.0% row pass rate" in text


def test_trend_excludes_broken_rule_rows_from_numerator_and_denominator(client):
    dataset_id = _make_dataset(client.session_factory)
    run_id = _make_run(
        client.session_factory,
        dataset_id,
        results=[
            {"rule_id": None, "rule_name": "good_rule", "verdict": "pass", "pass_count": 4, "fail_count": 1},
            {
                "rule_id": None,
                "rule_name": "broken_rule",
                "verdict": "broken",
                "pass_count": None,
                "fail_count": None,
                "broken_reason": "unknown column(s): x",
            },
        ],
    )

    response = client.get(f"/datasets/{dataset_id}")
    text = response.text

    assert response.status_code == 200
    assert f"Run {run_id}" in text
    # 4/5 rows from the non-broken rule only, not diluted by the broken one.
    assert "80.0% row pass rate" in text
    assert "4/5 rows evaluated" in text


def test_trend_excludes_run_with_zero_evaluated_rows_not_plotted_as_zero(client):
    dataset_id = _make_dataset(client.session_factory)
    eligible_run_id = _make_run(
        client.session_factory,
        dataset_id,
        filename="eligible.csv",
        results=[{"rule_id": None, "rule_name": "R1", "verdict": "pass", "pass_count": 3, "fail_count": 0}],
    )
    # Every rule broken -> zero evaluated rows -> must be excluded entirely.
    _make_run(
        client.session_factory,
        dataset_id,
        filename="all-broken.csv",
        results=[
            {
                "rule_id": None,
                "rule_name": "broken_rule",
                "verdict": "broken",
                "pass_count": None,
                "fail_count": None,
                "broken_reason": "boom",
            }
        ],
    )

    response = client.get(f"/datasets/{dataset_id}")
    text = response.text
    trend_section = text[text.index('<section id="trend">'):text.index('<section id="runs">')]

    assert response.status_code == 200
    assert trend_section.count("trend-point") == 1
    assert f"Run {eligible_run_id}" in trend_section
    assert "100.0% row pass rate" in trend_section


def test_trend_run_with_no_rules_defined_has_zero_evaluated_rows_and_is_excluded(client):
    dataset_id = _make_dataset(client.session_factory)
    _make_run(client.session_factory, dataset_id, results=[])

    response = client.get(f"/datasets/{dataset_id}")

    assert response.status_code == 200
    assert "No trend data yet." in response.text
    assert "trend-point" not in response.text


def test_trend_dataset_where_every_run_has_zero_evaluated_rows_shows_empty_state(client):
    dataset_id = _make_dataset(client.session_factory)
    _make_run(
        client.session_factory,
        dataset_id,
        filename="a.csv",
        results=[
            {
                "rule_id": None,
                "rule_name": "broken_rule",
                "verdict": "broken",
                "pass_count": None,
                "fail_count": None,
                "broken_reason": "boom",
            }
        ],
    )
    _make_run(client.session_factory, dataset_id, filename="b.csv", results=[])

    response = client.get(f"/datasets/{dataset_id}")

    assert response.status_code == 200
    assert "No trend data yet." in response.text
    assert "trend-point" not in response.text
    # Same empty-state markup as the zero-runs case.
    zero_run_dataset_id = _make_dataset(client.session_factory, name="empty-dataset")
    zero_runs_response = client.get(f"/datasets/{zero_run_dataset_id}")
    assert "No trend data yet." in zero_runs_response.text
