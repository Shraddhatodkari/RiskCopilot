"""
Live ingestion helper (ADR-012): resolves a ticker or CIK, fetches real SEC
XBRL data, computes both deterministic scores, and returns a structured
result — the same primitives `src/cli.py` already uses
(`resolve_ticker`, `SecEdgarClient`, `build_financial_snapshot`,
`compute_altman_z_prime`, `compute_piotroski_f_score`), factored out here
so the dashboard's "Analyze a new company" flow can call the exact same
tested logic instead of re-implementing it inline in `dashboard.py`
(architecture requirement: business logic belongs in `src/`, not the
dashboard file).

This performs a REAL network call to data.sec.gov when given a real
`SecEdgarClient`. This project's own cloud development sandbox cannot
reach that host (see docs/03_data_provenance.md) — `tests/unit/
test_live_ingest.py` verifies this function's logic against the same
fixture-backed fake client pattern used throughout this test suite; a live
end-to-end run happens on the user's own machine, exactly like
`python -m src.cli`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from src.analysis.altman_z import InsufficientDataError as AltmanInsufficientDataError
from src.analysis.altman_z import ZScoreResult, compute_altman_z_prime
from src.analysis.piotroski import InsufficientDataError as PiotroskiInsufficientDataError
from src.analysis.piotroski import PiotroskiResult, compute_piotroski_f_score
from src.config import Settings
from src.ingestion.filing_document_client import cache_evidence, extract_item_1a_chunks, fetch_10k_primary_document_html
from src.ingestion.models import FinancialSnapshot
from src.ingestion.sec_edgar_client import SecEdgarError
from src.ingestion.ticker_lookup import TickerLookupError, resolve_ticker
from src.ingestion.xbrl_facts import build_financial_snapshot
from src.persistence.storage import (
    invalidate_altman_result,
    invalidate_piotroski_result,
    save_altman_result,
    save_piotroski_result,
    save_snapshot,
)
from src.reporting.evidence_registry import evidence_available

_DEFAULT_EVIDENCE_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "evidence_cache"


class _ConceptClient(Protocol):
    def get_company_concept(self, cik: str, taxonomy: str, tag: str) -> dict: ...


class _EvidenceClient(Protocol):
    """The two additional methods `fetch_10k_primary_document_html` needs
    (see src/ingestion/filing_document_client.py) — a real `SecEdgarClient`
    already implements both (ADR-013); a fixture-backed fake client used
    only for score computation in tests need not, which is exactly why
    this is checked with `hasattr` rather than required on `_ConceptClient`
    itself (a caller with a scores-only fake client can still pass
    `fetch_evidence=False`, the default, without needing to also fake
    submissions/document-fetch methods it never uses)."""

    def get_submissions(self, cik: str) -> dict: ...
    def get_document_html(self, cik: str, accession_number: str, filename: str) -> str: ...


def _find_10k_accession_number(snapshot: FinancialSnapshot) -> str | None:
    """The accession number of the real 10-K filing this snapshot's facts
    were sourced from, for evidence fetching. Every fact on a given
    snapshot comes from the same annual filing, so any populated FactPoint
    whose `form` is "10-K" identifies it. Returns None (never a guess) if
    nothing on this snapshot came from a 10-K — e.g. a company whose only
    available facts came from a 10-Q."""
    for fact in snapshot.all_facts().values():
        if fact is not None and fact.form == "10-K":
            return fact.accession_number
    return None


class LiveIngestResult(BaseModel):
    cik: str
    entity_name: str
    fiscal_year: int
    altman_result: ZScoreResult | None = None
    altman_error: str | None = None
    piotroski_result: PiotroskiResult | None = None
    piotroski_error: str | None = None
    # ADR-013: whether a real, company-specific risk-factor evidence file
    # was fetched and cached for this run (see analyze_and_persist's
    # `fetch_evidence` parameter). None if fetching wasn't attempted at
    # all; False + evidence_error set if it was attempted and failed —
    # never silently skipped without saying so.
    evidence_cached: bool | None = None
    evidence_error: str | None = None


class LiveIngestError(RuntimeError):
    """Raised for failures before a snapshot could even be attempted
    (ticker resolution, network/config errors) — distinct from a per-model
    InsufficientDataError, which is a legitimate, honestly-reported partial
    result, not a failure of the whole operation."""


def analyze_company(
    client: _ConceptClient,
    *,
    ticker: str | None = None,
    cik: str | None = None,
    entity_name: str | None = None,
    fiscal_year: int,
    settings: Settings | None = None,
    with_piotroski: bool = False,
) -> tuple[FinancialSnapshot, LiveIngestResult, FinancialSnapshot | None]:
    """Resolve (if given a ticker), fetch, and score exactly one company/
    fiscal year. Returns the raw `FinancialSnapshot` (for persistence)
    alongside the `LiveIngestResult`, plus the prior fiscal year's
    `FinancialSnapshot` when `with_piotroski=True` and that fetch actually
    succeeded (`None` otherwise) — real facts this call already fetched
    from SEC EDGAR to compute Piotroski's year-over-year comparison, and
    which the caller should persist too (see `analyze_and_persist`) rather
    than discard, since they are genuine, already-paid-for historical data
    for this company. Raises `LiveIngestError` only for failures that
    prevent any analysis at all; a missing individual score
    (InsufficientDataError) is captured in the result's `*_error` field
    instead, so the caller always gets back whatever could honestly be
    computed."""
    if ticker:
        if settings is None:
            raise LiveIngestError("settings (for SEC_EDGAR_USER_AGENT) are required to resolve a ticker")
        try:
            cik, entity_name = resolve_ticker(ticker, settings.sec_edgar_user_agent)
        except TickerLookupError as exc:
            raise LiveIngestError(f"Could not resolve ticker '{ticker}': {exc}") from exc
    if not cik or not entity_name:
        raise LiveIngestError("Either --ticker, or both cik and entity_name, are required")

    try:
        snapshot = build_financial_snapshot(client, cik, entity_name, fiscal_year)
    except SecEdgarError as exc:
        raise LiveIngestError(f"Could not fetch SEC data for {entity_name} (CIK {cik}): {exc}") from exc

    result = LiveIngestResult(cik=cik, entity_name=entity_name, fiscal_year=fiscal_year)

    try:
        result.altman_result = compute_altman_z_prime(snapshot)
    except AltmanInsufficientDataError as exc:
        result.altman_error = str(exc)

    prior_snapshot = None
    if with_piotroski:
        try:
            prior_snapshot = build_financial_snapshot(client, cik, entity_name, fiscal_year - 1)
            result.piotroski_result = compute_piotroski_f_score(snapshot, prior_snapshot)
        except SecEdgarError as exc:
            result.piotroski_error = f"Could not fetch prior-year data: {exc}"
            prior_snapshot = None
        except PiotroskiInsufficientDataError as exc:
            # The prior-year fetch itself succeeded (prior_snapshot has real
            # facts) — only the Piotroski score computation couldn't run
            # (e.g. a missing signal input). Those real prior-year facts are
            # still worth persisting for ratio/trend history, so
            # prior_snapshot is intentionally NOT cleared here.
            result.piotroski_error = str(exc)

    return snapshot, result, prior_snapshot


def analyze_and_persist(
    conn,
    client: _ConceptClient,
    *,
    ticker: str | None = None,
    cik: str | None = None,
    entity_name: str | None = None,
    fiscal_year: int,
    settings: Settings | None = None,
    with_piotroski: bool = False,
    fetch_evidence: bool = False,
    evidence_cache_dir: Path | None = None,
) -> LiveIngestResult:
    """Same as `analyze_company`, plus persists the snapshot and any
    computed scores to `conn` — what the dashboard's "Analyze a new
    company" button actually calls.

    Derived-data lifecycle (ADR-013, the "stale-derived-score" bug fix):
    a fresh recomputation attempt is made for this exact (cik, fiscal_year)
    every time this function runs. If it succeeds, the new result replaces
    whatever was stored before (`save_altman_result`/`save_piotroski_result`
    already do this via `INSERT OR REPLACE`). If it genuinely fails
    (`result.altman_error`/`result.piotroski_error` is set), any
    PREVIOUSLY stored result for that same period is explicitly deleted —
    never left sitting in the database to be read back as if it were still
    current. This is what makes it safe to re-run this function on a
    schedule against a company whose underlying filing data may have
    changed (a restatement, a newly-missing tag) without risking a stale
    score surviving silently.

    Evidence fetching (ADR-013, `fetch_evidence=True`): if this exact CIK
    has no registered risk-factor evidence yet (`evidence_registry.
    evidence_available`), and `client` supports the two extra methods
    `fetch_10k_primary_document_html` needs (a real `SecEdgarClient`
    does), attempt to fetch and cache this company's REAL Item 1A text so
    the Filing Risk Intelligence / narrative tabs work for it too — not
    just the deterministic scores. This is best-effort and never fatal to
    the overall analysis: any failure (network, parsing, no 10-K on this
    snapshot, client doesn't support it) is recorded honestly on
    `result.evidence_error` rather than raised, because a company's
    Altman/Piotroski scores and ratios are still valid and worth
    persisting even when evidence fetching didn't work. Already-registered
    evidence (including this project's curated fixtures) is never
    re-fetched or overwritten by this path.
    """
    snapshot, result, prior_snapshot = analyze_company(
        client,
        ticker=ticker,
        cik=cik,
        entity_name=entity_name,
        fiscal_year=fiscal_year,
        settings=settings,
        with_piotroski=with_piotroski,
    )
    save_snapshot(conn, snapshot)
    if prior_snapshot is not None:
        # Real prior-year facts already fetched above (to compute
        # Piotroski's year-over-year comparison) — persist them too so
        # this company's Historical Trends actually has >=2 real fiscal
        # years to work with, instead of silently discarding a year of
        # real SEC data this same call already paid the network cost for.
        save_snapshot(conn, prior_snapshot)
    if result.altman_result is not None:
        save_altman_result(conn, result.altman_result)
    elif result.altman_error is not None:
        invalidate_altman_result(conn, result.cik, result.fiscal_year, result.altman_error)
    if result.piotroski_result is not None:
        save_piotroski_result(conn, result.piotroski_result)
    elif result.piotroski_error is not None:
        invalidate_piotroski_result(conn, result.cik, result.fiscal_year, result.piotroski_error)

    if fetch_evidence:
        if evidence_available(result.cik):
            result.evidence_cached = False
            result.evidence_error = (
                f"Evidence already registered for CIK {result.cik}; not re-fetched."
            )
        elif not (hasattr(client, "get_submissions") and hasattr(client, "get_document_html")):
            result.evidence_cached = False
            result.evidence_error = (
                "This client does not support fetching filing documents "
                "(get_submissions/get_document_html) — evidence was not fetched."
            )
        else:
            accession_number = _find_10k_accession_number(snapshot)
            if accession_number is None:
                result.evidence_cached = False
                result.evidence_error = (
                    "No 10-K filing could be identified from this snapshot's "
                    "sourced facts — evidence was not fetched."
                )
            else:
                try:
                    document_html = fetch_10k_primary_document_html(client, result.cik, accession_number)
                    chunks = extract_item_1a_chunks(
                        document_html,
                        cik=result.cik,
                        entity_name=result.entity_name,
                        fiscal_year=result.fiscal_year,
                        accession_number=accession_number,
                        source_document_url=(
                            f"https://www.sec.gov/Archives/edgar/data/"
                            f"{int(result.cik)}/{accession_number.replace('-', '')}/"
                        ),
                    )
                    if not chunks:
                        result.evidence_cached = False
                        result.evidence_error = (
                            "Item 1A (Risk Factors) could not be located or contained no "
                            "chunk of usable length in this filing's HTML."
                        )
                    else:
                        cache_evidence(
                            chunks,
                            cik=result.cik,
                            entity_name=result.entity_name,
                            fiscal_year=result.fiscal_year,
                            accession_number=accession_number,
                            source_document_url=(
                                f"https://www.sec.gov/Archives/edgar/data/"
                                f"{int(result.cik)}/{accession_number.replace('-', '')}/"
                            ),
                            cache_dir=evidence_cache_dir or _DEFAULT_EVIDENCE_CACHE_DIR,
                        )
                        result.evidence_cached = True
                except Exception as exc:  # noqa: BLE001 — deliberately broad: any failure here
                    # (network, SEC 404/rate-limit, parsing) must be recorded honestly,
                    # never silently swallowed and never allowed to abort an otherwise-
                    # successful score computation that has already been persisted above.
                    result.evidence_cached = False
                    result.evidence_error = f"Evidence fetch failed: {exc}"

    conn.commit()
    return result
