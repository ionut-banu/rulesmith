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
| `RULESMITH_FAIL_UPLOADS` | unset (off) | test-only failure switch, see *Alerting*; refused when `RULESMITH_ENV` is `prod`/`production` |

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
   `load_file`, `evaluate_rules`, `persist_results` and one span per SQL
   statement (`db.statement` holds the parametrised text with placeholders;
   bound parameter values are never recorded).
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
| `rulesmith_checks_total` | counter, +1 per upload request (a POST with no `file` part counts as `rejected`) | `outcome` = `completed` / `rejected` / `error` |
| `rulesmith_check_duration_seconds` (`_bucket`, `_sum`, `_count`) | histogram | `outcome` |
| `rulesmith_rules_evaluated_total` | counter, +1 per rule per completed run | `verdict` = `pass` / `fail` / `broken` |

Outcomes: `completed` = run created and 303 returned (broken rules included);
`rejected` = user request refused, no run (bad extension 422, over 20 MB 413,
parse failure 422, unknown dataset 404); `error` = anything else (unhandled
exception, 500 on storage failure).

## Alerting

Rule `RulesmithChecksFailing`, provisioned from
`observability/grafana/alerting/rulesmith-alerts.yaml` (mounted by
`observability/docker-compose.yaml`; read-only in the Grafana UI, listed under
Alerting > Alert rules after `make obs-up`, no manual step). Hosted
alerting is a known gap (see below).

- Condition: errors / checks >= 20% **and** errors >= 3 over the last 5m, where
  errors = `sum(increase(rulesmith_checks_total{outcome="error"}[5m]))` and
  checks = the same with `outcome=~"completed|error"`. 20% is a meaningful
  share of users affected; the 3-error floor stops one or two unlucky
  requests from paging at low traffic; 5m smooths the 15 s metric export.
- Evaluated every 1m, pending for 1m, so it fires about 2-3 minutes after
  the errors start.
- `outcome="rejected"` is excluded from numerator and denominator: a flood of
  user mistakes can neither trigger nor dilute the alert. (100 rejected + 2
  errors + 8 completed: no; 50 rejected + 3 errors + 7 completed: yes, 30%.)
  No traffic, 2 errors of 5, or 3 of 20 (15%) do not fire.
- One instance per `deployment_environment`. No data (no traffic) resolves to
  Normal (`noDataState: OK`); a query error shows as Error (`execErrState:
  Error`) rather than silently reading as healthy. Both are commented in the file.
- Labels: `service`, `environment`, `owner="on-call-agent"`, `severity`.
  `on-call-engineer/poller.py` (`make on-call`) picks up alerts with that owner.
  Annotations: `summary` (percentage and environment), `version` (the
  `service_version` with the most errors), `dashboard_url` (this dashboard
  filtered to the environment). Only ids, ratios, environment and version names.
- Quirk: Prometheus `increase` ignores the first sample of a new series, so the
  very first error after an app start is not counted.

### Failure switch and simulation

`RULESMITH_FAIL_UPLOADS=1` (any non-empty value) makes otherwise-valid uploads
fail with a 500 counted as `error`, before anything is persisted; requests
that would be rejected anyway still are. It is off when unset or empty, is not
set in `docker-compose.yaml`, the Dockerfile or the Fly configs, and the app
refuses to start with it when `RULESMITH_ENV` is `prod` or `production`.

```bash
make obs-up
RULESMITH_FAIL_UPLOADS=1 OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run uvicorn rulesmith.app:app
# Docker stack: RULESMITH_FAIL_UPLOADS=1 OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4318 make up
make simulate-failures      # ~3 minutes; BASE_URL=http://host:port to override
```

`scripts/simulate_failures.py` uploads a fixed tiny CSV about once a second,
prints how many requests returned 500, and exits non-zero if the app is
unreachable or no 500 was seen. Turn the switch off by restarting the app
without the variable.

### Manual check

1. Start the stack and the app with the switch on (above); run `make simulate-failures`.
2. Grafana > Alerting > Alert rules: `RulesmithChecksFailing` is Pending, then
   Firing within 3 minutes of the first errors. Open the instance: `summary`,
   `version` and `dashboard_url` are filled, no raw `{{ }}`.
3. Stop the simulation, restart the app without the switch. The alert returns
   to Normal within 8 minutes (the 5m window must roll off).

## Privacy

No file contents, file names, row values or rule expressions reach spans,
metrics or logs; only ids, counts, format and outcome. Exceptions are recorded
by type only (a span processor scrubs messages the ASGI instrumentation
records; the log formatter and OTLP log handler drop messages and tracebacks).

## Known gaps

- Requests rejected by FastAPI before the handler runs (missing `file` part,
  non-integer dataset id) are not counted in `rulesmith_checks_total`.
- Shipping telemetry from hosted (Fly) environments is not set up, so
  `RulesmithChecksFailing` runs only on the local stack; provisioning it
  against hosted Grafana is a follow-up.
