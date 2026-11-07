"""Keep the sample-data walkthrough true.

Loads each sample file with `load_table`, runs the walkthrough's four rules
with `rules.run`, and checks every expected result against both the
scenario below and the text of `sample-data/README.md`. No FastAPI app, no
database, no browser.

The walkthrough and this file are tied both ways: every result line built
from SCENARIO must appear in the walkthrough word for word, and every line in
the walkthrough that states a result (a count, an order id, a trend figure,
an error, a report row) must be one of those built lines.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pandas.testing
import pytest

from rulesmith.loader import TableLoadError, load_table
from rulesmith.rules import RuleInput, run
from rulesmith.trend import row_pass_counts

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample-data"
WALKTHROUGH = SAMPLE_DIR / "README.md"
GENERATOR = SAMPLE_DIR / "make_parquet.py"

SUPPORTED_EXTENSIONS = {".csv": "csv", ".json": "json", ".parquet": "parquet"}

RULES = [
    ("Amount is positive", "amount > 0"),
    ("Quantity between 1 and 100", "quantity >= 1 and quantity <= 100"),
    ("Known status", "status == 'paid' or status == 'shipped' or status == 'refunded'"),
    ("Currency is EUR", "currency == 'EUR'"),
]

# Per rule: (verdict, pass_count, fail_count, broken_reason, failing order_ids)
SCENARIO = [
    {
        "file": "01-orders-clean.csv",
        "rows": 10,
        "ids": ("ORD-0001", "ORD-0010"),
        "summary": "Clean pass: 4 passed, 0 failed, 0 broken.",
        "results": {
            "Amount is positive": ("pass", 10, 0, None, []),
            "Quantity between 1 and 100": ("pass", 10, 0, None, []),
            "Known status": ("pass", 10, 0, None, []),
            "Currency is EUR": ("pass", 10, 0, None, []),
        },
        "trend": (40, 40, "100.0"),
    },
    {
        "file": "02-orders-with-bad-rows.csv",
        "rows": 10,
        "ids": ("ORD-0011", "ORD-0020"),
        "summary": "2 passed, 2 failed, 0 broken.",
        "results": {
            "Amount is positive": ("fail", 8, 2, None, ["ORD-0013", "ORD-0017"]),
            "Quantity between 1 and 100": ("fail", 8, 2, None, ["ORD-0015", "ORD-0017"]),
            "Known status": ("pass", 10, 0, None, []),
            "Currency is EUR": ("pass", 10, 0, None, []),
        },
        "trend": (36, 40, "90.0"),
    },
    {
        "file": "03-orders-old-export.json",
        "rows": 8,
        "ids": ("ORD-0021", "ORD-0028"),
        "summary": "2 passed, 1 failed, 1 broken.",
        "results": {
            "Amount is positive": ("pass", 8, 0, None, []),
            "Quantity between 1 and 100": ("pass", 8, 0, None, []),
            "Known status": ("fail", 7, 1, None, ["ORD-0024"]),
            "Currency is EUR": ("broken", None, None, "unknown column(s): currency", []),
        },
        "trend": (23, 24, "95.8"),
    },
    {
        "file": "04-orders-wide.parquet",
        "rows": 6,
        "ids": ("ORD-0029", "ORD-0034"),
        "columns": 22,
        "summary": "3 passed, 1 failed, 0 broken.",
        "results": {
            "Amount is positive": ("pass", 6, 0, None, []),
            "Quantity between 1 and 100": ("pass", 6, 0, None, []),
            "Known status": ("pass", 6, 0, None, []),
            "Currency is EUR": ("fail", 4, 2, None, ["ORD-0031", "ORD-0033"]),
        },
        "trend": (22, 24, "91.7"),
    },
]

WRONG_EXTENSION_FILE = "05-orders-wrong-extension.txt"
TRUNCATED_FILE = "06-orders-truncated.json"
REJECTED_FILES = [WRONG_EXTENSION_FILE, TRUNCATED_FILE]
PARSE_ERROR_PREFIX = "Could not parse uploaded file:"
REPORT_UPLOAD = "03-orders-old-export.json"

EXPECTED_FILES = {
    *(upload["file"] for upload in SCENARIO),
    *REJECTED_FILES,
    "README.md",
    "make_parquet.py",
}

TEXT_DATA_FILES = [
    "01-orders-clean.csv",
    "02-orders-with-bad-rows.csv",
    "03-orders-old-export.json",
    "05-orders-wrong-extension.txt",
    "06-orders-truncated.json",
]

NUMERIC_COLUMNS = ["amount", "quantity"]

# A walkthrough line matching any of these states a result, so it must be one
# of the lines built from SCENARIO.
RESULT_LINE_PATTERNS = [
    re.compile(p)
    for p in [
        r"ORD-\d{4}",
        r"\d+ passed",
        r"\d+ failed",
        r"\d+ broken",
        r"row pass rate \(",
        r"rows evaluated",
        r"\d+ rows",
        r"\d+ columns",
        r"\d+ (runs|points)",
        r"\d+(\.\d+)?%",
        r"unknown column",
        r"Unrecognized file extension",
        r"Could not parse",
        *(rf"\*\*{re.escape(name)}\*\*" for name, _ in RULES),
        r"^rule_name,",
        *(rf"^{re.escape(name)}," for name, _ in RULES),
        r"^\| .+ \| `.+` \|$",
    ]
]


def _walkthrough_lines() -> list[str]:
    return WALKTHROUGH.read_text(encoding="utf-8").splitlines()


def _walkthrough_rules() -> list[tuple[str, str]]:
    """Read the rule table (name | `expression`) from the walkthrough."""
    rules = []
    for line in _walkthrough_lines():
        match = re.fullmatch(r"\| (.+?) \| `(.+)` \|", line)
        if match:
            rules.append((match.group(1), match.group(2)))
    return rules


def _load(file_name: str) -> pd.DataFrame:
    path = SAMPLE_DIR / file_name
    return load_table(path, SUPPORTED_EXTENSIONS[path.suffix])


def _summary_line(results) -> str:
    """The run summary line, as `runs/detail.html` prints it."""
    passed = sum(1 for r in results if r.verdict == "pass")
    failed = sum(1 for r in results if r.verdict == "fail")
    broken = sum(1 for r in results if r.verdict == "broken")
    line = f"{passed} passed, {failed} failed, {broken} broken."
    return f"Clean pass: {line}" if failed == 0 and broken == 0 else line


def _rule_line(name: str, expected) -> str:
    verdict, pass_count, fail_count, reason, failing_ids = expected
    if verdict == "broken":
        return f'- **{name}**: BROKEN, reason `{reason}`. It has no "Expand failing rows" button.'
    line = f"- **{name}**: {verdict.upper()}, {pass_count} passed, {fail_count} failed."
    if verdict == "fail":
        ids = ", ".join(f"`{order_id}`" for order_id in failing_ids)
        line += f' "Expand failing rows" shows, in order: {ids}.'
    return line


def _report_lines(upload) -> list[str]:
    """The "Download report (CSV)" contents for one upload."""
    lines = ["rule_name,verdict,pass_count,fail_count,broken_reason"]
    for name, _ in RULES:
        verdict, pass_count, fail_count, reason, _ = upload["results"][name]
        counts = ["" if c is None else str(c) for c in (pass_count, fail_count)]
        lines.append(",".join([name, verdict, *counts, reason or ""]))
    return lines


def _wrong_extension_error() -> str:
    ext = Path(WRONG_EXTENSION_FILE).suffix
    return f"Unrecognized file extension: {ext!r}. Expected .csv, .json, or .parquet."


def _expected_lines() -> list[str]:
    """Every result line the walkthrough must contain, built from SCENARIO."""
    lines = [f"| {name} | `{expression}` |" for name, expression in RULES]
    for upload in SCENARIO:
        first_id, last_id = upload["ids"]
        file_line = f"File: `{upload['file']}`, {upload['rows']} rows, `{first_id}` to `{last_id}`"
        if "columns" in upload:
            file_line += f", {upload['columns']} columns"
        lines.append(file_line + ".")
        lines.append(f"Run summary line: `{upload['summary']}`")
        lines.extend(_rule_line(name, upload["results"][name]) for name, _ in RULES)
    lines.append(
        f"Go back to the dataset page. The **Row pass rate trend** chart has "
        f"{len(SCENARIO)} points with a line between them."
    )
    for number, upload in enumerate(SCENARIO, start=1):
        passing, evaluated, rate = upload["trend"]
        lines.append(
            f"{number}. `{rate}% row pass rate ({passing}/{evaluated} rows evaluated)` (upload {number})"
        )
    lines.extend(_report_lines(next(u for u in SCENARIO if u["file"] == REPORT_UPLOAD)))
    lines.append(f"`{_wrong_extension_error()}`")
    lines.append(f"`{PARSE_ERROR_PREFIX}`")
    lines.append(
        f"No run is created. The dataset page still lists {len(SCENARIO)} runs "
        f"and the trend still has {len(SCENARIO)} points."
    )
    return lines


# Folder contents


def test_sample_folder_holds_exactly_the_expected_files():
    names = {p.name for p in SAMPLE_DIR.iterdir() if not p.name.startswith(".")}
    assert names == EXPECTED_FILES


def test_sample_folder_size_limits():
    sizes = {p.name: p.stat().st_size for p in SAMPLE_DIR.rglob("*") if p.is_file()}
    assert sum(sizes.values()) < 100 * 1024
    for name, size in sizes.items():
        assert size < 20 * 1024, name


@pytest.mark.parametrize("file_name", TEXT_DATA_FILES)
def test_text_files_are_plain_ascii(file_name):
    (SAMPLE_DIR / file_name).read_bytes().decode("ascii")


@pytest.mark.parametrize("upload", SCENARIO, ids=lambda u: u["file"])
def test_data_files_have_no_blanks_and_numeric_amounts(upload):
    df = _load(upload["file"])
    assert not df.isna().any().any()
    assert not (df.astype(str) == "").any().any()
    for column in NUMERIC_COLUMNS:
        assert pd.api.types.is_numeric_dtype(df[column]), column
    assert df["order_id"].is_unique


def test_json_file_is_an_array_of_records_without_currency():
    rows = json.loads((SAMPLE_DIR / "03-orders-old-export.json").read_text(encoding="ascii"))
    assert isinstance(rows, list) and rows
    for row in rows:
        assert isinstance(row, dict)
        assert "currency" not in row
        for column in NUMERIC_COLUMNS:
            assert isinstance(row[column], (int, float)) and not isinstance(row[column], bool)


def test_parquet_file_is_wide():
    df = _load("04-orders-wide.parquet")
    assert len(df.columns) >= 20
    assert {"order_id", "amount", "quantity", "status", "currency"} <= set(df.columns)


@pytest.mark.parametrize("upload", SCENARIO, ids=lambda u: u["file"])
def test_data_is_obviously_synthetic(upload):
    df = _load(upload["file"])
    for column in df.columns:
        assert not re.search(r"name|email|phone|address|street", column), column
    values = df.astype(str)
    assert not values.apply(lambda col: col.str.contains("@")).any().any()
    assert df["order_id"].str.fullmatch(r"ORD-\d{4}").all()
    assert df["customer_id"].str.fullmatch(r"CUST-\d{3}").all()


# Results


def test_walkthrough_rule_table_is_the_four_rules():
    assert _walkthrough_rules() == RULES


@pytest.mark.parametrize("upload", SCENARIO, ids=lambda u: u["file"])
def test_upload_results_match_scenario(upload):
    df = _load(upload["file"])
    rules = [RuleInput(name=name, expression=expr) for name, expr in _walkthrough_rules()]

    results = run(df, rules)

    first_id, last_id = upload["ids"]
    assert len(df) == upload["rows"]
    assert (df["order_id"].iloc[0], df["order_id"].iloc[-1]) == (first_id, last_id)
    if "columns" in upload:
        assert len(df.columns) == upload["columns"]
    assert [r.rule_name for r in results] == [name for name, _ in RULES]
    for result in results:
        actual = (
            result.verdict,
            result.pass_count,
            result.fail_count,
            result.broken_reason,
            df.iloc[result.failing_row_indices]["order_id"].tolist(),
        )
        assert actual == upload["results"][result.rule_name], result.rule_name
    assert _summary_line(results) == upload["summary"]

    passing, evaluated = row_pass_counts(results)
    assert (passing, evaluated, f"{passing / evaluated * 100:.1f}") == upload["trend"]


def test_wrong_extension_file_is_outside_supported_formats():
    assert Path(WRONG_EXTENSION_FILE).suffix not in SUPPORTED_EXTENSIONS


def test_truncated_json_fails_to_load():
    with pytest.raises(TableLoadError):
        _load(TRUNCATED_FILE)


def test_parquet_generator_rebuilds_the_committed_file(tmp_path):
    output = tmp_path / "rebuilt.parquet"
    subprocess.run([sys.executable, str(GENERATOR), str(output)], check=True, capture_output=True)

    pandas.testing.assert_frame_equal(
        load_table(output, "parquet"), _load("04-orders-wide.parquet")
    )


# Walkthrough text


def test_walkthrough_states_every_expected_line():
    lines = _walkthrough_lines()
    missing = [line for line in _expected_lines() if line not in lines]
    assert missing == []


def test_walkthrough_states_no_unchecked_results():
    expected = set(_expected_lines())
    unchecked = [
        line
        for line in _walkthrough_lines()
        if any(p.search(line) for p in RESULT_LINE_PATTERNS) and line not in expected
    ]
    assert unchecked == []


def test_walkthrough_report_block_is_verbatim():
    text = WALKTHROUGH.read_text(encoding="utf-8")
    report = "\n".join(_report_lines(next(u for u in SCENARIO if u["file"] == REPORT_UPLOAD)))
    assert f"```csv\n{report}\n```" in text


def test_walkthrough_names_the_parquet_command():
    command = "uv run python sample-data/make_parquet.py"
    assert command in WALKTHROUGH.read_text(encoding="utf-8")
    assert command in GENERATOR.read_text(encoding="utf-8")
