"""End-to-end scenarios against the real containerized app."""

from conftest import (
    FIXTURES,
    MALFORMED_CSV,
    ORDERS_CSV,
    add_rule,
    count_runs,
    create_dataset,
    parse_run,
    restart_app,
    upload,
    wait_for_health,
)

# Known data in tests/fixtures/integration/orders.csv: 5 rows, 2 with a
# negative amount (customers charlie-bad and echo-bad).
BAD_CUSTOMERS = ("charlie-bad", "echo-bad")
GOOD_CUSTOMERS = ("alpha-ok", "bravo-ok", "delta-ok")


def failing_rows_html(client, verdict):
    resp = client.get(verdict.failing_rows_url)
    assert resp.status_code == 299, "QA throwaway"
    return resp.text


def assert_happy_run(client, happy):
    page = client.get(happy.run_url)
    assert page.status_code == 200
    verdicts = {v.name: v for v in parse_run(page.text)}
    assert set(verdicts) == {happy.pass_rule, happy.fail_rule}
    assert [v.verdict for v in verdicts.values()].count("pass") == 1
    assert [v.verdict for v in verdicts.values()].count("fail") == 1
    assert verdicts[happy.pass_rule].verdict == "pass"
    assert verdicts[happy.pass_rule].counts == "5 passed, 0 failed"
    assert verdicts[happy.fail_rule].verdict == "fail"
    assert verdicts[happy.fail_rule].counts == "3 passed, 2 failed"

    rows = failing_rows_html(client, verdicts[happy.fail_rule])
    for customer in BAD_CUSTOMERS:
        assert customer in rows
    for customer in GOOD_CUSTOMERS:
        assert customer not in rows
    return verdicts


def test_happy_path(client, happy):
    verdicts = assert_happy_run(client, happy)

    report = client.get(happy.run_url.rstrip("/") + "/report.csv")
    assert report.status_code == 200
    assert report.headers["content-type"].startswith("text/csv")
    # The report is per rule: it carries the failing rule with its verdict
    # and failing-row count.
    lines = [line for line in report.text.splitlines() if happy.fail_rule in line]
    assert len(lines) == 1
    assert ",fail,3,2," in lines[0]
    assert verdicts[happy.fail_rule].verdict == "fail"


def test_broken_rule_is_visible_and_other_rules_still_run(client):
    dataset_id, _ = create_dataset(client)
    broken_rule, valid_rule = "uses-missing-column", "positive-amount"
    add_rule(client, dataset_id, broken_rule, "no_such_column > 0")
    add_rule(client, dataset_id, valid_rule, "amount > 0")
    resp = upload(client, dataset_id, ORDERS_CSV)
    assert resp.status_code == 200

    verdicts = {v.name: v for v in parse_run(resp.text)}
    assert verdicts[broken_rule].verdict == "broken"
    assert verdicts[valid_rule].verdict in ("pass", "fail")
    assert "Clean pass" not in resp.text
    assert "BROKEN" in resp.text


def test_data_survives_app_restart(client, happy):
    before_dataset = client.get(f"/datasets/{happy.dataset_id}")
    assert happy.dataset_name in before_dataset.text
    before = assert_happy_run(client, happy)

    restart_app()
    wait_for_health()

    dataset_page = client.get(f"/datasets/{happy.dataset_id}")
    assert dataset_page.status_code == 200
    assert happy.dataset_name in dataset_page.text
    assert happy.pass_rule in dataset_page.text
    assert happy.fail_rule in dataset_page.text

    after = assert_happy_run(client, happy)
    assert {n: (v.verdict, v.counts) for n, v in after.items()} == {
        n: (v.verdict, v.counts) for n, v in before.items()
    }


def test_malformed_upload_is_rejected_without_a_run(client):
    dataset_id, _ = create_dataset(client)
    add_rule(client, dataset_id, "positive-amount", "amount > 0")
    runs_before = count_runs(client, dataset_id)

    resp = upload(client, dataset_id, MALFORMED_CSV)
    assert 400 <= resp.status_code < 500
    assert 'class="error"' in resp.text
    assert "Could not parse uploaded file" in resp.text
    assert count_runs(client, dataset_id) == runs_before


def test_unsupported_extension_is_rejected_without_a_run(client):
    dataset_id, _ = create_dataset(client)
    runs_before = count_runs(client, dataset_id)

    resp = upload(client, dataset_id, ORDERS_CSV, filename="orders.txt")
    assert 400 <= resp.status_code < 500
    assert 'class="error"' in resp.text
    assert "Unrecognized file extension" in resp.text
    assert count_runs(client, dataset_id) == runs_before
