"""
Tests for the deterministic risk-factor categorizer (ADR-013), run
against REAL, verbatim-quoted filing text from all three of this
project's evidence fixtures (Apple, Microsoft, NVIDIA) — not synthetic
sentences constructed to make the categorizer look good. Several of these
tests exist because they caught real bugs during this module's own
development (documented inline) — kept as permanent regression tests.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.ingestion.risk_categorizer import RISK_CATEGORIES, categorize_chunk

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _chunks(fixture_name: str) -> list[dict]:
    return json.loads((FIXTURES_DIR / fixture_name).read_text())["chunks"]


def test_every_returned_category_is_in_the_documented_taxonomy():
    for fixture in ("aapl_fy2025_risk_factors.json", "msft_fy2025_risk_factors.json",
                     "nvda_fy2025_risk_factors.json"):
        for chunk in _chunks(fixture):
            for cat in categorize_chunk(chunk["heading"], chunk["text"]):
                assert cat in RISK_CATEGORIES


def test_apple_supply_chain_and_geopolitical_chunk_is_tagged_correctly():
    chunk = next(c for c in _chunks("aapl_fy2025_risk_factors.json") if c["chunk_id"] == "aapl-2025-rf-2")
    cats = categorize_chunk(chunk["heading"], chunk["text"])
    assert "geopolitical" in cats
    assert "supply_chain" in cats


def test_msft_cyberattacks_heading_matches_despite_plural_word_form():
    """Real regression: the categorizer's first word-boundary-safe version
    required an EXACT word match, which broke on MSFT's real heading
    'Cyberattacks and Security Vulnerabilities' (plural) against a
    singular 'cyberattack' keyword. Fixed by listing both forms
    explicitly rather than reintroducing an unsafe bare substring
    match — this test locks that fix in."""
    chunk = next(c for c in _chunks("msft_fy2025_risk_factors.json") if c["chunk_id"] == "msft-2025-rf-3")
    assert "cybersecurity" in categorize_chunk(chunk["heading"], chunk["text"])


def test_nvda_malware_text_does_not_false_positive_as_geopolitical():
    """Real regression: a naive substring check on the keyword "war" (for
    geopolitical risk, e.g. armed conflict) matched inside the word
    "malware" in NVIDIA's real cybersecurity chunk. Word-boundary matching
    fixes this; this test locks the fix in against the actual real text
    that exposed the bug."""
    chunk = next(c for c in _chunks("nvda_fy2025_risk_factors.json") if c["chunk_id"] == "nvda-2025-rf-4")
    assert "malware" in chunk["text"].lower()
    cats = categorize_chunk(chunk["heading"], chunk["text"])
    assert "cybersecurity" in cats
    assert "geopolitical" not in cats


def test_nvda_export_control_chunk_is_tagged_geopolitical_and_regulation():
    chunk = next(c for c in _chunks("nvda_fy2025_risk_factors.json") if c["chunk_id"] == "nvda-2025-rf-5")
    cats = categorize_chunk(chunk["heading"], chunk["text"])
    assert "geopolitical" in cats
    assert "regulation" in cats


def test_nvda_customer_concentration_chunk_is_tagged_correctly():
    chunk = next(c for c in _chunks("nvda_fy2025_risk_factors.json") if c["chunk_id"] == "nvda-2025-rf-6")
    assert "customer_concentration" in categorize_chunk(chunk["heading"], chunk["text"])


def test_uncategorized_chunk_returns_empty_list_not_a_guess():
    """A chunk about macroeconomic conditions in general terms, with none
    of this taxonomy's specific keywords, must come back uncategorized —
    never forced into the nearest guess."""
    cats = categorize_chunk(
        "Macroeconomic and Industry Risks",
        "Our results are affected by macroeconomic conditions worldwide, including inflation, "
        "interest rates, and slower economic growth.",
    )
    assert cats == []


def test_matching_is_case_insensitive():
    assert "cybersecurity" in categorize_chunk("CYBERSECURITY", "A CYBER-ATTACK could occur.")


def test_short_keyword_does_not_match_inside_an_unrelated_longer_word():
    """'ai' is a real, intentional keyword (for AI/technology risk) but
    must not match merely because it appears as a substring inside an
    unrelated word like 'contains' or 'certain'."""
    cats = categorize_chunk("Heading", "This section contains certain forward-looking statements about air travel.")
    assert "technology_ai" not in cats
