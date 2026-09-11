"""
Tests for src/reporting/live_ingest.py — the function backing the
dashboard's "Analyze a new company" flow — using the same fixture-backed
fake client pattern as tests/conftest.py and tests/unit/test_cli.py. This
verifies the real fetch/score/persist logic without a live network call;
an actual live run against data.sec.gov happens on the user's own machine,
exactly like `python -m src.cli` (see docs/03_data_provenance.md).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import Settings
from src.ingestion.sec_edgar_client import SecEdgarNotFound
from src.ingestion.ticker_lookup import TickerLookupError
from src.persistence.storage import connect, get_altman_history, get_piotroski_history
from src.reporting.live_ingest import LiveIngestError, analyze_and_persist, analyze_company

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


class _FixtureBackedClient:
    def __init__(self, fixture_name: str):
        with open(FIXTURES_DIR / fixture_name) as f:
            self._data = json.load(f)

    def get_company_concept(self, cik: str, taxonomy: str, tag: str) -> dict:
        if tag not in self._data:
            raise SecEdgarNotFound(f"fixture has no tag {tag}")
        return self._data[tag]


@pytest.fixture
def msft_client():
    return _FixtureBackedClient("msft_fy2025_companyconcept.json")


@pytest.fixture
def settings():
    return Settings(
        sec_edgar_user_agent="Test test@example.com",
        fred_api_key=None,
        anthropic_api_key=None,
    )


def test_analyze_company_by_cik_computes_altman(msft_client):
    snapshot, result, prior_snapshot = analyze_company(
        msft_client, cik="0000789019", entity_name="Microsoft Corporation", fiscal_year=2025
    )
    assert result.cik == "0000789019"
    assert result.altman_result is not None
    assert result.altman_result.zone.value == "grey"
    assert result.altman_error is None
    assert prior_snapshot is None  # with_piotroski defaults to False — nothing prior was fetched


def test_analyze_company_with_piotroski_fetches_prior_year_too(msft_client):
    _, result, prior_snapshot = analyze_company(
        msft_client,
        cik="0000789019",
        entity_name="Microsoft Corporation",
        fiscal_year=2024,
        with_piotroski=True,
    )
    assert result.piotroski_result is not None
    assert result.piotroski_result.f_score == 5
    # The real FY2023 facts fetched to compute this comparison must be
    # returned to the caller (see analyze_and_persist), not discarded.
    assert prior_snapshot is not None
    assert prior_snapshot.fiscal_year == 2023


def test_analyze_company_reports_insufficient_data_honestly_not_as_a_crash(msft_client):
    """FY2024 genuinely lacks complete Altman-relevant data in this
    fixture (see tests/unit/test_xbrl_facts.py) — analyze_company must
    surface that as an honest per-model error, not raise or fabricate a
    score."""
    _, result, _ = analyze_company(
        msft_client, cik="0000789019", entity_name="Microsoft Corporation", fiscal_year=2024
    )
    assert result.altman_result is None
    assert result.altman_error is not None
    assert "Refusing to substitute" in result.altman_error


def test_ticker_without_settings_raises_live_ingest_error(msft_client):
    with pytest.raises(LiveIngestError, match="settings"):
        analyze_company(msft_client, ticker="MSFT", fiscal_year=2025)


def test_ticker_resolution_failure_is_wrapped_as_live_ingest_error(msft_client, settings, monkeypatch):
    import src.reporting.live_ingest as live_ingest_module

    def _raise(*_a, **_k):
        raise TickerLookupError("unknown ticker ZZZZ")

    monkeypatch.setattr(live_ingest_module, "resolve_ticker", _raise)

    with pytest.raises(LiveIngestError, match="ZZZZ"):
        analyze_company(msft_client, ticker="ZZZZ", fiscal_year=2025, settings=settings)


def test_missing_cik_and_ticker_both_raises():
    with pytest.raises(LiveIngestError):
        analyze_company(object(), fiscal_year=2025)


def test_analyze_and_persist_saves_snapshot_and_score(msft_client):
    conn = connect(":memory:")
    result = analyze_and_persist(
        conn, msft_client, cik="0000789019", entity_name="Microsoft Corporation", fiscal_year=2025
    )
    assert result.altman_result is not None

    history = get_altman_history(conn, "0000789019")
    assert len(history) == 1
    assert history[0]["fiscal_year"] == 2025
    conn.close()


def test_analyze_and_persist_with_piotroski_saves_both_scores(msft_client):
    conn = connect(":memory:")
    result = analyze_and_persist(
        conn,
        msft_client,
        cik="0000789019",
        entity_name="Microsoft Corporation",
        fiscal_year=2024,
        with_piotroski=True,
    )
    assert result.piotroski_result is not None
    assert get_piotroski_history(conn, "0000789019")[0]["f_score"] == 5
    conn.close()


def test_analyze_and_persist_with_piotroski_also_persists_the_prior_years_real_facts(msft_client):
    """Regression test for a real defect: `analyze_company` already fetches
    a real prior-year snapshot from SEC EDGAR to compute Piotroski's
    year-over-year comparison, but until this fix `analyze_and_persist`
    only ever called `save_snapshot` for the current fiscal year — so a
    user running the dashboard's live "Analyze a new company" flow (with
    Piotroski) ended up with only ONE real fiscal year in storage even
    though a second, genuinely real year had just been fetched, causing
    Historical Trends to wrongly report "only 1 real fiscal year available"
    for every ratio. Both real fiscal years fetched by this one call must
    now be queryable via get_all_fiscal_years."""
    from src.persistence.storage import get_all_fiscal_years

    conn = connect(":memory:")
    analyze_and_persist(
        conn,
        msft_client,
        cik="0000789019",
        entity_name="Microsoft Corporation",
        fiscal_year=2024,
        with_piotroski=True,
    )
    assert get_all_fiscal_years(conn, "0000789019") == [2023, 2024]
    conn.close()


# --- ADR-013: evidence fetching -------------------------------------------
#
# `fetch_evidence=True` is best-effort and must never turn a successful
# score computation into a failed one; every branch below asserts the
# scores are STILL persisted correctly regardless of what happened with
# evidence, alongside the honest evidence_cached/evidence_error state.


def test_fetch_evidence_is_skipped_when_this_cik_already_has_registered_evidence(msft_client):
    """Microsoft already has a curated evidence fixture
    (tests/fixtures/msft_fy2025_risk_factors.json) — analyze_and_persist
    must not attempt to re-fetch/overwrite it, and must say so honestly
    rather than silently reporting evidence_cached=True without having
    done anything."""
    conn = connect(":memory:")
    result = analyze_and_persist(
        conn, msft_client, cik="0000789019", entity_name="Microsoft Corporation",
        fiscal_year=2025, fetch_evidence=True,
    )
    assert result.altman_result is not None  # scores still computed normally
    assert result.evidence_cached is False
    assert result.evidence_error is not None
    assert "already registered" in result.evidence_error
    conn.close()


def test_fetch_evidence_reports_honest_error_when_client_cannot_fetch_documents(msft_client):
    """A CIK with no registered evidence, using a client that only
    implements get_company_concept (like every other test's fixture-backed
    client) — must not crash, and must not claim success."""
    conn = connect(":memory:")
    result = analyze_and_persist(
        conn, msft_client, cik="0000000099", entity_name="Some Other Company",
        fiscal_year=2025, fetch_evidence=True,
    )
    assert result.altman_result is not None  # scores still computed normally
    assert result.evidence_cached is False
    assert result.evidence_error is not None
    assert "does not support" in result.evidence_error
    conn.close()


class _FullFixtureAndEvidenceClient(_FixtureBackedClient):
    """A fixture-backed client that ALSO supports the submissions/document
    endpoints `fetch_10k_primary_document_html` needs — same duck-typed
    pattern as tests/unit/test_filing_document_client.py's
    _FakeSubmissionsClient, combined with score-computation support so one
    fake client can exercise the whole analyze_and_persist(fetch_evidence=True)
    path end to end."""

    def __init__(self, fixture_name: str, accession_number: str, document_html: str):
        super().__init__(fixture_name)
        self._accession_number = accession_number
        self._document_html = document_html

    def get_submissions(self, cik: str) -> dict:
        return {
            "filings": {
                "recent": {
                    "accessionNumber": [self._accession_number],
                    "form": ["10-K"],
                    "primaryDocument": ["annual-report.htm"],
                }
            }
        }

    def get_document_html(self, cik: str, accession_number: str, filename: str) -> str:
        return self._document_html


_REAL_STRUCTURE_10K_HTML = """
<html><body>
<p>Item 1A. Risk Factors ... 8</p>
<p><b>Item 1A. Risk Factors</b></p>
<p><b>Intense competition could harm our business.</b></p>
<p>We compete with many companies in every market we serve, and some of these
competitors have greater financial and technical resources than we do.</p>
<p>Item 1B. Unresolved Staff Comments</p>
</body></html>
"""


def test_fetch_evidence_fetches_and_caches_real_new_company_evidence_end_to_end(tmp_path):
    """A company with NO curated fixture, whose fiscal-year facts genuinely
    came from a 10-K (form="10-K" on the fixture's FactPoints), using a
    client that supports document fetching — the full new-company evidence
    path, with zero pre-existing evidence for this CIK."""
    fake_cik = "0000000099"
    # Must match the real FY2025 10-K accession number already present in
    # this fixture (msft_fy2025_companyconcept.json's own Assets/FY2025
    # entry) — analyze_and_persist finds it from the snapshot's own sourced
    # facts, not from a value the test invents.
    client = _FullFixtureAndEvidenceClient(
        "msft_fy2025_companyconcept.json", "0000950170-25-100235", _REAL_STRUCTURE_10K_HTML,
    )
    conn = connect(":memory:")
    result = analyze_and_persist(
        conn, client, cik=fake_cik, entity_name="Some Other Company", fiscal_year=2025,
        fetch_evidence=True, evidence_cache_dir=tmp_path,
    )
    assert result.altman_result is not None
    assert result.evidence_cached is True
    assert result.evidence_error is None

    cached_files = list(tmp_path.glob("*_risk_factors.json"))
    assert len(cached_files) == 1
    payload = json.loads(cached_files[0].read_text())
    assert payload["cik"] == fake_cik
    assert any("compete" in c["text"].lower() for c in payload["chunks"])
    conn.close()


def test_fetch_evidence_failure_does_not_prevent_score_persistence(tmp_path):
    """If the document fetch itself raises (network error, bad accession,
    anything) the already-computed, already-valid Altman/Piotroski scores
    must still be persisted — evidence fetching is additive, never a
    reason to discard a real, valid deterministic result."""
    class _BrokenEvidenceClient(_FixtureBackedClient):
        def get_submissions(self, cik: str) -> dict:
            raise RuntimeError("simulated network failure")

        def get_document_html(self, cik: str, accession_number: str, filename: str) -> str:
            raise RuntimeError("should not be reached")

    client = _BrokenEvidenceClient("msft_fy2025_companyconcept.json")
    conn = connect(":memory:")
    result = analyze_and_persist(
        conn, client, cik="0000000098", entity_name="Another Company", fiscal_year=2025,
        fetch_evidence=True, evidence_cache_dir=tmp_path,
    )
    assert result.altman_result is not None
    history = get_altman_history(conn, "0000000098")
    assert len(history) == 1
    assert result.evidence_cached is False
    assert "Evidence fetch failed" in result.evidence_error
    conn.close()
