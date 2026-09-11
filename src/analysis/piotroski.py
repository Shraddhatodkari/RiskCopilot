"""
Piotroski F-Score: a 9-signal, binary (0/1 per signal) composite measure of
a company's financial strength trend, computed deterministically from two
consecutive fiscal years of real XBRL data.

Source: Piotroski, J.D. (2000), "Value Investing: The Use of Historical
Financial Statement Information to Separate Winners from Losers," Journal
of Accounting Research. The nine signals (profitability: ROA, CFO, ΔROA,
Accruals; leverage/liquidity: ΔLeverage, ΔLiquidity, no new shares;
operating efficiency: ΔGross Margin, ΔAsset Turnover) are Piotroski's
original definitions.

Documented simplification (stated plainly, not hidden): Piotroski's
original paper computes ROA using *beginning-of-year* total assets. This
implementation uses *end-of-year* total assets for both the numerator
year and the comparison year — consistent with how this project's Altman
Z'-Score (src/analysis/altman_z.py) already treats total assets, and
avoiding a dependency on a third year of data purely for this one ratio's
denominator. This changes ROA's precise magnitude slightly but not its
sign in the overwhelming majority of real cases (assets rarely swing
enough within a year to flip the sign of net income divided by assets),
and does not affect the direction-of-change signals (Δ-prefixed) at all,
since the same convention is applied consistently to both years being
compared.

Like the Altman Z'-Score, this refuses to compute a score when required
inputs are missing rather than guessing — see InsufficientDataError.
"""
from __future__ import annotations

from pydantic import BaseModel

from src.ingestion.models import FinancialSnapshot

REQUIRED_FIELDS = (
    "total_assets",
    "current_assets",
    "current_liabilities",
    "net_income",
    "cash_flow_from_operations",
    "long_term_debt",
    "shares_outstanding",
    "revenues",
    "cost_of_goods_sold",
)


class InsufficientDataError(RuntimeError):
    def __init__(self, fiscal_year: int, missing_concepts: list[str]):
        self.fiscal_year = fiscal_year
        self.missing_concepts = missing_concepts
        super().__init__(
            f"Cannot compute Piotroski F-Score: fiscal year {fiscal_year} is "
            f"missing real data for {missing_concepts}. Refusing to "
            f"substitute an assumed or fabricated value."
        )


class PiotroskiSignal(BaseModel):
    name: str
    description: str
    passed: bool
    current_value: float
    prior_value: float


class PiotroskiResult(BaseModel):
    cik: str
    entity_name: str
    fiscal_year: int
    prior_fiscal_year: int
    f_score: int  # 0-9
    signals: list[PiotroskiSignal]

    @property
    def interpretation(self) -> str:
        # Cutoffs per common practitioner usage of Piotroski's score
        # (Piotroski's own paper focused on the extremes, 8-9 = strong,
        # 0-1 = weak, when screening within the high book-to-market decile).
        if self.f_score >= 8:
            return "strong"
        if self.f_score <= 1:
            return "weak"
        return "moderate"


def _require(snapshot: FinancialSnapshot, fiscal_year: int) -> dict[str, float]:
    facts = snapshot.all_facts()
    missing = [name for name in REQUIRED_FIELDS if facts.get(name) is None]
    if missing:
        raise InsufficientDataError(fiscal_year, missing)
    return {name: facts[name].value for name in REQUIRED_FIELDS}


def compute_piotroski_f_score(
    current: FinancialSnapshot, prior: FinancialSnapshot
) -> PiotroskiResult:
    if current.cik != prior.cik:
        raise ValueError("current and prior snapshots must be for the same company")
    if prior.fiscal_year != current.fiscal_year - 1:
        raise ValueError(
            f"prior snapshot must be exactly one fiscal year before current "
            f"(got current={current.fiscal_year}, prior={prior.fiscal_year})"
        )

    c = _require(current, current.fiscal_year)
    p = _require(prior, prior.fiscal_year)

    def roa(y: dict[str, float]) -> float:
        return y["net_income"] / y["total_assets"]

    def leverage(y: dict[str, float]) -> float:
        return y["long_term_debt"] / y["total_assets"]

    def current_ratio(y: dict[str, float]) -> float:
        return y["current_assets"] / y["current_liabilities"]

    def gross_margin(y: dict[str, float]) -> float:
        return (y["revenues"] - y["cost_of_goods_sold"]) / y["revenues"]

    def asset_turnover(y: dict[str, float]) -> float:
        return y["revenues"] / y["total_assets"]

    roa_c, roa_p = roa(c), roa(p)
    lev_c, lev_p = leverage(c), leverage(p)
    cr_c, cr_p = current_ratio(c), current_ratio(p)
    gm_c, gm_p = gross_margin(c), gross_margin(p)
    at_c, at_p = asset_turnover(c), asset_turnover(p)

    signals = [
        PiotroskiSignal(
            name="roa_positive",
            description="Net income / total assets > 0 in the current year",
            passed=roa_c > 0,
            current_value=roa_c,
            prior_value=roa_p,
        ),
        PiotroskiSignal(
            name="cfo_positive",
            description="Operating cash flow > 0 in the current year",
            passed=c["cash_flow_from_operations"] > 0,
            current_value=c["cash_flow_from_operations"],
            prior_value=p["cash_flow_from_operations"],
        ),
        PiotroskiSignal(
            name="delta_roa_positive",
            description="ROA improved vs. the prior year",
            passed=roa_c > roa_p,
            current_value=roa_c,
            prior_value=roa_p,
        ),
        PiotroskiSignal(
            name="accruals_quality",
            description="Operating cash flow exceeds net income (earnings quality)",
            passed=c["cash_flow_from_operations"] > c["net_income"],
            current_value=c["cash_flow_from_operations"],
            prior_value=c["net_income"],
        ),
        PiotroskiSignal(
            name="leverage_decreased",
            description="Long-term debt / total assets decreased vs. the prior year",
            passed=lev_c < lev_p,
            current_value=lev_c,
            prior_value=lev_p,
        ),
        PiotroskiSignal(
            name="liquidity_increased",
            description="Current ratio increased vs. the prior year",
            passed=cr_c > cr_p,
            current_value=cr_c,
            prior_value=cr_p,
        ),
        PiotroskiSignal(
            name="no_new_shares",
            description="Shares outstanding did not increase vs. the prior year",
            passed=c["shares_outstanding"] <= p["shares_outstanding"],
            current_value=c["shares_outstanding"],
            prior_value=p["shares_outstanding"],
        ),
        PiotroskiSignal(
            name="gross_margin_increased",
            description="Gross margin increased vs. the prior year",
            passed=gm_c > gm_p,
            current_value=gm_c,
            prior_value=gm_p,
        ),
        PiotroskiSignal(
            name="asset_turnover_increased",
            description="Sales / total assets increased vs. the prior year",
            passed=at_c > at_p,
            current_value=at_c,
            prior_value=at_p,
        ),
    ]

    return PiotroskiResult(
        cik=current.cik,
        entity_name=current.entity_name,
        fiscal_year=current.fiscal_year,
        prior_fiscal_year=prior.fiscal_year,
        f_score=sum(1 for s in signals if s.passed),
        signals=signals,
    )
