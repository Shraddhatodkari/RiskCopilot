"""
Tests for the Piotroski F-Score, against real (fixture-captured) Microsoft
data spanning three real fiscal years (FY2023, FY2024, FY2025).

Two real-world cases, mirroring the Altman Z' tests in spirit:

1. FY2024 vs FY2023: every required field is present and clean under both
   fiscal years' own 10-Ks -> a complete, real F-Score, independently
   hand-verified in scripts/verify_msft_piotroski.py.
2. FY2025 vs FY2024: Microsoft's FY2025 10-K itself has no clean fy=2025
   LongTermDebtNoncurrent/LongTermDebt fact (confirmed live against
   data.sec.gov — see docs/03_data_provenance.md) -> the system must
   refuse to compute a score rather than guess.
"""
from __future__ import annotations

import pytest

from src.analysis.piotroski import InsufficientDataError, compute_piotroski_f_score
from src.ingestion.xbrl_facts import build_financial_snapshot


def _snapshot(client, cik, name, fy):
    return build_financial_snapshot(client, cik, name, fy)


def test_msft_fy2024_full_f_score_matches_independent_verification(msft_client):
    current = _snapshot(msft_client, "0000789019", "Microsoft Corporation", 2024)
    prior = _snapshot(msft_client, "0000789019", "Microsoft Corporation", 2023)

    result = compute_piotroski_f_score(current, prior)

    # Independently verified via scripts/verify_msft_piotroski.py against
    # the same real FY2023/FY2024 10-K figures.
    assert result.f_score == 5
    assert result.interpretation == "moderate"

    by_name = {s.name: s.passed for s in result.signals}
    assert by_name["roa_positive"] is True
    assert by_name["cfo_positive"] is True
    assert by_name["delta_roa_positive"] is False  # ROA dipped slightly YoY
    assert by_name["accruals_quality"] is True
    assert by_name["leverage_decreased"] is True
    assert by_name["liquidity_increased"] is False  # current ratio fell
    assert by_name["no_new_shares"] is False  # shares rose 7,432M -> 7,434M
    assert by_name["gross_margin_increased"] is True
    assert by_name["asset_turnover_increased"] is False


def test_msft_fy2025_raises_on_missing_long_term_debt(msft_client):
    current = _snapshot(msft_client, "0000789019", "Microsoft Corporation", 2025)
    prior = _snapshot(msft_client, "0000789019", "Microsoft Corporation", 2024)

    with pytest.raises(InsufficientDataError) as exc_info:
        compute_piotroski_f_score(current, prior)

    assert "long_term_debt" in exc_info.value.missing_concepts
    assert exc_info.value.fiscal_year == 2025


def test_requires_consecutive_fiscal_years(msft_client):
    fy2025 = _snapshot(msft_client, "0000789019", "Microsoft Corporation", 2025)
    fy2023 = _snapshot(msft_client, "0000789019", "Microsoft Corporation", 2023)
    with pytest.raises(ValueError, match="one fiscal year before"):
        compute_piotroski_f_score(fy2025, fy2023)


def test_shares_outstanding_uses_shares_unit_not_usd(msft_client):
    """Regression test for a real bug caught during development: the
    extractor originally hard-coded units['USD'], which silently returned
    no data for any share-count concept (SEC reports those under
    units['shares']). This asserts the fix: a real share count is
    extracted with the correct magnitude (billions of shares, not
    dollars)."""
    snapshot = _snapshot(msft_client, "0000789019", "Microsoft Corporation", 2024)
    assert snapshot.shares_outstanding is not None
    assert snapshot.shares_outstanding.unit == "shares"
    assert snapshot.shares_outstanding.value == 7_434_000_000
