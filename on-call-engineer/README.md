# On-call agent

`poller.py` polls Grafana for firing alerts and, for each new one labelled
`owner="on-call-agent"`, starts a headless `claude -p` session in the on-call
role ([`_docs/team/on-call-engineer.md`](../_docs/team/on-call-engineer.md)).
The agent investigates, reproduces the failure with a test, commits a
`fix(on-call): ...` on its own branch and opens an `Incident: <alert>` issue.
It cannot push, merge or deploy; a human reviews and promotes the branch.

## Prerequisites

- `claude` CLI installed and logged in (sessions use your quota)
- `gh auth login` against the real GitHub repo (the incident issue is a remote write)
- `make obs-up` (Grafana at http://localhost:3000)
- `uv sync`

## Run

```bash
make on-call                          # poll every 60s until Ctrl-C
make on-call ARGS="--once"            # handle current alerts, exit
make on-call ARGS="--once --dry-run"  # print alert + exact claude command, run nothing
```

Flags: `--once`, `--dry-run`, `--state-file PATH` (default
`on-call-engineer/state.json`), `--timeout SECONDS` (default 1800).

Environment: `GRAFANA_URL` (default `http://localhost:3000`), `GRAFANA_USER` /
`GRAFANA_PASSWORD` (default `admin` / `admin`, the local stack) or
`GRAFANA_TOKEN` (bearer, wins if set). Credentials are never printed.

## Behaviour

- Only alerts that are firing (`active`) with label `owner="on-call-agent"` are handled.
- Dedup is by Grafana `fingerprint`, kept in `state.json` (gitignored). The
  fingerprint is recorded before the session starts, so a crash or failed fix is
  never retried. It is removed once the alert stops firing, so a later refire is
  a new incident. A corrupt state file stops the poller (exit 2); delete it to reset.
- Before each session the poller creates (or reuses) branch
  `on-call/<alertname>-<fingerprint8>` from the current HEAD, and switches back
  afterwards. Start from a clean working tree.
- Alerts are handled one at a time. A session over the timeout is killed and
  the poller carries on. A Grafana outage is logged and retried next tick (with
  `--once` it exits 1). If `claude` or the role file is missing it exits non-zero.
- Each session's stdout+stderr goes to
  `on-call-engineer/logs/<UTC timestamp>-<alertname>.log` (gitignored). A
  committed *illustrative* example: `examples/illustrative-session.log`.

## Permission model

Enforced on the command line with `--allowedTools` / `--disallowedTools`
(never `--dangerously-skip-permissions`); the full lists and reasoning are the
comment block at the top of `poller.py`. In short: the agent may read/edit
files, run `uv run pytest`/`uv sync`, use local git (`status|diff|log|add|commit|checkout|switch|branch`)
and `gh issue create|view|list|comment`, `gh pr create|view`. It may not
`git push`, `gh pr merge`, `gh workflow`, `gh release`, `fly`/`flyctl`/`docker`, or `rm -rf`.
Alert labels and annotations are passed as data, and the prompt says so; only
ids, ratios, environment and version are in them, never file contents or row values.

## End-to-end drill

1. `make obs-up`.
2. On a scratch branch, inject a bug into the upload path (e.g. make
   `POST /datasets/{id}/uploads` raise for valid files) and start the app with
   `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318`. Fallback that exercises
   only the alert path: start the app with `RULESMITH_FAIL_UPLOADS=1` (see
   `_docs/observability.md`, *Alerting*).
3. `make simulate-failures` so uploads return 500.
4. After about 2-3 minutes `RulesmithChecksFailing` fires (check
   http://localhost:3000/alerting/list).
5. `make on-call ARGS="--once --dry-run"` shows what would run; then `make on-call`.
6. The agent reproduces the failure with a test, commits `fix(on-call): ...` on
   its branch and opens the `Incident: RulesmithChecksFailing` issue.
7. Verify: `git log on-call/RulesmithChecksFailing-*`, `gh issue list`, the file
   in `on-call-engineer/logs/`, `uv run pytest` on the branch. Clean up: stop
   the app, `make obs-down`, delete `on-call-engineer/state.json` to re-arm,
   delete the scratch and `on-call/*` branches, close the test incident issue.

## Future work (out of scope here)

- Run the poller in the cloud (Lambda / container job)
- Notify humans (Slack/PagerDuty/email) when an incident opens or the agent fails; escalate if it cannot fix
- Parallel sessions, priorities, retries of failed sessions, cost/rate limits
