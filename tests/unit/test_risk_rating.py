"""
Tests for the deterministic overall risk-tier classifier (ADR-013,
src/analysis/risk_rating.py). Takes plain primitives (zone string,
F-Score int, current-ratio float) — exactly the shape `CompanyDossier`
already carries as dicts read back from SQLite — rather than full
ZScoreResult/PiotroskiResult objects (see the module's own docstring for
why). The liquidity-flag bump is exercised against a REAL below-1.0
current ratio computed from Apple's actual FY2025 filing data (see
test_financial_ratios.py), not a synthetic number.
"""
from __future__ import annotations

from src.analysis.altman_z import compute_altman_z_prime
from src.analysis.financial_ratios import compute_financial_ratios
from src.analysis.risk_rating import RiskTier, classify_risk_tier
from src.ingestion.xbrl_facts import build_financial_snapshot


def test_both_unavailable_is_insufficient_data_not_a_guess():
    rating = classify_risk_tier(None, None)
    assert rating.tier == RiskTier.INSUFFICIENT_DATA
    assert "No tier is assigned" in rating.basis


def test_altman_safe_alone_gives_low_tier():
    rating = classify_risk_tier("safe", None)
    assert rating.tier == RiskTier.LOW


def test_altman_distress_dominates_piotroski_strong():
    """The classifier takes the WORSE of the two model-implied tiers --
    a distress-zone Altman score must not be masked by a strong Piotroski
    score."""
    rating = classify_risk_tier("distress", 9)
    assert rating.tier == RiskTier.HIGH


def test_piotroski_weak_dominates_altman_safe():
    rating = classify_risk_tier("safe", 0)
    assert rating.tier == RiskTier.HIGH


def test_grey_and_moderate_piotroski_gives_moderate():
    rating = classify_risk_tier("grey", 5)
    assert rating.tier == RiskTier.MODERATE


def test_real_below_one_current_ratio_bumps_tier_up_one_level(aapl_client):
    """Apple's real FY2025 current ratio (0.89, from live SEC data) is
    exactly the case this liquidity-flag rule exists for: a company that
    would otherwise be rated LOW gets bumped to MODERATE, and the bump is
    stated explicitly in `basis`, not silently applied."""
    snapshot = build_financial_snapshot(aapl_client, "0000320193", "Apple Inc.", 2025)
    ratios = compute_financial_ratios(snapshot)
    current_ratio = ratios.get("current_ratio").value
    assert current_ratio < 1.0  # sanity: real data, not assumed

    rating = classify_risk_tier("safe", None, current_ratio)
    assert rating.tier == RiskTier.MODERATE
    assert rating.liquidity_flag is True
    assert "current ratio" in rating.basis
    assert "bumped" in rating.basis


def test_high_tier_is_not_bumped_further_it_is_already_the_ceiling():
    rating = classify_risk_tier("distress", None, current_ratio=0.5)
    assert rating.tier == RiskTier.HIGH


def test_real_msft_end_to_end_matches_manually_derived_expectation(msft_client):
    """A real, non-synthetic end-to-end check: MSFT's real Altman zone is
    grey (see test_storage.py) and its real current ratio (1.35) is above
    1.0, so no liquidity bump should apply."""
    snapshot = build_financial_snapshot(msft_client, "0000789019", "Microsoft Corporation", 2025)
    altman = compute_altman_z_prime(snapshot)
    ratios = compute_financial_ratios(snapshot)
    current_ratio = ratios.get("current_ratio").value

    rating = classify_risk_tier(altman.zone.value, None, current_ratio)
    assert rating.tier == RiskTier.MODERATE
    assert rating.liquidity_flag is False
