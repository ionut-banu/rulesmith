# On-call drill, 2026-11-07

A real run of the end-to-end drill in [`../../README.md`](../../README.md),
against the local stack (`make obs-up`, app with
`OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318`).

| Step | What happened |
|---|---|
| Injected regression | `b76b399` "Simplify Upload.stored_path assignment", made by hand for the drill: `stored_path=str(final_path)` became `stored_path=final_path`, so every valid upload raised `sqlite3.ProgrammingError` and returned 500 |
| Load | `scripts/simulate_failures.py --duration 600` |
| Alert | `RulesmithChecksFailing` fired at 16:38:10Z, error ratio 1.0, fingerprint `4e96aff3f7ede986` |
| Poller | `make on-call ARGS="--once"` started the session at 16:38:23Z on branch `on-call/RulesmithChecksFailing-4e96aff3` ([`poller-terminal.txt`](poller-terminal.txt)) |
| Agent | Found the root cause, added `test_stored_path_is_persisted_as_a_string` (failing before, passing after), committed `4d2d95d` `fix(on-call): bind stored_path as str (RulesmithChecksFailing)`, opened [issue #29](https://github.com/ionut-banu/rulesmith/issues/29); session finished at 16:40:31Z, about 2 minutes |
| Human review | Full suite on the fix branch: 254 passed. The branch was reviewed and merged by a human; the agent did not push, merge or deploy |

[`session.log`](session.log) is the session's output exactly as the poller
captured it (`claude -p` prints its final report). It contains ids, counts and
code references only, no file contents, file names from users or row values.
