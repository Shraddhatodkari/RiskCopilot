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
   metadata, locates the real "Item 1A" section, strips markup and page
   furniture, splits it into bounded paragraph-level chunks, and attaches
   the preceding bold risk title to each chunk. Fully unit-testable without
   any network call — see tests/unit/test_filing_document_client.py.

   Verification boundary, stated plainly: on 2026-09-26 the extractor was
   run against nine real FY2025 10-Ks fetched live from SEC EDGAR (Alphabet,
   Amazon, Apple, Coca-Cola, Johnson & Johnson, JPMorgan Chase, Microsoft,
   NVIDIA, Tesla); each produced 34-83 correctly headed chunks of at most
   MAX_CHUNK_CHARS with no exhibit-index or signature text. The previous
   version failed on all nine (see docs/09_final_verification_report.md
   §1f). Nine filers is evidence, not proof: an unusual filing template can
   still defeat the heading heuristics, in which case this returns [] (an
   honest miss) rather than guessed text.

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
# Real risk-factor titles are often a full sentence or two (e.g. Alphabet's
# ~290-character advertising risk title), so the heading limit must allow
# that. Bold text longer than this is kept as BODY text, never discarded.
_MAX_HEADING_CHARS = 400
# Upper bound on one chunk's body. A risk factor longer than this is split
# on paragraph boundaries into several chunks sharing its heading, so no
# single chunk can swamp retrieval scoring or a local model's context
# window (a whole-section chunk of ~170k characters was the root cause of
# an off-topic narrative — see docs/09_final_verification_report.md §1f).
MAX_CHUNK_CHARS = 2000

# Bumped whenever extraction logic changes in a way that invalidates
# previously cached live evidence; see evidence_registry.py, which ignores
# live-cache files written by an older extractor instead of trusting them.
EXTRACTOR_VERSION = 2

# The space between "Item" and the number is often a non-breaking-space
# entity in real filings (e.g. Amazon's "Item&#160;1A."), not a plain space.
_ITEM_GAP = r"(?:\s|&#160;|&#xa0;|&nbsp;|\xa0)*"
_ITEM_1A_PATTERN = re.compile(r"item" + _ITEM_GAP + r"1a\b", re.IGNORECASE)
# Item 1C (Cybersecurity) exists in 10-Ks filed since late 2023 and can
# directly follow Item 1A when a filer omits Item 1B.
_NEXT_ITEM_PATTERN = re.compile(r"item" + _ITEM_GAP + r"(1b|1c|2)\b", re.IGNORECASE)
_TAG_PATTERN = re.compile(r"<[^>]+>")
# Bold runs: <b>/<strong>, or the inline-styled spans modern inline-XBRL
# 10-Ks actually use (e.g. <span style="...font-weight:700...">).
_BOLD_LINE_PATTERN = re.compile(
    r"<(b|strong)\b[^>]*>(?P<tagged>.*?)</\1>"
    r"|<span\b[^>]*font-weight\s*:\s*(?:bold|[6-9]00)\b[^>]*>(?P<styled>.*?)</span>",
    re.IGNORECASE | re.DOTALL,
)
_BLOCK_TAG_PATTERN = re.compile(r"<(?:div|p|td|th|tr|li|h[1-6]|br|table|body)\b[^>]*>", re.IGNORECASE)
_BLOCK_BREAK_PATTERN = re.compile(r"</?(?:div|p|td|th|tr|li|h[1-6]|br|table)\b[^>]*>", re.IGNORECASE)
_PART_PREFIX_PATTERN = re.compile(r"part\s+[ivx]+\W*", re.IGNORECASE)
_RISK_FACTORS_PATTERN = re.compile(r"risk\s+factors", re.IGNORECASE)
_WHITESPACE_PATTERN = re.compile(r"[ \t\r\f\v\xa0]+")
_SENTENCE_END_PATTERN = re.compile(r"(?<=[.!?])\s+")
_MAX_FURNITURE_CHARS = 60  # running headers/footers are short (e.g. "Table of Contents")
_PAGE_NUMBER_PATTERN = re.compile(r"(?:page\s*)?(?:\d{1,4}|[ivxlc]{1,6})\.?", re.IGNORECASE)


class _ConceptClient(Protocol):
    def get_submissions(self, cik: str) -> dict: ...
    def get_document_html(self, cik: str, accession_number: str, filename: str) -> str: ...


def _strip_tags(fragment: str) -> str:
    return html.unescape(_TAG_PATTERN.sub(" ", fragment)).strip()


def _plain(fragment: str) -> str:
    return html.unescape(_TAG_PATTERN.sub("", fragment)).replace("\xa0", " ").strip()


def _starts_a_block(full_html: str, pos: int) -> bool:
    """True if the text at `pos` begins its own block (paragraph, div,
    table cell...), i.e. nothing but tags/whitespace (or a "PART I" label)
    sits between the most recent block-level tag and `pos`. Section
    headings start a block; cross-references ("...see Item 1A Risk Factors
    of this Annual Report") sit mid-sentence and do not."""
    window = full_html[max(0, pos - 3000):pos]
    last_block = None
    for last_block in _BLOCK_TAG_PATTERN.finditer(window):
        pass
    between = window[last_block.end():] if last_block is not None else window
    text = _plain(between)
    return text == "" or _PART_PREFIX_PATTERN.fullmatch(text) is not None


def _followed_by_risk_factors(full_html: str, pos: int) -> bool:
    return _RISK_FACTORS_PATTERN.search(_plain(full_html[pos:pos + 600])[:80]) is not None


def _isolate_item_1a_html(full_html: str) -> str | None:
    """Return the raw HTML of the REAL Item 1A section: from an "Item 1A"
    heading up to (not including) the next "Item 1B"/"Item 1C"/"Item 2"
    heading. None if no such bounded section exists — an honest miss, never
    a guess.

    Real 10-Ks mention "Item 1A" many times: in the table of contents, in
    the forward-looking-statements note, and in cross-references throughout
    later sections ("see Item 1A Risk Factors"). The previous rule ("the
    LAST occurrence is the heading") picked such a cross-reference in a
    real Alphabet FY2025 10-K and, finding no next-item heading after it,
    returned everything to the end of the filing — financial statements,
    exhibit index and signatures. Instead:

    1. candidate starts are occurrences that begin their own block and are
       immediately followed by "Risk Factors" (headings, not prose);
    2. candidate ends are next-item occurrences that begin their own block;
    3. each start is paired with the first end after it; a start with no
       end is rejected rather than run to the end of the document;
    4. the longest bounded span wins — the table-of-contents entry spans
       one line, the real section spans the whole risk-factor discussion.
    """
    starts = [
        m.start() for m in _ITEM_1A_PATTERN.finditer(full_html)
        if _starts_a_block(full_html, m.start()) and _followed_by_risk_factors(full_html, m.start())
    ]
    ends = [
        m.start() for m in _NEXT_ITEM_PATTERN.finditer(full_html)
        if _starts_a_block(full_html, m.start())
    ]
    best: tuple[int, int] | None = None
    for start in starts:
        end = next((e for e in ends if e > start + 10), None)
        if end is None:
            continue
        if best is None or (end - start) > (best[1] - best[0]):
            best = (start, end)
    if best is None:
        return None
    # Begin at the block element that contains the heading, so the heading's
    # own opening (bold) tag is included and it is recognized as a heading.
    start = best[0]
    window_start = max(0, start - 3000)
    last_block = None
    for last_block in _BLOCK_TAG_PATTERN.finditer(full_html, window_start, start):
        pass
    if last_block is not None:
        start = last_block.start()
    return full_html[start:best[1]]


def _block_texts(raw_html: str) -> list[str]:
    return [
        _WHITESPACE_PATTERN.sub(" ", _strip_tags(p)).strip()
        for p in _BLOCK_BREAK_PATTERN.split(raw_html)
    ]


def _page_furniture(section_html: str) -> set[str]:
    """Short blocks that repeat on 3+ pages of the section (running headers
    and footers such as "Table of Contents" or the company name) — layout,
    not risk-factor content."""
    counts: dict[str, int] = {}
    for text in _block_texts(section_html):
        if text and len(text) <= _MAX_FURNITURE_CHARS:
            counts[text] = counts.get(text, 0) + 1
    return {text for text, n in counts.items() if n >= 3}


def _split_long_body(raw_segment: str, furniture: frozenset[str] = frozenset()) -> list[str]:
    """Split one heading's body into pieces of at most MAX_CHUNK_CHARS,
    breaking on paragraph (block) boundaries and, for a single paragraph
    that is itself too long, on sentence boundaries. Page furniture (bare
    page numbers and the running headers/footers in `furniture`) is
    dropped; the filing's own sentences are never rewritten or reordered."""
    units: list[str] = []
    for p in _block_texts(raw_segment):
        if not p or p in furniture or _PAGE_NUMBER_PATTERN.fullmatch(p):
            continue
        if len(p) <= MAX_CHUNK_CHARS:
            units.append(p)
            continue
        sentence_run = ""
        for sentence in _SENTENCE_END_PATTERN.split(p):
            if sentence_run and len(sentence_run) + 1 + len(sentence) > MAX_CHUNK_CHARS:
                units.append(sentence_run)
                sentence_run = ""
            sentence_run = f"{sentence_run} {sentence}".strip()
            while len(sentence_run) > MAX_CHUNK_CHARS:  # a single sentence longer than the cap
                units.append(sentence_run[:MAX_CHUNK_CHARS])
                sentence_run = sentence_run[MAX_CHUNK_CHARS:]
        if sentence_run:
            units.append(sentence_run)

    pieces: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + 1 + len(unit) > MAX_CHUNK_CHARS:
            pieces.append(current)
            current = ""
        current = f"{current} {unit}".strip()
    if current:
        pieces.append(current)
    return pieces


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

    furniture = frozenset(_page_furniture(section_html))
    bold_counts: dict[str, int] = {}
    for m in _BOLD_LINE_PATTERN.finditer(section_html):
        text = _plain(m.group("tagged") if m.group("tagged") is not None else m.group("styled"))
        bold_counts[text] = bold_counts.get(text, 0) + 1
    running_headers = {
        text for text, n in bold_counts.items() if n >= 3 and len(text) <= _MAX_FURNITURE_CHARS
    }
    if running_headers:
        # A bold line repeated on 3+ pages (e.g. the company name at the top
        # of each page) is a running header, not a risk-factor heading.
        section_html = _BOLD_LINE_PATTERN.sub(
            lambda m: "" if _plain(
                m.group("tagged") if m.group("tagged") is not None else m.group("styled")
            ) in running_headers else m.group(0),
            section_html,
        )

    # A "heading" candidate is a bolded run of text; everything between two
    # headings becomes that heading's body. Adjacent bold runs separated only
    # by markup/whitespace (e.g. "ITEM 1A." + "RISK FACTORS" in two spans)
    # are merged into one heading. Bold text too long to be a heading is
    # kept as body text rather than dropped.
    pieces: list[tuple[str | None, str]] = []  # (heading_or_None, raw_html_segment)
    last_end = 0
    last_heading: str | None = None
    pending_heading = ""
    for m in _BOLD_LINE_PATTERN.finditer(section_html):
        preceding = section_html[last_end:m.start()]
        bold_html = m.group("tagged") if m.group("tagged") is not None else m.group("styled")
        bold_text = _WHITESPACE_PATTERN.sub(" ", _strip_tags(bold_html)).strip()
        same_block = _BLOCK_BREAK_PATTERN.search(preceding) is None
        if _plain(preceding) == "" and pending_heading and same_block:
            candidate = f"{pending_heading} {bold_text}".strip()
        else:
            if _plain(preceding):
                if pending_heading:
                    last_heading = pending_heading
                pieces.append((last_heading, preceding))
            elif pending_heading:
                last_heading = pending_heading
            candidate = bold_text
        if 0 < len(candidate) <= _MAX_HEADING_CHARS:
            pending_heading = candidate
        else:
            # Too long to be a heading: keep it as body text of the current
            # heading instead of losing it.
            if pending_heading:
                last_heading = pending_heading
                pending_heading = ""
            if bold_text:
                pieces.append((last_heading, m.group(0)))
        last_end = m.end()
    if pending_heading:
        last_heading = pending_heading
    tail = section_html[last_end:]
    if _plain(tail):
        pieces.append((last_heading, tail))

    # Merge consecutive segments under the same heading, then split any
    # body longer than MAX_CHUNK_CHARS on paragraph boundaries.
    merged: list[tuple[str | None, str]] = []
    for heading, raw_segment in pieces:
        if merged and merged[-1][0] == heading:
            merged[-1] = (heading, merged[-1][1] + "\n<div>" + raw_segment)
        else:
            merged.append((heading, raw_segment))

    chunks: list[RiskFactorChunk] = []
    seq = 0
    prefix = entity_name.split()[0][:4].lower() or "co"
    for heading, raw_segment in merged:
        for body in _split_long_body(raw_segment, furniture):
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
        "extractor_version": EXTRACTOR_VERSION,
        "chunks": [
            {"chunk_id": c.chunk_id, "heading": c.heading, "text": c.text} for c in chunks
        ],
    }
    path.write_text(json.dumps(payload, indent=2))
    return path
