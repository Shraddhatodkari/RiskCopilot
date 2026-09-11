"""
Extracts a `FinancialSnapshot` for one company/fiscal-year from SEC EDGAR's
XBRL "company concept" API.

Key real-world data-quality problem this module exists to handle (discovered
during Phase-1 development against live Apple Inc. data — see
docs/03_data_provenance.md for the worked example): companies do not use a
single, stable XBRR tag for a given accounting concept across years, and the
`companyconcept` endpoint only returns *non-dimensional* facts — a concept
reported only inside a dimensional breakdown (e.g. a statement-of-equity
rollforward) will not appear at all, even though the company clearly
discloses it. Silently treating a missing/renamed tag as "zero" would
corrupt every downstream financial ratio. This module therefore:

  1. Tries an ordered list of known-equivalent tags per concept.
  2. If none resolve, records an explicit DataQualityIssue and leaves the
     field as None rather than guessing.
  3. Never fabricates a number.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from src.ingestion.models import DataQualityIssue, FactPoint, FinancialSnapshot
from src.ingestion.sec_edgar_client import SecEdgarClient, SecEdgarNotFound

logger = logging.getLogger(__name__)

# How many days a "duration" fact must span to count as a full fiscal year
# (guards against accidentally picking up a quarterly figure). Fiscal years
# are 350-380 days to allow for 52/53-week fiscal calendars (e.g. Apple's
# fiscal year end floats between late September and early October).
_MIN_ANNUAL_DAYS = 350
_MAX_ANNUAL_DAYS = 380


@dataclass(frozen=True)
class ConceptSpec:
    canonical_name: str
    fact_type: str  # "instant" or "duration"
    tag_candidates: tuple[str, ...]
    taxonomy: str = "us-gaap"
    unit: str = "USD"  # the key under the XBRL response's "units" object to
    # read from — SEC reports monetary facts under "USD" but share counts
    # under "shares" (a real distinction that a hard-coded "USD" lookup
    # would silently miss, returning "no data" for every share-count
    # concept even when the filing has it).


# Ordered by which tag is most standard/common first. Fallbacks cover known
# real-world renamings and company-specific tagging choices.
CONCEPT_SPECS: tuple[ConceptSpec, ...] = (
    ConceptSpec("total_assets", "instant", ("Assets",)),
    ConceptSpec("total_liabilities", "instant", ("Liabilities",)),
    ConceptSpec("current_assets", "instant", ("AssetsCurrent",)),
    ConceptSpec("current_liabilities", "instant", ("LiabilitiesCurrent",)),
    ConceptSpec(
        "retained_earnings",
        "instant",
        ("RetainedEarningsAccumulatedDeficit",),
    ),
    ConceptSpec(
        "operating_income",
        "duration",
        ("OperatingIncomeLoss",),
    ),
    ConceptSpec(
        "revenues",
        "duration",
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ),
    ),
    ConceptSpec("stockholders_equity", "instant", ("StockholdersEquity",)),
    # Added in Phase 2 for the Piotroski F-Score.
    ConceptSpec("net_income", "duration", ("NetIncomeLoss",)),
    ConceptSpec(
        "cash_flow_from_operations",
        "duration",
        ("NetCashProvidedByUsedInOperatingActivities",),
    ),
    ConceptSpec(
        "long_term_debt",
        "instant",
        ("LongTermDebtNoncurrent", "LongTermDebt"),
    ),
    ConceptSpec(
        "shares_outstanding",
        "instant",
        ("CommonStockSharesOutstanding",),
        unit="shares",
    ),
    ConceptSpec(
        "cost_of_goods_sold",
        "duration",
        ("CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"),
    ),
    # Added for the expanded deterministic ratio engine
    # (src/analysis/financial_ratios.py, ADR-013).
    ConceptSpec("inventory", "instant", ("InventoryNet",)),
    ConceptSpec(
        "interest_expense",
        "duration",
        ("InterestExpense", "InterestExpenseDebt"),
    ),
    ConceptSpec(
        "capital_expenditures",
        "duration",
        (
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsForCapitalImprovements",
        ),
    ),
)


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _extract_annual_fact(
    concept_json: dict, spec: ConceptSpec, tag_used: str, fiscal_year: int
) -> FactPoint | None:
    """Pick the best matching annual, audited (10-K) data point for one
    fiscal year out of a raw companyconcept payload."""
    units = concept_json.get("units", {})
    unit_entries = units.get(spec.unit, [])

    candidates: list[dict] = []
    for entry in unit_entries:
        if entry.get("form") != "10-K":
            continue
        if entry.get("fy") != fiscal_year:
            continue
        end = _parse_date(entry["end"])

        if spec.fact_type == "duration":
            if "start" not in entry:
                continue
            start = _parse_date(entry["start"])
            span_days = (end - start).days
            if not (_MIN_ANNUAL_DAYS <= span_days <= _MAX_ANNUAL_DAYS):
                continue
        else:
            if entry.get("fp") != "FY":
                continue

        candidates.append(entry)

    if not candidates:
        return None

    # If multiple 10-K filings tag the same fiscal year (e.g. an original
    # 10-K plus a later 10-K/A amendment), prefer the most recently filed —
    # it supersedes the original. If two candidates also tie on filed date
    # (seen in practice: a filing can carry more than one fact for the same
    # fy/form/fp, e.g. a duplicate tag), break the tie by the latest period
    # end date, since a later end date is the one actually representing the
    # target fiscal year's period rather than an adjacent comparative.
    best = max(candidates, key=lambda e: (e["filed"], e["end"]))

    return FactPoint(
        concept=spec.canonical_name,
        tag_used=tag_used,
        taxonomy=spec.taxonomy,
        value=float(best["val"]),
        unit=spec.unit,
        period_start=_parse_date(best["start"]) if "start" in best else None,
        period_end=_parse_date(best["end"]),
        fiscal_year=best["fy"],
        fiscal_period=best["fp"],
        form=best["form"],
        filed=_parse_date(best["filed"]),
        accession_number=best["accn"],
    )


def build_financial_snapshot(
    client: SecEdgarClient, cik: str, entity_name: str, fiscal_year: int
) -> FinancialSnapshot:
    """Fetch and assemble one fiscal year's worth of real, sourced XBRL
    facts for a company. Never raises on missing individual concepts —
    those are recorded as DataQualityIssue entries instead, so the caller
    can decide whether the resulting snapshot is complete enough to score.
    """
    snapshot = FinancialSnapshot(cik=cik, entity_name=entity_name, fiscal_year=fiscal_year)
    values: dict[str, FactPoint] = {}
    issues: list[DataQualityIssue] = []

    for spec in CONCEPT_SPECS:
        resolved: FactPoint | None = None
        tried: list[str] = []
        for tag in spec.tag_candidates:
            tried.append(tag)
            try:
                concept_json = client.get_company_concept(cik, spec.taxonomy, tag)
            except SecEdgarNotFound:
                continue
            fact = _extract_annual_fact(concept_json, spec, tag, fiscal_year)
            if fact is not None:
                resolved = fact
                break

        if resolved is None:
            detail = (
                f"No value found for fiscal_year={fiscal_year} after trying "
                f"tag(s) {tried}. Either the company does not report this "
                f"concept, uses a non-standard tag not yet in our fallback "
                f"list, or the value is only reported inside a dimensional "
                f"breakdown that the companyconcept API omits."
            )
            logger.warning("Data quality issue for %s/%s: %s", cik, spec.canonical_name, detail)
            issues.append(
                DataQualityIssue(concept=spec.canonical_name, severity="missing", detail=detail)
            )
        else:
            values[spec.canonical_name] = resolved

    snapshot.total_assets = values.get("total_assets")
    snapshot.total_liabilities = values.get("total_liabilities")
    snapshot.current_assets = values.get("current_assets")
    snapshot.current_liabilities = values.get("current_liabilities")
    snapshot.retained_earnings = values.get("retained_earnings")
    snapshot.operating_income = values.get("operating_income")
    snapshot.revenues = values.get("revenues")
    snapshot.stockholders_equity = values.get("stockholders_equity")
    snapshot.net_income = values.get("net_income")
    snapshot.cash_flow_from_operations = values.get("cash_flow_from_operations")
    snapshot.long_term_debt = values.get("long_term_debt")
    snapshot.shares_outstanding = values.get("shares_outstanding")
    snapshot.cost_of_goods_sold = values.get("cost_of_goods_sold")
    snapshot.inventory = values.get("inventory")
    snapshot.interest_expense = values.get("interest_expense")
    snapshot.capital_expenditures = values.get("capital_expenditures")
    snapshot.data_quality_issues = issues
    return snapshot
