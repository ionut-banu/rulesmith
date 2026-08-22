import pytest

from rulesmith.expression import InvalidExpressionError, referenced_columns


def test_single_column_expression():
    assert referenced_columns("age > 18") == {"age"}


def test_multi_column_expression_deduplicated():
    result = referenced_columns(
        "amount * quantity <= 1000 and status == 'active' and amount > 0"
    )

    assert result == {"amount", "quantity", "status"}


def test_literal_only_expression_returns_empty_set():
    assert referenced_columns("1 < 2") == set()


def test_invalid_syntax_raises_invalid_expression_error():
    with pytest.raises(InvalidExpressionError):
        referenced_columns("age >")


def test_function_call_name_is_not_treated_as_a_column():
    assert referenced_columns("undefined_function(age)") == {"age"}
