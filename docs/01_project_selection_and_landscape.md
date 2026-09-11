# Phase 0 — Landscape Research and Project Selection

## Purpose of this document

Per the project's operating instructions, the project, business problem,
architecture, and technology stack were not to be chosen by the user — they
were to be researched and decided independently. This document records that
research and the resulting decision, so the reasoning is auditable and
defensible in an interview rather than asserted after the fact.

## What was researched (2026-09-08)

- Current (2026) state of agentic AI adoption in financial services, and
  where it is genuinely delivering value vs. where it is hype — see
  [Agentic AI in Financial Services: A Research Roundup for 2026](https://neurons-lab.com/articles/agentic-ai-in-financial-services-2026/),
  [Agentic AI Updates Reshaping Compliance in 2026 (360factors)](https://www.360factors.com/blog/agentic-ai-updates/),
  [AI Agents in Finance 2026: A CFO Guide to Reality vs Hype (Houseblend)](https://www.houseblend.io/articles/ai-agents-finance-cfo-guide-2026).
- What MBB-style consulting firms are actually doing with AI in 2026, and
  where "technology/AI strategy for finance" work sits in their practice —
  see [2026 Consulting's AI Revolution Update (Future of Consulting)](https://futureofconsulting.ai/ai-leadership/2026-consultings-ai-revolution-update/)
  and [AI is forcing McKinsey, BCG, Bain to rethink consulting fees (TheStreet)](https://www.thestreet.com/markets/ai-is-forcing-mckinsey-bcg-bain-to-rethink-consulting-fees).
- Real, authoritative, freely accessible financial data sources: the SEC
  EDGAR XBRL "company facts"/"company concept" API and the Federal Reserve's
  FRED API — see [SEC EDGAR API rate limits & best practices](https://tldrfiling.com/blog/sec-edgar-api-rate-limits-best-practices),
  the [SEC's own rate-control policy](https://www.sec.gov/filergroup/announcements-old/new-rate-control-limits),
  and the [FRED series/observations API docs](https://fred.stlouisfed.org/docs/api/fred/series_observations.html).
  Both were confirmed reachable and inspected against real, live data before
  any architecture decision was finalized (see
  `docs/03_data_provenance.md`).
- Established, citable deterministic financial-distress models suitable for
  a lightweight, XBRL-driven pipeline (Altman Z-Score family) — cross
  checked across [Wikipedia's Altman Z-score article](https://en.wikipedia.org/wiki/Altman_Z-score),
  [Wall Street Prep](https://www.wallstreetprep.com/knowledge/altman-z-score/),
  and [StableBread](https://stablebread.com/altman-z-score/). This
  cross-checking mattered in practice: a fourth source
  ([CreditGuru](https://www.creditguru.com/index.php/bankruptcy-and-insolvency/altman-z-score-insolvency-predictor-for-non-manufacturers-emerging-markets))
  gave a formula variant with a plausible-looking coefficient typo, which is
  exactly why this project treats "financial correctness" as something to
  verify from multiple independent sources and then test in code, not
  something to assume.

## Candidate directions considered

| Direction | Why considered | Why not chosen as the flagship |
|---|---|---|
| Generic RAG chatbot over financial documents | Easy to build, popular pattern | Explicitly excluded by the project's own constraints; does not demonstrate financial or consulting judgment, only plumbing |
| Stock price prediction / trading signal | High "AI" visibility | Explicitly excluded; requires market-timing claims that are neither honestly testable nor MBB-relevant (MBB does not do proprietary trading) |
| Multi-agent "autonomous portfolio manager" | Trendy agentic AI framing | Multi-agent architecture would not be justified by the task (portfolio construction from public data has no real tool-use or coordination problem that needs multiple agents); high fabrication risk (would require simulating "returns") |
| **Credit / counterparty financial-risk due-diligence copilot** | Matches a real, recurring MBB and corporate-finance workload (credit risk assessment, restructuring diagnostics, M&A financial due diligence); has genuinely authoritative, free, real-time data (SEC XBRL); has a well-established deterministic baseline (Altman/Piotroski/Beneish) that can be tested for correctness independent of any LLM; agentic reasoning is *additive*, not decorative, once qualitative risk-factor extraction and narrative synthesis are added (Phase 3+) | **Selected** |

## Decision

**Project: an AI-assisted Credit & Counterparty Financial-Risk Due-Diligence
Copilot**, built on real SEC EDGAR XBRL filings (and, from Phase 2, FRED
macro data), that:

1. Computes deterministic, formula-verifiable financial-distress and
   earnings-quality indicators directly from a company's real filed
   financial statements (no LLM in this calculation path — see ADR-005).
2. (Phase 3+) Retrieves and grounds qualitative risk signals from the real
   text of a company's MD&A and risk-factor disclosures, with mandatory
   source citation and an automated critic step that rejects any generated
   claim that cannot be traced to a retrieved passage or a computed number.
3. Produces an executive-readable risk memo — the kind of artifact a credit
   analyst, corporate lender, or M&A due-diligence associate would actually
   use — rather than a raw model output.

### Why this fits the stated objectives specifically

- **MBB relevance**: credit risk, restructuring diagnostics, and financial
  due diligence are recurring, real engagement types across corporate
  finance and risk-focused consulting work — this project mirrors that
  workflow rather than inventing a toy scenario.
- **Real data, non-negotiable**: SEC XBRL is authoritative (it is the
  source of truth companies themselves file with the regulator), free, has
  no usage restrictions beyond a fair-use rate limit, and every number is
  independently traceable to a specific filing and accession number — this
  is directly demonstrated in `docs/sample_output.txt`.
- **Hardware/infra fit**: Phase 1 needs nothing beyond `requests`,
  `pydantic`, and SQLite — no GPU, no Docker, no cloud service. Phase 2+
  additions (small local embedding model, a single hosted LLM call for
  narrative generation) were chosen specifically to stay within this
  budget — see `docs/02_architecture_decision_record.md`.
- **Genuine (not decorative) use of agentic AI**: a single LLM call over a
  10-K's full text would hallucinate financial figures routinely — this is
  well documented and is exactly why this project computes every number
  deterministically and reserves the LLM for a narrowly scoped, grounded,
  citation-checked qualitative layer with an explicit critic/validator step
  (Phase 3). That structure is what makes "agentic" a real architectural
  answer here rather than a buzzword — see ADR-006 for the full
  justification and the alternative that was rejected.
- **Measurable, honest results**: Phase 4's evaluation plan is a historical
  backtest — checking whether the deterministic model would have flagged
  companies that are now known, matters of public record, to have
  experienced real financial distress — rather than an unfalsifiable claim
  of "AI accuracy."

## Sources

- [Agentic AI in Financial Services: A Research Roundup for 2026](https://neurons-lab.com/articles/agentic-ai-in-financial-services-2026/)
- [Agentic AI Updates Reshaping Compliance in 2026 — 360factors](https://www.360factors.com/blog/agentic-ai-updates/)
- [AI Agents in Finance 2026: A CFO Guide to Reality vs Hype — Houseblend](https://www.houseblend.io/articles/ai-agents-finance-cfo-guide-2026)
- [2026 Consulting's AI Revolution Update — Future of Consulting](https://futureofconsulting.ai/ai-leadership/2026-consultings-ai-revolution-update/)
- [AI is forcing McKinsey, BCG, Bain to rethink consulting fees — TheStreet](https://www.thestreet.com/markets/ai-is-forcing-mckinsey-bcg-bain-to-rethink-consulting-fees)
- [SEC EDGAR API rate limits & best practices](https://tldrfiling.com/blog/sec-edgar-api-rate-limits-best-practices)
- [SEC rate control announcement](https://www.sec.gov/filergroup/announcements-old/new-rate-control-limits)
- [FRED series/observations API documentation](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)
- [Altman Z-score — Wikipedia](https://en.wikipedia.org/wiki/Altman_Z-score)
- [Altman Z-Score — Wall Street Prep](https://www.wallstreetprep.com/knowledge/altman-z-score/)
- [Altman Z-Score — StableBread](https://stablebread.com/altman-z-score/)
