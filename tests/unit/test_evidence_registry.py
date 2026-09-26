"""
Tests for the company-scoped evidence registry (ADR-012) — the fix for a
real cross-company-contamination bug: before this registry existed, the
dashboard used Apple's risk-factor text for whichever company was selected,
including Microsoft.
"""
from __future__ import annotations

import json

import pytest

from src.reporting.evidence_registry import (
    EvidenceIntegrityError,
    evidence_available,
    load_evidence_index,
    registered_ciks,
)

AAPL_CIK = "0000320193"
MSFT_CIK = "0000789019"
NVDA_CIK = "0001045810"


def test_registered_companies_include_all_three_real_evidence_files_today():
    """ADR-013: the registry auto-discovers every real `*_risk_factors.json`
    file under tests/fixtures/ (plus data/evidence_cache/ at runtime) —
    it is not a hardcoded 2-entry map. NVIDIA's real fixture
    (tests/fixtures/nvda_fy2025_risk_factors.json) was added the same way
    Apple/Microsoft's were, with zero changes to evidence_registry.py
    itself, which is the concrete proof of that."""
    assert {AAPL_CIK, MSFT_CIK, NVDA_CIK} <= set(registered_ciks())


def test_evidence_available_true_for_registered_ciks():
    assert evidence_available(AAPL_CIK) is True
    assert evidence_available(MSFT_CIK) is True


def test_evidence_available_false_for_an_unregistered_cik():
    assert evidence_available("0000000001") is False


def test_unregistered_cik_returns_none_never_a_substitute():
    assert load_evidence_index("0000000001") is None


def test_apple_index_contains_only_apple_chunks():
    index = load_evidence_index(AAPL_CIK)
    assert index is not None
    # Note: TF-IDF tokenizes exact word forms (ADR-010's documented
    # limitation) — "regulatory compliance" matches real chunk text;
    # a bare "risk" wouldn't (chunks say "risks", a different token).
    results = index.search("regulatory compliance data security", top_k=100)
    assert results  # non-empty
    assert all(r.chunk.cik == AAPL_CIK for r in results)
    assert all(r.chunk.entity_name == "Apple Inc." for r in results)


def test_microsoft_index_contains_only_microsoft_chunks():
    index = load_evidence_index(MSFT_CIK)
    assert index is not None
    results = index.search("regulatory compliance data security", top_k=100)
    assert results
    assert all(r.chunk.cik == MSFT_CIK for r in results)
    assert all(r.chunk.entity_name == "Microsoft Corporation" for r in results)


def test_nvidia_index_contains_only_nvidia_chunks():
    """The third, independently-added real company (ADR-013) — proves the
    registry generalizes beyond the original two, with the same isolation
    guarantee."""
    index = load_evidence_index(NVDA_CIK)
    assert index is not None
    results = index.search("export regulation competition security", top_k=100)
    assert results
    assert all(r.chunk.cik == NVDA_CIK for r in results)
    assert all(r.chunk.entity_name == "NVIDIA Corporation" for r in results)


def test_apple_microsoft_and_nvidia_evidence_are_all_genuinely_different_text():
    """The real regression test for the bug this module fixes: three
    different companies must retrieve three different, non-overlapping
    sets of real filing text for the same query."""
    aapl_results = load_evidence_index(AAPL_CIK).search("competition regulatory risk", top_k=8)
    msft_results = load_evidence_index(MSFT_CIK).search("competition regulatory risk", top_k=8)
    nvda_results = load_evidence_index(NVDA_CIK).search("competition regulatory risk", top_k=8)

    aapl_texts = {r.chunk.text for r in aapl_results}
    msft_texts = {r.chunk.text for r in msft_results}
    nvda_texts = {r.chunk.text for r in nvda_results}
    assert aapl_texts.isdisjoint(msft_texts)
    assert aapl_texts.isdisjoint(nvda_texts)
    assert msft_texts.isdisjoint(nvda_texts)

    aapl_chunk_ids = {r.chunk.chunk_id for r in aapl_results}
    msft_chunk_ids = {r.chunk.chunk_id for r in msft_results}
    nvda_chunk_ids = {r.chunk.chunk_id for r in nvda_results}
    assert all(cid.startswith("aapl-") for cid in aapl_chunk_ids)
    assert all(cid.startswith("msft-") for cid in msft_chunk_ids)
    assert all(cid.startswith("nvda-") for cid in nvda_chunk_ids)


def test_duplicate_cik_across_two_evidence_files_is_caught_not_silently_resolved(tmp_path, monkeypatch):
    """Defense-in-depth for the generalized, directory-scanning registry
    (ADR-013): if two evidence files ever claim the same CIK, this must
    fail loudly rather than silently pick one and hide the ambiguity."""
    import src.reporting.evidence_registry as registry_module

    def _fixture(cik: str, entity_name: str, chunk_id: str) -> dict:
        return {
            "cik": cik, "entity_name": entity_name, "fiscal_year": 2025,
            "accession_number": "0000000000-00-000000",
            "source_document_url": "https://example.invalid/",
            "chunks": [{"chunk_id": chunk_id, "heading": "H", "text": "T"}],
        }

    (tmp_path / "a_risk_factors.json").write_text(json.dumps(_fixture("0000111111", "Co A", "a-1")))
    (tmp_path / "b_risk_factors.json").write_text(json.dumps(_fixture("0000111111", "Co B", "b-1")))

    monkeypatch.setattr(registry_module, "_FIXTURES_DIR", tmp_path)
    monkeypatch.setattr(registry_module, "_LIVE_CACHE_DIR", tmp_path / "does-not-exist")

    with pytest.raises(EvidenceIntegrityError):
        registry_module.load_evidence_index("0000111111")


def test_evidence_directory_scan_ignores_unrelated_json_files(tmp_path, monkeypatch):
    """A JSON file that doesn't match the *_risk_factors.json naming
    convention, or has no `cik` field, must be silently ignored rather
    than crashing the registry."""
    import src.reporting.evidence_registry as registry_module

    (tmp_path / "not_evidence.json").write_text(json.dumps({"unrelated": True}))
    (tmp_path / "good_risk_factors.json").write_text(json.dumps({
        "cik": "0000222222", "entity_name": "Co C", "fiscal_year": 2025,
        "accession_number": "0000000000-00-000000",
        "source_document_url": "https://example.invalid/",
        "chunks": [{"chunk_id": "c-1", "heading": "H", "text": "T"}],
    }))

    monkeypatch.setattr(registry_module, "_FIXTURES_DIR", tmp_path)
    monkeypatch.setattr(registry_module, "_LIVE_CACHE_DIR", tmp_path / "does-not-exist")

    assert registry_module.evidence_available("0000222222") is True
    assert registry_module.evidence_available("0000999999") is False


import src.reporting.evidence_registry as registry_module  # noqa: E402


def _write_live_cache(directory, extractor_version):
    import json

    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "cik": "0001652044",
        "entity_name": "Alphabet Inc.",
        "fiscal_year": 2025,
        "accession_number": "0001652044-26-000018",
        "source_document_url": "https://example.invalid/goog-20251231.htm",
        "chunks": [{
            "chunk_id": "alph-2025-live-1",
            "heading": "Risk Factor 1",
            "text": "Item 1A, \"Risk Factors\" of this Annual Report ... 10.01 Form of Indemnification Agreement ...",
        }],
    }
    if extractor_version is not None:
        payload["extractor_version"] = extractor_version
    (directory / "alphabet_inc_fy2025_risk_factors.json").write_text(json.dumps(payload))


def test_live_cache_written_by_the_old_extractor_is_ignored(tmp_path, monkeypatch):
    """A user's existing data/evidence_cache/ file from the original
    extractor (no extractor_version: the whole back half of the filing as
    one "Risk Factor 1" chunk) must not keep being served after the fix.
    Ignoring it makes the company show as having no evidence, so the next
    "Analyze a new company" run re-fetches it with the current extractor."""
    live = tmp_path / "evidence_cache"
    _write_live_cache(live, extractor_version=None)
    monkeypatch.setattr(registry_module, "_FIXTURES_DIR", tmp_path / "no-fixtures")
    monkeypatch.setattr(registry_module, "_LIVE_CACHE_DIR", live)
    assert registry_module.evidence_available("0001652044") is False
    assert registry_module.load_evidence_index("0001652044") is None


def test_live_cache_written_by_the_current_extractor_is_used(tmp_path, monkeypatch):
    from src.ingestion.filing_document_client import EXTRACTOR_VERSION

    live = tmp_path / "evidence_cache"
    _write_live_cache(live, extractor_version=EXTRACTOR_VERSION)
    monkeypatch.setattr(registry_module, "_FIXTURES_DIR", tmp_path / "no-fixtures")
    monkeypatch.setattr(registry_module, "_LIVE_CACHE_DIR", live)
    assert registry_module.evidence_available("0001652044") is True


def test_curated_fixtures_are_never_treated_as_stale_live_caches():
    """The curated fixtures carry no extractor_version (they are not
    live-extracted) and must stay registered."""
    assert {"0000320193", "0000789019", "0001045810"} <= set(registry_module.registered_ciks())
