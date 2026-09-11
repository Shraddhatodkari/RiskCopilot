"""
Local, CPU-only retrieval over real filing text, using TF-IDF + cosine
similarity (scikit-learn) rather than a transformer sentence-embedding
model.

ADR-010 (see docs/02_architecture_decision_record.md) documents why: a
transformer embedding model (`sentence-transformers`) was the original
Phase 3 plan, but installing it during development pulled in PyTorch as a
transitive dependency — measured directly in this environment at 3+
minutes and still incomplete when the install was cancelled, versus 10
seconds for the entire `scikit-learn` + `numpy` + `scipy` stack this module
actually uses. For a project whose own hard constraint is "must run on an
old, resource-constrained laptop," shipping a multi-hundred-megabyte deep
learning runtime to embed a few dozen paragraphs of risk-factor text per
company is disproportionate to the problem, so the decision was revised —
consistent with the project's own instruction to change an earlier
decision when it turns out to be weak, rather than defend it because code
was already planned around it.

TF-IDF is a legitimate, classic information-retrieval technique here, not
a downgrade in disguise: risk-factor and MD&A text is short (a few dozen
paragraphs per filing, not millions of documents), heavily keyword-driven
(a query like "supply chain single-source component risk" shares
distinctive vocabulary directly with the relevant passage), and the result
needs to be exact and explainable for a due-diligence citation — TF-IDF
retrieval is deterministic and fully inspectable (which exact terms drove
the match), whereas a dense embedding's similarity score is not. If a
future phase's real usage shows TF-IDF missing paraphrased/semantic
matches that keyword overlap can't catch, ADR-010 documents the upgrade
path (a small, CPU-friendly ONNX sentence-embedding model) as a considered
alternative, not a foreclosed one.
"""
from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from src.retrieval.models import RetrievedChunk, RiskFactorChunk


class TfidfRiskFactorIndex:
    def __init__(self, chunks: list[RiskFactorChunk]):
        if not chunks:
            raise ValueError("cannot build an index over zero chunks")
        self._chunks = chunks
        self._vectorizer = TfidfVectorizer(stop_words="english")
        corpus = [f"{c.heading}. {c.text}" for c in chunks]
        self._matrix = self._vectorizer.fit_transform(corpus)

    def search(self, query: str, top_k: int = 3) -> list[RetrievedChunk]:
        query_vector = self._vectorizer.transform([query])
        scores = cosine_similarity(query_vector, self._matrix)[0]
        ranked = sorted(
            zip(self._chunks, scores), key=lambda pair: pair[1], reverse=True
        )
        return [
            RetrievedChunk(chunk=chunk, score=float(score))
            for chunk, score in ranked[:top_k]
            if score > 0.0  # never return a chunk with zero lexical overlap
        ]


def load_chunks_from_fixture(fixture: dict) -> list[RiskFactorChunk]:
    """Build RiskFactorChunk objects from the JSON shape used by
    tests/fixtures/*_risk_factors.json (and, in Phase 3.x, by the real SEC
    full-text-search-backed loader that will replace this for live use)."""
    return [
        RiskFactorChunk(
            chunk_id=c["chunk_id"],
            cik=fixture["cik"],
            entity_name=fixture["entity_name"],
            fiscal_year=fixture["fiscal_year"],
            accession_number=fixture["accession_number"],
            source_document_url=fixture["source_document_url"],
            heading=c["heading"],
            text=c["text"],
        )
        for c in fixture["chunks"]
    ]
