"""
Deterministic multi-year trend classification (ADR-013). Turns a bare
series of (fiscal_year, value) points — already computed by
`src/analysis/financial_ratios.py`, `src/analysis/altman_z.py`, or
`src/analysis/piotroski.py` — into one of four plain, documented labels:
improving, deteriorating, stable, or insufficient_data. No LLM involved:
this is arithmetic over already-real numbers, per ADR-005.

Rule, stated in full (not hidden behind a model):

1. Fewer than two real fiscal years of data for a metric -> INSUFFICIENT_DATA.
   A single data point cannot show a trend, and this project does not
   invent a second one.
2. Compare the earliest and latest available real fiscal year's values
   (not just any two years — the full span this company has real data
   for). Relative change = (last - first) / abs(first), with `first == 0`
   handled as a special case (see code) rather than dividing by zero.
3. |relative change| < STABLE_THRESHOLD (5%, a fixed, documented cutoff —
   not tuned per company) -> STABLE.
4. Otherwise, whether that change counts as IMPROVING or DETERIORATING
   depends on the metric's known polarity: for most metrics a larger
   number is better (current ratio, ROA, margins, cash flow, Altman Z',
   Piotroski F-Score...); for a small, explicit set (debt/equity,
   debt/assets) a SMALLER number is better. `_LOWER_IS_BETTER` is that
   explicit list — anything not in it is assumed higher-is-better.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

STABLE_THRESHOLD = 0.05  # 5% relative change — fixed and documented, not tuned per company

# The only metrics in this project where a SMALLER value is the better
# outcome. Every other metric this module is ever called with (current
# ratio, quick ratio, ROA, ROE, margins, OCF, FCF, interest coverage,
# Altman Z', Piotroski F-Score, revenue) is higher-is-better.
_LOWER_IS_BETTER = {"debt_to_equity", "debt_to_assets"}


class TrendDirection(str, Enum):
    IMPROVING = "improving"
    DETERIORATING = "deteriorating"
    STABLE = "stable"
    INSUFFICIENT_DATA = "insufficient_data"


class MetricTrend(BaseModel):
    metric: str
    points: list[tuple[int, float]]  # (fiscal_year, value), ascending, real values only
    direction: TrendDirection
    detail: str


def classify_trend(metric: str, points: list[tuple[int, float]]) -> MetricTrend:
    pts = sorted(points, key=lambda p: p[0])
    if len(pts) < 2:
        return MetricTrend(
            metric=metric, points=pts, direction=TrendDirection.INSUFFICIENT_DATA,
            detail=(
                f"Only {len(pts)} real fiscal year(s) of data available for {metric} — "
                f"at least two are required to show a trend. Not estimated or interpolated."
            ),
        )

    first_year, first_val = pts[0]
    last_year, last_val = pts[-1]

    if first_val == 0:
        rel_change = 0.0 if last_val == 0 else (float("inf") if last_val > 0 else float("-inf"))
    else:
        rel_change = (last_val - first_val) / abs(first_val)

    lower_is_better = metric in _LOWER_IS_BETTER

    if abs(rel_change) < STABLE_THRESHOLD:
        direction = TrendDirection.STABLE
    elif (rel_change > 0) != lower_is_better:
        direction = TrendDirection.IMPROVING
    else:
        direction = TrendDirection.DETERIORATING

    pct_display = "n/a (from zero)" if rel_change in (float("inf"), float("-inf")) else f"{rel_change:+.1%}"
    detail = (
        f"FY{first_year}={first_val:.4g} -> FY{last_year}={last_val:.4g} "
        f"({pct_display} over {last_year - first_year} fiscal year(s))"
    )
    return MetricTrend(metric=metric, points=pts, direction=direction, detail=detail)
