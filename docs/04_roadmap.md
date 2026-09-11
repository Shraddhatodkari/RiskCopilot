# Roadmap

Each phase is scoped to add a meaningfully testable capability, not to pad
the timeline. A phase is not considered done until its own quality gate
(tests passing, docs updated, no known unresolved correctness issue) is
met — consistent with the project's quality-gate requirement. Time
estimates assume part-time, focused solo development consistent with a
6-7 month upskilling window; they are planning estimates, not commitments.

**Status as of this writing: Phases 0-8 all complete.** This section is
kept in its original phase-by-phase form (rather than collapsed into a
single "done" line) because the original plan-vs-actual record — including
the things that were deliberately descoped — is itself part of this
project's honesty commitment.

## Phase 0 — Landscape research & project selection (DONE)
Independent research into AI+finance opportunities, MBB relevance, and
real data sources; selection of the flagship problem. See
`docs/01_project_selection_and_landscape.md`.

## Phase 1 — Foundation: real data ingestion + deterministic risk engine (DONE)
- SEC EDGAR XBRL client with rate limiting, retries, mandatory User-Agent.
- Tag-fallback fact extraction with honest, explicit handling of missing/
  ambiguous data (no fabrication) — validated against two real companies
  with genuinely different data-quality profiles.
- Altman Z'-Score, deterministic, unit- and fixture-tested against real
  filings, cross-checked against multiple independent published sources.
- CLI producing a sourced, cited risk memo.
**Quality gate met**: unit tests passing against real captured SEC data;
formula independently re-derived in a standalone script; one real bug
(fiscal-year tie-breaking ambiguity) found and fixed during testing,
documented in this repo's history rather than hidden.

## Phase 2 — Broaden the deterministic layer + persistence (DONE)
- Piotroski F-Score (9-signal profitability/leverage/efficiency model,
  requires 2 fiscal years of data) — implemented, unit-tested against a
  real 3-fiscal-year MSFT fixture covering both a complete happy path
  (FY2024 vs FY2023) and a genuine missing-data case (FY2025).
- SQLite persistence layer (`src/persistence/storage.py`, ADR-007) storing
  every ingested `FactPoint`, computed score, and recorded data-quality
  issue, with `INSERT OR REPLACE` idempotency on natural keys.
- FRED integration (`src/ingestion/fred_client.py`) for macro credit-spread
  context, read alongside — never blended into — company scores (ADR-009).
- Ticker→CIK resolution against SEC's full `company_tickers.json`
  (`src/ingestion/ticker_lookup.py`), with local caching and a fallback set.
- **Descoped, deliberately, and stated here rather than hidden**: the
  Beneish M-Score (earnings-manipulation risk model) originally planned for
  this phase was not built. Reason: it requires reliable multi-year COGS,
  receivables, PP&E, SG&A, and depreciation figures, which — consistent
  with the honest data-quality findings already surfacing elsewhere in this
  project (e.g. the `long_term_debt` gaps documented in
  `docs/03_data_provenance.md`) — proved to be exactly the kind of
  input-heavy model where rushing it to hit a phase deadline would have
  meant either fabricating missing inputs (forbidden by this project's core
  constraint) or shipping a model that raises `InsufficientDataError` on
  most real companies and therefore demonstrates little. Given a fixed time
  budget, Phase 4's historical backtest and Phase 3's agentic layer were
  judged higher-value uses of the remaining time — a genuine engineering
  tradeoff, not an oversight. Building Beneish properly remains a
  well-scoped, real future-work item.
**Quality gate met**: `tests/unit/test_piotroski.py`,
`tests/unit/test_storage.py`, `tests/unit/test_fred_client.py`,
`tests/unit/test_ticker_lookup.py` all passing against real captured data;
one real bug (shares-outstanding unit mismatch — `units.shares` vs.
`units.USD`) found and fixed with a regression test.

## Phase 3 — Grounded qualitative layer (agentic, per ADR-006) (DONE)
- TF-IDF + cosine-similarity retrieval (`src/retrieval/tfidf_index.py`)
  over real Apple 10-K risk-factor text — chosen over transformer
  sentence-embeddings after empirically hitting the hardware constraint
  (see ADR-010: a `sentence-transformers` install pulling in PyTorch timed
  out after 3+ minutes on this project's own target-hardware-conscious
  environment).
- LLM narrative generation (`src/agentic/narrative.py`), strictly grounded
  in retrieved passages and Phase 1/2 deterministic scores via mandatory
  inline citations.
- Deterministic (non-LLM) grounding critic (`src/agentic/critic.py`) —
  chosen over a second LLM-based critic call (the alternative this ADR
  originally left open) because a regex-based check against the *actual*
  set of retrieved chunk ids/metric names cannot itself hallucinate,
  closing exactly the failure mode a second LLM call would only reduce.
- Human approval state machine (`src/agentic/approval.py`): a memo cannot
  reach `APPROVED` status if the critic failed, unless a human reviewer
  supplies an explicit, recorded override reason.
- **Phase 3.1 update (ADR-011): free, local real LLM backend via Ollama.**
  The default real `LLMClient` implementation is now `OllamaLLMClient`
  (`src/agentic/llm_client.py`), calling a locally-running Ollama server —
  zero API cost, no API key, filing text never leaves the machine.
  `AnthropicLLMClient` remains in the codebase as an alternative pluggable
  implementation of the same protocol but is not used by default anywhere.
  This required zero changes to retrieval, the grounding critic, or the
  approval workflow — exactly the payoff of designing `LLMClient` as a
  minimal, swappable protocol back in Phase 3's original design.
- **Known, stated limitation, not hidden**: no live LLM call has been made
  *during this project's own development*, because this project's cloud
  development sandbox cannot reach a server on a user's own machine's
  localhost — fabricating a "captured" LLM output to look like a real
  result would violate this project's own no-fabrication rule more
  seriously than leaving the gap visible. Instead, `OllamaLLMClient`'s
  HTTP request/response handling is verified end-to-end against a real
  local HTTP server implementing Ollama's actual API contract
  (`tests/unit/test_ollama_client.py`), and the full narrative pipeline is
  additionally tested against a scripted `FakeLLMClient` that can be told
  to hallucinate on command — proving the *governance* logic actually
  catches bad output regardless of which concrete client produced it.
  `scripts/verify_ollama_connection.py` is the artifact a user runs on
  their own machine (with Ollama installed) for the final, real-model
  verification this sandbox cannot perform. The dashboard (Phase 6)
  detects Ollama automatically and uses the real model by default when
  reachable, falling back to a clearly-labeled scripted demo only when it
  is not.
**Quality gate met**: `tests/unit/test_tfidf_retrieval.py`,
`tests/unit/test_critic.py`, `tests/unit/test_narrative_and_approval.py`
all passing, including adversarial scripted-hallucination cases that
verify the critic actually catches both a fabricated citation and an
uncited numeric claim.

## Phase 4 — Historical backtest & evaluation (DONE)
- Real, documented Chapter 11 bankruptcy cases (Bed Bath & Beyond, Party
  City) with real filing dates, run through the unmodified
  `compute_altman_z_prime` function.
- Honest reporting of both a hit (Party City, 382 days lead time on its
  earlier filing) and a genuine miss (Bed Bath & Beyond's FY2021 10-K,
  scored "safe" ~14 months before its actual bankruptcy, due to a
  retained-earnings artifact from historical share buybacks).
- A real data-extraction error (a FY2020/FY2021 comparative-figure mixup
  for Party City) caught and corrected during fixture assembly by
  cross-checking against complete raw XBRL JSON — documented as a finding
  about manual-extraction risk, not quietly fixed and forgotten.
- Full write-up: `docs/06_historical_backtest.md`.
**Quality gate met**: `tests/unit/test_backtest.py` passing with
independently pre-computed expected Z' values (verified by hand before the
test was written, per this project's evaluation-honesty commitment); one
real arithmetic slip in a hand-typed test assertion (322 vs. the correct
382 days) was itself caught and fixed via independent date-math
verification.
**Stated limitation**: N=2 companies, 4 snapshots. This is real evidence
that the methodology and the model both work as intended, not a
statistically powered accuracy claim — a larger backtest with a
non-bankrupt control group is the single highest-value future-work item
for this evaluation (see `docs/06_historical_backtest.md`'s own
limitations section).

## Phase 5 — Security, reliability, and performance hardening (DONE)
- Closed a real, previously-untested gap: the SEC EDGAR client's
  retry/backoff logic (flagged as untested since Phase 1's
  `docs/05_testing_strategy.md`) now has a dedicated 6-test suite
  (`tests/unit/test_sec_edgar_client_retry.py`) using a scripted fake
  `requests.Session` — and writing it surfaced a real bug: a persistent
  network error survived retries but was re-raised unwrapped instead of as
  a `SecEdgarError`, breaking the exception contract callers rely on.
  Found and fixed in this phase.
- Dependency vulnerability scan via `pip-audit`: found and fixed one real
  CVE (`pytest 8.4.2`, `PYSEC-2026-1845`) by upgrading the pinned range to
  `pytest>=9.0.3`; a second scan after the fix confirmed a clean result
  across every declared dependency.
- Full self-review: `docs/07_security_review.md` — SQL injection defenses
  (verified: every query is parameterized), prompt-injection defenses for
  the Phase 3 LLM layer (two independent layers: instruction + deterministic
  critic), secrets handling, and network-client hardening.
- Performance: `scripts/benchmark.py`, run on this development sandbox with
  real, printed timing numbers (not assumed) for every CPU-bound step in
  the pipeline (snapshot assembly, Altman/Piotroski computation, TF-IDF
  index build/query, SQLite read/write) — all sub-2ms per operation on this
  machine. Explicitly and honestly scoped: this sandbox cannot reach
  data.sec.gov, so real end-to-end network latency on the user's actual
  target laptop is not and cannot be measured here; the script is written
  to be re-run on that hardware, with its own docstring stating this
  limitation rather than implying the sandbox numbers generalize.

## Phase 6 — Executive dashboard (DONE)
- `dashboard.py`: a local Streamlit app (ADR-002-consistent: no JS build
  toolchain) with three views — company Altman/Piotroski score history and
  data-quality issues (read from SQLite, computed entirely by the
  already-tested `src/analysis` modules), the Phase 4 historical backtest
  results, and a clearly-labeled demo of the Phase 3 agentic layer using
  the scripted `FakeLLMClient` (never presented as a real model output).
- `scripts/seed_dashboard_data.py` populates the database with real,
  already-verified fixture-derived results so the dashboard has genuine
  data to show before a user configures their own SEC/FRED credentials.
- Verified with Streamlit's `AppTest` harness (headless, no browser) to
  confirm the app renders all three tabs with no exception.
- **Phase 6.1 update (ADR-012): fixed a real cross-company-contamination
  bug and hardened company isolation end to end.** A user-reported defect
  confirmed real: the Agentic Narrative tab loaded a hardcoded company's
  risk evidence/metrics regardless of which company was actually selected
  elsewhere in the dashboard. Root-caused to there being no single,
  company-scoped read path — fixed by introducing `CompanyDossier`
  (`src/reporting/company_dossier.py`), a single function that assembles
  everything a selected company's dashboard view needs, and rewriting
  `dashboard.py` so every tab reads exclusively from it. Evidence lookups
  now go through an explicit `CIK -> fixture` registry
  (`src/reporting/evidence_registry.py`) with integrity checks that raise
  rather than silently substitute another company's data; a company with
  no registered evidence shows an explicit unavailable state instead of
  falling back to someone else's. In the same pass: fixed
  `save_snapshot()` so a data-quality issue that is genuinely resolved by
  a later run no longer persists as a stale warning, and no longer
  duplicates on repeated ingestion; fixed `list_companies()` to include a
  company with real ingested facts but no computable score (previously
  invisible in the picker); and added `src/reporting/live_ingest.py` so
  the dashboard can analyze any ticker/CIK, not only the two seeded
  companies, without duplicating `src/cli.py`'s logic. Full rationale in
  ADR-012; verification in `docs/08_final_audit.md`.
**Quality gate met**: `tests/unit/test_company_dossier.py`,
`tests/unit/test_evidence_registry.py`, `tests/unit/test_live_ingest.py`
(new) and 8 new tests in `tests/unit/test_storage.py`, all passing;
rewritten `dashboard.py` re-verified with `AppTest` switching between
Apple and Microsoft with no cross-company leakage in any tab.
**Stated limitation (as of Phase 6/7; superseded by Phase 8 below)**: real,
registered risk-factor evidence existed for exactly two companies (Apple,
Microsoft) at this point; a live-analyzed company outside that set
correctly showed "no evidence available" rather than borrowing another
company's text — see ADR-012. Phase 8 (ADR-013) closes this by
generalizing the registry to a directory scan and adding a real,
automated fetch-and-cache path for any company's evidence.

## Phase 7 — Final documentation & enterprise audit (DONE)
- This roadmap and `README.md` updated to reflect actual, verified
  completion status of every phase (this document).
- `docs/02_architecture_decision_record.md` completed with the two ADRs
  (ADR-009: macro context kept separate from scores; ADR-010: TF-IDF over
  transformer embeddings) that earlier phases' code already referenced but
  that had not yet been written up — closed in this phase rather than left
  as dangling references.
- `requirements.txt` corrected to include every dependency actually used
  (`scikit-learn`, `numpy`, `scipy`, `streamlit`) and the security-patched
  `pytest` range — it had drifted out of sync with the real, growing
  codebase during Phases 3-6, a real gap this phase closes rather than
  papers over.
- Final independent audit and full regression test run (see
  `docs/08_final_audit.md`) before packaging.

## Phase 8 — Company-agnostic risk-intelligence expansion (DONE)
- Fixed the stale-derived-score lifecycle bug: a previously-valid Altman/
  Piotroski result is now explicitly invalidated (deleted, with a recorded
  data-quality reason) the moment a recomputation attempt for that same
  fiscal year genuinely fails, rather than continuing to be read back as
  if it were still current. See ADR-013 §1.
- Expanded deterministic ratio engine (`src/analysis/financial_ratios.py`):
  11 liquidity/leverage/profitability/cash-flow ratios, each independently
  honest about availability — never all-or-nothing. See ADR-013 §2.
- Historical multi-year trend classification
  (`src/analysis/trend.py`) and a deterministic overall risk tier
  (`src/analysis/risk_rating.py`, the 🟢🟡🟠🔴 rating) — both fully
  documented, fixed rule tables, no LLM involved. See ADR-013 §3-4.
- Generalized filing-risk-factor evidence from a hardcoded 2-company dict
  to a directory-scanning registry, plus a deterministic risk-factor
  categorizer (`src/ingestion/risk_categorizer.py`) and a deterministic
  live Item 1A HTML extractor (`src/ingestion/filing_document_client.py`)
  wired into the dashboard's "Analyze a new company" flow. Proved this
  generalizes with a genuinely independent third company — NVIDIA
  Corporation — added with zero changes to the registry/ratio/trend/
  rating code. See ADR-013 §5.
- `dashboard.py` gained Risk Overview, Financial Metrics, and Historical
  Trends tabs, and category tags on the Filing Risk Intelligence tab — all
  reading exclusively from the existing company-scoped `CompanyDossier`.
  See ADR-013 §6.
**Quality gate met**: `tests/unit/test_financial_ratios.py`,
`tests/unit/test_risk_rating.py`, `tests/unit/test_trend.py`,
`tests/unit/test_risk_categorizer.py`, `tests/unit/test_filing_document_
client.py`, and `tests/unit/test_nvda_third_company.py` are new; 4 new
tests in `tests/unit/test_storage.py` for the stale-score fix; 4 new tests
in `tests/unit/test_live_ingest.py` for evidence fetching;
`tests/unit/test_dashboard_isolation.py` extended to three companies.
Full test count, pass/fail breakdown, and the Section-20-style final
checklist: `docs/09_final_verification_report.md`.
**Explicitly descoped, stated honestly rather than silently dropped**: a
dedicated peer-comparison tab, PDF report export, "what-if" scenario
analysis, and free-form filing Q&A/RAG chat. See ADR-013 §7 and
"Future work" below.

## Future work (not built, stated honestly rather than silently dropped)

- **Peer comparison tab**: `CompanyDossier` already produces fully
  company-isolated data for any company, so a side-by-side view (e.g.
  NVIDIA vs. AMD vs. Intel) is additive — call `build_company_dossier` for
  each CIK and render them in adjacent columns. Not built in Phase 8
  because doing it well means also sourcing real evidence/fixtures for
  enough peer companies to make the comparison meaningful, which was
  judged lower-value than finishing the ratio/trend/risk-tier engine and
  the stale-score fix within the available time.
- **PDF report export**: every dashboard section already renders from
  structured, real data (`CompanyDossier`) — a PDF export would format the
  same data, not compute anything new. Deferred rather than rushed with an
  unstyled/undertested export path.
- **"What-if" scenario analysis** (e.g. "revenue falls 15%"): would need a
  clearly-separated, unmistakably-labeled hypothetical-input mode layered
  on top of the existing deterministic ratio functions (which already
  accept a `FinancialSnapshot` — a hypothetical one could be constructed
  the same way real historical ones are in `build_snapshot_from_stored_
  facts`). Not built because getting the "this is not real data" labeling
  and isolation from the real-data code paths right needs its own careful
  design pass, not a bolt-on.
- **Free-form filing Q&A / RAG chat**: the TF-IDF retrieval
  (`src/retrieval/tfidf_index.py`) and the grounding critic
  (`src/agentic/critic.py`) already exist and already ground every claim
  in a retrieved chunk — a chat interface would be a new UI over the same
  retrieval-then-generate-then-critic pipeline the Agentic Narrative tab
  already uses, not new grounding infrastructure. Deferred as a UI-only
  addition, lower priority than the deterministic-engine work in Phase 8.
- **Beneish M-Score**: descoped in Phase 2 (see above) and still not
  built — the same real, input-heavy data-quality constraint applies.
- **A larger, statistically powered distress backtest**: still N=2
  companies / 4 snapshots (Phase 4's stated limitation, unchanged by
  Phase 8) — a larger real backtest with a non-bankrupt control group
  remains the single highest-value future item for the evaluation layer.
