"""
CompanyDossier (ADR-012): a single, company-scoped bundle of everything the
dashboard needs to render one company — assembled here, in `src/`, rather
than inline in `dashboard.py`, so (a) it's unit-testable without Streamlit,
and (b) there is exactly one place that reads storage for a company, making
"did we accidentally mix up two companies' data" a question with one
answer instead of one per dashboard section.

This module computes nothing — it only reads back what
`src/analysis/altman_z.py`, `src/analysis/piotroski.py`, and
`src/ingestion/xbrl_facts.py` already computed and `src/persistence/storage.py`
already stored. No financial arithmetic lives here (ADR-005).
"""
from __future__ import annotations

import sqlite3

from pydantic import BaseModel

from src.analysis.financial_ratios import FinancialRatioSet, RatioStatus, compute_financial_ratios
from src.analysis.risk_rating import RiskRating, classify_risk_tier
from src.analysis.trend import MetricTrend, classify_trend
from src.persistence.storage import (
    build_snapshot_from_stored_facts,
    get_all_fiscal_years,
    get_altman_history,
    get_data_quality_issues,
    get_facts,
    get_filing_metadata,
    get_latest_altman,
    get_latest_known_fiscal_year,
    get_latest_piotroski,
    get_piotroski_history,
)
from src.reporting.evidence_registry import evidence_available

# Every ratio name financial_ratios.py can produce, in fixed display order —
# used to compute a trend for each one across every fiscal year this
# company has real stored facts for.
_TREND_RATIO_NAMES = (
    "current_ratio", "quick_ratio",
    "debt_to_equity", "debt_to_assets", "interest_coverage",
    "return_on_assets", "return_on_equity", "operating_margin", "net_margin",
    "operating_cash_flow", "free_cash_flow",
)


def filing_source_url(cik: str, accession_number: str) -> str:
    """Same URL scheme as FactPoint.source_url (src/ingestion/models.py) —
    duplicated as a plain function here because `get_filing_metadata`
    returns a dict (already-persisted data), not a live FactPoint object,
    but a reviewer still needs a clickable link to the actual filing."""
    cik_unpadded = str(int(cik))
    accn_no_dashes = accession_number.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik_unpadded}/"
        f"{accn_no_dashes}/{accession_number}-index.htm"
    )


class CompanyDossier(BaseModel):
    cik: str
    entity_name: str

    latest_altman: dict | None = None
    altman_history: list[dict] = []
    latest_piotroski: dict | None = None
    piotroski_history: list[dict] = []

    # The fiscal year the "current" data-quality/facts/filing sections
    # below describe — the latest fiscal year with a stored score, so the
    # dashboard shows one coherent point in time, not a mix of years.
    current_fiscal_year: int | None = None
    data_quality_issues: list[dict] = []
    source_facts: list[dict] = []
    filing_metadata: dict | None = None

    has_risk_factor_evidence: bool = False

    # Expanded deterministic ratio engine + historical trends (ADR-013).
    current_ratios: dict | None = None  # FinancialRatioSet.model_dump() for current_fiscal_year
    ratio_history: dict[int, dict] = {}  # {fiscal_year: FinancialRatioSet.model_dump()}
    metric_trends: dict[str, dict] = {}  # {metric_name: MetricTrend.model_dump()}, incl. "altman_z_score"/"piotroski_f_score"
    risk_rating: dict | None = None  # RiskRating.model_dump()

    @property
    def overall_risk_note(self) -> str:
        """A short, deterministic (not LLM-generated) plain-language
        summary of the two scores together — used by the Company Overview
        section. Intentionally simple pattern-matching on the already-
        computed zone/interpretation, not a new judgment call."""
        if self.latest_altman is None and self.latest_piotroski is None:
            return "No deterministic score could be computed for this company/year — see Data Quality."
        parts = []
        if self.latest_altman is not None:
            parts.append(
                f"Altman Z' zone (FY{self.latest_altman['fiscal_year']}): "
                f"{self.latest_altman['zone'].upper()}"
            )
        if self.latest_piotroski is not None:
            parts.append(
                f"Piotroski F-Score (FY{self.latest_piotroski['fiscal_year']}): "
                f"{self.latest_piotroski['f_score']}/9"
            )
        return " | ".join(parts)


def build_company_dossier(conn: sqlite3.Connection, cik: str, entity_name: str) -> CompanyDossier:
    """Assemble the complete, company-scoped dossier for exactly this CIK.
    Every read below is filtered by `cik` at the SQL layer (see
    src/persistence/storage.py) — there is no code path in this function
    that could pull in another company's row."""
    latest_altman = get_latest_altman(conn, cik)
    altman_history = get_altman_history(conn, cik)
    latest_piotroski = get_latest_piotroski(conn, cik)
    piotroski_history = get_piotroski_history(conn, cik)

    # Deliberately NOT derived only from latest_altman/latest_piotroski:
    # a company can have a real, honestly-ingested fiscal year with real
    # data-quality issues and NO computable score at all (e.g. a year
    # whose filing is missing retained_earnings). That year must still be
    # "current" for data-quality/facts display purposes — see
    # get_latest_known_fiscal_year's own docstring.
    current_fy = get_latest_known_fiscal_year(conn, cik)

    issues: list[dict] = []
    facts: list[dict] = []
    filing_meta: dict | None = None
    if current_fy is not None:
        issues = get_data_quality_issues(conn, cik, current_fy)
        facts = get_facts(conn, cik, current_fy)
        filing_meta = get_filing_metadata(conn, cik, current_fy)

    # ---- Expanded ratio engine + historical trends (ADR-013) ----
    # Recompute ratios for every fiscal year this company has real stored
    # facts for, from storage alone (no re-fetch) — see
    # build_snapshot_from_stored_facts's own docstring for why this is not
    # fabrication. One company's ratio history can never mix in another's:
    # every read below is filtered by this exact `cik`.
    fiscal_years = get_all_fiscal_years(conn, cik)
    ratio_history: dict[int, FinancialRatioSet] = {}
    for fy in fiscal_years:
        fy_snapshot = build_snapshot_from_stored_facts(conn, cik, entity_name, fy)
        ratio_history[fy] = compute_financial_ratios(fy_snapshot)

    current_ratios = ratio_history.get(current_fy) if current_fy is not None else None

    metric_trends: dict[str, MetricTrend] = {}
    for name in _TREND_RATIO_NAMES:
        points: list[tuple[int, float]] = []
        for fy, rset in ratio_history.items():
            r = rset.get(name)
            if r is not None and r.status == RatioStatus.AVAILABLE:
                points.append((fy, r.value))
        metric_trends[name] = classify_trend(name, points)

    altman_points = [(h["fiscal_year"], h["z_score"]) for h in altman_history]
    metric_trends["altman_z_score"] = classify_trend("altman_z_score", altman_points)
    piotroski_points = [(h["fiscal_year"], float(h["f_score"])) for h in piotroski_history]
    metric_trends["piotroski_f_score"] = classify_trend("piotroski_f_score", piotroski_points)

    current_ratio_value = None
    if current_ratios is not None:
        cr = current_ratios.get("current_ratio")
        if cr is not None and cr.status == RatioStatus.AVAILABLE:
            current_ratio_value = cr.value

    rating = classify_risk_tier(
        altman_zone=latest_altman["zone"] if latest_altman is not None else None,
        piotroski_f_score=latest_piotroski["f_score"] if latest_piotroski is not None else None,
        current_ratio=current_ratio_value,
    )

    return CompanyDossier(
        cik=cik,
        entity_name=entity_name,
        latest_altman=latest_altman,
        altman_history=altman_history,
        latest_piotroski=latest_piotroski,
        piotroski_history=piotroski_history,
        current_fiscal_year=current_fy,
        data_quality_issues=issues,
        source_facts=facts,
        filing_metadata=filing_meta,
        has_risk_factor_evidence=evidence_available(cik),
        current_ratios=current_ratios.model_dump() if current_ratios is not None else None,
        ratio_history={fy: rset.model_dump() for fy, rset in ratio_history.items()},
        metric_trends={name: t.model_dump() for name, t in metric_trends.items()},
        risk_rating=rating.model_dump(),
    )
