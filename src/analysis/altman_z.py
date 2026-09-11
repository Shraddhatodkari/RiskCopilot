"""
Altman Z'-Score (private-firm variant), computed deterministically in plain
Python from real, sourced SEC XBRL data — no LLM involved in this
calculation, by design (see docs/02_architecture_decision_record.md,
ADR-005: "deterministic-first financial math").

Formula and cutoffs (independently cross-checked against two sources during
Phase-1 research — see docs/03_data_provenance.md):

    Z' = 0.717*X1 + 0.847*X2 + 3.107*X3 + 0.420*X4 + 0.998*X5

    X1 = Working Capital / Total Assets
       = (Current Assets - Current Liabilities) / Total Assets
    X2 = Retained Earnings / Total Assets
    X3 = EBIT / Total Assets                  (proxied by Operating Income)
    X4 = Book Value of Equity / Total Liabilities
    X5 = Sales / Total Assets                 (proxied by Revenues)

    Zones: Z' > 2.90            -> "safe"
           1.23 <= Z' <= 2.90   -> "grey"
           Z' < 1.23            -> "distress"

Sources: Altman, E.I. (2000), "Predicting Financial Distress of Companies:
Revisiting the Z-Score and ZETA Models"; cross-checked against
https://www.wallstreetprep.com/knowledge/altman-z-score/ and
https://stablebread.com/altman-z-score/ (both independently give identical
coefficients and cutoffs).

Known limitation of this variant, stated plainly rather than hidden: the
Z'-Score was originally validated on private *manufacturing* firms. It is
used here as a general-purpose, book-value-only proxy for financial distress
because it requires no market price data (keeping the pipeline lightweight
and usable for private/unlisted counterparties too), not because Apple Inc.
is a private manufacturer. Phase 2 adds the market-value-based original
Z-Score as a cross-check for public companies, and a documented backtest
against real historical distress/bankruptcy cases to measure how well
either variant actually performs out of sample — the business case does
not rest on the classifier's accuracy being assumed correct.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from src.ingestion.models import FinancialSnapshot


class RiskZone(str, Enum):
    SAFE = "safe"
    GREY = "grey"
    DISTRESS = "distress"


class ZScoreComponent(BaseModel):
    name: str
    formula: str
    value: float
    source_concepts: list[str]


class ZScoreResult(BaseModel):
    cik: str
    entity_name: str
    fiscal_year: int
    z_score: float
    zone: RiskZone
    components: list[ZScoreComponent]
    is_complete: bool  # True only if every required input was a real, sourced fact


class InsufficientDataError(RuntimeError):
    """Raised when required inputs are missing and the caller has not opted
    into a best-effort partial calculation."""

    def __init__(self, missing_concepts: list[str]):
        self.missing_concepts = missing_concepts
        super().__init__(
            f"Cannot compute Altman Z'-Score: missing real data for "
            f"{missing_concepts}. Refusing to substitute an assumed or "
            f"fabricated value for a financial-risk calculation."
        )


def compute_altman_z_prime(snapshot: FinancialSnapshot) -> ZScoreResult:
    required = {
        "total_assets": snapshot.total_assets,
        "total_liabilities": snapshot.total_liabilities,
        "current_assets": snapshot.current_assets,
        "current_liabilities": snapshot.current_liabilities,
        "retained_earnings": snapshot.retained_earnings,
        "operating_income": snapshot.operating_income,
        "revenues": snapshot.revenues,
    }
    missing = [name for name, fact in required.items() if fact is None]
    if missing:
        raise InsufficientDataError(missing)

    total_assets = required["total_assets"].value
    total_liabilities = required["total_liabilities"].value
    current_assets = required["current_assets"].value
    current_liabilities = required["current_liabilities"].value
    retained_earnings = required["retained_earnings"].value
    operating_income = required["operating_income"].value
    revenues = required["revenues"].value

    if total_assets == 0:
        raise ZeroDivisionError(
            "total_assets is 0 — cannot compute Z'-Score ratios (all five "
            "components are normalized by total assets)."
        )

    # Prefer the directly-reported book value of equity when SEC has a
    # clean, non-dimensional StockholdersEquity fact; otherwise derive it
    # from the accounting identity Assets = Liabilities + Equity. The two
    # are mathematically equivalent when both source figures come from the
    # same balance sheet date, so deriving it is not an approximation — it
    # is the same number, computed from inputs we already have and trust.
    if snapshot.stockholders_equity is not None:
        book_equity = snapshot.stockholders_equity.value
        equity_source = ["stockholders_equity"]
    else:
        book_equity = total_assets - total_liabilities
        equity_source = ["total_assets", "total_liabilities"]

    working_capital = current_assets - current_liabilities

    x1 = working_capital / total_assets
    x2 = retained_earnings / total_assets
    x3 = operating_income / total_assets
    x4 = book_equity / total_liabilities if total_liabilities != 0 else float("inf")
    x5 = revenues / total_assets

    z = 0.717 * x1 + 0.847 * x2 + 3.107 * x3 + 0.420 * x4 + 0.998 * x5

    if z > 2.90:
        zone = RiskZone.SAFE
    elif z >= 1.23:
        zone = RiskZone.GREY
    else:
        zone = RiskZone.DISTRESS

    components = [
        ZScoreComponent(
            name="X1_working_capital_to_assets",
            formula="(current_assets - current_liabilities) / total_assets",
            value=x1,
            source_concepts=["current_assets", "current_liabilities", "total_assets"],
        ),
        ZScoreComponent(
            name="X2_retained_earnings_to_assets",
            formula="retained_earnings / total_assets",
            value=x2,
            source_concepts=["retained_earnings", "total_assets"],
        ),
        ZScoreComponent(
            name="X3_ebit_to_assets",
            formula="operating_income / total_assets  (EBIT proxy)",
            value=x3,
            source_concepts=["operating_income", "total_assets"],
        ),
        ZScoreComponent(
            name="X4_book_equity_to_liabilities",
            formula="book_value_of_equity / total_liabilities",
            value=x4,
            source_concepts=equity_source + ["total_liabilities"],
        ),
        ZScoreComponent(
            name="X5_sales_to_assets",
            formula="revenues / total_assets",
            value=x5,
            source_concepts=["revenues", "total_assets"],
        ),
    ]

    return ZScoreResult(
        cik=snapshot.cik,
        entity_name=snapshot.entity_name,
        fiscal_year=snapshot.fiscal_year,
        z_score=round(z, 4),
        zone=zone,
        components=components,
        is_complete=True,
    )
