from pathlib import Path
from typing import Literal

import pandas as pd

TableFormat = Literal["csv", "json", "parquet"]

_SUPPORTED_FORMATS = {"csv", "json", "parquet"}


class TableLoadError(Exception):
    """Raised for every way loading a table can fail.

    Wraps parse failures from the underlying format-specific reader, an
    unrecognized ``format`` value, a missing path, and an empty (zero-byte)
    file, so callers only ever need to handle this one exception type.
    """


def load_table(path: str | Path, format: TableFormat) -> pd.DataFrame:
    """Load a CSV, JSON, or Parquet file into a DataFrame.

    ``format`` is supplied explicitly by the caller and is never sniffed
    from file content or the path's extension.

    For ``format="json"``, the file must contain a JSON array of row
    objects (i.e. ``pandas.read_json(path, orient="records")`` shape) --
    a single JSON object, or any other JSON shape, is not supported.

    The source file's row order is preserved exactly: no reordering,
    sorting, or index resets are performed.

    Raises:
        TableLoadError: if the path does not exist, the file is empty,
            ``format`` is not one of ``"csv"``, ``"json"``, ``"parquet"``,
            or the file's content cannot be parsed as the given format.
    """
    if format not in _SUPPORTED_FORMATS:
        raise TableLoadError(f"unrecognized format: {format!r}")

    file_path = Path(path)

    if not file_path.exists():
        raise TableLoadError(f"file not found: {file_path}")

    if file_path.stat().st_size == 0:
        raise TableLoadError(f"file is empty: {file_path}")

    try:
        if format == "csv":
            return pd.read_csv(file_path)
        elif format == "json":
            return pd.read_json(file_path, orient="records")
        else:
            return pd.read_parquet(file_path)
    except TableLoadError:
        raise
    except Exception as exc:
        raise TableLoadError(f"failed to load {format} file {file_path}: {exc}") from exc
