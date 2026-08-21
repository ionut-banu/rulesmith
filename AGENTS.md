Data quality rule builder. See `_docs/plan.md` for product requirements
and `_docs/architecture.md` for the tech stack and design. Tasks live as
GitHub issues; see `_docs/process.md` for the per-task workflow and
`_docs/tasks.md` for the full backlog.

Commands

- `uv sync` - install dependencies
- `uv run pytest` - the whole suite
- `uv run pytest tests/test_health.py` - one test file
- `uv run uvicorn rulesmith.app:app --reload` - run the dev server

Layout

- `src/rulesmith/` - the package (importable as `rulesmith`)
- `tests/` - pytest tests, one file per module under test

Rules

- Dependencies are added in `pyproject.toml`. Do not add one without
  asking.
- User-supplied rule expressions must never reach `eval` or `exec`,
  including with a restricted globals dict. Use simpleeval.
- Expression evaluation and rule running must be plain functions with no
  FastAPI or database imports, so they're testable without a browser or
  a running app.
- Rule evaluation must be deterministic: the same file and rule always
  produce the same verdict.

  Documents

- `_docs/process.md` - how work is organized
