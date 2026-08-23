from dataclasses import dataclass, field
from datetime import datetime, timezone

from rulesmith.trend import (
    build_chart_points,
    build_trend_points,
    polyline_points,
    row_pass_counts,
)


@dataclass
class FakeResult:
    verdict: str
    pass_count: int | None
    fail_count: int | None


@dataclass
class FakeRun:
    id: int
    created_at: datetime
    rule_results: list = field(default_factory=list)


def test_row_pass_counts_sums_non_broken_results():
    results = [
        FakeResult("pass", 8, 2),
        FakeResult("fail", 3, 7),
    ]

    passing, evaluated = row_pass_counts(results)

    assert passing == 11
    assert evaluated == 20


def test_row_pass_counts_excludes_broken_rule_rows():
    results = [
        FakeResult("pass", 8, 2),
        FakeResult("broken", None, None),
    ]

    passing, evaluated = row_pass_counts(results)

    assert passing == 8
    assert evaluated == 10


def test_row_pass_counts_all_broken_is_zero_evaluated():
    results = [FakeResult("broken", None, None)]

    passing, evaluated = row_pass_counts(results)

    assert passing == 0
    assert evaluated == 0


def test_row_pass_counts_no_results_is_zero_evaluated():
    passing, evaluated = row_pass_counts([])

    assert passing == 0
    assert evaluated == 0


def test_build_trend_points_excludes_runs_with_zero_evaluated_rows():
    now = datetime.now(timezone.utc)
    eligible_run = FakeRun(1, now, [FakeResult("pass", 9, 1)])
    broken_run = FakeRun(2, now, [FakeResult("broken", None, None)])
    empty_run = FakeRun(3, now, [])

    points = build_trend_points([eligible_run, broken_run, empty_run])

    assert [p.run_id for p in points] == [1]
    assert points[0].rate == 0.9
    assert points[0].passing_rows == 9
    assert points[0].evaluated_rows == 10


def test_build_trend_points_preserves_input_order():
    now = datetime.now(timezone.utc)
    run1 = FakeRun(1, now, [FakeResult("pass", 5, 5)])
    run2 = FakeRun(2, now, [FakeResult("pass", 10, 0)])

    points = build_trend_points([run1, run2])

    assert [p.run_id for p in points] == [1, 2]


def test_build_trend_points_ignores_broken_rules_rows_in_mixed_run():
    now = datetime.now(timezone.utc)
    run = FakeRun(
        1,
        now,
        [
            FakeResult("pass", 5, 5),
            FakeResult("broken", None, None),
        ],
    )

    points = build_trend_points([run])

    assert points[0].evaluated_rows == 10
    assert points[0].rate == 0.5


def test_build_chart_points_single_point_is_centered():
    now = datetime.now(timezone.utc)
    trend_points = build_trend_points([FakeRun(1, now, [FakeResult("pass", 1, 0)])])

    chart_points = build_chart_points(trend_points, width=600, height=160, padding=30)

    assert len(chart_points) == 1
    assert chart_points[0].x == 30 + (600 - 60) / 2


def test_build_chart_points_spreads_multiple_points_across_width():
    now = datetime.now(timezone.utc)
    runs = [
        FakeRun(1, now, [FakeResult("pass", 5, 5)]),
        FakeRun(2, now, [FakeResult("pass", 10, 0)]),
        FakeRun(3, now, [FakeResult("pass", 0, 10)]),
    ]
    trend_points = build_trend_points(runs)

    chart_points = build_chart_points(trend_points, width=600, height=160, padding=30)

    assert chart_points[0].x == 30
    assert chart_points[-1].x == 570
    # Higher rate plots higher up (smaller y).
    assert chart_points[1].y < chart_points[0].y < chart_points[2].y


def test_polyline_points_formats_as_svg_points_attribute():
    now = datetime.now(timezone.utc)
    trend_points = build_trend_points(
        [
            FakeRun(1, now, [FakeResult("pass", 5, 5)]),
            FakeRun(2, now, [FakeResult("pass", 10, 0)]),
        ]
    )
    chart_points = build_chart_points(trend_points, width=600, height=160, padding=30)

    result = polyline_points(chart_points)

    assert result == f"{chart_points[0].x},{chart_points[0].y} {chart_points[1].x},{chart_points[1].y}"


def test_polyline_points_empty_for_no_points():
    assert polyline_points([]) == ""
