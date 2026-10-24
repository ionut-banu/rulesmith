Data quality rule builder. See `_docs/plan.md` for product requirements
and `_docs/architecture.md` for the tech stack and design. Tasks live as
GitHub issues (the only active backlog); see `_docs/process.md` for the
per-task workflow.

Commands

- `uv sync` - install dependencies
- `uv run pytest` - the whole unit suite
- `uv run pytest tests/test_health.py` - one test file
- `uv run uvicorn rulesmith.app:app --reload` - run the dev server

Layout

- `src/rulesmith/` - the package (importable as `rulesmith`)
- `tests/` - pytest tests, one file per module under test
- `_docs/` - spec, architecture, process and team roles

Rules

- Dependencies are added in `pyproject.toml`. Do not add one without
  asking, unless the issue you are working on names it explicitly.
- User-supplied rule expressions must never reach `eval` or `exec`,
  including with a restricted globals dict. Use simpleeval.
- Expression evaluation and rule running must be plain functions with no
  FastAPI or database imports, so they're testable without a browser or
  a running app.
- Rule evaluation must be deterministic: the same file and rule always
  produce the same verdict.
- Never send uploaded file contents, file names or row values to logs,
  metrics or traces. Use ids and counts.
- When an issue changes how to run, test or deploy the app, update the
  matching section of `README.md` and the criteria map at its end.

Documents

- `_docs/process.md` - how work is organized (read before starting any issue)
- `_docs/task-template.md` - the shape of a groomed issue
- `_docs/team/` - one file per agent role (PM, engineer, QA, on-call)
- `_docs/plan.md` - product spec
- `_docs/architecture.md` - stack and design
- `_docs/deployment.md` - environments, CI/CD, promotion and rollback (once it exists)
- `_docs/observability.md` - telemetry, dashboards, alerts (once it exists)
