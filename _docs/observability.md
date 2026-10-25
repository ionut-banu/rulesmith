# Observability

The upload-and-check journey (`POST /datasets/{id}/uploads`) emits traces,
metrics and JSON logs that share a trace ID. Telemetry is opt-in: with
`OTEL_EXPORTER_OTLP_ENDPOINT` unset no exporter is created (logs are still JSON
on stdout). Setup lives in `src/rulesmith/telemetry.py`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset (telemetry off) | OTLP/HTTP base URL, protocol `http/protobuf`, e.g. `http://localhost:4318` |
| `RULESMITH_ENV` | `local` | resource attribute `deployment.environment` |
| `RULESMITH_VERSION` | `dev` | resource attribute `service.version` |
| `OTEL_METRIC_EXPORT_INTERVAL` | 15000 ms | metric export interval |

`service.name` is always `rulesmith`. Export is asynchronous; an unreachable
collector never slows requests, and exporter warnings are limited to one per
signal per minute.

## Local stack

`make obs-up` starts `grafana/otel-lgtm` (pinned in
`observability/docker-compose.yaml`, project `rulesmith-obs`, own volume) with
Grafana on http://localhost:3000 (admin / admin) and OTLP on port 4318.
`make obs-down` stops it (add `-v` to the compose command to delete its data).

- App on the host: `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run uvicorn rulesmith.app:app`
- App in Docker: `OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4318 make up`
  (`extra_hosts: host-gateway` makes this work on Linux too)

The dashboard "Rulesmith — checks" is provisioned from files in
`observability/grafana/`; it has `environment` and `version` variables that
filter every panel.

## Manual check

1. `make obs-up`, then start the app with the endpoint set (above).
2. Create a dataset with a rule, upload a CSV to it.
3. Grafana > Explore > Tempo: search `{ resource.service.name = "rulesmith" }`.
   Open the `POST /datasets/{dataset_id}/uploads` trace: it has child spans
   `load_file`, `evaluate_rules`, `persist_results` and SQL spans.
4. From the span use "Logs for this span", or in Explore > Loki run
   `{service_name="rulesmith"} | trace_id="<trace id>"`.
5. Open the "Rulesmith — checks" dashboard (metrics arrive within ~15 s):
   checks per minute, error ratio, p95 duration and rule verdicts move.
   The error-ratio panel shows "No data" when there were no checks.

## Metric contract (relied on by #24)

Names as queried in Prometheus. Resource attributes `deployment_environment`
and `service_version` (plus `service_name`) are promoted to labels by the
stack.

| Metric | Type | Labels |
|---|---|---|
| `rulesmith_checks_total` | counter, +1 per upload request | `outcome` = `completed` / `rejected` / `error` |
| `rulesmith_check_duration_seconds` (`_bucket`, `_sum`, `_count`) | histogram | `outcome` |
| `rulesmith_rules_evaluated_total` | counter, +1 per rule per completed run | `verdict` = `pass` / `fail` / `broken` |

Outcomes: `completed` = run created and 303 returned (broken rules included);
`rejected` = user request refused, no run (bad extension 422, over 20 MB 413,
parse failure 422, unknown dataset 404); `error` = anything else (unhandled
exception, 500 on storage failure).

## Privacy

No file contents, file names, row values or rule expressions reach spans,
metrics or logs; only ids, counts, format and outcome. Exceptions are recorded
by type only (a span processor scrubs messages the ASGI instrumentation
records; the log formatter and OTLP log handler drop messages and tracebacks).

## Known gaps

- Requests rejected by FastAPI before the handler runs (missing `file` part,
  non-integer dataset id) are not counted in `rulesmith_checks_total`.
- Shipping telemetry from hosted (Fly) environments is not set up.
