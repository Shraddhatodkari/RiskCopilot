"""
Tests for TF-IDF retrieval over real Apple FY2025 10-K risk-factor text
(tests/fixtures/aapl_fy2025_risk_factors.json — see that file's
"_provenance" field). This is a genuine, working, non-LLM retrieval
component: it must actually rank the right real passage highest for a
realistic due-diligence query, not just "return without crashing."
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.retrieval.tfidf_index import TfidfRiskFactorIndex, load_chunks_from_fixture

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def aapl_index() -> TfidfRiskFactorIndex:
    fixture = json.loads((FIXTURES_DIR / "aapl_fy2025_risk_factors.json").read_text())
    chunks = load_chunks_from_fixture(fixture)
    return TfidfRiskFactorIndex(chunks)


@pytest.mark.parametrize(
    "query,expected_top_chunk_id",
    [
        ("single source component supply constraint pricing risk", "aapl-2025-rf-5"),
        ("unauthorized access confidential personal information breach attacks", "aapl-2025-rf-7"),
        ("tariffs international trade imports exports restrictions", "aapl-2025-rf-2"),
        ("antitrust privacy regulation compliance costs worldwide laws", "aapl-2025-rf-8"),
        ("minority market share smartphone competitors", "aapl-2025-rf-3"),
    ],
)
def test_retrieves_the_correct_real_passage_for_a_realistic_query(
    aapl_index, query, expected_top_chunk_id
):
    results = aapl_index.search(query, top_k=3)
    assert results, "expected at least one result for a query sharing real vocabulary"
    assert results[0].chunk.chunk_id == expected_top_chunk_id
    # Every retrieved chunk must carry full provenance back to the real filing.
    assert results[0].chunk.accession_number == "0000320193-25-000079"
    assert results[0].chunk.source_document_url.startswith("https://www.sec.gov/")


def test_scores_are_ordered_descending(aapl_index):
    results = aapl_index.search("supply chain component sourcing risk", top_k=8)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_irrelevant_query_does_not_force_a_confident_false_match(aapl_index):
    """A query sharing essentially no vocabulary with any real passage
    should not be reported with a misleadingly high similarity score —
    this matters because Phase 3's grounding critic (src/agentic/critic.py)
    treats a retrieved chunk as legitimate supporting evidence, so a
    spuriously high score on an unrelated query would let an LLM justify
    an unrelated claim by citing it."""
    results = aapl_index.search("recipe for chocolate chip cookies", top_k=3)
    for r in results:
        assert r.score < 0.15


def test_empty_chunk_list_rejected():
    with pytest.raises(ValueError):
        TfidfRiskFactorIndex([])
