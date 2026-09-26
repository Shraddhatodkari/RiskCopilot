"""
Company-scoped risk-factor evidence registry (ADR-012, generalized under
ADR-013).

Real gap this closes: before this module existed, the dashboard's Agentic
Narrative tab loaded `aapl_fy2025_risk_factors.json` unconditionally,
regardless of which company was actually selected — so choosing Microsoft
in the Company Scores tab still generated a "risk narrative" grounded in
Apple's real filing text. That is exactly the cross-company contamination
this project's own quality bar forbids: using another company's evidence
"because it happens to exist locally" is not acceptable even when the
narrative technically passes the grounding critic (the critic verifies a
citation resolves to *a* retrieved chunk — it has no way to know the chunk
belongs to the wrong company, because nothing enforced that link before).

ADR-013 generalization: this is no longer a hardcoded `{cik: filename}`
Python dict. It auto-discovers every `*_risk_factors.json` file under
`tests/fixtures/` (this project's curated, real, provenance-carrying
evidence — see each file's own `_provenance` field) AND under
`data/evidence_cache/` (where `src/ingestion/filing_document_client.py`'s
live extractor, or the dashboard's own caching of a freshly-fetched
company's evidence, can drop a new one at runtime), keyed by the `cik`
field written INSIDE each file. Registering a new company's evidence is
now "add a real fixture/cache file," not "edit a Python dict and redeploy
code" — the concrete proof this project is not architecturally limited to
Apple and Microsoft: `tests/fixtures/nvda_fy2025_risk_factors.json` (a
third, independently real company, added the same way) is picked up with
zero changes to this file.

This module remains the single enforcement point: a caller can only get a
`TfidfRiskFactorIndex` for a CIK this project has genuine, real,
provenance-carrying filing text for. If it doesn't, the caller gets an
explicit `None` — never a substitute from a different company.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.ingestion.filing_document_client import EXTRACTOR_VERSION
from src.retrieval.models import RiskFactorChunk
from src.retrieval.tfidf_index import TfidfRiskFactorIndex, load_chunks_from_fixture

_FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures"
_LIVE_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "evidence_cache"

_FIXTURE_GLOB = "*_risk_factors.json"


class EvidenceIntegrityError(RuntimeError):
    """Raised for exactly the class of bug this module exists to prevent:
    two different evidence files claiming the same CIK (registered_ciks()
    would then be ambiguous about which is authoritative), or a chunk
    whose own `cik` doesn't match the file it was loaded from."""


def _discover_evidence_files() -> list[Path]:
    files: list[Path] = []
    for directory in (_FIXTURES_DIR, _LIVE_CACHE_DIR):
        if directory.is_dir():
            files.extend(sorted(directory.glob(_FIXTURE_GLOB)))
    return files


def _is_stale_live_cache(path: Path, data: dict) -> bool:
    """A live-cache file written by an older Item 1A extractor is not
    trusted. Extractor version 1 took the last "Item 1A" mention in a
    filing as the section start, which in real 10-Ks (Alphabet, Apple,
    NVIDIA, Coca-Cola, Amazon, ...) is often a cross-reference, and so
    cached whole back halves of filings — financial statements, exhibit
    indexes, signatures — as "risk factors". Such a file is ignored here,
    so the company shows as having no evidence and the next "Analyze a new
    company" run re-fetches it with the current extractor. Curated
    fixtures under tests/fixtures/ are not live-extracted and are exempt."""
    if path.parent.resolve() != _LIVE_CACHE_DIR.resolve():
        return False
    return int(data.get("extractor_version", 1)) < EXTRACTOR_VERSION


def _build_registry() -> dict[str, Path]:
    """CIK -> evidence file path, built fresh from whatever real evidence
    files currently exist on disk. Rebuilt on every call (there are only a
    handful of files; this is not a hot path) so a file added mid-session
    (e.g. a freshly cached live fetch) is picked up immediately without
    restarting the process."""
    registry: dict[str, Path] = {}
    for path in _discover_evidence_files():
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue  # not a real evidence file; skip rather than crash the dashboard
        cik = data.get("cik")
        if not cik:
            continue
        if _is_stale_live_cache(path, data):
            continue
        if cik in registry and registry[cik] != path:
            raise EvidenceIntegrityError(
                f"Two different evidence files both claim CIK {cik}: "
                f"{registry[cik]} and {path}. Refusing to pick one arbitrarily — "
                f"that ambiguity is exactly the risk this registry exists to close."
            )
        registry[cik] = path
    return registry


def evidence_available(cik: str) -> bool:
    """Whether this project has real, company-specific filing evidence for
    this CIK. Callers MUST check this (or handle the None from
    `load_evidence_index`) and show an explicit "no evidence available"
    state rather than ever falling back to a different company's text."""
    return cik in _build_registry()


def load_evidence_index(cik: str) -> TfidfRiskFactorIndex | None:
    """Real, provenance-carrying risk-factor chunks for this exact CIK, or
    None if none exist yet. Never returns another company's evidence."""
    registry = _build_registry()
    path = registry.get(cik)
    if path is None:
        return None

    fixture = json.loads(path.read_text())
    if fixture["cik"] != cik:
        # Defensive: _build_registry() already keys by the file's own cik
        # field, so this should be unreachable — kept as a hard assertion
        # against exactly the contamination class this module prevents.
        raise EvidenceIntegrityError(
            f"Evidence file {path} is registered under CIK {cik} but its own "
            f"cik field says {fixture['cik']!r} — refusing to use it."
        )

    chunks: list[RiskFactorChunk] = load_chunks_from_fixture(fixture)
    # Second defense-in-depth check: every individual chunk must also
    # carry the requested CIK (load_chunks_from_fixture always stamps the
    # fixture-level cik onto every chunk today, but this assertion keeps
    # that invariant enforced even if that loader changes later).
    assert all(c.cik == cik for c in chunks), "chunk.cik must match the requested company"

    return TfidfRiskFactorIndex(chunks)


def registered_ciks() -> list[str]:
    """CIKs this project currently has real filing evidence for — used by
    the dashboard to tell the user plainly which companies support the
    Agentic Narrative tab today."""
    return list(_build_registry().keys())
