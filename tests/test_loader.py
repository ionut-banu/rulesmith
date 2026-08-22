from pathlib import Path

import pytest

from rulesmith.loader import TableLoadError, load_table

FIXTURES = Path(__file__).parent / "fixtures" / "loader"


def test_valid_csv_loads_columns_values_and_row_order():
    df = load_table(FIXTURES / "valid.csv", "csv")

    assert list(df.columns) == ["name", "age"]
    assert df["name"].tolist() == ["Alice", "Bob", "Carol"]
    assert df["age"].tolist() == [30, 25, 40]


def test_valid_json_loads_columns_values_and_row_order():
    df = load_table(FIXTURES / "valid.json", "json")

    assert list(df.columns) == ["name", "age"]
    assert df["name"].tolist() == ["Alice", "Bob", "Carol"]
    assert df["age"].tolist() == [30, 25, 40]


def test_valid_parquet_loads_columns_values_and_row_order():
    df = load_table(FIXTURES / "valid.parquet", "parquet")

    assert list(df.columns) == ["name", "age"]
    assert df["name"].tolist() == ["Alice", "Bob", "Carol"]
    assert df["age"].tolist() == [30, 25, 40]


def test_malformed_csv_raises_table_load_error_naming_path():
    path = FIXTURES / "malformed.csv"

    with pytest.raises(TableLoadError) as exc_info:
        load_table(path, "csv")

    assert str(path) in str(exc_info.value)
    assert exc_info.value.__cause__ is not None


def test_malformed_json_raises_table_load_error_naming_path():
    path = FIXTURES / "malformed.json"

    with pytest.raises(TableLoadError) as exc_info:
        load_table(path, "json")

    assert str(path) in str(exc_info.value)
    assert exc_info.value.__cause__ is not None


def test_malformed_parquet_raises_table_load_error_naming_path():
    path = FIXTURES / "malformed.parquet"

    with pytest.raises(TableLoadError) as exc_info:
        load_table(path, "parquet")

    assert str(path) in str(exc_info.value)
    assert exc_info.value.__cause__ is not None


def test_unrecognized_format_raises_table_load_error():
    with pytest.raises(TableLoadError):
        load_table(FIXTURES / "valid.csv", "yaml")  # type: ignore[arg-type]


def test_nonexistent_path_raises_table_load_error():
    with pytest.raises(TableLoadError):
        load_table(FIXTURES / "does_not_exist.csv", "csv")


def test_headers_only_csv_loads_as_empty_dataframe_with_columns():
    df = load_table(FIXTURES / "headers_only.csv", "csv")

    assert list(df.columns) == ["name", "age"]
    assert len(df) == 0


def test_zero_row_json_loads_as_empty_dataframe_with_no_columns():
    # `[]` is the only way to represent zero rows in the required
    # array-of-records JSON shape, and it carries no column/schema
    # information at all -- unlike zero-row CSV/Parquet, which retain their
    # header/schema. So, unlike the CSV/Parquet zero-row cases, this
    # documents (rather than hides) the fact that no columns are recovered.
    # See the caveat in `load_table`'s docstring.
    df = load_table(FIXTURES / "zero_rows.json", "json")

    assert len(df) == 0
    assert list(df.columns) == []


def test_zero_row_parquet_loads_as_empty_dataframe_with_columns():
    df = load_table(FIXTURES / "zero_rows.parquet", "parquet")

    assert list(df.columns) == ["name", "age"]
    assert len(df) == 0


def test_empty_csv_file_raises_table_load_error():
    with pytest.raises(TableLoadError):
        load_table(FIXTURES / "empty.csv", "csv")


def test_empty_json_file_raises_table_load_error():
    with pytest.raises(TableLoadError):
        load_table(FIXTURES / "empty.json", "json")


def test_empty_parquet_file_raises_table_load_error():
    with pytest.raises(TableLoadError):
        load_table(FIXTURES / "empty.parquet", "parquet")


def test_loading_same_file_twice_is_deterministic():
    first = load_table(FIXTURES / "valid.csv", "csv")
    second = load_table(FIXTURES / "valid.csv", "csv")

    assert first.columns.tolist() == second.columns.tolist()
    assert first.values.tolist() == second.values.tolist()
    assert first.index.tolist() == second.index.tolist()


def test_load_table_accepts_string_path():
    df = load_table(str(FIXTURES / "valid.csv"), "csv")

    assert len(df) == 3
