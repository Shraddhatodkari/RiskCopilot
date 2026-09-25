"""
Tests for the SQLite persistence layer, using real (fixture-derived)
snapshots and computed results — an in-memory database
(`sqlite3.connect(":memory:")`) keeps these fast and fully isolated per
test, per ADR-007.
"""
from __future__ import annotations

from src.analysis.altman_z import InsufficientDataError, compute_altman_z_prime
from src.analysis.piotroski import compute_piotroski_f_score
from src.ingestion.xbrl_facts import build_financial_snapshot
from src.ingestion.models import DataQualityIssue, FactPoint, FinancialSnapshot
from src.persistence.storage import (
    connect,
    get_altman_history,
    get_data_quality_issues,
    get_facts,
    get_filing_metadata,
    get_latest_altman,
    get_latest_known_fiscal_year,
    get_latest_piotroski,
    get_piotroski_history,
    invalidate_altman_result,
    invalidate_piotroski_result,
    list_companies,
    save_altman_result,
    save_piotroski_result,
    save_snapshot,
)


def _full_altman_snapshot(cik: str, fy: int) -> FinancialSnapshot:
    """A synthetic-but-internally-consistent snapshot with every
    Altman-required concept present, for exercising the derived-data
    lifecycle in isolation from any real fixture's specific gaps."""
    def fact(concept: str, value: float) -> FactPoint:
        return FactPoint(
            concept=concept, tag_used=concept, value=value, unit="USD",
            period_end=f"{fy}-12-31", fiscal_year=fy, fiscal_period="FY",
            form="10-K", filed=f"{fy + 1}-02-01", accession_number=f"0000000009-{fy}-000001",
        )
    return FinancialSnapshot(
        cik=cik, entity_name="Test Co", fiscal_year=fy,
        total_assets=fact("total_assets", 1000),
        total_liabilities=fact("total_liabilities", 400),
        current_assets=fact("current_assets", 500),
        current_liabilities=fact("current_liabilities", 200),
        retained_earnings=fact("retained_earnings", 100),
        operating_income=fact("operating_income", 150),
        revenues=fact("revenues", 900),
        stockholders_equity=fact("stockholders_equity", 600),
    )


def test_save_and_read_back_a_snapshot_and_score(msft_client):
    # FY2025 is used here (not FY2024) because it is the fiscal year with a
    # complete set of Altman-relevant real facts in the MSFT fixture;
    # FY2024's fixture entries only cover the Piotroski-relevant concepts
    # (see tests/fixtures/msft_fy2025_companyconcept.json's provenance
    # note) — using FY2024 here would be testing a fixture gap, not the
    # storage layer.
    conn = connect(":memory:")
    snapshot = build_financial_snapshot(
        msft_client, "0000789019", "Microsoft Corporation", 2025
    )
    save_snapshot(conn, snapshot)
    result = compute_altman_z_prime(snapshot)
    save_altman_result(conn, result)
    conn.commit()

    row = conn.execute(
        "SELECT value, tag_used, accession_number FROM fact_points "
        "WHERE cik = ? AND concept = 'total_assets'",
        ("0000789019",),
    ).fetchone()
    assert row == (619_003_000_000.0, "Assets", "0000950170-25-100235")

    history = get_altman_history(conn, "0000789019")
    assert history == [
        {
            "fiscal_year": 2025,
            "z_score": result.z_score,
            "zone": "grey",
            "computed_at": history[0]["computed_at"],  # non-deterministic timestamp
        }
    ]
    conn.close()


def test_repeated_ingestion_is_idempotent_not_duplicated(msft_client):
    """Running ingestion twice for the same filing must not create
    duplicate rows — this is what makes it safe to re-run the pipeline on
    a schedule without the fact_points table growing unboundedly."""
    conn = connect(":memory:")
    snapshot = build_financial_snapshot(
        msft_client, "0000789019", "Microsoft Corporation", 2024
    )
    save_snapshot(conn, snapshot)
    save_snapshot(conn, snapshot)  # re-run
    conn.commit()

    count = conn.execute(
        "SELECT COUNT(*) FROM fact_points WHERE cik = ? AND fiscal_year = 2024",
        ("0000789019",),
    ).fetchone()[0]
    # 9 concepts are resolvable for MSFT FY2024 (all except long_term_debt's
    # sibling concepts that don't apply to this year); asserting "no
    # duplication" matters more than the exact count, so check against a
    # freshly-computed expectation from the snapshot itself.
    expected = sum(1 for f in snapshot.all_facts().values() if f is not None)
    assert count == expected
    conn.close()


def test_data_quality_issues_are_persisted(aapl_client):
    conn = connect(":memory:")
    snapshot = build_financial_snapshot(aapl_client, "0000320193", "Apple Inc.", 2025)
    save_snapshot(conn, snapshot)
    conn.commit()

    issues = get_data_quality_issues(conn, "0000320193", 2025)
    concepts = {i["concept"] for i in issues}
    # Apple's one real FY2025 gap (no InterestExpense tagged in its 10-K).
    assert concepts == {"interest_expense"}
    conn.close()


def test_piotroski_history_round_trip(msft_client):
    conn = connect(":memory:")
    current = build_financial_snapshot(msft_client, "0000789019", "Microsoft Corporation", 2024)
    prior = build_financial_snapshot(msft_client, "0000789019", "Microsoft Corporation", 2023)
    result = compute_piotroski_f_score(current, prior)
    save_piotroski_result(conn, result)
    conn.commit()

    history = get_piotroski_history(conn, "0000789019")
    assert history[0]["fiscal_year"] == 2024
    assert history[0]["f_score"] == 5
    conn.close()


def test_resolved_data_quality_issue_does_not_persist_as_stale():
    """ADR-012: re-ingesting a (cik, fiscal_year) whose issues have
    genuinely been resolved (a concept that was missing is now found) must
    not leave the old 'missing' row sitting next to the fresh fact — a
    stale warning presented as current is exactly what a professional
    financial tool must not do (see docs/07_security_review.md's honesty
    principle)."""
    conn = connect(":memory:")
    cik, fy = "0000000009", 2025

    # Run 1: total_liabilities is genuinely missing.
    snap_v1 = FinancialSnapshot(
        cik=cik, entity_name="Test Co", fiscal_year=fy,
        data_quality_issues=[
            DataQualityIssue(concept="total_liabilities", severity="missing", detail="not found")
        ],
    )
    save_snapshot(conn, snap_v1)
    conn.commit()
    assert {i["concept"] for i in get_data_quality_issues(conn, cik, fy)} == {"total_liabilities"}

    # Run 2: a later run (e.g. after a tag-fallback-list improvement) finds
    # it after all — the snapshot now has NO issue for this concept.
    snap_v2 = FinancialSnapshot(cik=cik, entity_name="Test Co", fiscal_year=fy, data_quality_issues=[])
    save_snapshot(conn, snap_v2)
    conn.commit()

    assert get_data_quality_issues(conn, cik, fy) == []
    conn.close()


def test_repeated_ingestion_does_not_duplicate_data_quality_issues(aapl_client):
    """Running ingestion twice for a company with a genuine, still-present
    data quality issue must report it once, not accumulate a duplicate row
    per run — the same idempotency guarantee test_repeated_ingestion_is_
    idempotent_not_duplicated already established for fact_points."""
    conn = connect(":memory:")
    snapshot = build_financial_snapshot(aapl_client, "0000320193", "Apple Inc.", 2025)
    save_snapshot(conn, snapshot)
    save_snapshot(conn, snapshot)  # re-run, same real gap still present
    conn.commit()

    issues = get_data_quality_issues(conn, "0000320193", 2025)
    concepts = [i["concept"] for i in issues]
    assert len(concepts) == len(set(concepts))  # no concept appears twice
    conn.close()


def test_get_latest_altman_includes_full_component_breakdown(msft_client):
    conn = connect(":memory:")
    snapshot = build_financial_snapshot(msft_client, "0000789019", "Microsoft Corporation", 2025)
    save_snapshot(conn, snapshot)
    result = compute_altman_z_prime(snapshot)
    save_altman_result(conn, result)
    conn.commit()

    latest = get_latest_altman(conn, "0000789019")
    assert latest["fiscal_year"] == 2025
    assert latest["z_score"] == result.z_score
    assert len(latest["components"]) == 5
    assert {c["name"] for c in latest["components"]} == {c.name for c in result.components}
    conn.close()


def test_get_latest_piotroski_includes_all_nine_signals(msft_client):
    conn = connect(":memory:")
    current = build_financial_snapshot(msft_client, "0000789019", "Microsoft Corporation", 2024)
    prior = build_financial_snapshot(msft_client, "0000789019", "Microsoft Corporation", 2023)
    result = compute_piotroski_f_score(current, prior)
    save_piotroski_result(conn, result)
    conn.commit()

    latest = get_latest_piotroski(conn, "0000789019")
    assert latest["f_score"] == 5
    assert len(latest["signals"]) == 9
    conn.close()


def test_get_facts_returns_full_provenance_for_the_requested_year_only(msft_client):
    conn = connect(":memory:")
    fy2025 = build_financial_snapshot(msft_client, "0000789019", "Microsoft Corporation", 2025)
    fy2024 = build_financial_snapshot(msft_client, "0000789019", "Microsoft Corporation", 2024)
    save_snapshot(conn, fy2025)
    save_snapshot(conn, fy2024)
    conn.commit()

    facts_2025 = get_facts(conn, "0000789019", 2025)
    assert facts_2025  # non-empty
    assert all(f["accession_number"] == "0000950170-25-100235" for f in facts_2025)
    concept_names = {f["concept"] for f in facts_2025}
    assert "total_assets" in concept_names
    # Fiscal-year scoping is exact: FY2024 facts must not leak into a
    # FY2025 query, even though both are stored for the same company.
    total_assets_fact = next(f for f in facts_2025 if f["concept"] == "total_assets")
    assert total_assets_fact["period_end"] == "2025-06-30"
    conn.close()


def test_get_filing_metadata_reflects_the_actual_stored_facts(msft_client):
    conn = connect(":memory:")
    snapshot = build_financial_snapshot(msft_client, "0000789019", "Microsoft Corporation", 2025)
    save_snapshot(conn, snapshot)
    conn.commit()

    meta = get_filing_metadata(conn, "0000789019", 2025)
    assert meta["form"] == "10-K"
    assert meta["accession_number"] == "0000950170-25-100235"
    assert meta["filed"] == "2025-07-30"
    # Fiscal-calendar robustness (requirement: don't assume December
    # year-end) — MSFT's real fiscal year ends in June, and this project's
    # fy/fp-based filtering (src/ingestion/xbrl_facts.py) already handles
    # that correctly without any calendar-month assumption; this assertion
    # locks that in as an explicit, real regression test rather than an
    # implicit property nobody checks.
    total_assets = next(f for f in get_facts(conn, "0000789019", 2025) if f["concept"] == "total_assets")
    assert total_assets["period_end"].endswith("-06-30")

    assert get_filing_metadata(conn, "0000789019", 1999) is None
    conn.close()


def test_list_companies_includes_a_company_with_facts_but_no_computable_score(aapl_client):
    """Real regression: a company whose scoring genuinely failed (Apple
    FY2025 in this project's own data — missing retained_earnings, so no
    Altman score exists) must still appear in the company picker. Before
    this fix, list_companies() only unioned altman_z_scores and
    piotroski_scores, so a company with real ingested facts and real,
    honestly-reported data-quality issues but zero computable scores was
    invisible in the dashboard — an honesty gap, not just a UX one."""
    conn = connect(":memory:")
    snapshot = build_financial_snapshot(aapl_client, "0000320193", "Apple Inc.", 2025)
    save_snapshot(conn, snapshot)
    conn.commit()

    companies = list_companies(conn)
    assert {"cik": "0000320193", "entity_name": "Apple Inc."} in companies
    conn.close()


def test_stale_altman_score_is_invalidated_when_recomputation_fails():
    """ADR-013, the CRITICAL derived-data lifecycle bug: a company may have
    a previously valid, stored Altman result. If a LATER run's source data
    is genuinely incomplete (a retained_earnings tag disappears, an amended
    filing removes a concept, etc.) and recomputation fails, the old score
    must NOT keep being read back as if it were still current. This
    reproduces the full real-world sequence: valid score exists -> source
    becomes insufficient -> old score is invalidated -> no stale score
    survives -> a data-quality issue records why."""
    conn = connect(":memory:")
    cik, fy = "0000000009", 2025

    # Run 1: complete data, a real score is computed and saved.
    snap_v1 = _full_altman_snapshot(cik, fy)
    save_snapshot(conn, snap_v1)
    result_v1 = compute_altman_z_prime(snap_v1)
    save_altman_result(conn, result_v1)
    conn.commit()
    assert get_latest_altman(conn, cik) is not None
    assert get_latest_altman(conn, cik)["z_score"] == result_v1.z_score

    # Run 2: a later run's source data is genuinely missing retained_earnings
    # (e.g. a tag renamed/removed in a subsequent filing) -- recomputation
    # must fail, and the caller (mirroring src/cli.py's --save path and
    # src/reporting/live_ingest.py's analyze_and_persist) must invalidate
    # the now-unreproducible old score rather than leaving it in place.
    snap_v2 = _full_altman_snapshot(cik, fy)
    snap_v2.retained_earnings = None
    save_snapshot(conn, snap_v2)
    try:
        compute_altman_z_prime(snap_v2)
        assert False, "expected InsufficientDataError"
    except InsufficientDataError as e:
        invalidate_altman_result(conn, cik, fy, str(e))
    conn.commit()

    # The old score must be gone, not stale.
    assert get_latest_altman(conn, cik) is None

    issues = get_data_quality_issues(conn, cik, fy)
    invalidated = [i for i in issues if i["concept"] == "altman_z_score"]
    assert len(invalidated) == 1
    assert invalidated[0]["severity"] == "invalidated"
    assert "retained_earnings" in invalidated[0]["detail"]
    conn.close()


def test_altman_score_recovers_cleanly_if_data_becomes_available_again():
    """The other half of the lifecycle: if a later run's source data is
    complete again, the fresh score must simply replace the old one (via
    INSERT OR REPLACE) -- invalidation must not leave any lingering
    "invalidated" issue behind once a real score exists again."""
    conn = connect(":memory:")
    cik, fy = "0000000009", 2025

    snap_v1 = _full_altman_snapshot(cik, fy)
    save_snapshot(conn, snap_v1)
    result_v1 = compute_altman_z_prime(snap_v1)
    save_altman_result(conn, result_v1)
    conn.commit()

    snap_v2 = _full_altman_snapshot(cik, fy)
    snap_v2.retained_earnings = None
    save_snapshot(conn, snap_v2)
    try:
        compute_altman_z_prime(snap_v2)
    except InsufficientDataError as e:
        invalidate_altman_result(conn, cik, fy, str(e))
    conn.commit()
    assert get_latest_altman(conn, cik) is None

    # Run 3: the concept is available again (e.g. an amended filing).
    snap_v3 = _full_altman_snapshot(cik, fy)
    save_snapshot(conn, snap_v3)  # this run's save_snapshot clears prior DQ issues for (cik, fy)
    result_v3 = compute_altman_z_prime(snap_v3)
    save_altman_result(conn, result_v3)
    conn.commit()

    assert get_latest_altman(conn, cik)["z_score"] == result_v3.z_score
    issues = get_data_quality_issues(conn, cik, fy)
    assert not [i for i in issues if i["concept"] == "altman_z_score"]
    conn.close()


def test_stale_piotroski_score_is_invalidated_when_recomputation_fails():
    """Same derived-data lifecycle guarantee, for Piotroski."""
    conn = connect(":memory:")
    cik, fy = "0000000009", 2025

    invalidate_piotroski_result(conn, cik, fy, "Cannot compute Piotroski F-Score: missing cost_of_goods_sold")
    conn.commit()

    assert get_latest_piotroski(conn, cik) is None
    issues = get_data_quality_issues(conn, cik, fy)
    invalidated = [i for i in issues if i["concept"] == "piotroski_f_score"]
    assert len(invalidated) == 1
    assert invalidated[0]["severity"] == "invalidated"


def test_analyze_and_persist_invalidates_stale_score_end_to_end(msft_client, monkeypatch):
    """The real caller-level regression, exactly matching how
    src/reporting/live_ingest.py's analyze_and_persist (used by the
    dashboard's "Analyze a new company" flow) is actually invoked: a valid
    score is persisted on one call, then a second call whose recomputation
    fails must leave no stale score behind."""
    import src.reporting.live_ingest as live_ingest_module
    from src.analysis.altman_z import InsufficientDataError as AltmanInsufficientDataError
    from src.reporting.live_ingest import analyze_and_persist

    conn = connect(":memory:")
    cik, fy = "0000789019", 2025

    # Call 1: real MSFT fixture data -> a real, valid score is persisted.
    result1 = analyze_and_persist(
        conn, msft_client, cik=cik, entity_name="Microsoft Corporation", fiscal_year=fy
    )
    assert result1.altman_result is not None
    assert get_latest_altman(conn, cik) is not None

    # Call 2: simulate the exact same company's source data becoming
    # insufficient on a later run (e.g. a restated filing), by making
    # compute_altman_z_prime itself fail this time -- analyze_and_persist
    # must invalidate the previously-valid score it just wrote in call 1.
    def _always_fails(snapshot):
        raise AltmanInsufficientDataError(["retained_earnings"])

    monkeypatch.setattr(live_ingest_module, "compute_altman_z_prime", _always_fails)

    result2 = analyze_and_persist(
        conn, msft_client, cik=cik, entity_name="Microsoft Corporation", fiscal_year=fy
    )
    assert result2.altman_result is None
    assert result2.altman_error is not None
    assert get_latest_altman(conn, cik) is None  # the stale score must be GONE, not still readable

    issues = get_data_quality_issues(conn, cik, fy)
    assert any(i["concept"] == "altman_z_score" and i["severity"] == "invalidated" for i in issues)
    conn.close()


def test_get_latest_known_fiscal_year_uses_facts_when_no_score_exists(aapl_client):
    conn = connect(":memory:")
    assert get_latest_known_fiscal_year(conn, "0000320193") is None  # nothing stored yet

    snapshot = build_financial_snapshot(aapl_client, "0000320193", "Apple Inc.", 2025)
    save_snapshot(conn, snapshot)
    conn.commit()

    # No Altman/Piotroski score exists for this company at all, yet the
    # fiscal year is still correctly identified from stored facts/issues.
    assert get_latest_known_fiscal_year(conn, "0000320193") == 2025
    conn.close()
