import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULE_FILE = ROOT / "observability" / "grafana" / "alerting" / "rulesmith-alerts.yaml"


def _load():
    # The file is JSON (valid YAML) with full-line '#' comments.
    lines = [ln for ln in RULE_FILE.read_text().splitlines() if not ln.lstrip().startswith("#")]
    return json.loads("\n".join(lines))


def _rule():
    doc = _load()
    (group,) = doc["groups"]
    (rule,) = group["rules"]
    return group, rule


def test_rule_file_parses_with_expected_shape():
    group, rule = _rule()
    assert rule["title"] == "RulesmithChecksFailing"
    assert group["interval"] == "1m"
    assert rule["for"] == "1m"
    assert rule["noDataState"] == "OK"
    assert rule["execErrState"] == "Error"
    assert rule["labels"]["owner"] == "on-call-agent"
    for label in ("service", "environment", "severity"):
        assert rule["labels"][label]
    for annotation in ("summary", "version", "dashboard_url"):
        assert rule["annotations"][annotation]
    assert "rulesmith-checks" in rule["annotations"]["dashboard_url"]


def test_rule_uses_metric_contract_and_thresholds():
    _, rule = _rule()
    queries = {d["refId"]: d["model"] for d in rule["data"]}
    expr = queries["A"]["expr"]
    assert 'outcome="error"' in expr
    assert 'outcome=~"completed|error"' in expr
    assert "rejected" not in expr.replace('outcome=~"completed|error"', "")
    assert "[5m]" in expr and ">= 3" in expr
    assert "deployment_environment" in expr
    assert queries["C"]["expression"] == "$B >= 0.2"
    assert rule["condition"] == "C"


def test_rule_file_is_mounted_by_the_obs_stack():
    compose = (ROOT / "observability" / "docker-compose.yaml").read_text()
    assert "rulesmith-alerts.yaml" in compose
    assert "provisioning/alerting" in compose
