import pandas as pd
import pytest

from rulesmith import rules as rules_module
from rulesmith.rules import RuleInput, RuleRunResult, run


def test_all_rules_pass():
    df = pd.DataFrame({"age": [20, 25, 30]})
    result = run(df, [RuleInput(name="adult", expression="age >= 18")])

    assert result == [
        RuleRunResult(
            rule_name="adult",
            verdict="pass",
            pass_count=3,
            fail_count=0,
            broken_reason=None,
            failing_row_indices=[],
        )
    ]


def test_mixed_pass_fail_uses_actual_index_values():
    df = pd.DataFrame({"age": [10, 20, 5, 30]}, index=[100, 101, 102, 103])
    result = run(df, [RuleInput(name="adult", expression="age >= 18")])

    r = result[0]
    assert r.verdict == "fail"
    assert r.pass_count == 2
    assert r.fail_count == 2
    assert r.failing_row_indices == [100, 102]


def test_missing_column_produces_broken_verdict_and_does_not_call_evaluate(monkeypatch):
    df = pd.DataFrame({"age": [20, 25]})

    def boom(*args, **kwargs):
        raise AssertionError("evaluate() should not be called for a broken rule")

    monkeypatch.setattr(rules_module, "evaluate", boom)

    result = run(df, [RuleInput(name="bad_col", expression="height > 100")])

    r = result[0]
    assert r.verdict == "broken"
    assert r.pass_count is None
    assert r.fail_count is None
    assert "height" in r.broken_reason
    assert r.failing_row_indices == []


def test_invalid_expression_produces_broken_verdict():
    df = pd.DataFrame({"age": [20, 25]})

    result = run(df, [RuleInput(name="bad_syntax", expression="age >")])

    r = result[0]
    assert r.verdict == "broken"
    assert r.pass_count is None
    assert r.fail_count is None
    assert r.broken_reason is not None
    assert r.failing_row_indices == []


def test_one_broken_rule_does_not_stop_others():
    df = pd.DataFrame({"age": [20, 10]})

    result = run(
        df,
        [
            RuleInput(name="passing", expression="age > 0"),
            RuleInput(name="broken", expression="height > 100"),
            RuleInput(name="failing", expression="age >= 18"),
        ],
    )

    assert len(result) == 3
    assert result[0].verdict == "pass"
    assert result[1].verdict == "broken"
    assert result[2].verdict == "fail"


def test_order_is_preserved():
    df = pd.DataFrame({"age": [20]})

    result = run(
        df,
        [
            RuleInput(name="a", expression="age > 100"),
            RuleInput(name="b", expression="age > 0"),
            RuleInput(name="c", expression="unknown_col > 0"),
        ],
    )

    assert [r.rule_name for r in result] == ["a", "b", "c"]
    assert [r.verdict for r in result] == ["fail", "pass", "broken"]


def test_empty_rules_list_returns_empty_list():
    df = pd.DataFrame({"age": [20]})
    assert run(df, []) == []


def test_empty_dataframe_is_vacuous_pass():
    df = pd.DataFrame({"age": pd.Series([], dtype=int)})

    result = run(df, [RuleInput(name="adult", expression="age >= 18")])

    r = result[0]
    assert r.verdict == "pass"
    assert r.pass_count == 0
    assert r.fail_count == 0
    assert r.failing_row_indices == []


def test_duplicate_rule_names_are_evaluated_independently():
    df = pd.DataFrame({"age": [20, 10]})

    result = run(
        df,
        [
            RuleInput(name="dup", expression="age >= 18"),
            RuleInput(name="dup", expression="age < 18"),
        ],
    )

    assert len(result) == 2
    assert result[0].rule_name == "dup"
    assert result[1].rule_name == "dup"
    assert result[0].verdict == "fail"
    assert result[1].verdict == "fail"
    assert result[0].failing_row_indices == [1]
    assert result[1].failing_row_indices == [0]


def test_run_is_deterministic():
    df = pd.DataFrame({"age": [20, 10, 30]}, index=[5, 6, 7])
    rules = [
        RuleInput(name="adult", expression="age >= 18"),
        RuleInput(name="broken", expression="height > 100"),
    ]

    first = run(df, rules)
    second = run(df, rules)

    assert first == second
