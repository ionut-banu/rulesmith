# Backlog

Tasks for the data quality rule builder described in `_docs/plan.md`.
The stack and architecture are settled in
`docs/superpowers/specs/2026-08-20-data-quality-rule-builder-design.md`:
Python, FastAPI, SQLite via SQLAlchemy, Pandas, simpleeval, and
server-rendered Jinja2 templates with HTMX.

Tasks are listed in a sensible build order, but each one states its own
context so it can be picked up without reading the others.

## 1. Project skeleton with a passing test
Goal: Stand up an empty, runnable Python project with a green test suite.
Description: Create the repository layout (a source package and a `tests/`
directory), the dependency and config files for FastAPI, SQLAlchemy, Pandas,
simpleeval, Jinja2 and pytest, and a single trivial test that passes. Include
a minimal FastAPI application object with one health route so the server can
be started and smoke-tested. There is no domain logic in this task — the
deliverable is `pytest` running green and the app booting.

## 2. Expression evaluator
Goal: Evaluate a rule expression against a table and get a per-row boolean result.
Description: Write a pure function that takes a Pandas DataFrame and an
expression string and returns one boolean per row, using simpleeval so that
user-supplied text is never passed to `eval` or `exec`, not even with a
restricted globals dictionary. Support comparisons, arithmetic and boolean
operators over columns referenced by name. This module must not import FastAPI
or the database — it is tested directly against small in-memory DataFrames.

## 3. Column reference extraction
Goal: Determine which columns an expression references without evaluating it.
Description: Write a pure function that parses an expression string and returns
the set of column names it references. This powers two things elsewhere in the
app: validating a rule at the moment it is saved, and detecting at run time
that an uploaded file is missing a column some rule depends on. It must work on
any syntactically valid expression without needing a table to run against.

## 4. Tabular file loading
Goal: Load an uploaded CSV, JSON, or Parquet file into a DataFrame.
Description: Write a pure function that dispatches on file format and returns a
Pandas DataFrame, raising a clearly typed error when a file cannot be parsed.
Row order from the source file must be preserved, since rule evaluation has to
be deterministic across repeated runs of the same file. Test with small fixture
files in each of the three formats, including a malformed example of each.

## 5. Database models and session setup
Goal: Persist datasets, rules, uploads, runs, and rule results in SQLite.
Description: Define SQLAlchemy models for Dataset, Rule, Upload, Run and
RuleResult along with the relationships between them, plus the engine and
session setup pointing at a SQLite file and creating the tables. RuleResult
records the rule's name and verdict as they were at run time, so that editing
or renaming a rule later never alters a run that already happened. Tests write
records and read them back through a temporary database.

## 6. Rule run engine
Goal: Evaluate all of a dataset's rules against one loaded table.
Description: Write a pure function that takes a DataFrame and a list of rules
(each a name and an expression) and returns, for every rule, a verdict of pass,
fail or broken together with pass and fail counts and the indices of the failing
rows. A rule whose referenced columns are absent from the table is marked broken
with a reason and skipped, while every remaining rule still runs. Like the
evaluator, this module stays free of web and database imports so it can be
tested as a plain function.

## 7. Dataset pages
Goal: Create, list, and open datasets in the browser.
Description: Add the FastAPI routes and Jinja2 templates for listing all
datasets, creating a new one by name, and viewing a single dataset's page.
There is no login, account or permission model in this product — anyone who can
reach the URL can see and change everything. The dataset page is the shell that
later tasks hang rules, uploads and run history off of.

## 8. Rule management pages
Goal: Add, edit, and delete a dataset's rules through the UI.
Description: Add the routes and templates for managing the rules belonging to
one dataset, where each rule has a human-readable name and an expression typed
into a text box rather than assembled through a form. Validate the expression's
syntax when it is saved and show the author the error immediately, instead of
letting a malformed rule surface as a failure during a later run. Editing or
deleting a rule must leave the results of past runs untouched.

## 9. Upload and run pipeline
Goal: Uploading a file to a dataset stores it and produces a run.
Description: Add the upload route that writes the incoming file to disk, records
an Upload row, loads it into a DataFrame, evaluates the dataset's rules against
it and persists a Run with one result per rule. A file that cannot be parsed is
rejected before any Run row is created, so a partial run is never stored.
Settle the maximum upload size and whether large files must be processed in the
background before starting — the plan leaves both open.

## 10. Run detail page
Goal: Show the outcome of one run, rule by rule.
Description: Add a page for a single run listing every rule with its verdict —
pass, fail or broken — and the number of rows that passed and failed. Broken
rules must be displayed explicitly and never quietly dropped, and a run that
contains any broken rule must not be presented as a clean pass. The page should
also identify which uploaded file the run was produced from.

## 11. Failing rows viewer
Goal: Inspect the rows that failed a given rule.
Description: Let a user expand a failed rule on the run detail page to see the
actual failing rows, loaded as an HTMX partial rather than a full page reload.
The rows are read from the retained upload file combined with the failing row
indices recorded for that rule. Cap how many rows render at once so that a rule
failing across a very large file cannot hang the page.

## 12. Run report download
Goal: Download a run's result as a file.
Description: Add a download action on the run detail page that produces the
run's result as a report file. Decide the format before starting — the plan
leaves open whether this is a CSV of the failing rows or a full summary document
covering every rule's verdict and counts. Whichever is chosen, broken rules have
to appear in it.

## 13. Dataset run history
Goal: See a dataset's past runs and open any of them.
Description: Add a list of previous runs to the dataset page, newest first, each
one linking through to its run detail page. Every entry should carry enough
summary information — when it ran, which file it used, and the headline outcome
— to be scannable without opening it. Past runs are a historical record and are
never recomputed.

## 14. Pass-rate trend
Goal: Show how a dataset's pass rate has moved across its runs.
Description: Add a trend visualization to the dataset page plotting the pass
rate across the sequence of past runs. Decide what pass rate means before
starting — the plan notes that the share of rules passing and the share of rows
passing diverge sharply when a single rule fails on most rows. Label the chosen
measure in the UI so the number cannot be misread.
