"""Typed structures for the Phase 3 retrieval layer."""
from __future__ import annotations

from pydantic import BaseModel


class RiskFactorChunk(BaseModel):
    """One retrievable passage of real, filed SEC text, with full
    provenance — the same principle as FactPoint (ADR-004) applied to
    qualitative text instead of a number."""

    chunk_id: str
    cik: str
    entity_name: str
    fiscal_year: int
    accession_number: str
    source_document_url: str
    heading: str
    text: str


class RetrievedChunk(BaseModel):
    chunk: RiskFactorChunk
    score: float
