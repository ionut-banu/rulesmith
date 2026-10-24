# Data Quality Rule Builder — Architecture

Implements the product described in `plan.md`.

## Tech stack

- **Backend**: Python, FastAPI
- **Database**: SQLite via SQLAlchemy
- **Tabular processing**: Pandas (CSV, JSON, Parquet)
- **Expression evaluation**: simpleeval (safe evaluation, no `eval`/`exec`)
- **Frontend**: Server-rendered Jinja2 templates + HTMX for partial updates
- **Deployment shape**: single Python process/deployable

This stack was chosen over two alternatives — a Node/TypeScript full-stack
(weaker native Parquet support) and a Rails/Django "boring monolith"
(weaker tabular/data ecosystem than Python) — because the two constraints
that matter most for this app are a safe non-eval expression evaluator and
native, symmetric handling of CSV/JSON/Parquet, both of which Python's
ecosystem serves directly.

## Architecture

Single FastAPI app in three layers:

- **Web layer**: FastAPI routes returning full Jinja2 pages and HTMX
  fragments (e.g. expanding a rule's failing rows in place without a full
  page reload).
- **Domain layer**: plain Python modules with no framework dependency.
  - `evaluator.py` — takes a Pandas DataFrame and an expression string,
    returns a per-row boolean Series, or raises a distinct error when the
    expression references a column not present in the frame.
  - `runner.py` — orchestrates a run: given an uploaded DataFrame and a
    dataset's rules, produces per-rule verdicts (pass/fail/broken) with
    pass/fail counts and failing row indices.
  - These modules are import-only — no DB or HTTP calls inside them — so
    they satisfy the plan's requirement that expression evaluation be
    testable as a plain function over a table and an expression string.
- **Persistence layer**: SQLAlchemy models (`Dataset`, `Rule`, `Upload`,
  `Run`, `RuleResult`) over SQLite. Uploaded files are stored on local disk,
  referenced by path from the `Upload` row; they are retained, never
  mutated in place.

## Data model (high level)

- **Dataset**: id, name, created_at. Owns Rules and Uploads.
- **Rule**: id, dataset_id, name, expression, created_at. Rules are
  versioned implicitly by not being retroactively reinterpreted — editing
  a rule does not change past `RuleResult` rows, since those store the
  verdict/counts as of the run, not a live reference to the rule's current
  expression.
- **Upload**: id, dataset_id, original filename, stored path, format
  (csv/json/parquet), uploaded_at.
- **Run**: id, dataset_id, upload_id, created_at. One run per upload,
  created automatically at upload time.
- **RuleResult**: id, run_id, rule_id, rule_name (denormalized at run time
  so renaming a rule later doesn't rewrite history), verdict
  (pass/fail/broken), pass_count, fail_count, broken_reason (nullable),
  failing_row_ref (pointer to where failing row data is stored for
  on-demand viewing).

## Data flow

1. User uploads a file to a dataset. The route saves the file to disk and
   creates an `Upload` row.
2. The route loads the file into a DataFrame, dispatching on file
   extension/declared format (CSV/JSON/Parquet). If the file fails to
   parse, the upload fails with an error and no `Run` is created — no
   partial run is ever persisted.
3. A `Run` row is created, then `runner.run(df, dataset.rules)` is called.
4. For each rule, `runner` checks whether every column the rule references
   exists in `df.columns`.
   - If not: the rule is recorded as **broken** for this run (with a
     reason) and evaluation moves to the next rule. Broken rules are never
     silently dropped and are always visible in the run result.
   - If so: `evaluator.evaluate(df, expression)` runs, producing a
     per-row boolean result; pass/fail counts and failing row indices are
     computed from it.
5. One `RuleResult` row per rule is persisted under the `Run`. A run
   containing any broken rules is never reported as a clean pass.
6. The response redirects to the run detail page.

## Error handling

- **Missing column at run time**: broken verdict for that one rule only;
  the run continues and completes normally. This is a designed outcome,
  not an exception surfaced to the user.
- **Malformed upload file**: the whole upload is rejected before any `Run`
  row exists. The user sees an error; nothing partial is persisted.
- **Malformed rule expression**: caught at rule *creation/edit* time by
  parse-checking the expression (e.g. against an empty or sample frame),
  so rule authors get immediate feedback rather than discovering a syntax
  error only when a run happens. A rule that parses but references a
  column absent from a particular upload is still only caught at run
  time — that is the broken-rule path described above, by design.

## Testing

- `evaluator.py` and `runner.py` are unit-tested directly with pytest as
  plain functions over DataFrames/dicts, with no app, browser, or database
  involved — satisfying the plan's explicit testability constraint.
- Determinism: no randomness or wall-clock dependency in evaluation; row
  order is preserved from the source file, so the same file and rule
  always produce the same verdict and same failing-row set.
- The web layer gets thinner integration tests (upload → run → verify
  verdicts) using FastAPI's `TestClient` against small fixture files per
  supported format (CSV/JSON/Parquet).

## Open questions (deliberately deferred per the plan)

These are unchanged from `plan.md` and are not resolved by this document;
they need answers before the tasks that touch them:

- Maximum upload size, and whether large files need background processing
  instead of synchronous handling during the upload request.
- The report format for a downloaded run result (failing-rows CSV vs. full
  summary document).
- What "pass rate" means in the dataset trend chart — share of rules
  passing vs. share of rows passing.
