from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from rulesmith.expression import (
    InvalidExpressionError,
    MissingColumnError,
    evaluate,
    referenced_columns,
)


@dataclass(frozen=True)
class RuleInput:
    name: str
    expression: str


Verdict = Literal["pass", "fail", "broken"]


@dataclass(frozen=True)
class RuleRunResult:
    rule_name: str
    verdict: Verdict
    pass_count: int | None
    fail_count: int | None
    broken_reason: str | None = None
    failing_row_indices: list = field(default_factory=list)


def _run_one(df: pd.DataFrame, rule: RuleInput) -> RuleRunResult:
    try:
        missing = referenced_columns(rule.expression) - set(df.columns)
    except InvalidExpressionError as exc:
        return RuleRunResult(
            rule_name=rule.name,
            verdict="broken",
            pass_count=None,
            fail_count=None,
            broken_reason=str(exc),
            failing_row_indices=[],
        )

    if missing:
        broken_reason = f"unknown column(s): {', '.join(sorted(missing))}"
        return RuleRunResult(
            rule_name=rule.name,
            verdict="broken",
            pass_count=None,
            fail_count=None,
            broken_reason=broken_reason,
            failing_row_indices=[],
        )

    try:
        results = evaluate(df, rule.expression)
    except (InvalidExpressionError, MissingColumnError) as exc:
        return RuleRunResult(
            rule_name=rule.name,
            verdict="broken",
            pass_count=None,
            fail_count=None,
            broken_reason=str(exc),
            failing_row_indices=[],
        )

    pass_count = int(results.sum())
    fail_count = int((~results).sum())
    failing_row_indices = results.index[~results].tolist()

    verdict: Verdict = "pass" if fail_count == 0 else "fail"

    return RuleRunResult(
        rule_name=rule.name,
        verdict=verdict,
        pass_count=pass_count,
        fail_count=fail_count,
        broken_reason=None,
        failing_row_indices=failing_row_indices,
    )


def run(df: pd.DataFrame, rules: list[RuleInput]) -> list[RuleRunResult]:
    return [_run_one(df, rule) for rule in rules]
