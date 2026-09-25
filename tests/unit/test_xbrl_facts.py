"""
Tests for the SEC XBRL ingestion/extraction logic, against real (fixture-
captured) SEC EDGAR data for two real companies with genuinely different
data-quality characteristics:

- Apple Inc. (FY2025): every Altman concept, including
  RetainedEarningsAccumulatedDeficit and StockholdersEquity, is present
  under the FY2025 10-K (accession 0000320193-25-000079). An earlier
  version of the fixture omitted those two entries and this file asserted
  they were "genuinely absent"; re-verified live on 2026-09-25, they are
  not. Apple's real remaining gap is interest_expense. The fixture also
  carries distractor entries (a 2018 filing, a Q1 FY2026 10-Q) that the
  extractor must ignore.
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


def test_aapl_fy2025_extracts_stockholders_equity_and_retained_earnings(aapl_client):
    """Regression test: Apple's FY2025 10-K reports both concepts, and the
    dashboard was wrongly showing them as missing because the fixture
    lacked the FY2025 entries. Values re-verified live against data.sec.gov
    (accession 0000320193-25-000079, filed 2025-10-31, period end
    2025-09-27)."""
    snapshot = build_financial_snapshot(
        aapl_client, cik="0000320193", entity_name="Apple Inc.", fiscal_year=2025
    )

    assert snapshot.stockholders_equity is not None
    assert snapshot.stockholders_equity.value == 73_733_000_000
    assert snapshot.stockholders_equity.accession_number == "0000320193-25-000079"
    assert snapshot.stockholders_equity.period_end.isoformat() == "2025-09-27"

    # Real FY2025 value is a deficit; the extractor must NOT fall back to the
    # 2018-filing distractors (52.6B / 62.9B) or the FY2024 value (-19.154B).
    assert snapshot.retained_earnings is not None
    assert snapshot.retained_earnings.value == -14_264_000_000
    assert snapshot.retained_earnings.accession_number == "0000320193-25-000079"
    assert snapshot.retained_earnings.period_end.isoformat() == "2025-09-27"

    issue_concepts = {issue.concept for issue in snapshot.data_quality_issues}
    assert "retained_earnings" not in issue_concepts
    assert "stockholders_equity" not in issue_concepts


def test_aapl_snapshot_reports_its_real_remaining_gap_honestly(aapl_client):
    snapshot = build_financial_snapshot(
        aapl_client, cik="0000320193", entity_name="Apple Inc.", fiscal_year=2025
    )

    # Clean fields still resolve correctly.
    assert snapshot.total_assets.value == 359_241_000_000
    assert snapshot.operating_income.value == 133_050_000_000
    assert snapshot.revenues.value == 416_161_000_000

    # interest_expense is Apple's one real FY2025 gap: it must be None and
    # reported, never fabricated or defaulted to 0.
    issue_concepts = {issue.concept for issue in snapshot.data_quality_issues}
    assert issue_concepts == {"interest_expense"}
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
