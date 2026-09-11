"""
Deterministic Item 1A ("Risk Factors") extraction from a real 10-K's
primary HTML document (ADR-013) — the piece that makes company-specific
risk evidence genuinely company-agnostic, rather than limited to whatever
companies happen to have a hand-curated fixture in
`src/reporting/evidence_registry.py`.

Two responsibilities, kept separate on purpose:

1. `fetch_10k_primary_document_html` — a REAL network operation: looks up
   a company's filing in SEC EDGAR's submissions API, then fetches that
   filing's actual primary HTML document. Requires a real, reachable
   `SecEdgarClient`-style HTTP session (see src/ingestion/sec_edgar_client.py)
   — like the rest of this project's live SEC integration, this project's
   own cloud development sandbox cannot reach data.sec.gov/www.sec.gov to
   exercise this function end to end (see docs/03_data_provenance.md); it
   runs for real on the user's own machine, exactly like `python -m
   src.cli`.

2. `extract_item_1a_chunks` — a pure, deterministic function: given HTML
   text (however it was obtained) plus the company's identity/filing
   metadata, locates the "Item 1A" section, strips markup, splits it into
   paragraph-level chunks, and attaches a bold/short-line heading to each
   chunk where one precedes it. This is fully unit-testable without any
   network call — see tests/unit/test_filing_document_client.py, which
   exercises it against a constructed HTML fixture built to match a real
   10-K's actual heading/paragraph structure (bold `<b>`/`<strong>`
   sub-headings immediately followed by body paragraphs — the structure
   real filings, including this project's own curated Apple/Microsoft/
   NVIDIA fixtures, actually use). Stated plainly: this sandbox cannot
   fetch a live company's raw, unprocessed HTML bytes to test this
   function against arbitrary real markup variation, so this is a
   verified-against-representative-structure guarantee, not a
   verified-against-every-real-company's-actual-template one — the same
   kind of verification boundary already documented for
   `OllamaLLMClient` (ADR-011) and the SEC EDGAR client's retry logic.

A chunk of real HTML this function fails to parse usefully (no "Item 1A"
heading found, or no chunk of reasonable length extracted) returns an
empty list — never fabricated or approximated text.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Protocol

from src.retrieval.models import RiskFactorChunk

_MIN_CHUNK_CHARS = 80   # shorter than this is almost always a stray heading/whitespace fragment, not real content
_MAX_HEADING_CHARS = 150  # a candidate heading line longer than this is almost certainly body text, not a heading

_ITEM_1A_PATTERN = re.compile(r"item\s*1a\b", re.IGNORECASE)
_NEXT_ITEM_PATTERN = re.compile(r"item\s*(1b|2)\b", re.IGNORECASE)
_TAG_PATTERN = re.compile(r"<[^>]+>")
_BOLD_LINE_PATTERN = re.compile(r"<(b|strong)[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_WHITESPACE_PATTERN = re.compile(r"[ \t\r\f\v]+")


class _ConceptClient(Protocol):
    def get_submissions(self, cik: str) -> dict: ...
    def get_document_html(self, cik: str, accession_number: str, filename: str) -> str: ...


def _strip_tags(fragment: str) -> str:
    return html.unescape(_TAG_PATTERN.sub(" ", fragment)).strip()


def _isolate_item_1a_html(full_html: str) -> str | None:
    """Return the raw HTML slice from the REAL "Item 1A" heading up to
    (not including) the next "Item 1B"/"Item 2" heading. None if "Item 1A"
    cannot be located at all — an honest miss, not a guess at the whole
    document.

    Real 10-Ks' tables of contents also say "Item 1A" (with a page number,
    no real body). Rather than guess a length threshold to distinguish a
    ToC line from the real section (fragile — a short representative test
    fixture and a genuinely brief real risk-factors section would both
    fail a hand-picked cutoff), this relies on real 10-K document
    structure instead: the table of contents always precedes the body, so
    the LAST "Item 1A" occurrence in the document is the real section
    heading, and everything up to the next "Item 1B"/"Item 2" occurrence
    after THAT is the real section."""
    matches = list(_ITEM_1A_PATTERN.finditer(full_html))
    if not matches:
        return None
    start = matches[-1].start()
    next_item = _NEXT_ITEM_PATTERN.search(full_html, pos=start + 10)
    if next_item is None:
        return full_html[start:]
    return full_html[start:next_item.start()]


def extract_item_1a_chunks(
    full_html: str, *, cik: str, entity_name: str, fiscal_year: int, accession_number: str,
    source_document_url: str,
) -> list[RiskFactorChunk]:
    """Deterministically extract headed, chunked risk-factor passages from
    one company's real 10-K HTML. Returns [] (never a fabricated chunk) if
    "Item 1A" can't be located or no chunk of reasonable length results."""
    section_html = _isolate_item_1a_html(full_html)
    if not section_html:
        return []

    # A "heading" candidate is a bolded run of text; everything between
    # two headings (or between a heading and the next tag-stripped
    # paragraph break) becomes that heading's chunk body.
    pieces: list[tuple[str | None, str]] = []  # (heading_or_None, raw_html_segment)
    last_end = 0
    last_heading: str | None = None
    for m in _BOLD_LINE_PATTERN.finditer(section_html):
        preceding = section_html[last_end:m.start()]
        if preceding.strip():
            pieces.append((last_heading, preceding))
        heading_text = _strip_tags(m.group(2))
        if 0 < len(heading_text) <= _MAX_HEADING_CHARS:
            last_heading = heading_text
        last_end = m.end()
    tail = section_html[last_end:]
    if tail.strip():
        pieces.append((last_heading, tail))

    chunks: list[RiskFactorChunk] = []
    seq = 0
    prefix = entity_name.split()[0][:4].lower() or "co"
    for heading, raw_segment in pieces:
        body = _WHITESPACE_PATTERN.sub(" ", _strip_tags(raw_segment)).strip()
        if len(body) < _MIN_CHUNK_CHARS:
            continue
        seq += 1
        chunks.append(
            RiskFactorChunk(
                chunk_id=f"{prefix}-{fiscal_year}-live-{seq}",
                cik=cik,
                entity_name=entity_name,
                fiscal_year=fiscal_year,
                accession_number=accession_number,
                source_document_url=source_document_url,
                heading=heading or f"Risk Factor {seq}",
                text=body,
            )
        )
    return chunks


def fetch_10k_primary_document_html(client: _ConceptClient, cik: str, accession_number: str) -> str:
    """Real network call: resolve `accession_number`'s primary document
    filename via SEC EDGAR's submissions API, then fetch that document's
    HTML. See this module's own docstring for why this cannot be exercised
    end to end from this project's development sandbox."""
    submissions = client.get_submissions(cik)
    recent = submissions["filings"]["recent"]
    try:
        idx = recent["accessionNumber"].index(accession_number)
    except ValueError as exc:
        raise ValueError(f"Accession {accession_number} not found in {cik}'s recent filings") from exc
    primary_document = recent["primaryDocument"][idx]
    return client.get_document_html(cik, accession_number, primary_document)


def cache_evidence(chunks: list[RiskFactorChunk], cik: str, entity_name: str, fiscal_year: int,
                    accession_number: str, source_document_url: str, cache_dir: Path) -> Path:
    """Write freshly-extracted chunks to `data/evidence_cache/` in the
    same JSON shape as this project's curated `tests/fixtures/
    *_risk_factors.json` files, so `src/reporting/evidence_registry.py`'s
    directory scan picks it up automatically — no registry code change
    needed to support a newly-analyzed company (ADR-013)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_name = entity_name.lower().replace(" ", "_").replace(".", "").replace(",", "")
    path = cache_dir / f"{safe_name}_fy{fiscal_year}_risk_factors.json"
    payload = {
        "_provenance": (
            f"Live-extracted by src/ingestion/filing_document_client.py from {entity_name}'s "
            f"real FY{fiscal_year} 10-K ({source_document_url}), accession {accession_number}, "
            f"on the machine that ran the fetch. Deterministic keyword/markup extraction, "
            f"never LLM-generated or fabricated."
        ),
        "cik": cik,
        "entity_name": entity_name,
        "fiscal_year": fiscal_year,
        "accession_number": accession_number,
        "source_document_url": source_document_url,
        "chunks": [
            {"chunk_id": c.chunk_id, "heading": c.heading, "text": c.text} for c in chunks
        ],
    }
    path.write_text(json.dumps(payload, indent=2))
    return path
