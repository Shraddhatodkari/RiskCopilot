"""Tests for the deterministic multi-year trend classifier (ADR-013)."""
from __future__ import annotations

from src.analysis.trend import STABLE_THRESHOLD, TrendDirection, classify_trend


def test_single_data_point_is_insufficient_data():
    result = classify_trend("current_ratio", [(2025, 1.2)])
    assert result.direction == TrendDirection.INSUFFICIENT_DATA


def test_no_data_points_is_insufficient_data():
    result = classify_trend("current_ratio", [])
    assert result.direction == TrendDirection.INSUFFICIENT_DATA


def test_higher_is_better_metric_increasing_is_improving():
    result = classify_trend("return_on_assets", [(2023, 0.10), (2024, 0.12), (2025, 0.18)])
    assert result.direction == TrendDirection.IMPROVING
    assert "FY2023" in result.detail and "FY2025" in result.detail


def test_higher_is_better_metric_decreasing_is_deteriorating():
    result = classify_trend("current_ratio", [(2023, 2.0), (2025, 1.0)])
    assert result.direction == TrendDirection.DETERIORATING


def test_lower_is_better_metric_decreasing_is_improving():
    """debt_to_equity going DOWN is a good sign -- the classifier must flip
    the usual polarity for this documented, explicit metric."""
    result = classify_trend("debt_to_equity", [(2023, 0.8), (2025, 0.4)])
    assert result.direction == TrendDirection.IMPROVING


def test_lower_is_better_metric_increasing_is_deteriorating():
    result = classify_trend("debt_to_assets", [(2023, 0.2), (2025, 0.5)])
    assert result.direction == TrendDirection.DETERIORATING


def test_small_change_within_threshold_is_stable():
    # Just under the 5% documented cutoff.
    change = STABLE_THRESHOLD - 0.01
    result = classify_trend("operating_margin", [(2023, 1.0), (2025, 1.0 + change)])
    assert result.direction == TrendDirection.STABLE


def test_change_at_or_above_threshold_is_not_stable():
    change = STABLE_THRESHOLD + 0.01
    result = classify_trend("operating_margin", [(2023, 1.0), (2025, 1.0 + change)])
    assert result.direction == TrendDirection.IMPROVING


def test_zero_baseline_does_not_divide_by_zero():
    result = classify_trend("free_cash_flow", [(2023, 0.0), (2025, 500.0)])
    assert result.direction == TrendDirection.IMPROVING
    assert "n/a (from zero)" in result.detail


def test_zero_to_zero_is_stable_not_a_crash():
    result = classify_trend("free_cash_flow", [(2023, 0.0), (2025, 0.0)])
    assert result.direction == TrendDirection.STABLE


def test_uses_earliest_and_latest_year_not_just_first_two_points():
    """Three years of data: the trend must reflect the full span (2022 ->
    2025), not just the first two points in the list, regardless of input
    order."""
    result = classify_trend(
        "return_on_assets", [(2024, 0.05), (2022, 0.20), (2025, 0.02)]
    )
    assert result.points[0] == (2022, 0.20)
    assert result.points[-1] == (2025, 0.02)
    assert result.direction == TrendDirection.DETERIORATING
