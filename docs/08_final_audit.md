# Final Independent Audit (Phase 7)

This is the closing self-review the project's own working method commits
to before any "complete" declaration: an adversarial pass across
architecture, financial correctness, security, evaluation, business value,
and interview defensibility — with genuine defects fixed and the full
regression suite re-run, not just asserted clean.

## 1. What was actually re-checked in this pass, and what was found

| Area | Method | Real finding | Resolution |
|---|---|---|---|
| SEC EDGAR client retry/error handling | Wrote the retry test suite `docs/05_testing_strategy.md` had flagged as an open gap since Phase 1 | A persistent network error survived retries but was re-raised unwrapped instead of as `SecEdgarError`, breaking the exception contract callers rely on | Fixed in `src/ingestion/sec_edgar_client.py`; regression test added and passing |
| Dependency vulnerabilities | `pip-audit -r requirements.txt` | `pytest 8.4.2` had a real published CVE (`PYSEC-2026-1845`) | `requirements.txt` raised to `pytest>=9.0.3`; venv upgraded to 9.1.1; re-scanned clean |
| `requirements.txt` accuracy | Diffed against actual imports across `src/`, `dashboard.py`, and the test suite | It had drifted since Phase 1 — missing `scikit-learn`, `numpy`, `scipy` (Phase 3) and `streamlit` (Phase 6) entirely | Rewritten to declare every runtime dependency actually imported, with the security-patched `pytest` range |
| Cross-document references | `grep`'d every `docs/NN_*.md` reference in code/tests against the actual files on disk | `src/evaluation/backtest.py`, `scripts/run_backtest.py`, `tests/unit/test_backtest.py`, and the backtest fixture's own provenance note all pointed at the wrong filename (`docs/04_historical_backtest.md` — a stale number from when it was drafted, before `docs/07_security_review.md` claimed that slot) | All four corrected to `docs/06_historical_backtest.md`; `docs/02_architecture_decision_record.md`'s ADR-009/ADR-010 (referenced from `fred_client.py` and `tfidf_index.py` since Phase 2/3 but never written) closed out in this phase |
| Test coverage completeness | `pytest --cov=src --cov-report=term-missing` | `src/cli.py` — 95 statements, the project's actual user-facing entry point — had **0% coverage**. Every other module had a dedicated test file; the CLI did not. | Added `tests/unit/test_cli.py` (7 tests: config-error exit code, argparse usage error, a full successful Altman run, a mixed Altman-fails/Piotroski-succeeds run, an insufficient-data run, a ticker-lookup failure, and a `--save` persistence round-trip). `src/cli.py` coverage: 0% → 92%. Two of those tests initially failed against my own first-draft assertions (a wrong hostname substring, and an assumed exit code that didn't account for FY2024 genuinely lacking Altman-complete data) — both were test bugs, not code bugs, caught and fixed by actually running the suite rather than trusting the draft. |
| Full regression | `pytest -v --cov=src --cov-report=term-missing` after every fix above | — | **61 passed, 1 deselected** (the deselected test is the live-network SEC integration test, which cannot run in this sandbox — see `docs/03_data_provenance.md`). Overall coverage: **93%** (up from 81% before this phase's `test_cli.py` addition). |
| Script smoke tests | Actually ran every script in `scripts/`, not just its tests | All five (`verify_msft_zscore.py`, `verify_msft_piotroski.py`, `run_backtest.py`, `benchmark.py`, `seed_dashboard_data.py`) and `dashboard.py` (via Streamlit's headless `AppTest` harness) executed cleanly with exit code 0 and no unhandled exception | None needed — confirms the fixes above didn't regress anything downstream |
| SQL injection surface | Manual read of every `conn.execute(...)` call in `src/persistence/storage.py` | None — all 9 calls use `?` parameter placeholders, zero string-built SQL | No fix needed; documented in `docs/07_security_review.md` |
| Secrets in source control | `grep` for `sk-`, `key=`, and inspection of `.gitignore`/`.env.example` | None found; `.env` is git-ignored, `.env.example` has placeholders only | No fix needed |
| TLS verification | `grep -rn "verify=False"` across `src/` | None found | No fix needed |

Six real, previously-undetected defects were found and fixed in this
project across Phases 5 and 7 (the retry-exception bug, the pytest CVE, the
incomplete requirements.txt, four stale doc cross-references treated as one
class of fix, the missing ADR write-ups, and the CLI coverage gap). None
were cosmetic — each would have been a real problem for a user relying on
this project (a masked network-failure exception type, a known-vulnerable
test dependency, an incomplete install, broken documentation links, and an
untested primary entry point, respectively).

## 2. Financial correctness — re-verified, not re-assumed

- Altman Z' and Piotroski F formulas are unchanged since their original
  independent verification (`scripts/verify_msft_zscore.py`,
  `scripts/verify_msft_piotroski.py`, both re-run in this phase with
  identical output: Z'=2.0060/grey, F=5/9).
- Both remain pure Python arithmetic with no LLM in the calculation path
  (ADR-005) — re-confirmed by inspection of `src/analysis/altman_z.py` and
  `src/analysis/piotroski.py`, neither of which imports anything from
  `src/agentic/`.
- The Phase 4 backtest's real, honest miss (Bed Bath & Beyond FY2021) is
  still reported as a miss, not smoothed over — `docs/06_historical_backtest.md`
  and `tests/unit/test_backtest.py` are unchanged in substance.

## 3. Business value and interview defensibility — a direct check

Per this project's own business case (`docs/00_business_case.md`), the
claim is narrow and falsifiable: "does a deterministic, source-cited
distress score flag real, historical bankruptcies with useful lead time?"
The Phase 4 backtest answers this honestly (75% of real pre-event snapshots
flagged, with the one miss's root cause investigated and explained, on a
real but small N=2 sample) rather than overclaiming. An interviewer probing
this project should be able to ask "show me one real bug you found and how
you found it" and get a specific, concrete answer from the table above —
that traceability was a deliberate design goal of keeping this audit
itself as a document, not just a private checklist.

## 4. What remains explicitly unresolved, stated plainly

- **No live LLM call has ever been made in this project.** This is not
  fixable from within this environment (no `ANTHROPIC_API_KEY` is
  available here) and is not being papered over: `README.md`,
  `docs/04_roadmap.md`, and the dashboard's own UI all say this outright.
- **Real end-to-end network/LLM latency is not measured.** `scripts/benchmark.py`
  measures every CPU-bound step honestly and explains exactly why it cannot
  measure the rest from this sandbox.
- **The Phase 4 backtest is N=2.** A larger backtest with a non-bankrupt
  control group remains the single highest-value piece of future work,
  exactly as `docs/06_historical_backtest.md` already states.
- **The Beneish M-Score was descoped**, not built — a deliberate scope
  decision under a fixed time budget, explained in `docs/04_roadmap.md`'s
  Phase 2 section, not an oversight discovered late.
- **93% test coverage, not 100%.** The remaining gaps are largely
  defensive/unreachable branches (e.g. `src/ingestion/sec_edgar_client.py`
  lines 149-162 are a rare non-200/403/429/404 HTTP status path, and
  `src/agentic/llm_client.py`'s uncovered lines are the real
  `AnthropicLLMClient` implementation, which cannot be exercised without a
  live API key by design). Each is named here rather than hidden behind an
  aggregate percentage.

## 5. Post-completion addendum: local LLM backend (ADR-011)

After this audit's original "PROJECT COMPLETE" declaration, the Phase 3
LLM backend was changed at the user's explicit request from a paid-API
model to a free, local one: `OllamaLLMClient` (`src/agentic/llm_client.py`)
is now the default real `LLMClient` implementation, calling a locally-run
Ollama server. `AnthropicLLMClient` remains in the code as an unused
alternative. This required editing exactly one file's client class plus
`config.py` (new optional `OLLAMA_BASE_URL`/`OLLAMA_MODEL` settings, both
with working defaults) and `dashboard.py` (now attempts a real Ollama call
by default, falling back to a labeled scripted demo only when Ollama isn't
reachable) — retrieval, the grounding critic, and the approval workflow
were untouched, exactly as designed by ADR-006's original protocol-based
`LLMClient` abstraction.

New verification added: `tests/unit/test_ollama_client.py` (14 tests) —
scripted-session tests for every error path (connection refused, timeout,
404 unpulled model, non-200 status, malformed response shape, env-var
defaulting), plus two tests that make a **real HTTP call over a real
socket** to a throwaway local server implementing Ollama's actual
`/api/chat` and `/api/tags` contracts, proving the client's request/
response handling end-to-end — not just against a mocked Python object.
`scripts/verify_ollama_connection.py` was added as the artifact a user
runs on their own machine (the one place a real llama3.2 call can actually
happen) — this project's own sandbox still cannot reach that model, and
that boundary is stated in the script's own docstring, `dashboard.py`'s
UI, `README.md`, and ADR-011, not glossed over.

Full regression after this change: **75 passed** (61 original + 14 new),
**1 deselected** (the live SEC integration test, unchanged), coverage
**93%**, `pip-audit` clean. The dashboard's Agentic tab was re-verified
with Streamlit's headless `AppTest` harness, including simulating a real
button click through the full retrieval → LLM → critic → approval flow —
no exception, critic passed, memo reached `pending_human_approval`.

## 6. Final regression result (re-run immediately before packaging)

```
75 passed, 1 deselected in 3.38s
TOTAL coverage: 93%
pip-audit: No known vulnerabilities found
```

**Conclusion**: every defect this audit surfaced was real, was fixed, and
the fix was verified by re-running the actual suite — not by assertion.
The limitations listed in Section 4 are genuine and remain open; they are
recorded here, in the roadmap, and in the README consistently, which is
the standard this project holds itself to throughout. On that basis, and
consistent with every quality gate defined across
`docs/00_business_case.md` through `docs/07_security_review.md` being met
or its exception explicitly documented, this project is declared
**PROJECT COMPLETE** for all 7 planned phases.

---

## 7. Post-completion addendum 2: company-isolation hardening (ADR-012)

This addendum is written to the same standard the rest of this document
holds itself to, and honors these constraints explicitly, verbatim, for
every claim below: **do not claim "real data" unless the displayed data is
actually sourced from legitimate external filings or stored
provenance-backed data; do not claim "company-specific" unless the
evidence and metrics actually belong to that company; do not claim
"grounded" unless the narrative claims can be traced to supplied evidence;
do not claim "enterprise-ready" merely because the UI looks professional.**
Every verification claim below is backed by a command actually run in this
session and its actual output, not an assertion of correctness.

### 7.1 The reported defect, confirmed real

The user reported (and supplied screenshots of) a real defect: an earlier
`dashboard.py`'s Agentic Narrative tab displayed hardcoded
Microsoft-shaped metrics text (`{'altman_z_score': '2.01 (grey zone,
FY2025)', 'piotroski_f_score': '5/9 (FY2024)'}`) and hardcoded Apple
risk-factor evidence regardless of which company was actually selected in
the Company Scores tab. Reading the prior `dashboard.py` confirmed the
root cause: no single code path took "the selected company" and produced
everything the page needed — each tab independently decided what to load,
and the Agentic tab's decision was, in fact, a hardcoded literal.

### 7.2 Files changed

New:
- `src/reporting/__init__.py`
- `src/reporting/company_dossier.py` — `CompanyDossier` model + `build_company_dossier()`, the one company-scoped read path.
- `src/reporting/evidence_registry.py` — `CIK -> fixture` registry with integrity checks; no fallback substitution.
- `src/reporting/live_ingest.py` — `analyze_company()`/`analyze_and_persist()` backing "Analyze a new company."
- `tests/unit/test_company_dossier.py`, `tests/unit/test_evidence_registry.py`, `tests/unit/test_live_ingest.py`, `tests/unit/test_dashboard_isolation.py`.
- `tests/fixtures/msft_fy2025_risk_factors.json` — real MSFT FY2025 10-K risk-factor text (accession `0000950170-25-100235`), fetched live from SEC EDGAR, needed to make two-company isolation actually testable (previously only Apple had a real evidence fixture).

Modified:
- `src/persistence/storage.py` — `save_snapshot()` now deletes an exact `(cik, fiscal_year)`'s prior `data_quality_issues` before inserting current ones (current-state semantics, not an accumulating log); added `get_latest_altman`, `get_latest_piotroski`, `get_facts`, `get_filing_metadata`, `get_latest_known_fiscal_year`; `list_companies()` now also unions `fact_points` so a company with real data and no computable score is visible.
- `tests/unit/test_storage.py` — 8 new tests for the above.
- `dashboard.py` — rewritten as a thin rendering layer: one company selector (plus an "Analyze a new company" `st.popover`) drives `dossier = build_company_dossier(...)`, and all six tabs (Company Overview, Financial Distress, Filing Risk Intelligence, Agentic Narrative, Historical Validation, Data Quality) read exclusively from `dossier`. No business logic was added to this file — every computation still lives in `src/analysis/`, `src/ingestion/`, or the new `src/reporting/`.
- `docs/02_architecture_decision_record.md` (new ADR-012), `docs/04_roadmap.md` (Phase 6.1 entry), `README.md` (Company isolation section + project structure + limitations).

Not touched: `src/analysis/altman_z.py`, `src/analysis/piotroski.py`, `src/agentic/*`, `src/retrieval/tfidf_index.py`, `src/cli.py` — none of this round's fixes required changing deterministic scoring, the LLM/critic/approval pipeline, retrieval ranking, or the CLI.

### 7.3 Architecture changes

Before: dashboard tabs each independently read storage/fixtures, with no single company-scoped path — the exact condition that allowed a hardcoded literal to survive undetected in one tab. After: `CompanyDossier` (ADR-012) is the only function that reads a company's data out of storage for the dashboard; `EvidenceRegistry` is the only path to risk-factor text and enforces CIK-match integrity checks that raise (`EvidenceIntegrityError`) rather than substitute; `live_ingest.py` extracts the fetch/score/persist flow as a reusable `src/` module so the dashboard's live-analysis feature doesn't duplicate `cli.py` or inline business logic into `dashboard.py`. This is an incremental hardening of the existing layered architecture (ingestion → analysis → persistence → reporting → dashboard), not a rewrite.

### 7.4 Data-source changes

None. The same two real, SEC-filed companies (Apple CIK 0000320193, Microsoft CIK 0000789019) and the same real XBRL companyconcept fixtures are used. The one addition is a second real evidence fixture — MSFT's actual FY2025 10-K Item 1A text, fetched live via SEC EDGAR during this session, quoted verbatim, tagged with its real accession number — added specifically so a genuine two-company isolation test could exist (previously only Apple had real risk-factor text, so "cross-company contamination" couldn't even be tested against two real sources). No synthetic or fabricated filing text was introduced anywhere.

### 7.5 Company-isolation verification (concrete evidence, not assertion)

Ran directly against the real seeded database (`data/riskcopilot.db`, produced by `scripts/seed_dashboard_data.py` against real SEC fixtures) in this session:

```
Apple Inc. (0000320193):    current_fy=2025, altman=None, piotroski=None,
                             7 data-quality issues, evidence chunk ids on
                             query: ['aapl-2025-rf-8', 'aapl-2025-rf-7']
Microsoft Corp (0000789019): current_fy=2025, altman=2.006 (grey),
                             piotroski=5/9, 1 data-quality issue, evidence
                             chunk ids on query: ['msft-2025-rf-5', 'msft-2025-rf-7']
evidence ciks match requested company for both: True
```

And against the real `dashboard.py` file itself, via Streamlit's headless `AppTest` harness (`tests/unit/test_dashboard_isolation.py`, 5 tests, all passing): switching the company selector from Apple to Microsoft changes the Company Overview tab's filing accession number, the Agentic tab's displayed metrics (`Microsoft` appears, `msft-2025-rf` chunk ids appear, `aapl-2025-rf` chunk ids never appear, and vice versa for Apple), and Apple's Agentic tab shows the explicit error "no deterministic score is available to ground it in" rather than a fabricated or borrowed score. This directly reproduces, and refutes, the user's originally reported bug against the real dashboard file.

### 7.6 SEC provenance verification

Every fact and evidence chunk shown carries its real accession number (`0000320193-25-000079` for Apple, `0000950170-25-100235` for Microsoft) and a working `filing_source_url()` link into SEC EDGAR's own archive. `get_filing_metadata()` derives filing metadata via `GROUP BY` over the actually-stored `fact_points` rows, so it cannot drift out of sync with the facts it describes. Verified by `test_get_filing_metadata_reflects_the_actual_stored_facts`, which also locks in fiscal-calendar robustness: MSFT's real June 30 fiscal year-end is correctly reflected (`period_end` ends `-06-30`), confirming `xbrl_facts.py`'s existing `fy`/`fp`/`form`-based filtering (not calendar-month assumptions) was already correct — a real, checked finding, not a new fix.

### 7.7 Altman Z' verification

Unchanged formula, unchanged code path (`src/analysis/altman_z.py`, ADR-005) — this round touched no arithmetic. Re-confirmed live in this session: Microsoft's stored score is Z'=2.0060 (grey), matching the value independently re-derived in `scripts/verify_msft_zscore.py` and unchanged since the original Phase 7 audit (Section 2 above). Apple FY2025 correctly shows no score, with the real cause (`InsufficientDataError: missing real data for ['retained_earnings']`) surfaced as a data-quality issue rather than silently defaulted. *(Correction, 2026-09-25: that missing value was a gap in this project's seed fixture, not in Apple's actual filing. Apple's FY2025 10-K reports retained earnings of -$14,264M; with the fixture corrected, Apple FY2025 scores Z'=2.3464, grey.)*

### 7.8 Piotroski F-Score verification

Unchanged formula, unchanged code path (`src/analysis/piotroski.py`). Re-confirmed live: Microsoft's stored F-Score is 5/9 for FY2024 (vs FY2023), matching `scripts/verify_msft_piotroski.py`'s independent re-derivation. `get_latest_piotroski()` exposes all 9 named signals with pass/fail and description — not just the aggregate number — verified by `test_get_latest_piotroski_includes_all_nine_signals`.

### 7.9 Risk-evidence verification

Both companies' evidence is now real, filing-sourced, and provably disjoint: querying "regulatory compliance data security" against Apple's index returns only `aapl-2025-rf-*` chunk ids carrying Apple's own CIK and accession number; the identical query against Microsoft's index returns only `msft-2025-rf-*` chunk ids carrying Microsoft's own CIK and accession number (`evidence ciks match requested: True` for both, shown above). `EvidenceIntegrityError` is raised, not silently ignored, if a fixture's embedded CIK ever disagreed with its registry key or a loaded chunk's CIK disagreed with the requested CIK (`tests/unit/test_evidence_registry.py`, including a deliberately-mismatched-fixture test). A company with no registered evidence gets an explicit "no company-specific evidence available" state in both the Filing Risk Intelligence and Agentic tabs — never a substituted company's text.

### 7.10 LLM grounding verification

Unchanged: `OllamaLLMClient` remains the default real backend (ADR-011); retrieval, prompt construction, and the deterministic (non-LLM) grounding critic (`src/agentic/critic.py`) are untouched by this round. What changed is only what's fed into that unchanged pipeline: the metrics dict and retrieved chunks passed to `draft_risk_narrative()` are now always the selected company's own (`dossier.latest_altman`, `dossier.latest_piotroski`, and `load_evidence_index(dossier.cik)`), confirmed above. The critic still rejects any narrative claim that doesn't trace to a retrieved chunk id or a passed-in metric name, and a failed critic still blocks approval pending human override (`src/agentic/approval.py`, unchanged).

### 7.11 Data-quality verification

`save_snapshot()`'s new delete-then-insert semantics were verified with two new, real (not simulated) scenarios: `test_resolved_data_quality_issue_does_not_persist_as_stale` (an issue present in run 1, genuinely absent in run 2, correctly gone after run 2 — not shown as stale) and `test_repeated_ingestion_does_not_duplicate_data_quality_issues` (the same real Apple gap reported once after two identical ingestion runs, not twice). `list_companies()`'s fix was verified with `test_list_companies_includes_a_company_with_facts_but_no_computable_score` — re-run live against the real seeded database in this session, confirming Apple (real facts, real issues, zero computable score) is visible in the picker. Stated limitation, not hidden: this is "current state" via delete-and-replace, not a fuller lifecycle with a separate resolved-issue timeline or a stale-vs-resolved distinction — recorded in ADR-012, `docs/04_roadmap.md`, and `README.md`.

### 7.12 Security checks

- SQL injection: every `conn.execute(...)` call across `src/persistence/storage.py` (15 call sites, including all 5 added this round) uses `?` parameter placeholders; `grep` confirmed zero f-string/`.format()`-built SQL anywhere in `src/persistence/` or `src/reporting/`.
- Secrets: no API keys or credentials are used or logged by any new code (`OllamaLLMClient` needs none; the evidence registry and dossier builder touch no network or secrets at all).
- `pip-audit -r requirements.txt`: **No known vulnerabilities found** (re-run in this session; no new dependencies were added).
- LLM/prompt-injection surface: unchanged from ADR-006/`docs/07_security_review.md` — the critic step is still a deterministic, non-LLM check independent of anything the model itself claims, so nothing in this round weakens that boundary.

### 7.13 Performance checks

No new network calls or heavyweight computation were introduced for the two seeded companies' normal dashboard path — `CompanyDossier` assembly is a handful of parameterized, already-indexed-by-primary-key SQLite reads (`src/persistence/storage.py` is 100% covered and unchanged in its indexing). No caching was added or needed at this data volume (two companies, single-digit fiscal years each); this remains consistent with `docs/07_security_review.md`/Phase 5's benchmark scope. The "Analyze a new company" live-fetch path's cost is unchanged (bounded by SEC EDGAR's own response time, with the existing rate-limiter/retry logic from Phase 1/5 unmodified).

### 7.14 Full test results (re-run immediately before packaging)

```
108 passed, 1 deselected in 8.08s
TOTAL coverage: 94%
pip-audit: No known vulnerabilities found
```

108 = 75 (prior total, Section 6) + 8 new `test_storage.py` tests + 4 `test_company_dossier.py` + 8 `test_evidence_registry.py` + 8 `test_live_ingest.py` + 5 `test_dashboard_isolation.py` = 108. `src/reporting/company_dossier.py` and `src/reporting/evidence_registry.py` are both at 100% line coverage; `src/persistence/storage.py` remains at 100%. The 1 deselected test is unchanged (the live SEC integration test, which needs real internet access this sandbox doesn't have).

### 7.15 Remaining limitations, stated plainly (not buried)

- Real, registered risk-factor evidence exists for exactly **two** companies (Apple, Microsoft). A company analyzed live via "Analyze a new company" gets real deterministic scores but an honest "no evidence available" Agentic/Filing tab state until real filing text for it is fetched and added to `evidence_registry.py` — this is correct behavior under the "never substitute" rule, not an oversight, but it does mean the platform's *evidence* coverage, as opposed to its *scoring* coverage, is narrow today.
- The data-quality "current state" model is delete-and-replace, not a fuller issue-lifecycle with separate timestamps for first-seen/resolved-at or a stale-due-to-staleness distinction (see 7.11).
- No live Ollama model call has been made from this development sandbox (unchanged from Section 5/ADR-011) — `scripts/verify_ollama_connection.py` remains the artifact for that final check on the user's own machine.
- This round's isolation testing covers exactly the two companies this project has real fixtures for (Apple, Microsoft) — the user's Section 18 request to validate "at minimum Apple and Microsoft" is met; broader N was out of scope for this pass because no additional company has real, registered evidence yet (see the first bullet above).

**Conclusion for this addendum**: the user-reported cross-company-contamination defect was real, was root-caused to the absence of a single company-scoped read path, and is now fixed at the architecture level (one dossier, one evidence registry, both enforced with integrity checks) rather than patched at the symptom level — verified against the actual `dashboard.py` file via `AppTest`, against the real seeded database directly, and via 25 new passing tests. This is reported as correct, company-specific, and grounded to the extent demonstrated above — not described as "enterprise-ready" merely because the dashboard looks organized; the limitations in 7.15 are the honest boundary of what this round actually closes.
