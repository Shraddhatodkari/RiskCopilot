"""Typed data structures shared by ingestion and analysis.

Using pydantic (rather than plain dicts) is a deliberate architecture
decision (ADR-004): every number that flows into a financial calculation
carries its own provenance (which XBRL tag, which filing, which accession
number, which URL) end-to-end, so a risk score can always be traced back to
a specific real SEC filing rather than an opaque number.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class FactPoint(BaseModel):
    """One real, sourced XBRL data point."""

    concept: str = Field(..., description="Canonical concept name, e.g. 'total_assets'")
    tag_used: str = Field(..., description="Actual XBRL tag that supplied the value, e.g. 'Assets'")
    taxonomy: str = "us-gaap"
    value: float
    unit: str
    period_start: date | None = None
    period_end: date
    fiscal_year: int
    fiscal_period: str
    form: str
    filed: date
    accession_number: str

    def source_url(self, cik: str) -> str:
        """A human-followable link to the actual filing index on sec.gov.

        `cik` is required (not stored on the FactPoint itself) because the
        SEC's filing-index URL scheme embeds the *unpadded* numeric CIK,
        which the caller (the parent FinancialSnapshot) already knows.
        """
        cik_unpadded = str(int(cik))
        accn_no_dashes = self.accession_number.replace("-", "")
        return (
            f"https://www.sec.gov/Archives/edgar/data/{cik_unpadded}/"
            f"{accn_no_dashes}/{self.accession_number}-index.htm"
        )


class DataQualityIssue(BaseModel):
    concept: str
    severity: str  # "missing" | "stale" | "ambiguous" | "invalidated" (see
    # src/persistence/storage.py's invalidate_altman_result/
    # invalidate_piotroski_result, ADR-013 — "invalidated" marks a
    # previously-valid derived score that could not be reproduced on a
    # later recomputation attempt, distinct from a concept that was never
    # computable at all)
    detail: str


class FinancialSnapshot(BaseModel):
    """All inputs required for the Phase-1 deterministic risk models, for
    one company, for one fiscal year — each field either a sourced
    FactPoint or, honestly, absent.
    """

    cik: str
    entity_name: str
    fiscal_year: int

    total_assets: FactPoint | None = None
    total_liabilities: FactPoint | None = None
    current_assets: FactPoint | None = None
    current_liabilities: FactPoint | None = None
    retained_earnings: FactPoint | None = None
    operating_income: FactPoint | None = None
    revenues: FactPoint | None = None
    stockholders_equity: FactPoint | None = None

    # Added in Phase 2 for the Piotroski F-Score (src/analysis/piotroski.py).
    # Kept on the same FinancialSnapshot model (rather than a parallel type)
    # so both models share one ingestion/fallback/data-quality code path.
    net_income: FactPoint | None = None
    cash_flow_from_operations: FactPoint | None = None
    long_term_debt: FactPoint | None = None
    shares_outstanding: FactPoint | None = None
    cost_of_goods_sold: FactPoint | None = None

    # Added for the expanded deterministic ratio engine
    # (src/analysis/financial_ratios.py): liquidity (quick ratio), leverage
    # (interest coverage), and cash-flow (free cash flow) metrics need these
    # three additional real XBRL concepts beyond what Altman/Piotroski use.
    # Same honesty rule as every other field: None, with a recorded
    # DataQualityIssue, when the filing genuinely doesn't tag it — never
    # estimated.
    inventory: FactPoint | None = None
    interest_expense: FactPoint | None = None
    capital_expenditures: FactPoint | None = None

    data_quality_issues: list[DataQualityIssue] = Field(default_factory=list)

    def all_facts(self) -> dict[str, FactPoint | None]:
        return {
            "total_assets": self.total_assets,
            "total_liabilities": self.total_liabilities,
            "current_assets": self.current_assets,
            "current_liabilities": self.current_liabilities,
            "retained_earnings": self.retained_earnings,
            "operating_income": self.operating_income,
            "revenues": self.revenues,
            "stockholders_equity": self.stockholders_equity,
            "net_income": self.net_income,
            "cash_flow_from_operations": self.cash_flow_from_operations,
            "long_term_debt": self.long_term_debt,
            "shares_outstanding": self.shares_outstanding,
            "cost_of_goods_sold": self.cost_of_goods_sold,
            "inventory": self.inventory,
            "interest_expense": self.interest_expense,
            "capital_expenditures": self.capital_expenditures,
        }
