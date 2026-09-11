"""
Demonstration script: runs the real Phase-1 pipeline (ingestion + Altman
Z'-Score) against the frozen real-data fixtures, without needing network
access or SEC_EDGAR_USER_AGENT configured. This exists purely so the
pipeline's actual output can be inspected/captured (see
docs/sample_output_msft.txt) without requiring a live SEC EDGAR connection
— the exact same code path (src/ingestion/xbrl_facts.py,
src/analysis/altman_z.py) that `python -m src.cli` runs against live data.

Run: python scripts/demo_report.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.conftest import FixtureBackedClient  # noqa: E402

from src.analysis.altman_z import InsufficientDataError, compute_altman_z_prime  # noqa: E402
from src.ingestion.xbrl_facts import build_financial_snapshot  # noqa: E402


def run(fixture_name: str, cik: str, entity_name: str, fiscal_year: int) -> None:
    client = FixtureBackedClient(fixture_name)
    snapshot = build_financial_snapshot(client, cik, entity_name, fiscal_year)

    print(f"=== Financial Risk Memo: {entity_name} — FY{fiscal_year} ===")
    print(f"(source: {fixture_name}, a frozen capture of real data.sec.gov responses)")
    print()

    if snapshot.data_quality_issues:
        print("DATA QUALITY ISSUES (real, not simulated):")
        for issue in snapshot.data_quality_issues:
            print(f"  - [{issue.severity}] {issue.concept}: {issue.detail}")
        print()

    try:
        result = compute_altman_z_prime(snapshot)
    except InsufficientDataError as e:
        print(f"Altman Z'-Score: CANNOT BE COMPUTED — {e}")
        print()
        return

    print(f"Altman Z'-Score: {result.z_score}  ->  zone: {result.zone.value.upper()}")
    print()
    print("Components:")
    for c in result.components:
        print(f"  {c.name} = {c.formula} = {c.value:.4f}")
    print()
    print("Source facts (each traceable to a real, filed SEC document):")
    for name, fact in snapshot.all_facts().items():
        if fact is None:
            continue
        print(
            f"  - {name}: ${fact.value:,.0f} | {fact.form} filed {fact.filed} "
            f"(accession {fact.accession_number}) | {fact.source_url(cik)}"
        )
    print()


if __name__ == "__main__":
    run("msft_fy2025_companyconcept.json", "0000789019", "Microsoft Corporation", 2025)
    print("=" * 78)
    print()
    run("aapl_fy2025_companyconcept.json", "0000320193", "Apple Inc.", 2025)
