"""
Tests for the deterministic ratio engine (ADR-013), against the same real,
fixture-captured SEC data every other analysis test uses. Expected values
were independently computed by hand from the same real fixture figures
already asserted in tests/unit/test_xbrl_facts.py (e.g. MSFT FY2025
total_assets=619,003,000,000, current_assets=191,131,000,000,
current_liabilities=141,218,000,000), not copied from the module under
test.
"""
from __future__ import annotations

import pytest

from src.analysis.financial_ratios import RatioStatus, compute_financial_ratios
from src.ingestion.xbrl_facts import build_financial_snapshot


def test_msft_liquidity_and_profitability_ratios_are_real_and_correct(msft_client):
    snapshot = build_financial_snapshot(
        msft_client, "0000789019", "Microsoft Corporation", 2025
    )
    ratios = compute_financial_ratios(snapshot)

    current_ratio = ratios.get("current_ratio")
    assert current_ratio.status == RatioStatus.AVAILABLE
    assert current_ratio.value == round(191_131_000_000 / 141_218_000_000, 4)

    quick_ratio = ratios.get("quick_ratio")
    assert quick_ratio.status == RatioStatus.AVAILABLE
    assert quick_ratio.value == round((191_131_000_000 - 938_000_000) / 141_218_000_000, 4)

    roa = ratios.get("return_on_assets")
    assert roa.value == round(101_832_000_000 / 619_003_000_000, 4)

    roe = ratios.get("return_on_equity")
    assert roe.value == round(101_832_000_000 / 343_479_000_000, 4)

    op_margin = ratios.get("operating_margin")
    assert op_margin.value == round(128_528_000_000 / 281_724_000_000, 4)

    net_margin = ratios.get("net_margin")
    assert net_margin.value == round(101_832_000_000 / 281_724_000_000, 4)

    ocf = ratios.get("operating_cash_flow")
    assert ocf.value == 136_162_000_000

    fcf = ratios.get("free_cash_flow")
    assert fcf.value == 136_162_000_000 - 64_551_000_000


def test_msft_leverage_ratios_are_honestly_unavailable_not_estimated(msft_client):
    """MSFT's own FY2025 10-K genuinely has no clean fy=2025 long_term_debt
    fact (see tests/unit/test_xbrl_facts.py) and no interest_expense value
    at all (confirmed live against data.sec.gov) — both leverage ratios
    that depend on those concepts must say so explicitly, not silently
    default to 0 or skip the metric without explanation."""
    snapshot = build_financial_snapshot(
        msft_client, "0000789019", "Microsoft Corporation", 2025
    )
    ratios = compute_financial_ratios(snapshot)

    for name in ("debt_to_equity", "debt_to_assets", "interest_coverage"):
        r = ratios.get(name)
        assert r.status == RatioStatus.INSUFFICIENT_DATA
        assert r.value is None
        assert "Refusing to substitute" in r.detail


def test_aapl_current_ratio_is_a_real_below_one_liquidity_signal(aapl_client):
    """A real, live finding, not a constructed test case: Apple's FY2025
    current ratio is genuinely below 1.0 (current liabilities exceed
    current assets) — this is exactly the real-world case
    src/analysis/risk_rating.py's liquidity-flag rule exists for."""
    snapshot = build_financial_snapshot(aapl_client, "0000320193", "Apple Inc.", 2025)
    ratios = compute_financial_ratios(snapshot)

    current_ratio = ratios.get("current_ratio")
    assert current_ratio.status == RatioStatus.AVAILABLE
    assert current_ratio.value == round(147_957_000_000 / 165_631_000_000, 4)
    assert current_ratio.value < 1.0


def test_aapl_fy2025_equity_ratios_compute_and_interest_coverage_is_honestly_missing(aapl_client):
    """Apple's real FY2025 10-K reports StockholdersEquity (73,733M), so the
    equity-dependent ratios compute. Expected values worked by hand from the
    filed figures: debt_to_equity = 78,328M / 73,733M = 1.0623;
    return_on_equity = 112,010M / 73,733M = 1.5191. Apple tags no
    InterestExpense for FY2025, so interest_coverage must say so."""
    snapshot = build_financial_snapshot(aapl_client, "0000320193", "Apple Inc.", 2025)
    ratios = compute_financial_ratios(snapshot)

    assert ratios.get("debt_to_equity").status == RatioStatus.AVAILABLE
    assert ratios.get("debt_to_equity").value == pytest.approx(1.0623, abs=1e-3)
    assert ratios.get("return_on_equity").status == RatioStatus.AVAILABLE
    assert ratios.get("return_on_equity").value == pytest.approx(1.5191, abs=1e-3)

    ic = ratios.get("interest_coverage")
    assert ic.status == RatioStatus.INSUFFICIENT_DATA
    assert ic.value is None


def test_equity_dependent_ratios_unavailable_when_stockholders_equity_is_missing(
    aapl_client_missing_equity_tags,
):
    """If stockholders_equity is unavailable (here: real Apple data with the
    tag deliberately hidden, see tests/conftest.py::TagHidingClient), the
    ratios that need it must say so explicitly instead of computing from a
    fabricated value, while ratios that don't need it still compute."""
    snapshot = build_financial_snapshot(
        aapl_client_missing_equity_tags, "0000320193", "Apple Inc.", 2025
    )
    ratios = compute_financial_ratios(snapshot)

    for name in ("debt_to_equity", "return_on_equity"):
        r = ratios.get(name)
        assert r.status == RatioStatus.INSUFFICIENT_DATA
        assert r.value is None

    for name in ("return_on_assets", "operating_margin", "net_margin"):
        assert ratios.get(name).status == RatioStatus.AVAILABLE


def test_ratio_set_available_count_reflects_real_partial_data(msft_client, aapl_client):
    """The two companies' real FY2025 filings have DIFFERENT gaps. MSFT: no
    long_term_debt or interest_expense tagged, so 3 of 11 ratios are
    unavailable. Apple: only interest_expense is missing, so only
    interest_coverage is unavailable (10 of 11)."""
    msft_snap = build_financial_snapshot(msft_client, "0000789019", "Microsoft Corporation", 2025)
    aapl_snap = build_financial_snapshot(aapl_client, "0000320193", "Apple Inc.", 2025)

    msft_ratios = compute_financial_ratios(msft_snap)
    aapl_ratios = compute_financial_ratios(aapl_snap)

    assert msft_ratios.available_count == 8   # 11 total minus the 3 leverage ratios above
    assert aapl_ratios.available_count == 10  # 11 total minus interest_coverage


def test_every_ratio_has_a_readable_formula_and_source_concepts(msft_client):
    snapshot = build_financial_snapshot(msft_client, "0000789019", "Microsoft Corporation", 2025)
    ratios = compute_financial_ratios(snapshot)
    for r in ratios.ratios:
        assert r.formula
        assert r.source_concepts
        assert r.category in {"liquidity", "leverage", "profitability", "cash_flow"}
