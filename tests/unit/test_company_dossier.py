"""
Tests for CompanyDossier (ADR-012) — the single company-scoped assembly
point the dashboard reads from. The key property under test is isolation:
building a dossier for company A must never surface company B's data, even
when both are stored in the same database.
"""
from __future__ import annotations

from src.analysis.altman_z import compute_altman_z_prime
from src.analysis.piotroski import compute_piotroski_f_score
from src.ingestion.xbrl_facts import build_financial_snapshot
from src.persistence.storage import connect, save_altman_result, save_piotroski_result, save_snapshot
from src.reporting.company_dossier import build_company_dossier

AAPL_CIK = "0000320193"
MSFT_CIK = "0000789019"


def _seed_both_companies(conn, aapl_client, msft_client):
    aapl_2025 = build_financial_snapshot(aapl_client, AAPL_CIK, "Apple Inc.", 2025)
    save_snapshot(conn, aapl_2025)
    # AAPL FY2025 genuinely lacks retained_earnings — no Altman score, by design.

    msft_2025 = build_financial_snapshot(msft_client, MSFT_CIK, "Microsoft Corporation", 2025)
    save_snapshot(conn, msft_2025)
    msft_altman = compute_altman_z_prime(msft_2025)
    save_altman_result(conn, msft_altman)

    msft_2024 = build_financial_snapshot(msft_client, MSFT_CIK, "Microsoft Corporation", 2024)
    msft_2023 = build_financial_snapshot(msft_client, MSFT_CIK, "Microsoft Corporation", 2023)
    save_snapshot(conn, msft_2024)
    save_snapshot(conn, msft_2023)
    msft_piotroski = compute_piotroski_f_score(msft_2024, msft_2023)
    save_piotroski_result(conn, msft_piotroski)
    conn.commit()


def test_dossier_for_one_company_never_includes_another_companys_scores(aapl_client, msft_client):
    conn = connect(":memory:")
    _seed_both_companies(conn, aapl_client, msft_client)

    aapl_dossier = build_company_dossier(conn, AAPL_CIK, "Apple Inc.")
    msft_dossier = build_company_dossier(conn, MSFT_CIK, "Microsoft Corporation")

    assert aapl_dossier.cik == AAPL_CIK
    assert aapl_dossier.latest_altman is None  # real, honest gap — not MSFT's score
    assert aapl_dossier.entity_name == "Apple Inc."

    assert msft_dossier.cik == MSFT_CIK
    assert msft_dossier.latest_altman is not None
    assert msft_dossier.latest_altman["fiscal_year"] == 2025
    assert msft_dossier.entity_name == "Microsoft Corporation"

    # The actual contamination bug this project fixed: verify explicitly
    # that AAPL's dossier data quality issues are AAPL's, not MSFT's.
    aapl_issue_concepts = {i["concept"] for i in aapl_dossier.data_quality_issues}
    assert "retained_earnings" in aapl_issue_concepts
    conn.close()


def test_dossier_source_facts_carry_only_the_requested_companys_accession(aapl_client, msft_client):
    conn = connect(":memory:")
    _seed_both_companies(conn, aapl_client, msft_client)

    msft_dossier = build_company_dossier(conn, MSFT_CIK, "Microsoft Corporation")
    assert msft_dossier.source_facts  # non-empty
    assert all(f["accession_number"] == "0000950170-25-100235" for f in msft_dossier.source_facts)
    conn.close()


def test_dossier_flags_risk_factor_evidence_availability_correctly(aapl_client, msft_client):
    conn = connect(":memory:")
    _seed_both_companies(conn, aapl_client, msft_client)

    assert build_company_dossier(conn, AAPL_CIK, "Apple Inc.").has_risk_factor_evidence is True
    assert build_company_dossier(conn, MSFT_CIK, "Microsoft Corporation").has_risk_factor_evidence is True
    # A company with no fixture registered must say so explicitly.
    other = build_company_dossier(conn, "0000000042", "Some Other Co")
    assert other.has_risk_factor_evidence is False
    conn.close()


def test_dossier_ratio_history_and_trends_are_real_and_company_isolated(aapl_client, msft_client):
    """ADR-013: the expanded ratio engine's multi-year history and trend
    classifications must be scoped by CIK exactly like every other dossier
    field -- MSFT's 3-year current-ratio history must never leak into
    Apple's dossier, and vice versa."""
    conn = connect(":memory:")
    _seed_both_companies(conn, aapl_client, msft_client)

    msft_dossier = build_company_dossier(conn, MSFT_CIK, "Microsoft Corporation")
    aapl_dossier = build_company_dossier(conn, AAPL_CIK, "Apple Inc.")

    # MSFT has 3 real fiscal years of stored facts (2023-2025) -> 3 years
    # of ratio history; Apple has exactly 1 (2025 only).
    assert sorted(msft_dossier.ratio_history.keys()) == [2023, 2024, 2025]
    assert sorted(aapl_dossier.ratio_history.keys()) == [2025]

    # current_ratio is computable for MSFT in all 3 years -> a real,
    # non-INSUFFICIENT_DATA trend with 3 points.
    msft_cr_trend = msft_dossier.metric_trends["current_ratio"]
    assert msft_cr_trend["direction"] != "insufficient_data"
    assert len(msft_cr_trend["points"]) == 3
    assert [p[0] for p in msft_cr_trend["points"]] == [2023, 2024, 2025]

    # Apple has only 1 fiscal year stored -> current_ratio trend must be
    # honestly INSUFFICIENT_DATA, never fabricated from a single point.
    assert aapl_dossier.metric_trends["current_ratio"]["direction"] == "insufficient_data"

    # Isolation: MSFT's real current-ratio values must never appear as
    # Apple's, and Apple's real (below-1.0) current ratio must never
    # appear as MSFT's.
    msft_values = {v for _, v in msft_cr_trend["points"]}
    aapl_current_ratio = aapl_dossier.current_ratios["ratios"]
    aapl_cr_value = next(r["value"] for r in aapl_current_ratio if r["name"] == "current_ratio")
    assert aapl_cr_value not in msft_values
    conn.close()


def test_dossier_risk_rating_is_deterministic_and_company_specific(aapl_client, msft_client):
    conn = connect(":memory:")
    _seed_both_companies(conn, aapl_client, msft_client)

    msft_dossier = build_company_dossier(conn, MSFT_CIK, "Microsoft Corporation")
    assert msft_dossier.risk_rating["tier"] == "moderate"  # real grey-zone Altman, current ratio > 1.0
    assert "Altman" in msft_dossier.risk_rating["basis"]

    # Apple has no computable Altman/Piotroski for FY2025 -> insufficient_data,
    # never a fabricated tier borrowed from Microsoft's real rating.
    aapl_dossier = build_company_dossier(conn, AAPL_CIK, "Apple Inc.")
    assert aapl_dossier.risk_rating["tier"] == "insufficient_data"
    conn.close()


def test_dossier_for_company_with_no_stored_data_is_explicit_not_a_crash():
    conn = connect(":memory:")
    dossier = build_company_dossier(conn, "0000000042", "Nobody Yet Inc.")
    assert dossier.latest_altman is None
    assert dossier.latest_piotroski is None
    assert dossier.current_fiscal_year is None
    assert dossier.data_quality_issues == []
    assert dossier.source_facts == []
    assert "No deterministic score" in dossier.overall_risk_note
    assert dossier.current_ratios is None
    assert dossier.ratio_history == {}
    assert dossier.risk_rating["tier"] == "insufficient_data"
    conn.close()
