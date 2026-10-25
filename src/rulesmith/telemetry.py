"""OpenTelemetry setup and the small API the web layer uses around it.

Telemetry is opt-in: with `OTEL_EXPORTER_OTLP_ENDPOINT` unset no exporter is
created and the span/metric helpers are no-ops. JSON logging on stdout is
always on.

Privacy (see AGENTS.md): nothing derived from uploaded files may reach a span,
metric or log. Exceptions are therefore recorded by type only, and a span
processor scrubs the exception messages that auto-instrumentation records.
"""

import copy
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, SpanProcessor, TracerProvider
from opentelemetry.trace import Status, StatusCode

ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
_MARKER = "_rulesmith_handler"
_WARNING_INTERVAL_SECONDS = 60.0

# Record attributes the JSON formatter and the OTLP handler pass on besides the
# standard ones. An allowlist, so a stray `extra=` can never leak a value.
SAFE_FIELDS = (
    "dataset_id",
    "run_id",
    "outcome",
    "row_count",
    "format",
    "pass_count",
    "fail_count",
    "broken_count",
    "error_type",
)


# --- state ------------------------------------------------------------------


@dataclass
class Telemetry:
    tracer: trace.Tracer
    checks: metrics.Counter
    duration: metrics.Histogram
    rules_evaluated: metrics.Counter
    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None
    logger_provider: LoggerProvider | None = None
    handlers: list[logging.Handler] = field(default_factory=list)


def _instruments(meter: metrics.Meter):
    # Names as queried in Prometheus minus the suffixes its translation adds
    # (`_total` for counters, `_seconds` from the unit for the histogram).
    return (
        meter.create_counter("rulesmith_checks", description="Upload checks by outcome"),
        meter.create_histogram(
            "rulesmith_check_duration",
            unit="s",
            description="Seconds per upload request",
            # The SDK default buckets suit milliseconds, not seconds.
            explicit_bucket_boundaries_advisory=[
                0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30,
            ],
        ),
        meter.create_counter("rulesmith_rules_evaluated", description="Rule verdicts"),
    )


def _noop() -> Telemetry:
    return Telemetry(trace.NoOpTracer(), *_instruments(metrics.NoOpMeter("rulesmith")))


_current: Telemetry = _noop()
_setup_done = False
_lock = threading.Lock()


# --- helpers used by the routes ----------------------------------------------


@contextmanager
def span(name: str) -> Iterator[trace.Span]:
    """A child span of the current one.

    On an exception the span gets error status and an `exception` event that
    carries the exception type only, then the exception propagates.
    """
    with _current.tracer.start_as_current_span(
        name, record_exception=False, set_status_on_exception=False
    ) as sp:
        try:
            yield sp
        except BaseException as exc:
            mark_error(sp, exc)
            raise


def mark_error(sp: trace.Span, exc: BaseException | None = None) -> None:
    """Error status without a description; the exception is recorded by type."""
    if not sp.is_recording():
        return
    sp.set_status(Status(StatusCode.ERROR))
    if exc is not None:
        sp.add_event("exception", {"exception.type": type(exc).__name__})


def record_check(outcome: str, seconds: float) -> None:
    _current.checks.add(1, {"outcome": outcome})
    _current.duration.record(seconds, {"outcome": outcome})


def record_verdicts(counts: dict[str, int]) -> None:
    for verdict, count in counts.items():
        if count:
            _current.rules_evaluated.add(count, {"verdict": verdict})


# --- logging ------------------------------------------------------------------


def _trace_ids() -> tuple[str, str] | None:
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None
    return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"


class JsonFormatter(logging.Formatter):
    """One JSON object per record. Exceptions are logged by type only."""

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        ids = _trace_ids()
        if ids:
            data["trace_id"], data["span_id"] = ids
        for key in SAFE_FIELDS:
            if hasattr(record, key):
                data[key] = getattr(record, key)
        if record.exc_info and record.exc_info[0] is not None:
            data["error_type"] = record.exc_info[0].__name__
        return json.dumps(data, default=str)


class _StdoutHandler(logging.StreamHandler):
    """Writes to whatever `sys.stdout` is at emit time (tests swap it)."""

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, value) -> None:  # StreamHandler.__init__ assigns it
        pass


class _OtelWarningLimiter(logging.Filter):
    """Lets one warning-or-worse record per exporter signal through per minute.

    An unreachable collector makes each exporter log on every failed export;
    this keeps that from flooding the logs.
    """

    def __init__(self, interval: float = _WARNING_INTERVAL_SECONDS) -> None:
        super().__init__()
        self._interval = interval
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _signal(name: str) -> str:
        for signal in ("trace", "metric", "log", "span"):
            if signal in name:
                return "traces" if signal == "span" else signal
        return name

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.name.startswith("opentelemetry") or record.levelno < logging.WARNING:
            return True
        key = self._signal(record.name)
        now = time.monotonic()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < self._interval:
                return False
            self._last[key] = now
        return True


class _OtlpLogHandler(LoggingHandler):
    """OTLP log handler that never exports exception messages or tracebacks."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("opentelemetry"):  # no feedback loop from exporter errors
            return
        record = copy.copy(record)
        if record.exc_info and record.exc_info[0] is not None:
            record.error_type = record.exc_info[0].__name__
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        for key in list(vars(record)):
            if key not in _STANDARD_ATTRS and key not in SAFE_FIELDS:
                delattr(record, key)
        super().emit(record)


_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


def setup_logging() -> None:
    """JSON logs on stdout for everything, uvicorn included. Idempotent."""
    root = logging.getLogger()
    if not any(getattr(h, _MARKER, False) for h in root.handlers):
        handler = _StdoutHandler()
        handler.setFormatter(JsonFormatter())
        handler.addFilter(_OtelWarningLimiter())
        setattr(handler, _MARKER, True)
        root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    # Uvicorn installs its own plain-text handlers; route them through ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True


# --- exporting ----------------------------------------------------------------


class _ExceptionScrubber(SpanProcessor):
    """Strips exception messages and stack traces from finished spans.

    The ASGI instrumentation records unhandled exceptions with their message,
    which can embed file names or content. Must be the first processor.
    """

    def on_end(self, span) -> None:
        events = getattr(span, "_events", None)
        if events:
            span._events = [
                Event(
                    e.name,
                    {"exception.type": e.attributes.get("exception.type")}
                    if e.name == "exception"
                    else e.attributes,
                    e.timestamp,
                )
                for e in events
            ]
        status = getattr(span, "_status", None)
        if status is not None and status.status_code == StatusCode.ERROR and status.description:
            span._status = Status(StatusCode.ERROR)


def build_resource() -> Resource:
    return Resource.create(
        {
            "service.name": "rulesmith",
            "deployment.environment": os.environ.get("RULESMITH_ENV") or "local",
            "service.version": os.environ.get("RULESMITH_VERSION") or "dev",
        }
    )


def build_telemetry(span_processors, metric_readers, log_processors) -> Telemetry:
    """Providers and instruments around the given processors/readers (not global)."""
    resource = build_resource()
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(_ExceptionScrubber())
    for processor in span_processors:
        tracer_provider.add_span_processor(processor)
    meter_provider = MeterProvider(resource=resource, metric_readers=list(metric_readers))
    logger_provider = LoggerProvider(resource=resource)
    for processor in log_processors:
        logger_provider.add_log_record_processor(processor)
    checks, duration, rules_evaluated = _instruments(meter_provider.get_meter("rulesmith"))
    return Telemetry(
        tracer=tracer_provider.get_tracer("rulesmith"),
        checks=checks,
        duration=duration,
        rules_evaluated=rules_evaluated,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        logger_provider=logger_provider,
    )


def activate(app, tel: Telemetry) -> None:
    """Make `tel` current: instrument FastAPI/SQLAlchemy and attach the OTLP log handler."""
    global _current
    deactivate(app)
    _current = tel
    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=tel.tracer_provider, meter_provider=tel.meter_provider
    )
    # instrument_app only wraps the stack builder; rebuild if a stack already exists.
    app.middleware_stack = app.build_middleware_stack()
    instrumentor = SQLAlchemyInstrumentor()
    if not instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.instrument(tracer_provider=tel.tracer_provider, enable_commenter=False)
    handler = _OtlpLogHandler(logger_provider=tel.logger_provider)
    logging.getLogger().addHandler(handler)
    tel.handlers.append(handler)


def deactivate(app) -> None:
    """Undo `activate` (shutdown and tests). Safe to call when inactive."""
    global _current
    if getattr(app, "_is_instrumented_by_opentelemetry", False):
        FastAPIInstrumentor.uninstrument_app(app)
    instrumentor = SQLAlchemyInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()
    for handler in _current.handlers:
        logging.getLogger().removeHandler(handler)
    _current = _noop()


def _otlp_telemetry() -> Telemetry:
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    interval = None if os.environ.get("OTEL_METRIC_EXPORT_INTERVAL") else 15000
    return build_telemetry(
        [BatchSpanProcessor(OTLPSpanExporter())],
        [PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=interval)],
        [BatchLogRecordProcessor(OTLPLogExporter())],
    )


def setup(app) -> None:
    """Configure logging, and telemetry when the endpoint is set. Idempotent."""
    global _setup_done
    with _lock:
        setup_logging()
        if _setup_done or not os.environ.get(ENDPOINT_ENV):
            return
        _setup_done = True
        tel = _otlp_telemetry()
        activate(app, tel)
        import atexit

        atexit.register(_shutdown, tel)


def _shutdown(tel: Telemetry) -> None:
    for provider in (tel.tracer_provider, tel.meter_provider, tel.logger_provider):
        try:
            provider.shutdown()
        except Exception:  # noqa: BLE001 - exit path, nothing useful to do
            pass
