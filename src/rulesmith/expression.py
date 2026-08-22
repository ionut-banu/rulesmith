import ast

import pandas as pd
import simpleeval


class InvalidExpressionError(Exception):
    pass


class MissingColumnError(Exception):
    def __init__(self, column_name: str):
        self.column_name = column_name
        super().__init__(f"expression references unknown column: {column_name!r}")


def referenced_columns(expression: str) -> set[str]:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise InvalidExpressionError(str(exc)) from exc

    call_func_ids = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id not in call_func_ids
    }


def evaluate(df: pd.DataFrame, expression: str) -> pd.Series:
    missing = referenced_columns(expression) - set(df.columns)
    if missing:
        raise MissingColumnError(sorted(missing)[0])

    results = []
    for _, row in df.iterrows():
        try:
            result = simpleeval.simple_eval(expression, names=row.to_dict())
        except simpleeval.InvalidExpression as exc:
            raise InvalidExpressionError(str(exc)) from exc
        results.append(bool(result))

    return pd.Series(results, index=df.index, dtype=bool)
