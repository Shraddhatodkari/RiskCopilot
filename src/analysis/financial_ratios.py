"""
Deterministic financial-ratio engine (ADR-013) — extends the Phase 1/2
Altman Z'/Piotroski F-Score models with the standard liquidity, leverage,
profitability, and cash-flow ratios a credit/investment analyst expects to
see, computed from the exact same real, sourced `FinancialSnapshot` those
models already use. Same rule as ADR-005 throughout this project: every
number here is either a value taken verbatim from a cited SEC filing, or a
plain arithmetic function of such values — an LLM never computes, adjusts,
or "double checks" any of it.

Each ratio is returned as a `RatioValue`: the formula (as a readable
string, not just a number), the source concept names it was built from,
and — when the required real inputs are not available — an explicit
`RatioStatus.INSUFFICIENT_DATA` with a plain-language reason, never a
fabricated, estimated, or zero-substituted value. This is the same
refuse-rather-than-guess contract `InsufficientDataError` already
enforces for Altman/Piotroski, applied metric-by-metric here instead of
all-or-nothing, since a company can legitimately have (for example) a
computable current ratio and simultaneously an uncomputable interest
coverage ratio (see `tests/unit/test_financial_ratios.py`'s real Apple/
Microsoft cases, where interest coverage is genuinely uncomputable for
both companies because neither's most recent 10-K tags a usable
InterestExpense/InterestExpenseDebt value at all).
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from src.ingestion.models import FinancialSnapshot


class RatioStatus(str, Enum):
    AVAILABLE = "available"
    INSUFFICIENT_DATA = "insufficient_data"
    NOT_APPLICABLE = "not_applicable"  # e.g. division by a zero denominator


class RatioValue(BaseModel):
    name: str
    category: str  # "liquidity" | "leverage" | "profitability" | "cash_flow"
    formula: str
    value: float | None
    status: RatioStatus
    detail: str | None = None  # populated when status != AVAILABLE
    source_concepts: list[str] = []


class FinancialRatioSet(BaseModel):
    cik: str
    entity_name: str
    fiscal_year: int
    ratios: list[RatioValue]

    def get(self, name: str) -> RatioValue | None:
        return next((r for r in self.ratios if r.name == name), None)

    @property
    def available_count(self) -> int:
        return sum(1 for r in self.ratios if r.status == RatioStatus.AVAILABLE)


def _ratio(
    name: str,
    category: str,
    formula: str,
    source_concepts: list[str],
    snapshot: FinancialSnapshot,
    compute,
) -> RatioValue:
    """Shared helper: look up every source concept on the snapshot, and
    either compute the ratio (if every input is a real, present fact) or
    return an explicit, reasoned INSUFFICIENT_DATA result. `compute` is
    called with the plain float values, in `source_concepts` order."""
    facts = snapshot.all_facts()
    missing = [c for c in source_concepts if facts.get(c) is None]
    if missing:
        return RatioValue(
            name=name,
            category=category,
            formula=formula,
            value=None,
            status=RatioStatus.INSUFFICIENT_DATA,
            detail=(
                f"Cannot compute {name}: {snapshot.entity_name}'s FY{snapshot.fiscal_year} "
                f"filing has no real value for {missing} (see Data Quality). "
                f"Refusing to substitute an assumed or fabricated value."
            ),
            source_concepts=source_concepts,
        )
    values = [facts[c].value for c in source_concepts]
    try:
        result = compute(*values)
    except ZeroDivisionError:
        return RatioValue(
            name=name,
            category=category,
            formula=formula,
            value=None,
            status=RatioStatus.NOT_APPLICABLE,
            detail=f"{name} is not applicable: its denominator is 0 in this filing.",
            source_concepts=source_concepts,
        )
    return RatioValue(
        name=name,
        category=category,
        formula=formula,
        value=round(result, 4),
        status=RatioStatus.AVAILABLE,
        source_concepts=source_concepts,
    )


def compute_financial_ratios(snapshot: FinancialSnapshot) -> FinancialRatioSet:
    """Compute every ratio this engine knows, independently per-ratio —
    one metric's missing input never blocks another metric that doesn't
    need it (unlike Altman/Piotroski, which are each all-or-nothing)."""
    ratios = [
        # ---- Liquidity ----
        _ratio(
            "current_ratio", "liquidity",
            "current_assets / current_liabilities",
            ["current_assets", "current_liabilities"],
            snapshot, lambda ca, cl: ca / cl,
        ),
        _ratio(
            "quick_ratio", "liquidity",
            "(current_assets - inventory) / current_liabilities",
            ["current_assets", "inventory", "current_liabilities"],
            snapshot, lambda ca, inv, cl: (ca - inv) / cl,
        ),
        # ---- Leverage ----
        _ratio(
            "debt_to_equity", "leverage",
            "long_term_debt / stockholders_equity",
            ["long_term_debt", "stockholders_equity"],
            snapshot, lambda d, e: d / e,
        ),
        _ratio(
            "debt_to_assets", "leverage",
            "long_term_debt / total_assets",
            ["long_term_debt", "total_assets"],
            snapshot, lambda d, a: d / a,
        ),
        _ratio(
            "interest_coverage", "leverage",
            "operating_income / interest_expense",
            ["operating_income", "interest_expense"],
            snapshot, lambda ebit, ie: ebit / ie,
        ),
        # ---- Profitability ----
        _ratio(
            "return_on_assets", "profitability",
            "net_income / total_assets",
            ["net_income", "total_assets"],
            snapshot, lambda ni, a: ni / a,
        ),
        _ratio(
            "return_on_equity", "profitability",
            "net_income / stockholders_equity",
            ["net_income", "stockholders_equity"],
            snapshot, lambda ni, e: ni / e,
        ),
        _ratio(
            "operating_margin", "profitability",
            "operating_income / revenues",
            ["operating_income", "revenues"],
            snapshot, lambda oi, rev: oi / rev,
        ),
        _ratio(
            "net_margin", "profitability",
            "net_income / revenues",
            ["net_income", "revenues"],
            snapshot, lambda ni, rev: ni / rev,
        ),
        # ---- Cash flow ----
        _ratio(
            "operating_cash_flow", "cash_flow",
            "cash_flow_from_operations (as reported, not a ratio)",
            ["cash_flow_from_operations"],
            snapshot, lambda ocf: ocf,
        ),
        _ratio(
            "free_cash_flow", "cash_flow",
            "cash_flow_from_operations - capital_expenditures",
            ["cash_flow_from_operations", "capital_expenditures"],
            snapshot, lambda ocf, capex: ocf - capex,
        ),
    ]
    return FinancialRatioSet(
        cik=snapshot.cik,
        entity_name=snapshot.entity_name,
        fiscal_year=snapshot.fiscal_year,
        ratios=ratios,
    )
