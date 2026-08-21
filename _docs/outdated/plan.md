# Data Quality Rule Builder — Project Plan

## The idea

A web app where you register a dataset, declare data-quality rules for it, and then
check every new upload of that dataset against those rules. The point is to answer
"is today's file good?" without writing a throwaway script each time.

## Users

A single shared workspace. No login, no accounts, no permissions. Anyone who can
reach the URL can see and change everything. This is a deliberate v1 simplification.

## Core concepts

- **Dataset** — a named, long-lived thing, e.g. "daily orders export". It owns rules
  and accumulates uploads over time.
- **Upload** — one file submitted to a dataset. CSV, JSON, or Parquet. Uploaded files
  are retained.
- **Rule** — belongs to one dataset. Has a human-readable name and an expression.
- **Run** — the result of evaluating a dataset's rules against one upload. Created
  automatically when the upload happens.

## Defining rules

- A rule is written as a short expression in a text box, not built through a form.
- The expression is evaluated per row and returns a boolean: true means the row passes.
- Expressions reference columns by name.
- Every rule also has a name so results are readable without reading the expression.
- Rules can be added, edited, and deleted at any time.
- Editing a rule does not change the results of runs that already happened. Past runs
  are a historical record.

## Uploading and running checks

- Uploading a file to a dataset creates a run and evaluates all of that dataset's
  rules against it.
- If the uploaded file is missing a column that a rule references, that rule is marked
  **broken** for that run and skipped. The remaining rules still run.
- Broken rules must be visible in the run result. They are never silently dropped, and
  a run containing broken rules is not reported as a clean pass.

## Seeing results

For a single run:

- Each rule shows a verdict: pass, fail, or broken.
- Each rule shows how many rows passed and how many failed.
- The failing rows themselves can be viewed for a given rule.
- The run result can be downloaded as a report.

For a dataset:

- A list of past runs, each openable.
- A pass-rate trend over time across those runs.

## Out of scope for v1

Each of these is a deliberate exclusion, not an oversight:

- Authentication, user accounts, permissions.
- Scheduled or automated ingestion. Uploads are manual.
- Alerting or notifications of any kind.
- Aggregate and cross-row rules, e.g. "row count within 10% of the previous upload"
  or "this column is unique". v1 rules are strictly row-level.
- Sharing rules between datasets. Rules belong to exactly one dataset.
- Editing, cleaning, or correcting the uploaded data.

## Constraints

- User-supplied expressions must never be passed to `eval()` or `exec()`, including
  with a restricted globals dictionary. Use a real parser or a sandboxed expression
  evaluator. The specific approach is the engineer's choice.
- Expression evaluation must be testable without a browser, as a plain function over
  a table and an expression string.
- Rule evaluation must be deterministic. The same file and the same rule always
  produce the same verdict.
- The tech stack has not been chosen yet. Decide it before creating the backlog.

## Deliberately deferred

These need an answer before the tasks that touch them, but not before the stack:

- Maximum upload size, and whether large files need to run in the background rather
  than during the upload request.
- The report format: a CSV of failing rows, or a full summary document.
- What "pass rate" means in the trend chart: the share of rules that passed, or the
  share of rows that passed. These diverge sharply when one rule fails on most rows.
