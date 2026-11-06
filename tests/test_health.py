from fastapi.testclient import TestClient

from rulesmith.app import app

client = TestClient(app)


def test_health_reports_the_version(monkeypatch):
    monkeypatch.setenv("RULESMITH_VERSION", "abc")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "abc"}


def test_health_version_defaults_to_dev_when_unset(monkeypatch):
    monkeypatch.delenv("RULESMITH_VERSION", raising=False)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "dev"}


def test_health_version_defaults_to_dev_when_empty(monkeypatch):
    monkeypatch.setenv("RULESMITH_VERSION", "")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "dev"}
