"""
Runs the Phase 4 historical distress backtest against real data and prints
a human-readable report. Run: python scripts/run_backtest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.evaluation.backtest import run_backtest  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "distress_backtest_cases.json"


def main() -> None:
    data = json.loads(FIXTURE_PATH.read_text())
    summary = run_backtest(data["cases"])

    print("=== Historical Distress Backtest (Altman Z'-Score) ===")
    print(f"Source: {FIXTURE_PATH.name} (real SEC XBRL data; see docs/06_historical_backtest.md)")
    print()

    for case in summary.cases:
        print(f"{case.company_name} (CIK {case.cik})")
        print(f"  Known event: {case.known_event} on {case.known_event_date}")
        for s in case.snapshots:
            filed_note = "filed BEFORE the event" if s.filed_before_event else "filed AFTER the event"
            print(
                f"  FY{s.fiscal_year} (period end {s.period_end}, {filed_note} on {s.filed}): "
                f"Z'={s.z_score:.4f} -> {s.zone.value.upper()} | "
                f"lead time vs. event: {s.lead_time_days} days | "
                f"{'FLAGGED' if s.flagged_for_review else 'MISSED'}"
            )
        print()

    print("--- Summary ---")
    print(f"Total pre-event snapshots evaluated: {summary.total_snapshots}")
    print(f"Flagged (grey or distress zone):     {summary.flagged_count} ({summary.flagged_rate:.0%})")
    print(f"  of which distress-zone (strong):   {summary.strong_signal_count}")
    print(f"Missed (safe zone):                  {summary.missed_count}")
    print()
    print(
        "This is an honest, small (N=2 companies, 4 snapshots) real backtest — "
        "not a statistically powered accuracy claim. See docs/06_historical_backtest.md "
        "for the full discussion, including the one real miss."
    )


if __name__ == "__main__":
    main()
