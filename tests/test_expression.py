import pandas as pd
import pytest

from rulesmith.expression import InvalidExpressionError, MissingColumnError, evaluate


def test_simple_comparison():
    df = pd.DataFrame({"age": [17, 18, 25]})

    result = evaluate(df, "age > 18")

    assert list(result) == [False, False, True]


def test_combined_boolean_and_arithmetic_expression():
    df = pd.DataFrame(
        {
            "amount": [10, 500, 10],
            "quantity": [10, 5, 200],
            "status": ["active", "active", "active"],
        }
    )

    result = evaluate(df, "amount * quantity <= 1000 and status == 'active'")

    assert list(result) == [True, False, False]


def test_missing_column_raises_before_evaluating_rows():
    df = pd.DataFrame({"age": [17, 18]})

    with pytest.raises(MissingColumnError) as exc_info:
        evaluate(df, "age > 18 and height < 200")

    assert exc_info.value.column_name == "height"


def test_invalid_syntax_raises_invalid_expression_error():
    df = pd.DataFrame({"age": [17, 18]})

    with pytest.raises(InvalidExpressionError):
        evaluate(df, "age >")


def test_disallowed_construct_raises_invalid_expression_error():
    df = pd.DataFrame({"age": [17, 18]})

    with pytest.raises(InvalidExpressionError):
        evaluate(df, "undefined_function(age)")


def test_evaluation_is_deterministic():
    df = pd.DataFrame({"age": [17, 18, 25]})

    first = evaluate(df, "age > 18")
    second = evaluate(df, "age > 18")

    assert list(first) == list(second)


def test_result_index_matches_input_index():
    df = pd.DataFrame({"age": [17, 18, 25]}, index=[10, 20, 30])

    result = evaluate(df, "age > 18")

    assert list(result.index) == [10, 20, 30]


def test_empty_dataframe_returns_empty_series():
    df = pd.DataFrame({"age": pd.Series([], dtype=int)})

    result = evaluate(df, "age > 18")

    assert len(result) == 0


def test_nan_in_referenced_column_does_not_raise():
    df = pd.DataFrame({"age": [float("nan"), 25]})

    result = evaluate(df, "age > 18")

    assert list(result) == [False, True]
