"""Row-level pass-rate trend computation for the dataset detail page.

Plain functions with no FastAPI or database imports, so they can be
tested without a running app or a browser. Nothing here mutates the
Run/RuleResult data it reads -- runs stay a historical record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol


class _RuleResultLike(Protocol):
    verdict: str
    pass_count: int | None
    fail_count: int | None


class _RunLike(Protocol):
    id: int
    created_at: datetime
    rule_results: Iterable[_RuleResultLike]


@dataclass(frozen=True)
class TrendPoint:
    """One eligible run's row-level pass rate."""

    run_id: int
    created_at: datetime
    passing_rows: int
    evaluated_rows: int
    rate: float


@dataclass(frozen=True)
class ChartPoint:
    """A TrendPoint placed at SVG coordinates."""

    x: float
    y: float
    run_id: int
    created_at: datetime
    passing_rows: int
    evaluated_rows: int
    rate: float


def row_pass_counts(rule_results: Iterable[_RuleResultLike]) -> tuple[int, int]:
    """Return (passing_rows, evaluated_rows) across non-broken rule results.

    Rows under a broken rule are excluded from both counts: a rule
    failing to run is not the same as its rows failing.
    """
    passing = 0
    evaluated = 0
    for result in rule_results:
        if result.verdict == "broken":
            continue
        pass_count = result.pass_count or 0
        fail_count = result.fail_count or 0
        passing += pass_count
        evaluated += pass_count + fail_count
    return passing, evaluated


def build_trend_points(runs_oldest_to_newest: Iterable[_RunLike]) -> list[TrendPoint]:
    """Build the trend series, excluding runs with zero evaluated rows.

    `runs_oldest_to_newest` must already be ordered oldest-to-newest.
    """
    points: list[TrendPoint] = []
    for run in runs_oldest_to_newest:
        passing, evaluated = row_pass_counts(run.rule_results)
        if evaluated == 0:
            continue
        points.append(
            TrendPoint(
                run_id=run.id,
                created_at=run.created_at,
                passing_rows=passing,
                evaluated_rows=evaluated,
                rate=passing / evaluated,
            )
        )
    return points


def build_chart_points(
    points: list[TrendPoint],
    *,
    width: float,
    height: float,
    padding: float,
) -> list[ChartPoint]:
    """Place trend points on an SVG coordinate grid.

    A single point is centered horizontally rather than erroring or
    being pinned to an edge.
    """
    plot_width = width - 2 * padding
    plot_height = height - 2 * padding
    n = len(points)

    chart_points: list[ChartPoint] = []
    for i, point in enumerate(points):
        if n == 1:
            x = padding + plot_width / 2
        else:
            x = padding + (i / (n - 1)) * plot_width
        y = padding + (1 - point.rate) * plot_height
        chart_points.append(
            ChartPoint(
                x=round(x, 2),
                y=round(y, 2),
                run_id=point.run_id,
                created_at=point.created_at,
                passing_rows=point.passing_rows,
                evaluated_rows=point.evaluated_rows,
                rate=point.rate,
            )
        )
    return chart_points


def polyline_points(chart_points: list[ChartPoint]) -> str:
    """Render chart points as an SVG `points` attribute value."""
    return " ".join(f"{cp.x},{cp.y}" for cp in chart_points)
