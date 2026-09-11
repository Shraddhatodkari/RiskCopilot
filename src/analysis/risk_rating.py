"""
Deterministic overall risk-tier classifier (ADR-013).

This exists to answer the platform's own Section-2 requirement: a single,
documented, code-visible "how did we get from these numbers to that risk
color" rule — never an LLM's judgment call. The LLM narrative layer
(src/agentic/) is only ever handed the tier this module already computed;
it never invents or overrides one (see src/agentic/narrative.py's prompt
construction, unchanged by this module).

Deliberately takes plain primitives (a zone string, an F-Score int, a
current-ratio float) rather than full `ZScoreResult`/`PiotroskiResult`/
`FinancialRatioSet` objects: the dashboard's `CompanyDossier`
(src/reporting/company_dossier.py) stores already-computed results as
plain dicts read back from SQLite (see `get_latest_altman`/
`get_latest_piotroski` in src/persistence/storage.py), not as those
richer pydantic objects, so this keeps the classifier usable directly
against what the dossier actually has without reconstructing objects
just to satisfy a type.

The rule table below is intentionally simple and stated in full, not
hidden behind a learned model or a black-box score: it combines the two
already-validated distress models (Altman Z'-Score zone, Piotroski
F-Score bucket) with one additional real-time liquidity check (current
ratio < 1.0, i.e. current liabilities exceed current assets — a
textbook near-term liquidity warning sign independent of either model),
and takes the WORSE of the two model-implied tiers plus that liquidity
flag. "Worse" is a total order fixed below, not a weighted score,
precisely so the rule stays auditable at a glance.

Missing an input never fabricates a tier: if BOTH Altman and Piotroski
are unavailable, the result is `RiskTier.INSUFFICIENT_DATA`, not a
guess. If only one is available, the tier is based on that one alone
(stated explicitly in `RiskRating.basis`).
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class RiskTier(str, Enum):
    LOW = "low"                 # 🟢
    MODERATE = "moderate"       # 🟡
    ELEVATED = "elevated"       # 🟠
    HIGH = "high"                # 🔴
    INSUFFICIENT_DATA = "insufficient_data"


_TIER_ORDER = {
    RiskTier.LOW: 0,
    RiskTier.MODERATE: 1,
    RiskTier.ELEVATED: 2,
    RiskTier.HIGH: 3,
}

# Fixed, documented mapping — Altman zone (as its plain string value:
# "safe" | "grey" | "distress") -> implied tier.
_ALTMAN_TIER = {
    "safe": RiskTier.LOW,
    "grey": RiskTier.MODERATE,
    "distress": RiskTier.HIGH,
}


def _piotroski_bucket(f_score: int) -> str:
    """Same cutoffs as PiotroskiResult.interpretation
    (src/analysis/piotroski.py) — duplicated here as a pure function of the
    int score (rather than importing the full model) since that's all the
    dossier's dict-shaped `latest_piotroski["f_score"]` gives us."""
    if f_score >= 8:
        return "strong"
    if f_score <= 1:
        return "weak"
    return "moderate"


# Fixed, documented mapping — Piotroski bucket -> implied tier.
_PIOTROSKI_TIER = {
    "strong": RiskTier.LOW,
    "moderate": RiskTier.MODERATE,
    "weak": RiskTier.HIGH,
}


class RiskRating(BaseModel):
    tier: RiskTier
    basis: str  # plain-language statement of exactly which inputs/rules produced this tier
    liquidity_flag: bool  # True if current_ratio < 1.0 pushed the tier up a level


def _worse(a: RiskTier, b: RiskTier) -> RiskTier:
    return a if _TIER_ORDER[a] >= _TIER_ORDER[b] else b


def _bump(tier: RiskTier) -> RiskTier:
    """One documented step worse, capped at HIGH."""
    order = [RiskTier.LOW, RiskTier.MODERATE, RiskTier.ELEVATED, RiskTier.HIGH]
    idx = order.index(tier)
    return order[min(idx + 1, len(order) - 1)]


def classify_risk_tier(
    altman_zone: str | None,
    piotroski_f_score: int | None,
    current_ratio: float | None = None,
) -> RiskRating:
    tiers: list[str] = []
    worst: RiskTier | None = None

    if altman_zone is not None:
        t = _ALTMAN_TIER[altman_zone]
        tiers.append(f"Altman Z' zone '{altman_zone}' -> {t.value}")
        worst = t if worst is None else _worse(worst, t)

    if piotroski_f_score is not None:
        bucket = _piotroski_bucket(piotroski_f_score)
        t = _PIOTROSKI_TIER[bucket]
        tiers.append(f"Piotroski F-Score {piotroski_f_score}/9 ('{bucket}') -> {t.value}")
        worst = t if worst is None else _worse(worst, t)

    if worst is None:
        return RiskRating(
            tier=RiskTier.INSUFFICIENT_DATA,
            basis="Neither Altman Z' nor Piotroski F-Score is computable for this "
                  "fiscal year — see Data Quality for why. No tier is assigned rather "
                  "than guessing.",
            liquidity_flag=False,
        )

    liquidity_flag = False
    if current_ratio is not None and current_ratio < 1.0:
        liquidity_flag = True
        pre_bump = worst
        worst = _bump(worst)
        tiers.append(
            f"current ratio {current_ratio:.2f} < 1.0 -> tier bumped from "
            f"{pre_bump.value} to {worst.value} (near-term liquidity warning)"
        )

    return RiskRating(tier=worst, basis="; ".join(tiers), liquidity_flag=liquidity_flag)
