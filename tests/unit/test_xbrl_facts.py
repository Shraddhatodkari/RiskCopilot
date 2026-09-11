"""
Tests for the SEC XBRL ingestion/extraction logic, against real (fixture-
captured) SEC EDGAR data for two real companies with genuinely different
data-quality characteristics:

- Apple Inc. (FY2025): total_assets/liabilities/current items/operating
  income/revenues are clean, but RetainedEarningsAccumulatedDeficit and a
  clean FY2025 StockholdersEquity fact are genuinely absent from what
  data.sec.gov's companyconcept API returns (confirmed live on 2026-09-08).
  This is the real-world "missing tag" case.
- Microsoft Corporation (FY2025): every Phase-1 (Altman) concept is present
  and clean under a single accession number. Phase 2 added long_term_debt
  (for the Piotroski F-Score) and discovered that Microsoft's OWN FY2025
  10-K has no clean fy=2025 fact for it either (see
  docs/03_data_provenance.md). The expanded ratio engine (ADR-013) then
  added `interest_expense`, and confirmed live against data.sec.gov that
  Microsoft's FY2025 10-K accession genuinely carries no InterestExpense
  (or InterestExpenseDebt, which 404s for this CIK entirely) value at all —
  a real reporting change, not a fixture gap. So this snapshot legitimately
  carries exactly two real data-quality issues, not zero and not one.
"""
from __future__ import annotations

from src.ingestion.xbrl_facts import build_financial_snapshot


def test_msft_snapshot_is_complete_and_correct(msft_client):
    snapshot = build_financial_snapshot(
        msft_client, cik="0000789019", entity_name="Microsoft Corporation", fiscal_year=2025
    )

    issue_concepts = {issue.concept for issue in snapshot.data_quality_issues}
    assert issue_concepts == {"long_term_debt", "interest_expense"}
    assert snapshot.total_assets is not None
    assert snapshot.total_assets.value == 619_003_000_000
    assert snapshot.total_assets.tag_used == "Assets"
    assert snapshot.total_assets.form == "10-K"
    assert snapshot.total_assets.accession_number == "0000950170-25-100235"

    assert snapshot.total_liabilities.value == 275_524_000_000
    assert snapshot.current_assets.value == 191_131_000_000
    assert snapshot.current_liabilities.value == 141_218_000_000
    assert snapshot.retained_earnings.value == 237_731_000_000
    assert snapshot.operating_income.value == 128_528_000_000
    assert snapshot.revenues.value == 281_724_000_000
    assert snapshot.revenues.tag_used == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert snapshot.stockholders_equity.value == 343_479_000_000

    # Duration fact must carry the full fiscal-year span, not a quarter.
    assert snapshot.operating_income.period_start.isoformat() == "2024-07-01"
    assert snapshot.operating_income.period_end.isoformat() == "2025-06-30"

    # New ratio-engine concepts (ADR-013), also real values confirmed live
    # against data.sec.gov on 2026-09-10.
    assert snapshot.inventory.value == 938_000_000
    assert snapshot.capital_expenditures.value == 64_551_000_000
    assert snapshot.interest_expense is None  # see module docstring: a real, confirmed gap


def test_msft_ignores_prior_year_comparative_in_same_filing(msft_client):
    """The FY2025 10-K also reports FY2024's Assets figure as a comparative
    balance. The extractor must pick the fy=2025 value, not just 'any entry
    with this accession number'."""
    snapshot = build_financial_snapshot(
        msft_client, cik="0000789019", entity_name="Microsoft Corporation", fiscal_year=2025
    )
    assert snapshot.total_assets.value == 619_003_000_000
    assert snapshot.total_assets.period_end.isoformat() == "2025-06-30"


def test_aapl_snapshot_flags_missing_retained_earnings_honestly(aapl_client):
    snapshot = build_financial_snapshot(
        aapl_client, cik="0000320193", entity_name="Apple Inc.", fiscal_year=2025
    )

    # Clean fields still resolve correctly.
    assert snapshot.total_assets.value == 359_241_000_000
    assert snapshot.operating_income.value == 133_050_000_000
    assert snapshot.revenues.value == 416_161_000_000

    # The genuinely missing/ambiguous fields must be None, NOT fabricated
    # or silently defaulted to 0 or to a stale (2018) value.
    assert snapshot.retained_earnings is None
    assert snapshot.stockholders_equity is None

    issue_concepts = {issue.concept for issue in snapshot.data_quality_issues}
    assert "retained_earnings" in issue_concepts
    assert "stockholders_equity" in issue_concepts
    for issue in snapshot.data_quality_issues:
        assert issue.severity == "missing"
        assert issue.detail  # a human-readable explanation must be present

    # New ratio-engine concepts: inventory/capex are real and present for
    # Apple FY2025; interest_expense is a real, confirmed gap (Apple's own
    # FY2024/2025 10-Ks carry no InterestExpense/InterestExpenseDebt value).
    assert snapshot.inventory.value == 5_718_000_000
    assert snapshot.capital_expenditures.value == 12_715_000_000
    assert snapshot.interest_expense is None
    assert "interest_expense" in issue_concepts


def test_ignores_quarterly_entries_for_annual_extraction(aapl_client):
    """Both fixtures include a 10-Q entry for the same fiscal year to prove
    the extractor requires form == '10-K' and (for duration facts) a
    ~350-380 day span, not just any entry tagged with the target fiscal
    year."""
    snapshot = build_financial_snapshot(
        aapl_client, cik="0000320193", entity_name="Apple Inc.", fiscal_year=2025
    )
    # The Q3 10-Q value (331,495,000,000) must NOT have been picked.
    assert snapshot.total_assets.value != 331_495_000_000
    assert snapshot.total_assets.value == 359_241_000_000
    assert snapshot.total_assets.form == "10-K"
