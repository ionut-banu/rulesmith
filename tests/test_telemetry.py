import io
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
import sqlalchemy
from sqlalchemy.pool import StaticPool

from rulesmith import telemetry
from rulesmith.app import app
from rulesmith.db import get_db
from rulesmith.models import Dataset, Rule, Run, Upload, get_session_factory, init_db
from rulesmith.routes import uploads as uploads_module

FIXTURES = Path(__file__).parent / "fixtures" / "loader"
MARKER = "SECRET-MARKER-9137"


class Env:
    pass


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    spans = InMemorySpanExporter()
    reader = InMemoryMetricReader()
    logs = InMemoryLogRecordExporter()
    tel = telemetry.build_telemetry(
        [SimpleSpanProcessor(spans)], [reader], [SimpleLogRecordProcessor(logs)]
    )
    telemetry.activate(app, tel)

    # Created after activation so the SQLAlchemy instrumentation sees the engine.
    engine = sqlalchemy.create_engine(
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
    monkeypatch.setattr(uploads_module, "UPLOAD_DIR", tmp_path / "uploads")

    e = Env()
    e.spans, e.reader, e.logs, e.session_factory = spans, reader, logs, session_factory
    e.client = TestClient(app, raise_server_exceptions=False)
    with e.client:
        yield e
    app.dependency_overrides.clear()
    telemetry.deactivate(app)


def _dataset(env, rules=()):
    with env.session_factory() as session:
        dataset = Dataset(name="orders")
        session.add(dataset)
        session.commit()
        for name, expression in rules:
            session.add(Rule(dataset_id=dataset.id, name=name, expression=expression))
        session.commit()
        return dataset.id


def _server(spans):
    return next(s for s in spans if s.kind == SpanKind.SERVER)


def _upload(env, dataset_id, fixture="valid.csv", filename=None):
    env.spans.clear()  # drop the spans of test setup
    with (FIXTURES / fixture).open("rb") as f:
        return env.client.post(
            f"/datasets/{dataset_id}/uploads",
            files={"file": (filename or fixture, f, "application/octet-stream")},
            follow_redirects=False,
        )


def _points(env, name):
    """{attributes-as-tuple: value-or-count} for one metric; {} if absent."""
    data = env.reader.get_metrics_data()
    out = {}
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name:
                    for p in metric.data.data_points:
                        value = p.count if hasattr(p, "count") else p.value
                        out[tuple(sorted(p.attributes.items()))] = value
    return out


def _outcomes(env):
    return {dict(k)["outcome"]: v for k, v in _points(env, "rulesmith_checks").items()}


# --- traces -------------------------------------------------------------------


def test_upload_span_has_child_spans_and_instrumented_db_spans(env):
    dataset_id = _dataset(env, [("a", "age > 18")])
    assert _upload(env, dataset_id).status_code == 303

    spans = env.spans.get_finished_spans()
    by_name = {s.name: s for s in spans}
    server = _server(spans)
    for name in ("load_file", "evaluate_rules", "persist_results"):
        assert by_name[name].parent.span_id == server.context.span_id
        assert by_name[name].context.trace_id == server.context.trace_id
    # Statement spans (not just `connect`) carry the parametrised SQL text.
    statements = [s.attributes["db.statement"] for s in spans if "db.statement" in s.attributes]
    assert any(st.lstrip().upper().startswith("SELECT") for st in statements)
    assert any(st.lstrip().upper().startswith("INSERT") for st in statements)
    assert all(s.attributes.get("db.system") == "sqlite" for s in spans if "db.statement" in s.attributes)


def test_unhandled_exception_ends_spans_with_error_status_and_no_message(env, monkeypatch):
    def boom(df, rules):
        raise RuntimeError(f"leaked {MARKER}")

    monkeypatch.setattr(uploads_module, "run_rules", boom)
    dataset_id = _dataset(env, [("a", "age > 18")])
    assert _upload(env, dataset_id).status_code == 500

    spans = env.spans.get_finished_spans()
    assert all(s.end_time for s in spans)
    evaluate = next(s for s in spans if s.name == "evaluate_rules")
    assert evaluate.status.status_code == StatusCode.ERROR
    server = _server(spans)
    assert server.status.status_code == StatusCode.ERROR
    assert MARKER not in "".join(s.to_json() for s in spans)
    exc_events = [e for s in spans for e in s.events if e.name == "exception"]
    assert exc_events
    assert all(set(e.attributes) == {"exception.type"} for e in exc_events)
    assert {e.attributes["exception.type"] for e in exc_events} == {"RuntimeError"}
    assert _outcomes(env) == {"error": 1}


# --- metrics ------------------------------------------------------------------


def test_completed_upload_counts_one_check_and_verdicts(env):
    dataset_id = _dataset(env, [("p", "age > 0"), ("f", "age > 30"), ("b", "nope > 1")])
    assert _upload(env, dataset_id).status_code == 303

    assert _outcomes(env) == {"completed": 1}
    verdicts = {dict(k)["verdict"]: v for k, v in _points(env, "rulesmith_rules_evaluated").items()}
    assert verdicts == {"pass": 1, "fail": 1, "broken": 1}
    durations = _points(env, "rulesmith_check_duration")
    assert list(durations.values()) == [1]
    assert [dict(k) for k in durations] == [{"outcome": "completed"}]


def test_run_with_zero_rules_adds_no_verdicts(env):
    dataset_id = _dataset(env)
    assert _upload(env, dataset_id).status_code == 303
    assert _outcomes(env) == {"completed": 1}
    assert _points(env, "rulesmith_rules_evaluated") == {}


def test_rejections_are_counted_as_rejected(env, monkeypatch):
    dataset_id = _dataset(env)
    assert _upload(env, dataset_id, filename="x.txt").status_code == 422
    assert _upload(env, dataset_id, "malformed.csv").status_code == 422
    assert _upload(env, 9999).status_code == 404
    monkeypatch.setattr(uploads_module, "MAX_UPLOAD_SIZE", 5)
    assert _upload(env, dataset_id).status_code == 413

    assert _outcomes(env) == {"rejected": 4}
    assert _points(env, "rulesmith_rules_evaluated") == {}


def test_request_without_file_part_is_counted_once_as_rejected(env):
    dataset_id = _dataset(env)
    env.spans.clear()
    env.logs.clear()
    response = env.client.post(f"/datasets/{dataset_id}/uploads", data={"x": "y"})
    assert response.status_code == 422

    assert _outcomes(env) == {"rejected": 1}
    records = [
        r.log_record for r in env.logs.get_finished_logs() if r.log_record.body == "upload checked"
    ]
    assert len(records) == 1
    assert records[0].attributes["outcome"] == "rejected"


def test_storage_failure_is_counted_as_error(env, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError(f"disk {MARKER}")

    monkeypatch.setattr(uploads_module.shutil, "move", fail)
    dataset_id = _dataset(env)
    assert _upload(env, dataset_id).status_code == 500
    assert _outcomes(env) == {"error": 1}
    server = _server(env.spans.get_finished_spans())
    assert server.status.status_code == StatusCode.ERROR


def test_metric_labels_are_bounded_and_resource_has_environment(env, monkeypatch):
    dataset_id = _dataset(env, [("p", "age > 0")])
    _upload(env, dataset_id)
    _upload(env, dataset_id, filename="x.txt")
    for name in ("rulesmith_checks", "rulesmith_check_duration", "rulesmith_rules_evaluated"):
        for key in _points(env, name):
            assert {k for k, _ in key} <= {"outcome", "verdict"}
    resource = env.reader.get_metrics_data().resource_metrics[0].resource.attributes
    assert resource["service.name"] == "rulesmith"
    assert resource["deployment.environment"] == "local"
    assert resource["service.version"] == "dev"


def test_resource_attributes_come_from_environment(monkeypatch):
    monkeypatch.setenv("RULESMITH_ENV", "staging")
    monkeypatch.setenv("RULESMITH_VERSION", "1.2.3")
    attrs = telemetry.build_resource().attributes
    assert attrs["deployment.environment"] == "staging"
    assert attrs["service.version"] == "1.2.3"


# --- logs ---------------------------------------------------------------------


def test_upload_log_has_trace_ids_and_fields_but_no_filename(env):
    dataset_id = _dataset(env, [("p", "age > 0"), ("f", "age > 30")])
    _upload(env, dataset_id, filename=f"{MARKER}.csv")

    server = _server(env.spans.get_finished_spans())
    records = [r.log_record for r in env.logs.get_finished_logs()]
    upload_logs = [r for r in records if r.body == "upload checked"]
    assert len(upload_logs) == 1
    rec = upload_logs[0]
    assert rec.trace_id == server.context.trace_id
    assert rec.span_id == server.context.span_id
    attrs = rec.attributes
    assert attrs["dataset_id"] == dataset_id
    assert attrs["outcome"] == "completed"
    assert attrs["row_count"] == 3
    assert attrs["format"] == "csv"
    assert attrs["pass_count"] == 1 and attrs["fail_count"] == 1 and attrs["broken_count"] == 0
    assert "run_id" in attrs
    assert "filename" not in attrs
    for r in records:
        if r.trace_id:
            assert r.trace_id == server.context.trace_id


def test_stdout_logs_are_json_with_trace_ids(env, capsys):
    telemetry.setup_logging()
    dataset_id = _dataset(env)
    _upload(env, dataset_id)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    rec = next(r for r in lines if r["message"] == "upload checked")
    server = _server(env.spans.get_finished_spans())
    assert rec["trace_id"] == f"{server.context.trace_id:032x}"
    assert rec["span_id"] == f"{server.context.span_id:016x}"
    assert rec["dataset_id"] == dataset_id
    assert "filename" not in rec


def test_json_formatter_logs_exceptions_by_type_only():
    try:
        raise ValueError(f"secret {MARKER}")
    except ValueError:
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", None, __import__("sys").exc_info())
    out = telemetry.JsonFormatter().format(record)
    assert json.loads(out)["error_type"] == "ValueError"
    assert MARKER not in out and "Traceback" not in out


def test_otlp_log_handler_exports_exception_type_only(env):
    log = logging.getLogger("rulesmith.test")
    try:
        raise ValueError(f"secret {MARKER}")
    except ValueError:
        log.error("failed", exc_info=True, extra={"filename_x": MARKER, "dataset_id": 1})
    rec = next(r.log_record for r in env.logs.get_finished_logs() if r.log_record.body == "failed")
    assert rec.attributes["error_type"] == "ValueError"
    assert MARKER not in repr(rec.attributes)
    assert "exception.stacktrace" not in rec.attributes


# --- privacy ------------------------------------------------------------------


def test_marker_in_filename_and_rule_expression_appears_nowhere(env, capsys):
    dataset_id = _dataset(env, [(f"r-{MARKER}", f"age > 0 and name != '{MARKER}'")])
    assert _upload(env, dataset_id, filename=f"{MARKER}.csv").status_code == 303
    first_spans = list(env.spans.get_finished_spans())
    assert _upload(env, dataset_id, "malformed.csv", filename=f"{MARKER}.csv").status_code == 422
    assert _upload(env, dataset_id, filename=f"{MARKER}.txt").status_code == 422

    assert MARKER in str(env.session_factory().__enter__().execute(
        __import__("sqlalchemy").text("select expression from rules")
    ).scalar())  # sanity: the marker really is in the database

    spans = first_spans + list(env.spans.get_finished_spans())
    # The upload's INSERT (which binds the marker file name) was traced, with
    # placeholders only: the marker is a bound parameter and must not show up.
    assert any(
        s.attributes.get("db.statement", "").lstrip().upper().startswith("INSERT INTO UPLOADS")
        for s in spans
    )
    assert MARKER not in "".join(s.to_json() for s in spans)
    data = env.reader.get_metrics_data()
    assert MARKER not in repr([m.data.data_points for rm in data.resource_metrics
                               for sm in rm.scope_metrics for m in sm.metrics])
    logs = [r.log_record for r in env.logs.get_finished_logs()]
    assert MARKER not in repr([(r.body, r.attributes) for r in logs])
    assert MARKER not in capsys.readouterr().out


# --- setup --------------------------------------------------------------------


def test_setup_without_endpoint_creates_no_exporter_and_is_idempotent(monkeypatch, capsys):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    before = len(logging.getLogger().handlers)
    telemetry.setup(app)
    telemetry.setup(app)
    assert len(logging.getLogger().handlers) == before
    assert telemetry._current.tracer_provider is None
    assert not getattr(app, "_is_instrumented_by_opentelemetry", False)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert "opentelemetry" not in capsys.readouterr().out.lower()


def test_activate_twice_does_not_duplicate_instrumentation(env):
    handlers = len(logging.getLogger().handlers)
    telemetry.activate(app, telemetry.build_telemetry([SimpleSpanProcessor(env.spans)], [], []))
    assert len(logging.getLogger().handlers) == handlers
    dataset_id = _dataset(env)
    _upload(env, dataset_id)
    servers = [s for s in env.spans.get_finished_spans() if s.kind == SpanKind.SERVER]
    assert len(servers) == 1


def test_warning_limiter_allows_one_per_signal_per_interval():
    limiter = telemetry._OtelWarningLimiter(interval=3600)

    def rec(name, level=logging.WARNING):
        return logging.LogRecord(name, level, __file__, 1, "m", None, None)

    trace_name = "opentelemetry.exporter.otlp.proto.http.trace_exporter"
    metric_name = "opentelemetry.exporter.otlp.proto.http.metric_exporter"
    assert limiter.filter(rec(trace_name))
    assert not limiter.filter(rec(trace_name))
    assert limiter.filter(rec(metric_name))
    assert limiter.filter(rec("rulesmith.x"))
    assert limiter.filter(rec("rulesmith.x"))


# --- failure switch (RULESMITH_FAIL_UPLOADS) ----------------------------------


def test_switch_unset_or_empty_leaves_uploads_normal(env, monkeypatch):
    dataset_id = _dataset(env)
    monkeypatch.delenv("RULESMITH_FAIL_UPLOADS", raising=False)
    assert _upload(env, dataset_id).status_code == 303
    monkeypatch.setenv("RULESMITH_FAIL_UPLOADS", "")
    assert _upload(env, dataset_id).status_code == 303
    assert _outcomes(env) == {"completed": 2}


def test_switch_fails_valid_uploads_as_error_without_persisting(env, monkeypatch, tmp_path):
    monkeypatch.setenv("RULESMITH_FAIL_UPLOADS", "1")
    monkeypatch.setenv("RULESMITH_ENV", "local")
    dataset_id = _dataset(env, [("a", "age > 18")])
    assert _upload(env, dataset_id).status_code == 500
    assert _outcomes(env) == {"error": 1}
    assert not (tmp_path / "uploads" / str(dataset_id)).exists()
    with env.session_factory() as session:
        assert session.query(Upload).count() == 0
        assert session.query(Run).count() == 0


def test_switch_does_not_turn_rejections_into_errors(env, monkeypatch):
    monkeypatch.setenv("RULESMITH_FAIL_UPLOADS", "1")
    dataset_id = _dataset(env)
    assert _upload(env, dataset_id, "malformed.csv").status_code == 422
    assert _upload(env, dataset_id, "valid.csv", filename="a.txt").status_code == 422
    assert _outcomes(env) == {"rejected": 2}
