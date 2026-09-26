"""
Tests for the deterministic Item 1A HTML extractor (ADR-013,
src/ingestion/filing_document_client.py). `extract_item_1a_chunks` is a
pure function tested here against a CONSTRUCTED HTML fixture built to
match real 10-K structure (a table-of-contents block mentioning "Item 1A"
right next to "Item 1B", followed by the real section with bold
sub-headings immediately followed by body paragraphs, ending at "Item
1B"). This project's own sandbox cannot fetch a live company's raw HTML
bytes (see the module's own docstring for why) — this is a verified-
against-representative-structure test, stated honestly, not a live-network
integration test. The live network half (`fetch_10k_primary_document_html`)
is a thin, directly-inspectable function and is not separately mocked
here for the same reason `SecEdgarClient`'s live calls aren't (see
docs/03_data_provenance.md).
"""
from __future__ import annotations

from src.ingestion.filing_document_client import extract_item_1a_chunks

_REALISTIC_10K_HTML = """
<html><body>
<p>TABLE OF CONTENTS</p>
<p>Item 1. Business ... 3</p>
<p>Item 1A. Risk Factors ... 8</p>
<p>Item 1B. Unresolved Staff Comments ... 22</p>
<p>Item 2. Properties ... 23</p>

<p>PART I</p>
<p><b>Item 1A. Risk Factors</b></p>
<p>The following risks could materially affect our business, financial condition, and results of
operations, and should be considered carefully in evaluating an investment in our securities.</p>

<p><b>Intense competition could harm our business.</b></p>
<p>We compete with many companies in every market we serve, and some of these competitors have
greater financial, technical, and marketing resources than we do, which could reduce our market
share and profitability over time.</p>

<p><b>Cyberattacks and security breaches could disrupt our operations.</b></p>
<p>Threat actors continuously attempt to gain unauthorized access to our systems through malware,
phishing, and other cyber-attacks, and a successful breach could harm our reputation and result in
significant remediation costs.</p>

<p><b>x</b></p>
<p>Too short.</p>

<p>Item 1B. Unresolved Staff Comments</p>
<p>None.</p>
</body></html>
"""


def test_extracts_only_the_item_1a_section_not_the_whole_document():
    chunks = extract_item_1a_chunks(
        _REALISTIC_10K_HTML, cik="0000000001", entity_name="Test Co",
        fiscal_year=2025, accession_number="0000000001-25-000001",
        source_document_url="https://example.invalid/10k.htm",
    )
    all_text = " ".join(c.text for c in chunks)
    assert "Unresolved Staff Comments" not in all_text
    assert "TABLE OF CONTENTS" not in all_text


def test_table_of_contents_item_1a_reference_is_skipped_not_used_as_the_section():
    """The ToC lists "Item 1A" right next to "Item 1B" with almost no real
    content between them — the extractor must skip past that and find the
    REAL section instead of returning a near-empty result."""
    chunks = extract_item_1a_chunks(
        _REALISTIC_10K_HTML, cik="0000000001", entity_name="Test Co",
        fiscal_year=2025, accession_number="0000000001-25-000001",
        source_document_url="https://example.invalid/10k.htm",
    )
    assert len(chunks) >= 2
    assert any("compete" in c.text.lower() for c in chunks)


def test_bold_sub_headings_are_attached_to_the_correct_following_chunk():
    chunks = extract_item_1a_chunks(
        _REALISTIC_10K_HTML, cik="0000000001", entity_name="Test Co",
        fiscal_year=2025, accession_number="0000000001-25-000001",
        source_document_url="https://example.invalid/10k.htm",
    )
    competition_chunk = next(c for c in chunks if "compete" in c.text.lower())
    assert "competition" in competition_chunk.heading.lower()

    cyber_chunk = next(c for c in chunks if "malware" in c.text.lower())
    assert "cyberattacks" in cyber_chunk.heading.lower() or "security" in cyber_chunk.heading.lower()


def test_body_shorter_than_the_minimum_length_produces_no_stray_chunk():
    """A tiny fragment like "Too short." (well under the documented
    minimum real-content length) must never become its own chunk — it's
    far more likely to be layout noise than a real risk factor."""
    chunks = extract_item_1a_chunks(
        _REALISTIC_10K_HTML, cik="0000000001", entity_name="Test Co",
        fiscal_year=2025, accession_number="0000000001-25-000001",
        source_document_url="https://example.invalid/10k.htm",
    )
    assert not any(c.text.strip() == "" for c in chunks)
    assert not any("Too short" in c.text for c in chunks)


def test_every_chunk_carries_full_real_provenance():
    chunks = extract_item_1a_chunks(
        _REALISTIC_10K_HTML, cik="0000000001", entity_name="Test Co",
        fiscal_year=2025, accession_number="0000000001-25-000001",
        source_document_url="https://example.invalid/10k.htm",
    )
    for c in chunks:
        assert c.cik == "0000000001"
        assert c.entity_name == "Test Co"
        assert c.fiscal_year == 2025
        assert c.accession_number == "0000000001-25-000001"


def test_missing_item_1a_returns_empty_list_not_a_guess():
    chunks = extract_item_1a_chunks(
        "<html><body><p>Just some unrelated document text.</p></body></html>",
        cik="0000000001", entity_name="Test Co", fiscal_year=2025,
        accession_number="0000000001-25-000001", source_document_url="https://example.invalid/x.htm",
    )
    assert chunks == []


def test_chunk_ids_are_unique_and_company_prefixed():
    chunks = extract_item_1a_chunks(
        _REALISTIC_10K_HTML, cik="0000000001", entity_name="Test Co",
        fiscal_year=2025, accession_number="0000000001-25-000001",
        source_document_url="https://example.invalid/10k.htm",
    )
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))
    assert all(cid.startswith("test-2025-live-") for cid in ids)


def test_cache_evidence_writes_a_file_the_evidence_registry_can_discover(tmp_path):
    """End-to-end proof that a freshly-extracted company's evidence
    becomes usable through the SAME registry as the curated fixtures,
    with zero code changes -- this is the concrete mechanism behind
    ADR-013's "not hardcoded to Apple/Microsoft" claim."""
    import src.reporting.evidence_registry as registry_module
    from src.ingestion.filing_document_client import cache_evidence

    chunks = extract_item_1a_chunks(
        _REALISTIC_10K_HTML, cik="0000000099", entity_name="Live Fetched Co",
        fiscal_year=2025, accession_number="0000000099-25-000001",
        source_document_url="https://example.invalid/10k.htm",
    )
    assert chunks

    cache_evidence(
        chunks, cik="0000000099", entity_name="Live Fetched Co", fiscal_year=2025,
        accession_number="0000000099-25-000001",
        source_document_url="https://example.invalid/10k.htm", cache_dir=tmp_path,
    )

    monkeypatched_dirs = (registry_module._FIXTURES_DIR, registry_module._LIVE_CACHE_DIR)
    try:
        registry_module._LIVE_CACHE_DIR = tmp_path
        registry_module._FIXTURES_DIR = tmp_path / "does-not-exist"
        assert registry_module.evidence_available("0000000099") is True
        index = registry_module.load_evidence_index("0000000099")
        assert index is not None
        results = index.search("compete", top_k=10)
        assert results
        assert all(r.chunk.cik == "0000000099" for r in results)
    finally:
        registry_module._FIXTURES_DIR, registry_module._LIVE_CACHE_DIR = monkeypatched_dirs


class _FakeSubmissionsClient:
    """Fixture-backed fake implementing the two methods
    fetch_10k_primary_document_html needs — same duck-typed pattern
    tests/conftest.py already uses for get_company_concept."""

    def __init__(self, submissions: dict, documents: dict[str, str]):
        self._submissions = submissions
        self._documents = documents

    def get_submissions(self, cik: str) -> dict:
        return self._submissions

    def get_document_html(self, cik: str, accession_number: str, filename: str) -> str:
        return self._documents[filename]


def test_fetch_10k_primary_document_html_resolves_the_right_filename_from_submissions():
    from src.ingestion.filing_document_client import fetch_10k_primary_document_html

    submissions = {
        "filings": {
            "recent": {
                "accessionNumber": ["0000000001-24-000099", "0000000001-25-000001"],
                "form": ["10-Q", "10-K"],
                "primaryDocument": ["q3.htm", "annual-report.htm"],
            }
        }
    }
    client = _FakeSubmissionsClient(submissions, {"annual-report.htm": "<html>real 10-K body</html>"})

    html = fetch_10k_primary_document_html(client, "0000000001", "0000000001-25-000001")
    assert html == "<html>real 10-K body</html>"


def test_fetch_10k_primary_document_html_raises_clearly_for_unknown_accession():
    from src.ingestion.filing_document_client import fetch_10k_primary_document_html

    submissions = {"filings": {"recent": {"accessionNumber": ["0000000001-24-000099"],
                                            "form": ["10-Q"], "primaryDocument": ["q3.htm"]}}}
    client = _FakeSubmissionsClient(submissions, {})

    import pytest
    with pytest.raises(ValueError, match="not found"):
        fetch_10k_primary_document_html(client, "0000000001", "0000000001-99-999999")


# ---------------------------------------------------------------------------
# Regression tests for the real Alphabet FY2025 failure (and the same defect
# found in Apple, NVIDIA, Coca-Cola, Amazon, JPMorgan, Microsoft, Tesla and
# J&J 10-Ks). See tests/fixtures/inline_xbrl_10k_structure.html for exactly
# which real structures that fixture reproduces.
# ---------------------------------------------------------------------------
from pathlib import Path  # noqa: E402

from src.ingestion.filing_document_client import MAX_CHUNK_CHARS  # noqa: E402

_INLINE_XBRL_HTML = (
    Path(__file__).parent.parent / "fixtures" / "inline_xbrl_10k_structure.html"
).read_text()


def _inline_chunks(html_text: str = _INLINE_XBRL_HTML):
    return extract_item_1a_chunks(
        html_text, cik="0001652044", entity_name="Alphabet Inc.",
        fiscal_year=2025, accession_number="0001652044-26-000018",
        source_document_url="https://example.invalid/goog-20251231.htm",
    )


def test_cross_reference_after_the_section_is_not_mistaken_for_the_heading():
    """The original extractor took the LAST "Item 1A" mention as the heading.
    In the real Alphabet 10-K that was a cross-reference in a later section,
    so the "risk factors" evidence became financial statements, the exhibit
    index (10.01) and signatures — one 172,261-character chunk."""
    chunks = _inline_chunks()
    text = " ".join(c.text for c in chunks)
    assert chunks
    assert "advertis" in text.lower() and "competition" in text.lower()
    for leaked in ("10.01", "EXHIBIT INDEX", "SIGNATURES", "Securities Exchange Act",
                   "Management's discussion", "forward-looking statements"):
        assert leaked not in text, leaked


def test_section_stops_at_item_1b_and_never_includes_item_1c():
    text = " ".join(c.text for c in _inline_chunks())
    assert "must never appear" not in text
    assert "UNRESOLVED STAFF COMMENTS" not in text
    assert "cybersecurity program description" not in text


def test_inline_styled_bold_headings_are_detected():
    """Modern inline-XBRL filings mark headings with font-weight:700 spans,
    not <b>/<strong>; the old extractor found no headings at all."""
    headings = {c.heading for c in _inline_chunks()}
    assert not any(h.startswith("Risk Factor ") for h in headings)
    assert any(h.startswith("We face intense competition") for h in headings)


def test_long_risk_titles_are_headings_not_silently_dropped():
    """Real risk titles run past the old 150-character heading limit; the old
    code discarded such bold text entirely (neither heading nor body)."""
    chunks = _inline_chunks()
    advertising = next(c for c in chunks if "Advertisers can terminate" in c.text)
    assert len(advertising.heading) > 150
    assert advertising.heading.startswith("We generate most of our revenues from advertising")


def test_running_headers_page_numbers_and_toc_links_are_removed():
    chunks = _inline_chunks()
    text = " ".join(c.text for c in chunks)
    assert "Table of Contents" not in text
    assert "Alphabet Inc." not in {c.heading for c in chunks}
    # The sentence broken across the page (with "21." and the header in the
    # middle) is rejoined without the page furniture.
    assert "our margins over time, and could require us" in text


def test_every_chunk_is_bounded_and_no_filing_text_is_lost():
    """A long body is split on paragraph boundaries; every sentence of the
    section survives exactly once."""
    long_para = "<div><span>" + " ".join(
        f"Sentence {i} describes a distinct liquidity risk in detail." for i in range(120)
    ) + "</span></div>"
    html_text = _INLINE_XBRL_HTML.replace(
        "<div><span style=\"font-style:italic;font-weight:700\">Cyberattacks",
        long_para + "<div><span style=\"font-style:italic;font-weight:700\">Cyberattacks",
    )
    chunks = _inline_chunks(html_text)
    assert max(len(c.text) for c in chunks) <= MAX_CHUNK_CHARS
    text = " ".join(c.text for c in chunks)
    for i in range(120):
        assert text.count(f"Sentence {i} describes") == 1


def test_non_breaking_space_entity_in_item_heading_is_recognized():
    """Real filings (e.g. Amazon's) write the heading as "Item&#160;1A."."""
    html_text = _INLINE_XBRL_HTML.replace(
        '<span style="font-weight:700">ITEM 1A.</span>',
        '<span style="font-weight:700">Item&#160;1A.</span>',
    ).replace(
        '<span style="font-weight:700">ITEM 1B.</span>',
        '<span style="font-weight:700">Item&#160;1B.</span>',
    )
    chunks = _inline_chunks(html_text)
    assert any("Advertisers can terminate" in c.text for c in chunks)
    assert "must never appear" not in " ".join(c.text for c in chunks)


def test_section_with_no_closing_item_heading_is_rejected_not_run_to_end():
    """With no Item 1B/1C/2 heading after Item 1A, the extractor must return
    nothing rather than treat the rest of the filing as risk factors."""
    html_text = (
        '<html><body><div><span style="font-weight:700">ITEM 1A. RISK FACTORS</span></div>'
        '<div><span>We face risks that could harm our business and financial condition in many ways '
        'described here at length for the purpose of this test.</span></div>'
        '<div><span style="font-weight:700">EXHIBIT INDEX</span></div>'
        '<div><span>10.01 Form of Indemnification Agreement, incorporated by reference.</span></div>'
        '</body></html>'
    )
    assert _inline_chunks(html_text) == []
