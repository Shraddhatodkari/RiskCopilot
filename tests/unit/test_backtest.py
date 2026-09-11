"""
Tests for the historical distress backtest, against real
tests/fixtures/distress_backtest_cases.json data. Expected Z' values below
were independently computed via a standalone script (shown in
docs/06_historical_backtest.md) before this test was written, not derived
by running the code under test and copying its output.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.analysis.altman_z import RiskZone
from src.evaluation.backtest import run_backtest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def backtest_cases():
    data = json.loads((FIXTURES_DIR / "distress_backtest_cases.json").read_text())
    return data["cases"]


def test_bed_bath_and_beyond_missed_at_one_year_but_caught_at_final_filing(backtest_cases):
    summary = run_backtest(backtest_cases)
    bbby = next(c for c in summary.cases if c.cik == "0000886158")

    fy2021, fy2022 = bbby.snapshots
    assert fy2021.fiscal_year == 2021
    assert fy2021.z_score == pytest.approx(2.9344, abs=1e-3)
    assert fy2021.zone == RiskZone.SAFE
    assert not fy2021.flagged_for_review  # a real, honestly-reported miss
    assert fy2021.lead_time_days == 421  # 2022-02-26 -> 2023-04-23

    assert fy2022.fiscal_year == 2022
    assert fy2022.z_score == pytest.approx(-2.0776, abs=1e-3)
    assert fy2022.zone == RiskZone.DISTRESS
    assert fy2022.strong_signal
    assert not fy2022.filed_before_event  # filed 2023-06-14, after the 2023-04-23 petition


def test_party_city_caught_with_real_lead_time_at_both_points(backtest_cases):
    summary = run_backtest(backtest_cases)
    prty = next(c for c in summary.cases if c.cik == "0001592058")

    fy2021, fy2022 = prty.snapshots
    assert fy2021.z_score == pytest.approx(0.7644, abs=1e-3)
    assert fy2021.zone == RiskZone.DISTRESS
    assert fy2021.filed_before_event  # filed 2022-02-28, well before 2023-01-17 petition
    assert fy2021.lead_time_days == 382  # 2021-12-31 -> 2023-01-17 (~12.5 months)

    assert fy2022.z_score == pytest.approx(-1.4793, abs=1e-3)
    assert fy2022.zone == RiskZone.DISTRESS


def test_summary_aggregates_honestly(backtest_cases):
    summary = run_backtest(backtest_cases)
    assert summary.total_snapshots == 4
    # 3 of 4 real pre-bankruptcy snapshots were flagged (grey or distress);
    # exactly 1 (BBBY one year out) was a genuine miss.
    assert summary.flagged_count == 3
    assert summary.missed_count == 1
    assert summary.strong_signal_count == 3  # all 3 flags were distress-zone, not just grey
    assert summary.flagged_rate == pytest.approx(0.75)
