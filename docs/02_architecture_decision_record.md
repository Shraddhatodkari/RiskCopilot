# Architecture Decision Record

Each ADR states the decision, the alternatives considered, and the
reasoning — including where a decision may be revisited later. Per the
project's working method, no earlier decision is protected just because it
is already implemented; if a later phase finds a decision weak, this file
will be updated with a superseding ADR rather than silently drifting from
what's documented.

---

## ADR-001: Python as the sole implementation language

**Decision**: All application code is Python 3.11+.

**Why**: Mandated by the project constraints. Also the pragmatically
correct choice independent of that mandate: the richest ecosystem for both
data engineering (`requests`, `pydantic`) and, from Phase 2, lightweight
local ML (`sentence-transformers`) and LLM orchestration.

---

## ADR-002: No containers, no orchestration platforms

**Decision**: The system runs as a plain Python virtual environment on a
single Windows laptop. No Docker, Compose, or Kubernetes at any phase.

**Why**: Mandated by the project constraints, and also appropriate to the
actual scale of the problem: a single-analyst research tool with a SQLite
database and, at most, a handful of concurrent local processes has no
distributed-systems problem to solve. Adding container infrastructure here
would be complexity theater, not engineering.

---

## ADR-003: Configuration and secrets via environment variables, not files or hard-coding

**Decision**: All environment-specific and sensitive configuration (the
SEC EDGAR contact User-Agent, the FRED API key from Phase 2, any LLM API
key from Phase 3) is read from environment variables, optionally loaded
from a local, git-ignored `.env` file via `python-dotenv`. Missing required
configuration raises immediately with an actionable error rather than
silently degrading (see `src/config.py`).

**Why**: Standard practice; avoids secrets in source control; and — the
concrete reason it mattered in Phase 1 — a *wrong* SEC User-Agent silently
accepted would risk the caller's IP being rate-limited or blocked by SEC's
automated abuse detection, which is a shared resource. Failing loudly here
is a correctness requirement, not just hygiene.

---

## ADR-004: Every financial figure is a typed, sourced fact — never a bare number

**Decision**: Financial inputs flow through the pipeline as `FactPoint`
pydantic objects (see `src/ingestion/models.py`) carrying the XBRL tag
used, the filing's form type, filing date, and SEC accession number — not
as plain floats.

**Why**: The project's core traceability requirement ("important financial
conclusions must be traceable to real source data") is not achievable if
provenance is bolted on after the fact or kept in a separate lookup table
that can drift out of sync with the number it describes. Attaching
provenance to the value itself, in the type system, makes it structurally
impossible to compute a ratio without also being able to cite where each
input came from — this is demonstrated end-to-end in
`docs/sample_output.txt`, where every source fact prints its own filing
citation.

---

## ADR-005: Deterministic-first financial math; no LLM in the numeric calculation path

**Decision**: All financial ratio/score calculations (Phase 1's Altman
Z'-Score; Phase 2's Piotroski F-Score and Beneish M-Score) are plain,
tested Python arithmetic. An LLM is never asked to compute, transcribe, or
"double check" a financial number.

**Why**: LLMs are well documented to be unreliable at exact arithmetic and
prone to silently plausible-looking numeric errors — precisely the failure
mode this project's own quality-gate requirements ("independently verify
important financial calculations using deterministic Python logic rather
than relying on an LLM") warn against. Every number in this system is
either a value taken verbatim from a cited SEC filing, or a deterministic
function of such values, and both are unit-tested (see
`docs/05_testing_strategy.md`).

**Where the LLM *is* used (Phase 3+)**: generating and lightly summarizing
qualitative narrative around retrieved, cited filing text — never
generating or altering a number — with an automated critic step rejecting
any claim not traceable to a retrieved source or a computed value from this
deterministic layer.

---

## ADR-006: Agentic architecture is scoped, not assumed — and only for the qualitative layer (Phase 3+)

**Decision**: Phase 1-2 (real data ingestion, deterministic scoring) use no
agent framework at all — they are a straight-line pipeline, because there
is no genuine multi-step tool-coordination or reasoning problem to solve
there. A bounded, tool-using workflow is introduced starting in Phase 3,
specifically for qualitative risk-factor extraction and narrative
synthesis, with these defined roles:

1. **Retrieval step** — given a company + fiscal year, retrieve the most
   relevant passages from that company's real MD&A / risk-factor
   disclosures (full-text SEC filings) via a local embedding index.
2. **Narrative-generation step** — an LLM call, given ONLY the retrieved
   passages and the Phase 1/2 deterministic scores as context, drafts a
   short risk narrative with inline citations to the specific retrieved
   passage or computed figure behind each claim.
3. **Critic/validator step** — a second, independent LLM call (or a
   deterministic check, evaluated in Phase 3 for which is more reliable)
   whose only job is to verify every claim in the draft narrative traces to
   a retrieved passage or a Phase 1/2 number, and to strip or flag any
   claim that doesn't.
4. **Human approval gate** — the memo is marked "draft" until a human
   reviewer accepts it; nothing in this pipeline auto-finalizes a risk
   conclusion.

**Why this shape and not a general-purpose multi-agent chatbot**: each step
has a narrow, independently testable responsibility and a clear reason to
exist — retrieval solves "the source text is too long to put entirely in
context," generation solves "turn cited evidence into readable prose,"
and — the part that is actually load-bearing — the critic step exists
because a single ungoverned LLM call over long financial text is exactly
where hallucinated claims creep in, and a due-diligence memo that states an
unsupported claim as fact is a real business risk to the analyst who trusts
it. A free-roaming multi-agent system with tool-selection autonomy was
considered and rejected for this problem: the tool set (retrieve, generate,
critique) is small, fixed, and known in advance, so a deterministic
pipeline with two grounded LLM calls achieves the same reliability with far
less failure surface than giving an agent autonomy to decide its own next
action.

**Status**: This ADR documents the Phase 3 design decided during Phase 0
research; the retrieval/generation/critic components themselves are
implemented in Phase 3, not in this initial phase (see
`docs/04_roadmap.md`).

---

## ADR-007: SQLite over a client/server database

**Decision**: Local persistence (Phase 2+, once ingestion runs are cached
rather than fetched fresh each time) uses SQLite, not Postgres/MySQL/a
hosted database service.

**Why**: A single-user, single-machine research tool has no concurrent
writer problem to solve. SQLite is zero-install, zero-configuration, and
the entire database is one file that's trivial to back up or inspect — the
right level of infrastructure for the actual requirement, consistent with
ADR-002's reasoning.

---

## ADR-008: Hand-rolled SEC EDGAR client, not a third-party wrapper package

**Decision**: `src/ingestion/sec_edgar_client.py` is ~150 lines of direct
`requests` usage rather than a dependency on a community "sec-edgar-api"
package.

**Why**: The community packages surveyed during Phase 1 development are
either unmaintained or bring in dependencies (e.g. full pandas
integration) not otherwise needed at this phase. The actual protocol
surface required — a rate limiter, a mandatory User-Agent header, and
403/429/404 handling with backoff — is small enough that writing it
directly keeps the dependency footprint minimal (per the hardware
constraint) and keeps every retry/rate-limit decision fully auditable in
project code rather than hidden inside a third-party library.

**Phase 5 update**: this client's retry loop had a real bug — a persistent
`requests.RequestException` (e.g. repeated timeouts) survived retries and
was re-raised unwrapped, instead of as a `SecEdgarError` like every other
failure path. This is exactly the kind of thing ADR-008's "fully auditable
in project code" argument is for: the bug was found by writing the retry
tests this project's own `docs/05_testing_strategy.md` had already flagged
as missing, not by chance. Fixed in Phase 5; see
`docs/07_security_review.md` and `tests/unit/test_sec_edgar_client_retry.py`.

---

## ADR-009: Macro/credit-spread context (FRED) is read alongside company
scores, never blended into the Altman/Piotroski formulas

**Decision**: `src/ingestion/fred_client.py` fetches the ICE BofA BBB US
Corporate Index Option-Adjusted Spread (`BAMLC0A4CBBB`) as independent
macro context for a human reviewer to read next to a company's Z'/F-Score —
it is never mathematically combined into either score.

**Why**: Both the Altman Z'-Score and Piotroski F-Score are published,
externally validated formulas with fixed coefficients derived from specific
historical company samples. Silently adding a macro adjustment term would
turn a citable, well-known model into an unpublished variant with no
external validation behind it — a serious problem for a tool whose entire
value proposition (`docs/00_business_case.md`) is defensible, traceable
scoring. A widening BBB spread is genuinely useful context (it signals
tightening credit conditions across the market, which raises the base rate
of financial stress for every borrower), so it is surfaced, just not
silently folded into a number that a reviewer would otherwise assume is the
textbook Altman/Piotroski formula unmodified. If a validated macro-adjusted
variant of either model is identified in future work, it would be
introduced as an explicitly separate, separately-named score, not a silent
change to the existing one.

**Status**: `FredClient` is built and unit-tested against a real (though
sandbox-unreachable, per `docs/03_data_provenance.md`) API shape; wiring its
output into the CLI/dashboard as a displayed-but-not-blended macro
indicator is scoped as near-term future work in `docs/04_roadmap.md`, since
it requires the user's own free FRED API key to run live and was not the
highest-priority remaining item versus Phases 4-7 of the original roadmap.

---

## ADR-010: TF-IDF + cosine similarity for filing-text retrieval, not transformer sentence embeddings

**Decision**: `src/retrieval/tfidf_index.py` retrieves relevant risk-factor
passages using scikit-learn's `TfidfVectorizer` + cosine similarity, not a
transformer-based sentence-embedding model (e.g. `sentence-transformers`).

**Why**: This was an empirical decision, not an assumption, and a real
example of revising an earlier plan when it proved weak (per this project's
own working-method commitment). `docs/01_project_selection_and_landscape.md`
originally scoped local embeddings as the retrieval mechanism. During Phase
3 implementation, `pip install sentence-transformers` was actually run and
timed out after 3+ minutes because it transitively pulls in PyTorch — a
direct, measured conflict with this project's hard "must run on an old,
resource-constrained Windows laptop" requirement. Pivoting to
scikit-learn's `TfidfVectorizer` (installed in ~10 seconds; the project's
entire venv, including scikit-learn/numpy/scipy, is a few hundred MB rather
than PyTorch's multi-GB footprint) resolved the conflict while still
correctly solving the actual retrieval problem this project has: ranking a
small number (single digits to low tens) of already-chunked risk-factor
passages per filing by relevance to a query. At this scale and with
domain-specific financial vocabulary (where exact term matches like
"single-source component" or "antitrust" are highly informative), TF-IDF's
lack of semantic/synonym matching is a real, documented limitation
(see `tests/unit/test_tfidf_retrieval.py::test_irrelevant_query_does_not_force_a_confident_false_match`
and the module docstring in `tfidf_index.py`) but not a disqualifying one —
and it is far preferable to a retrieval component that cannot install on
the target hardware at all.

**When this should be revisited**: if a future phase needs retrieval over
much larger, less exactly-worded text (e.g. general web research rather
than a company's own filing text, or cross-lingual filings), TF-IDF's
exact-term-matching limitation would become a real accuracy problem and a
lightweight embedding option (e.g. a small ONNX-exported model without a
full PyTorch dependency) should be re-evaluated then — not before, per this
project's "decide from evidence, not by default" principle.

---

## ADR-011: Ollama (local model) as the default real LLM backend, not a paid cloud API

**Decision**: `src/agentic/llm_client.py`'s `OllamaLLMClient` — calling a
locally-running Ollama server's `/api/chat` endpoint — is the default,
actually-used real implementation of the `LLMClient` protocol for the
Phase 3 narrative layer, superseding `AnthropicLLMClient` as this
project's default live backend. The dashboard (Phase 6) constructs
`OllamaLLMClient` by default; `AnthropicLLMClient` remains in the codebase
as an alternative pluggable implementation of the same protocol but is not
constructed or called anywhere by default.

**Why**: explicit user requirement — zero ongoing API cost and no API key
dependency for the qualitative layer, so the tool remains fully runnable
by anyone with a free local Ollama install, with no cloud account, billing
relationship, or per-call cost of any kind. This also strengthens, not
weakens, the project's existing data-handling posture: filing text and
computed metrics are the only "sensitive-ish" data this pipeline handles,
and with a local model, that content never leaves the machine it runs on
— a genuine privacy improvement over any cloud LLM API, not just a cost
one (see `docs/07_security_review.md`'s update for this phase).

**Why this doesn't touch anything else in the pipeline**: `LLMClient` was
already designed (ADR-006, Phase 3) as a one-method protocol specifically
so the provider could be swapped without touching retrieval, prompt
construction, the grounding critic, or the approval workflow — this
decision is exactly the payoff of that earlier design choice. Swapping the
concrete class required zero changes to `src/agentic/critic.py`,
`src/agentic/approval.py`, `src/retrieval/`, or any of the deterministic
financial modules.

**Real limitation, stated plainly**: a small (e.g. 3B-parameter) local
model instruction-follows less reliably than a large hosted model — it is
more likely to occasionally skip a required citation or phrase a claim
loosely. This is precisely why the grounding critic (ADR-006) is a
deterministic, non-LLM check rather than something that trusts the model's
good behavior: a citation-format slip from a smaller local model is
caught the same way a hosted model's hallucination would be, and routes to
the same human-approval gate. `scripts/verify_ollama_connection.py`'s own
output notes this explicitly if it happens during a real run.

**Verification boundary, stated plainly**: this project's own development
sandbox cannot reach a server on the user's own machine, so
`OllamaLLMClient`'s HTTP request/response handling was verified end-to-end
against a real local HTTP server implementing Ollama's actual `/api/chat`
contract (`tests/unit/test_ollama_client.py`), not against a real
llama3.2 model. `scripts/verify_ollama_connection.py` is the artifact a
user runs on their own machine to get that final, real-model
verification — see its own output for what a genuine run looks like.

---

## ADR-012: `CompanyDossier` — a single, company-scoped read path, closing a real cross-company-contamination bug

**The bug, stated plainly**: the pre-ADR-012 `dashboard.py` had its Agentic
Narrative tab written against one hardcoded company. It always loaded
Apple's risk-factor evidence file and, in one code path, a hardcoded
Microsoft CIK/metric string, regardless of which company the user had
selected in the Company Scores tab. A user selecting Apple would see
Apple's scores in one tab and Microsoft-shaped narrative text in another —
a real, user-reported defect, not a hypothetical one, and exactly the
failure mode a due-diligence tool must never exhibit: presenting one
company's evidence as if it belonged to another.

**Root cause**: there was no single place in the code that took "the
selected company" and produced everything the dashboard needed to render
it. Each tab independently decided what to load, and at least one tab's
"decision" was actually a hardcoded literal left over from earlier
development rather than a read keyed off the current selection.

**Decision**: `dashboard.py` now has exactly one company selector
(plus an "Analyze a new company" flow, see below), and immediately after
a company is selected, the entire dashboard is built from one function
call: `build_company_dossier(conn, cik, entity_name)`
(`src/reporting/company_dossier.py`). Every tab reads fields off the
returned `CompanyDossier` object — never storage, never a hardcoded
literal, never another tab's local variable. `CompanyDossier` bundles the
latest and historical Altman/Piotroski results, the current fiscal year's
source facts and filing metadata, current data-quality issues, and whether
company-specific risk-factor evidence exists at all
(`has_risk_factor_evidence`) — every field scoped by the SQL layer to the
one requested `cik` (see `src/persistence/storage.py`'s parameterized
queries). There is now exactly one code path that reads a company's data
out of storage, which makes "did two companies' data get mixed up" a
question with one place to answer it, instead of one per dashboard
section.

**Evidence substitution, closed the same way**: the Agentic tab's evidence
lookup previously loaded a literal fixture filename. It now goes through
`src/reporting/evidence_registry.py`, an explicit `CIK -> fixture file`
registry with two integrity checks on every load: the fixture's own
embedded `cik` field must match the registry key it's filed under, and
every retrieved chunk's `.cik` must match the CIK that was actually
requested. Both checks raise `EvidenceIntegrityError` rather than silently
returning mismatched data. If a company has no registered evidence (every
company except Apple and Microsoft, today — see the stated limitation
below), `evidence_available(cik)` returns `False` and the dashboard shows
an explicit "no company-specific evidence available" state; it does not
fall back to another company's fixture. This is the direct implementation
of "never substitute another company's evidence" as code, not just as a
comment.

**Data-quality "current state" semantics, fixed in the same pass**:
`save_snapshot()` previously only ever inserted `data_quality_issues` rows
and never removed old ones, so (a) re-running ingestion for the same
company/year duplicated every still-present issue once per run, and (b) an
issue that was genuinely resolved by a later run (e.g. a tag-fallback
improvement that finds a concept a previous run missed) would sit in the
database forever, next to the fresh data, and be displayed as if it were
still a live problem. `save_snapshot()` now deletes all
`data_quality_issues` rows for that exact `(cik, fiscal_year)` before
inserting the current run's issues, in the same transaction. This is a
"delete-then-insert" model of *current state*, not an audit log: the table
answers "what is wrong with this company's data right now," and a
resolved issue is not displayed at all after the run that resolved it.
Verified by `test_resolved_data_quality_issue_does_not_persist_as_stale`
and `test_repeated_ingestion_does_not_duplicate_data_quality_issues`
(`tests/unit/test_storage.py`).

**Stated scope limit of this fix**: this delete-then-insert model treats
"absent from the latest run" as the only signal for "resolved." It does
not yet track *when* an issue was first seen, keep a separate resolved-
issue history a reviewer could look back at, or distinguish "resolved"
from "stale because the source data hasn't been re-fetched in a while."
For this project's actual data-quality lifecycle (a single analyst
re-running ingestion on demand, not a scheduled multi-day pipeline), that
is judged sufficient — but it is a real, current limitation, not a solved
"data-quality lifecycle" in the fuller sense the issue-tracking literature
means by that phrase, and is recorded as future work in
`docs/04_roadmap.md`.

**A company can be "current" with facts and issues but no computable
score**: `CompanyDossier.current_fiscal_year` is deliberately **not**
derived only from the latest Altman/Piotroski score — a company can have a
real, honestly-ingested fiscal year (real facts, real data-quality issues)
with zero computable score at all (for example, a filing missing the
`retained_earnings` concept blocks the Altman calculation entirely — see
ADR-004/ADR-005's refuse-rather-than-guess principle). Apple FY2025 was
originally cited here as a real example; that turned out to be a
fixture-curation error (corrected 2026-09-25 — Apple's FY2025 10-K
reports both retained earnings and stockholders' equity). Deriving "current year" only from computed scores would
make that year, and its real data-quality issues, invisible. Instead
`get_latest_known_fiscal_year()` (`src/persistence/storage.py`) is a
`UNION ALL` across facts, issues, and both score tables, so a company is
"known" for dossier purposes the moment it has *any* stored data for a
year, not only once a score exists. The same gap previously made
`list_companies()` itself blind to such a company (it only unioned the two
score tables); it now also unions `fact_points`, so a company with real
ingested data and zero computable scores still appears in the picker at
all — a correctness/honesty fix, not merely a UX one.

**Live analysis without duplicating the CLI**: the dashboard's "Analyze a
new company" flow (any ticker or CIK, not just the two seeded companies)
is implemented as a new reusable module, `src/reporting/live_ingest.py`
(`analyze_company` / `analyze_and_persist`), rather than by inlining
fetch/score/persist logic into `dashboard.py` or by modifying the
already-tested `src/cli.py`. This keeps `dashboard.py` a thin rendering
layer (per this project's original architecture goal, restated in this
round's requirements: business logic belongs in `src/`, not in the
Streamlit file) and avoids risking the 7 existing passing `test_cli.py`
tests for a change the CLI itself didn't need.

**Verification**: `tests/unit/test_company_dossier.py`,
`tests/unit/test_evidence_registry.py`, and `tests/unit/test_live_ingest.py`
are new; `tests/unit/test_storage.py` gained 8 tests for the new storage
functions and the two fixed bugs above. The rewritten `dashboard.py` was
additionally verified with Streamlit's `AppTest` harness switching between
Apple and Microsoft and confirming every tab's displayed CIK, scores, and
evidence citations changed together and matched the selected company, with
Apple's Agentic tab showing an explicit "no score available" state rather
than fabricating or borrowing one. See `docs/08_final_audit.md` for the
consolidated verification report for this round.

**Stated limitation**: today, exactly two companies (Apple, CIK
0000320193; Microsoft, CIK 0000789019) have registered risk-factor
evidence in `evidence_registry.py`. A company analyzed via "Analyze a new
company" gets real deterministic scores from live SEC data, but its
Agentic tab will honestly show "no evidence available" until a real
risk-factor-text fixture is fetched and registered for it — this is the
correct, honest behavior given ADR-012's "never substitute" rule, not a
bug to be silently worked around by widening the registry with unverified
data. **Superseded by ADR-013 below**, which closes exactly this gap.

---

## ADR-013: Company-agnostic risk-intelligence expansion — ratio engine, historical trends, deterministic risk tiers, the stale-derived-score lifecycle fix, and generalized filing-evidence discovery

This ADR covers one coherent round of work whose stated goal was to make
this project's "company-agnostic" claim true in practice, not just in
architecture diagrams — and to close a real correctness bug in how a
previously-computed score can outlive the data it was computed from. It
touches several files; documented together here because they were
designed and delivered as one change with one goal.

### 1. The stale-derived-score bug (fixed first, as the highest-priority item)

**The bug, stated plainly**: `save_altman_result`/`save_piotroski_result`
use `INSERT OR REPLACE`, so a *successful* recomputation always overwrote
the old row correctly. But if a later recomputation attempt for the same
`(cik, fiscal_year)` genuinely **failed** (e.g. a filing amendment removed
a tag Altman needs), nothing ever ran `DELETE` — the old, no-longer-
reproducible score just sat in the database and kept being read back by
`get_latest_altman`/`get_latest_piotroski` as if it were still current.
A dashboard or report built on that read would show a valid-looking score
for a company whose current source data can no longer support one — the
exact "old score masquerading as current" failure this round's spec
called out as the single most important thing to fix.

**Fix**: `src/persistence/storage.py` gained
`invalidate_altman_result(conn, cik, fiscal_year, reason)` and
`invalidate_piotroski_result(conn, cik, fiscal_year, reason)`. Each
deletes any existing row for that `(cik, fiscal_year)` from the score
table and records a `data_quality_issues` row with
`severity="invalidated"` and the real failure reason. `src/reporting/
live_ingest.py::analyze_and_persist` and `src/cli.py`'s `--save` path both
call the matching invalidate function whenever a recomputation was
*attempted this run* and produced an error (never when a score simply
wasn't requested at all — `with_piotroski=False` must not wrongly
invalidate an existing Piotroski score nobody asked to recompute).

**Verification**: `tests/unit/test_storage.py` gained
`test_stale_altman_score_is_invalidated_when_recomputation_fails`,
`test_altman_score_recovers_cleanly_if_data_becomes_available_again`,
`test_stale_piotroski_score_is_invalidated_when_recomputation_fails`, and
`test_analyze_and_persist_invalidates_stale_score_end_to_end` — the last
one calls `analyze_and_persist` twice against the same in-memory database
with a monkeypatched second-call failure, and asserts
`get_latest_altman(conn, cik) is None` afterward. No existing test was
weakened to make this pass; these are new, additive regression tests.

### 2. Expanded deterministic ratio engine (`src/analysis/financial_ratios.py`)

Eleven ratios beyond Altman/Piotroski — liquidity (current, quick),
leverage (debt/equity, debt/assets, interest coverage), profitability
(ROA, ROE, operating margin, net margin), and cash flow (operating cash
flow, free cash flow) — computed from the same real, sourced
`FinancialSnapshot` Altman/Piotroski already use. Unlike those two
all-or-nothing models, each ratio here is independently
available/insufficient-data/not-applicable (`RatioStatus`): a company can
have a computable current ratio and simultaneously an honestly-
uncomputable interest coverage ratio, and the missing one never blocks the
others. Every `RatioValue` carries its formula string, the source concept
names, and — for anything other than `AVAILABLE` — a plain-language reason.
Three new XBRL concepts (`inventory`, `interest_expense`,
`capital_expenditures`) were added to `FinancialSnapshot` and
`src/ingestion/xbrl_facts.py`'s `CONCEPT_SPECS` to support this; both
Apple's and Microsoft's real fixtures were extended with real, live-fetched
values for these concepts, which is how this project discovered (and now
correctly reports, rather than hides) that neither company's most recent
10-K tags a usable `InterestExpense`/`InterestExpenseDebt` value at all.

### 3. Historical multi-year trend classification (`src/analysis/trend.py`)

`classify_trend(metric, points)` compares the **earliest and latest** real
fiscal year a company has stored data for (not just any two adjacent
years) and returns `IMPROVING` / `DETERIORATING` / `STABLE` /
`INSUFFICIENT_DATA` using a fixed, documented 5% relative-change threshold
and an explicit lower-is-better/higher-is-better polarity table (only
`debt_to_equity` and `debt_to_assets` are lower-is-better; everything else,
including Altman Z' and Piotroski F-Score, is higher-is-better). Fewer
than two real years of data is always `INSUFFICIENT_DATA` — never a
guess, never an interpolated point. Historical years are reconstructed via
`build_snapshot_from_stored_facts` (new in `storage.py`), which rebuilds a
`FinancialSnapshot` purely from already-stored `fact_points` rows rather
than re-fetching from SEC — this is not fabrication (the values are the
same already-real, already-sourced facts a prior ingestion run stored),
and it means trend computation needs no network access at all.

### 4. Deterministic overall risk tier (`src/analysis/risk_rating.py`)

`classify_risk_tier(altman_zone, piotroski_f_score, current_ratio)`
combines the Altman zone and the Piotroski bucket (strong/moderate/weak,
using the same cutoffs as `PiotroskiResult.interpretation`) via a fixed
"take the worse tier" rule, then applies one additional real-time
liquidity flag: a current ratio below 1.0 bumps the tier one level worse.
This produces the 🟢 LOW / 🟡 MODERATE / 🟠 ELEVATED / 🔴 HIGH /
⚪ INSUFFICIENT_DATA tier the dashboard's new Risk Overview tab displays.
The full rule table is stated in the module's own docstring and every
`RiskRating.basis` string names exactly which inputs and rule produced it
— never an LLM judgment call, and never a black-box weighted score.

### 5. Generalized filing-evidence discovery (closes ADR-012's stated limitation)

`src/reporting/evidence_registry.py` no longer contains a hardcoded
`{cik: filename}` dict. It scans `tests/fixtures/*_risk_factors.json` and
`data/evidence_cache/*_risk_factors.json`, keying the registry by each
file's own embedded `cik` field, and raises `EvidenceIntegrityError` if two
files ever claim the same CIK. Registering a new company's evidence is now
"add a file," not "edit code."

Two pieces make this concretely, not just architecturally, true:

- `src/ingestion/risk_categorizer.py`: deterministic, whole-word/whole-
  phrase keyword matching (never an LLM) tags a retrieved filing chunk
  with zero or more of a fixed 13-category taxonomy (liquidity, leverage/
  refinancing, litigation, cybersecurity, supply chain, customer
  concentration, competition, regulation, geopolitical, technology/AI,
  margin pressure, going concern, M&A). An early substring-based version
  false-positived on `"war"` inside `"malware"`; word-boundary (`\b`)
  matching fixed it, with explicit plural/variant keyword forms added
  where boundary-safety then broke a legitimate match (`"cyberattacks"`,
  `"regulations"`, etc.) — both bugs are locked in as permanent regression
  tests in `tests/unit/test_risk_categorizer.py`, run against real
  Apple/Microsoft/NVIDIA filing text.
- `src/ingestion/filing_document_client.py`: given a real 10-K's raw HTML
  (fetched via two new `SecEdgarClient` methods, `get_submissions` and
  `get_document_html` — a different host, `www.sec.gov`, than every other
  method on that client), `extract_item_1a_chunks` deterministically
  locates the real "Item 1A" section (using the fact that a table of
  contents always precedes the real section, so the *last* "Item 1A"
  occurrence in the document is the real one), strips markup, and splits
  it into headed, chunked passages. `cache_evidence` writes the result in
  the exact JSON shape `evidence_registry.py` already scans for.
  `src/reporting/live_ingest.py::analyze_and_persist` gained a
  `fetch_evidence` parameter that wires this into the "Analyze a new
  company" flow: best-effort, never fatal to an otherwise-successful score
  computation, with any failure recorded honestly on
  `LiveIngestResult.evidence_error` rather than swallowed or silently
  skipped.

**Concrete proof this generalizes past two companies**: NVIDIA
Corporation (CIK 0001045810) was added as a third, fully independent real
company — real FY2023–FY2025 XBRL values fetched live from data.sec.gov
(`tests/fixtures/nvda_fy2025_companyconcept.json`) and real FY2025 10-K
Item 1A text (`tests/fixtures/nvda_fy2025_risk_factors.json`) — with
**zero** changes to `evidence_registry.py`, `financial_ratios.py`,
`risk_rating.py`, or `trend.py`. NVIDIA's real numbers surfaced two more
genuine, independent data-quality gaps (no `InterestExpense` at all for
FY2025; no `PaymentsToAcquirePropertyPlantAndEquipment`/
`PaymentsForCapitalImprovements` for any fiscal year in the fixture — both
tags 404 live), each handled the same honest way as Apple's and
Microsoft's gaps. `scripts/seed_dashboard_data.py` now seeds all three
companies; `tests/unit/test_dashboard_isolation.py` and
`tests/unit/test_nvda_third_company.py` exercise the full pipeline and
the real dashboard file against all three, with explicit assertions that
no company's data ever appears while another is selected.

### 6. Dashboard expansion

`dashboard.py` gained three tabs — **Risk Overview** (the deterministic
tier plus its full "Why?" derivation), **Financial Metrics** (all eleven
ratios grouped by category, each with a "Why?" expander showing formula,
source concepts, and the real source values used), and **Historical
Trends** (multi-year charts plus the trend classification for every
metric) — and the existing Filing Risk Intelligence tab now tags each
retrieved passage with its deterministic risk category. The "Analyze a
new company" panel gained an evidence-fetching checkbox wired to
`fetch_evidence`. Every new section reads exclusively from the already-
company-scoped `CompanyDossier` (extended with `current_ratios`,
`ratio_history`, `metric_trends`, `risk_rating`) — no new code path reads
storage directly, preserving ADR-012's single-read-path guarantee.
`tests/unit/test_dashboard_isolation.py`'s tab indices were updated to
match the new tab order (this is a structural renumbering, not a
weakening: every original assertion still runs, against the same real
seeded database, and now against three companies instead of two).

### 7. Explicitly out of scope for this round

Stated honestly rather than silently dropped: peer-comparison-as-a-
dedicated-tab, PDF report export, "what-if" scenario analysis, and
free-form filing Q&A/RAG chat were part of the original wishlist but were
not built in this pass. The underlying data needed for a peer view (each
company's own `CompanyDossier`) already exists and is company-isolated, so
a peer-comparison tab is additive future work, not a redesign — tracked in
`docs/04_roadmap.md`. Building any of these under time pressure without
the same real-data, no-fabrication rigor applied everywhere else in this
project would have been a worse outcome than shipping fewer, fully honest
features.

**Verification**: see `docs/09_final_verification_report.md` for the
complete test count, pass/fail breakdown, and the full Section-20-style
checklist this round of work was verified against.
