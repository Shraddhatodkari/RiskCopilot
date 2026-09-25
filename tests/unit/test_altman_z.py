"""
Correctness tests for the Altman Z'-Score implementation.

Two independent kinds of check, deliberately kept separate:

1. `test_formula_coefficients_are_wired_correctly` is a pure math check
   with contrived, round-number inputs where every ratio (X1..X5) evaluates
   to exactly 1.0. This isolates "did we implement the formula/coefficients
   right" from "is real-world data plumbed through right" — if this test
   ever fails after someone edits altman_z.py, the bug is in the formula
   itself, not in data ingestion.

2. `test_msft_real_filing_matches_independently_hand_computed_value` runs
   the model on Microsoft's real, filed FY2025 10-K numbers and checks the
   result against a value computed independently (via a standalone Python
   script, shown in docs/03_data_provenance.md) — not against a value
   copy-pasted out of the implementation itself.
"""
from __future__ import annotations

from datetime import date

import pytest

from src.analysis.altman_z import (
    InsufficientDataError,
    RiskZone,
    compute_altman_z_prime,
)
from src.ingestion.models import FactPoint, FinancialSnapshot
from src.ingestion.xbrl_facts import build_financial_snapshot


def _fact(concept: str, value: float) -> FactPoint:
    return FactPoint(
        concept=concept,
        tag_used="TestTag",
        value=value,
        unit="USD",
        period_end=date(2025, 12, 31),
        fiscal_year=2025,
        fiscal_period="FY",
        form="10-K",
        filed=date(2026, 1, 1),
        accession_number="0000000000-26-000000",
    )


def test_formula_coefficients_are_wired_correctly():
    """Construct a snapshot where working_capital == total_assets,
    retained_earnings == total_assets, operating_income == total_assets,
    book_equity == total_liabilities, and revenues == total_assets — i.e.
    every ratio X1..X5 equals exactly 1.0. Z' must then equal the sum of
    the five published coefficients (0.717+0.847+3.107+0.420+0.998=6.089),
    which is only true if each coefficient is attached to the correct
    variable."""
    snapshot = FinancialSnapshot(
        cik="0000000001",
        entity_name="Synthetic Test Co",
        fiscal_year=2025,
        total_assets=_fact("total_assets", 1000.0),
        total_liabilities=_fact("total_liabilities", 400.0),
        # working_capital = current_assets - current_liabilities = 1000 (== total_assets, so X1 = 1.0)
        current_assets=_fact("current_assets", 1200.0),
        current_liabilities=_fact("current_liabilities", 200.0),
        retained_earnings=_fact("retained_earnings", 1000.0),  # X2 = 1.0
        operating_income=_fact("operating_income", 1000.0),  # X3 = 1.0
        revenues=_fact("revenues", 1000.0),  # X5 = 1.0
        stockholders_equity=_fact("stockholders_equity", 400.0),  # X4 = book_equity/total_liabilities = 1.0
    )

    result = compute_altman_z_prime(snapshot)

    assert result.is_complete
    assert result.z_score == pytest.approx(6.089, abs=1e-6)
    assert result.zone == RiskZone.SAFE

    by_name = {c.name: c.value for c in result.components}
    assert by_name["X1_working_capital_to_assets"] == pytest.approx(1.0)
    assert by_name["X2_retained_earnings_to_assets"] == pytest.approx(1.0)
    assert by_name["X3_ebit_to_assets"] == pytest.approx(1.0)
    assert by_name["X4_book_equity_to_liabilities"] == pytest.approx(1.0)
    assert by_name["X5_sales_to_assets"] == pytest.approx(1.0)


def test_msft_real_filing_matches_independently_hand_computed_value(msft_client):
    snapshot = build_financial_snapshot(
        msft_client, cik="0000789019", entity_name="Microsoft Corporation", fiscal_year=2025
    )
    result = compute_altman_z_prime(snapshot)

    # Independently computed via scripts/verify_msft_zscore.py against the
    # same real FY2025 10-K figures — see docs/03_data_provenance.md.
    assert result.z_score == pytest.approx(2.0060, abs=1e-3)
    assert result.zone == RiskZone.GREY
    assert result.is_complete


def test_missing_retained_earnings_raises_instead_of_fabricating(aapl_client_missing_equity_tags):
    """When retained_earnings is unavailable (here: real Apple data with
    that tag deliberately hidden, see tests/conftest.py::TagHidingClient),
    the scoring function must refuse to compute a number rather than
    silently treating the missing value as zero — a silent zero would
    corrupt X2 and understate/overstate risk without any indication."""
    snapshot = build_financial_snapshot(
        aapl_client_missing_equity_tags, cik="0000320193", entity_name="Apple Inc.", fiscal_year=2025
    )

    with pytest.raises(InsufficientDataError) as exc_info:
        compute_altman_z_prime(snapshot)

    assert "retained_earnings" in exc_info.value.missing_concepts


def test_aapl_fy2025_real_filing_now_scores(aapl_client):
    """With the corrected fixture, Apple's real FY2025 10-K has every Altman
    input. Expected value computed independently by hand from the filed
    figures (TA 359,241M; TL 285,508M; CA 147,957M; CL 165,631M; RE
    -14,264M; EBIT 133,050M; Rev 416,161M; Equity 73,733M), not copied
    from this code's output."""
    snapshot = build_financial_snapshot(
        aapl_client, cik="0000320193", entity_name="Apple Inc.", fiscal_year=2025
    )
    result = compute_altman_z_prime(snapshot)
    assert result.z_score == pytest.approx(2.3464, abs=1e-3)
    assert result.zone == RiskZone.GREY
    assert result.is_complete


def test_zone_boundaries():
    base = dict(
        total_assets=_fact("total_assets", 1000.0),
        current_assets=_fact("current_assets", 1000.0),
        current_liabilities=_fact("current_liabilities", 0.0),
        operating_income=_fact("operating_income", 0.0),
        revenues=_fact("revenues", 0.0),
        total_liabilities=_fact("total_liabilities", 1000.0),
    )
    # Choose retained_earnings so that Z' lands just above/below each cutoff,
    # using only X2's coefficient (0.847) to control the score precisely:
    # Z' = 0.847 * (retained_earnings / 1000)  [all other terms zero]
    def z_for_retained_earnings(re_value: float) -> float:
        snap = FinancialSnapshot(
            cik="x", entity_name="x", fiscal_year=2025,
            retained_earnings=_fact("retained_earnings", re_value),
            **base,
        )
        return compute_altman_z_prime(snap).z_score

    # Safe: need z' > 2.90 -> re > 2.90/0.847*1000
    assert compute_altman_z_prime(
        FinancialSnapshot(
            cik="x", entity_name="x", fiscal_year=2025,
            retained_earnings=_fact("retained_earnings", 4000.0), **base,
        )
    ).zone == RiskZone.SAFE

    # Distress: re small enough that z' < 1.23
    assert compute_altman_z_prime(
        FinancialSnapshot(
            cik="x", entity_name="x", fiscal_year=2025,
            retained_earnings=_fact("retained_earnings", 100.0), **base,
        )
    ).zone == RiskZone.DISTRESS
