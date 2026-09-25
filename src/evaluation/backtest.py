"""
Historical distress backtest (Phase 4): does the Altman Z'-Score
(src/analysis/altman_z.py) actually flag companies that are now, as a
matter of real public record, known to have filed for Chapter 11
bankruptcy — and how much lead time does it provide?

This is the project's core "does it work" evidence, per the business
case's own KPI definition (docs/00_business_case.md). It is reported
honestly, including the model's real misses, from
tests/fixtures/distress_backtest_cases.json — real XBRL figures for two
companies with real, publicly documented bankruptcy filings, cross-checked
against raw SEC data on 2026-09-08 (see that fixture's provenance note and
docs/06_historical_backtest.md for the full write-up, including a real
data-extraction error this cross-checking process caught and corrected).

This is deliberately a small, honest N=2 backtest, not a large-sample
statistical claim — the business case document is explicit that this
project makes no unfounded accuracy claim. A larger backtest (more
companies, more distress events, a control group of companies that did
NOT fail) is scoped as future work in docs/04_roadmap.md; what's here is
real and small, not fabricated and large.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from src.analysis.altman_z import RiskZone, ZScoreResult, compute_altman_z_prime
from src.ingestion.models import FactPoint, FinancialSnapshot

# The exact real XBRL tags used when this fixture data was originally
# fetched from data.sec.gov (see docs/03_data_provenance.md's methodology)
# — recorded here so a FactPoint built from a backtest record carries the
# same provenance shape as one built through the live ingestion path.
_TAG_FOR_CONCEPT = {
    "total_assets": "Assets",
    "total_liabilities": "Liabilities",
    "current_assets": "AssetsCurrent",
    "current_liabilities": "LiabilitiesCurrent",
    "retained_earnings": "RetainedEarningsAccumulatedDeficit",
    "operating_income": "OperatingIncomeLoss",
    "revenues": "RevenueFromContractWithCustomerExcludingAssessedTax",
}


class SnapshotResult(BaseModel):
    fiscal_year: int
    period_end: date
    filed: date
    z_score: float
    zone: RiskZone
    flagged_for_review: bool  # zone is grey or distress
    strong_signal: bool  # zone is distress specifically
    lead_time_days: int  # known_event_date - period_end (positive = advance warning)
    filed_before_event: bool  # was the filing itself public before the event?


class CaseResult(BaseModel):
    company_name: str
    cik: str
    known_event: str
    known_event_date: date
    snapshots: list[SnapshotResult]


class BacktestSummary(BaseModel):
    cases: list[CaseResult]
    total_snapshots: int
    flagged_count: int
    strong_signal_count: int
    missed_count: int  # zone == safe

    # Only snapshots whose 10-K was actually public BEFORE the bankruptcy
    # petition can count as a prediction: a filing that appeared after the
    # event could never have warned anyone in advance, even if the period
    # it covers is pre-petition. These fields are the honest headline
    # result; the all-snapshot counts above are supporting detail only.
    predictive_snapshots: int = 0
    predictive_flagged_count: int = 0
    predictive_missed_count: int = 0

    @property
    def flagged_rate(self) -> float:
        return self.flagged_count / self.total_snapshots if self.total_snapshots else 0.0

    @property
    def predictive_flagged_rate(self) -> float:
        if not self.predictive_snapshots:
            return 0.0
        return self.predictive_flagged_count / self.predictive_snapshots


def _build_snapshot(cik: str, entity_name: str, record: dict) -> FinancialSnapshot:
    fiscal_year = record["fiscal_year"]
    period_end = date.fromisoformat(record["period_end"])
    filed = date.fromisoformat(record["filed"])
    accession_number = record["accession_number"]

    def fact(concept: str) -> FactPoint:
        return FactPoint(
            concept=concept,
            tag_used=_TAG_FOR_CONCEPT[concept],
            value=float(record[concept]),
            unit="USD",
            period_end=period_end,
            fiscal_year=fiscal_year,
            fiscal_period="FY",
            form="10-K",
            filed=filed,
            accession_number=accession_number,
        )

    return FinancialSnapshot(
        cik=cik,
        entity_name=entity_name,
        fiscal_year=fiscal_year,
        total_assets=fact("total_assets"),
        total_liabilities=fact("total_liabilities"),
        current_assets=fact("current_assets"),
        current_liabilities=fact("current_liabilities"),
        retained_earnings=fact("retained_earnings"),
        operating_income=fact("operating_income"),
        revenues=fact("revenues"),
    )


def evaluate_case(case: dict) -> CaseResult:
    known_event_date = date.fromisoformat(case["known_event_date"])
    results: list[SnapshotResult] = []

    for record in case["snapshots"]:
        snapshot = _build_snapshot(case["cik"], case["company_name"], record)
        z_result: ZScoreResult = compute_altman_z_prime(snapshot)
        period_end = date.fromisoformat(record["period_end"])
        filed = date.fromisoformat(record["filed"])

        results.append(
            SnapshotResult(
                fiscal_year=record["fiscal_year"],
                period_end=period_end,
                filed=filed,
                z_score=z_result.z_score,
                zone=z_result.zone,
                flagged_for_review=z_result.zone != RiskZone.SAFE,
                strong_signal=z_result.zone == RiskZone.DISTRESS,
                lead_time_days=(known_event_date - period_end).days,
                filed_before_event=filed < known_event_date,
            )
        )

    return CaseResult(
        company_name=case["company_name"],
        cik=case["cik"],
        known_event=case["known_event"],
        known_event_date=known_event_date,
        snapshots=results,
    )


def run_backtest(cases: list[dict]) -> BacktestSummary:
    case_results = [evaluate_case(c) for c in cases]
    all_snapshots = [s for c in case_results for s in c.snapshots]
    predictive = [s for s in all_snapshots if s.filed_before_event]
    return BacktestSummary(
        cases=case_results,
        total_snapshots=len(all_snapshots),
        flagged_count=sum(1 for s in all_snapshots if s.flagged_for_review),
        strong_signal_count=sum(1 for s in all_snapshots if s.strong_signal),
        missed_count=sum(1 for s in all_snapshots if not s.flagged_for_review),
        predictive_snapshots=len(predictive),
        predictive_flagged_count=sum(1 for s in predictive if s.flagged_for_review),
        predictive_missed_count=sum(1 for s in predictive if not s.flagged_for_review),
    )
