"""
Command-line entry point: fetch one company's real SEC filing data and
produce a sourced, deterministic financial-risk memo.

This is intentionally a thin CLI, not a dashboard — Phase 6 of the roadmap
(docs/04_roadmap.md) adds a Streamlit dashboard once there is more than one
model's worth of output to visualize.

Usage:
    python -m src.cli --ticker AAPL --fiscal-year 2025
    python -m src.cli --ticker MSFT --fiscal-year 2024 --with-piotroski --save
    python -m src.cli --cik 0000789019 --entity-name "Microsoft Corporation" --fiscal-year 2025
"""
from __future__ import annotations

import argparse
import sys

from src.analysis.altman_z import InsufficientDataError, compute_altman_z_prime
from src.analysis.piotroski import InsufficientDataError as PiotroskiInsufficientDataError
from src.analysis.piotroski import compute_piotroski_f_score
from src.config import ConfigError, load_settings
from src.ingestion.sec_edgar_client import SecEdgarClient, SecEdgarError
from src.ingestion.ticker_lookup import TickerLookupError, resolve_ticker
from src.ingestion.xbrl_facts import build_financial_snapshot
from src.persistence.storage import (
    invalidate_altman_result,
    invalidate_piotroski_result,
    open_db,
    save_altman_result,
    save_piotroski_result,
    save_snapshot,
)


def _money(value: float) -> str:
    return f"${value:,.0f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ticker", help="Any US-listed ticker symbol (resolved via SEC's company_tickers.json)")
    group.add_argument("--cik", help="SEC CIK number (with or without leading zeros)")
    parser.add_argument("--entity-name", help="Required when using --cik", default=None)
    parser.add_argument("--fiscal-year", type=int, required=True)
    parser.add_argument(
        "--with-piotroski",
        action="store_true",
        help="Also compute the Piotroski F-Score (requires prior fiscal year's data too)",
    )
    parser.add_argument(
        "--save", action="store_true", help="Persist results to the local SQLite database"
    )
    parser.add_argument("--db-path", default="data/riskcopilot.db")
    args = parser.parse_args(argv)

    if args.cik and not args.entity_name:
        parser.error("--entity-name is required when using --cik")

    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"CONFIGURATION ERROR: {e}", file=sys.stderr)
        return 2

    if args.ticker:
        try:
            cik, entity_name = resolve_ticker(args.ticker, settings.sec_edgar_user_agent)
        except TickerLookupError as e:
            print(f"TICKER LOOKUP ERROR: {e}", file=sys.stderr)
            return 1
    else:
        cik, entity_name = args.cik, args.entity_name

    client = SecEdgarClient(settings)

    print(f"Fetching real SEC EDGAR XBRL data for {entity_name} (CIK {cik}), FY{args.fiscal_year}...")
    try:
        snapshot = build_financial_snapshot(client, cik, entity_name, args.fiscal_year)
    except SecEdgarError as e:
        print(f"DATA FETCH ERROR: {e}", file=sys.stderr)
        return 1

    print()
    print(f"=== Financial Risk Memo: {entity_name} — FY{args.fiscal_year} ===")
    print()

    if snapshot.data_quality_issues:
        print("DATA QUALITY ISSUES (real, not simulated):")
        for issue in snapshot.data_quality_issues:
            print(f"  - [{issue.severity}] {issue.concept}: {issue.detail}")
        print()

    exit_code = 0
    altman_result = None
    altman_error: str | None = None
    try:
        altman_result = compute_altman_z_prime(snapshot)
        print(f"Altman Z'-Score: {altman_result.z_score}  ->  zone: {altman_result.zone.value.upper()}")
        print("Components:")
        for c in altman_result.components:
            print(f"  {c.name} = {c.formula} = {c.value:.4f}")
    except InsufficientDataError as e:
        altman_error = str(e)
        print(f"Altman Z'-Score: CANNOT BE COMPUTED — {e}")
        exit_code = 1
    print()

    piotroski_result = None
    piotroski_error: str | None = None
    prior_snapshot = None
    if args.with_piotroski:
        print(f"Fetching FY{args.fiscal_year - 1} data for the Piotroski F-Score comparison...")
        try:
            prior_snapshot = build_financial_snapshot(
                client, cik, entity_name, args.fiscal_year - 1
            )
            piotroski_result = compute_piotroski_f_score(snapshot, prior_snapshot)
            print(
                f"Piotroski F-Score: {piotroski_result.f_score}/9 "
                f"({piotroski_result.interpretation})"
            )
            for s in piotroski_result.signals:
                mark = "PASS" if s.passed else "fail"
                print(f"  [{mark}] {s.name}: {s.description}")
        except (SecEdgarError, PiotroskiInsufficientDataError) as e:
            piotroski_error = str(e)
            print(f"Piotroski F-Score: CANNOT BE COMPUTED — {e}")
            exit_code = 1
        print()

    print("Source facts (each traceable to a real, filed SEC document):")
    for name, fact in snapshot.all_facts().items():
        if fact is None:
            continue
        print(
            f"  - {name}: {_money(fact.value)} | {fact.form} filed {fact.filed} "
            f"(accession {fact.accession_number}) | {fact.source_url(cik)}"
        )

    if args.save:
        # Derived-data lifecycle (ADR-013): a genuine recomputation failure
        # for this exact (cik, fiscal_year) invalidates any previously
        # stored score for it, rather than leaving a stale score from an
        # earlier, more-complete run sitting in the database as if it were
        # still current. See src/persistence/storage.py's
        # invalidate_altman_result/invalidate_piotroski_result docstrings.
        with open_db(args.db_path) as conn:
            save_snapshot(conn, snapshot)
            if prior_snapshot is not None:
                # Real prior-year facts already fetched above for the
                # Piotroski comparison — persist them too so this
                # company's stored history actually has >=2 real fiscal
                # years, instead of discarding a year of real SEC data
                # this same run already fetched.
                save_snapshot(conn, prior_snapshot)
            if altman_result is not None:
                save_altman_result(conn, altman_result)
            elif altman_error is not None:
                invalidate_altman_result(conn, cik, args.fiscal_year, altman_error)
            if piotroski_result is not None:
                save_piotroski_result(conn, piotroski_result)
            elif piotroski_error is not None:
                invalidate_piotroski_result(conn, cik, args.fiscal_year, piotroski_error)
        print(f"\nSaved to {args.db_path}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
