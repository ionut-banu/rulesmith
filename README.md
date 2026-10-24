# Rulesmith

A web app for checking whether a data file is fit to use, without writing a
throwaway script every time a new export lands.

## The problem

Teams that receive recurring data files (a daily orders export, a weekly
partner feed) keep asking the same question: *is today's file good?* The usual
answer is an ad-hoc pandas script or a manual eyeball in a spreadsheet. The
checks live in someone's head or a notebook, they are not repeatable, and
nobody can see whether quality is getting better or worse over time.

Rulesmith turns those checks into named, reusable rules attached to a dataset.
Every new upload is checked against the rules automatically, and the results
are kept as a history.

## What it does

- **Datasets** — create a named dataset (e.g. "daily orders export"). A
  dataset owns rules and collects uploads over time.
- **Rules** — write each rule as a short row-level expression, for example
  `amount > 0`, `quantity >= 1 and quantity <= 100` or
  `status == 'paid' or status == 'shipped'`. A row passes when the expression
  is true.
  Rules can be added, edited and deleted at any time.
- **Uploads** — upload a CSV, JSON or Parquet file (up to 20 MB) to a dataset.
  Uploading creates a **run** that evaluates every rule against every row.
- **Results** — for each run, every rule shows a verdict (`pass`, `fail` or
  `broken`) with pass and fail counts. You can expand a rule to see the failing
  rows and download the run as a CSV report.
- **History** — the dataset page lists past runs and shows a pass-rate trend
  (share of rows that passed, across runs).

## Expected behaviour

- A rule that references a column missing from the uploaded file is marked
  **broken** for that run and skipped. The other rules still run. A run with a
  broken rule is never reported as a clean pass.
- Editing a rule never changes past runs. Runs are a historical record.
- The same file and the same rule always produce the same verdict.
- Expressions are evaluated with
  [simpleeval](https://github.com/danthedeckie/simpleeval). User input never
  reaches Python's `eval` or `exec`.
- Invalid uploads (wrong extension, malformed file, over 20 MB) are rejected
  with a visible error on the dataset page, and no run is created.
- There is no login. Rulesmith is a single shared workspace (a deliberate v1
  simplification; see `_docs/plan.md`).

## Architecture

- **Backend:** Python, FastAPI, SQLAlchemy
- **Frontend:** server-rendered Jinja2 templates with HTMX for partial updates
  (for example, expanding failing rows in place)
- **Database:** SQLite for local development and tests, Postgres for
  containers and deployed environments
- **Data processing:** pandas and pyarrow (CSV, JSON, Parquet)

Details: [`_docs/architecture.md`](_docs/architecture.md).

## Running locally

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run uvicorn rulesmith.app:app --reload
```

Open http://localhost:8000/datasets.

Configuration (environment variables; unset or empty uses the default):

| Variable | Default | Meaning |
|---|---|---|
| `RULESMITH_DATABASE_URL` | `sqlite:///./data/rulesmith.db` | SQLAlchemy URL, e.g. `postgresql+psycopg://user:pass@host:5432/rulesmith` |
| `RULESMITH_UPLOAD_DIR` | `./data/uploads` | Where uploads are stored, as `<dir>/<dataset id>/` |

Tables are created on start. Paths are relative to the working directory, and
`./data` is created if missing and git-ignored.

## Running with Docker

Requires Docker with Compose v2. `make up` starts the app and Postgres 16.

```bash
make up     # build and start in the background (safe to run twice)
make logs   # tail the app logs
make down   # stop and remove containers, keep data
```

Open http://localhost:8000/datasets. Postgres is not published on a host port.

Data lives in two named volumes, `rulesmith_pgdata` (Postgres) and
`rulesmith_uploads` (uploaded files). They survive `make down`. To reset
all data, run `docker compose down -v`.

The compose file sets `RULESMITH_DATABASE_URL` and `RULESMITH_UPLOAD_DIR`
(see *Running locally*). The Postgres credentials are local-only,
dev-only placeholders defined in `docker-compose.yaml`; they are not used
in deployed environments.

Manual check that data survives a restart:

1. `make up`
2. Open http://localhost:8000/datasets and create a dataset.
3. Add a rule `age >= 30` to it.
4. Upload `tests/fixtures/loader/valid.csv` (3 rows). The run shows the rule
   failing for Bob's row.
5. `make down && make up`
6. `/datasets` still lists the dataset, the rule is still on its page, the run
   is still in its history, and the run's failing-rows view still shows Bob's
   row (the uploaded file survived, not only the database).

## Tests

```bash
uv run pytest
```

## How this was built

Rulesmith was built spec-first with a team of coding agents.

- **Specification:** [`_docs/plan.md`](_docs/plan.md) — the product spec,
  written before any code.
- **Task tracker:** [GitHub issues](https://github.com/ionut-banu/rulesmith/issues?q=is%3Aissue).
  Every issue is groomed into goal, acceptance criteria, out of scope and
  constraints before implementation. Agent activity is visible in the issue
  comments (grooming, implementation notes, `QA: PASS` / `QA: FAIL` verdicts).
- **Agent team:** product manager, software engineer, QA engineer and on-call
  engineer, defined in [`_docs/team/`](_docs/team/). The workflow between them
  (groom → implement → verify, loop on FAIL) is in
  [`_docs/process.md`](_docs/process.md).
- **Context:** [`AGENTS.md`](AGENTS.md) is the entry point for every agent
  session and links to the modular docs in `_docs/`.

## Final project criteria map

| Criterion | Where to find it |
|---|---|
| Problem description | This README |
| Product specification | `_docs/plan.md` + GitHub issues with acceptance criteria and agent comments |
| Agent team | `_docs/team/`, `_docs/process.md`, `.claude/agents/` |
| Context management | `AGENTS.md` → `_docs/` |
| Application | FastAPI + Jinja2/HTMX + SQLite (dev/tests) or Postgres (`RULESMITH_DATABASE_URL`); see *Running locally* |
| Testing | `tests/` (unit), `tests/integration/` (integration) |
| Containerization | `Dockerfile`, `docker-compose.yaml`, `Makefile`; see *Running with Docker* |
| Deployment | _TBD_ |
| CI/CD | _TBD_ |
| Observability | _TBD_ |
| Alerting | _TBD_ |
| On-call agent | _TBD_ |
