# Security Review (Phase 5)

This is a self-review against a threat model appropriate to what this
project actually is: a single-analyst research tool that (a) calls two
public, read-only financial data APIs (SEC EDGAR, FRED) with a locally
supplied API key, (b) persists results to a local SQLite file, and
(c) optionally calls a third-party LLM API with retrieved filing text. It is
not a multi-tenant service, does not accept untrusted network input, and is
not deployed behind a public endpoint — so several standard web-app threat
categories (auth bypass, CSRF, session fixation) do not apply and are noted
as out-of-scope rather than silently ignored.

## 1. Secrets handling

- `SEC_EDGAR_USER_AGENT`, `FRED_API_KEY`, and `ANTHROPIC_API_KEY` are read
  exclusively from environment variables (`src/config.py`), optionally via a
  local `.env` file. `.env` is listed in `.gitignore`; only `.env.example`
  (containing no real values, only placeholders and comments explaining
  where to obtain each key) is committed.
- `load_settings()` fails loudly (`ConfigError`) if the required
  `SEC_EDGAR_USER_AGENT` is missing or malformed, rather than silently
  falling back to a placeholder — a silent fallback here is a real risk
  (SEC EDGAR IP-blocks generic/missing User-Agents), not just a style
  preference (ADR-003).
- No secret is ever logged. `sec_edgar_client.py`'s logging statements log
  URLs and status codes, never headers.
- No secret is ever persisted to SQLite; `storage.py`'s schema has no column
  that could hold one.

**Finding**: none required fixing. **Residual risk**: a `.env` file with a
real Anthropic API key sitting on the user's own laptop is only as safe as
that laptop's filesystem permissions — outside this project's control, and
called out in `README.md`'s setup instructions (use a key with the lowest
practical spending limit).

## 2. Injection defenses

### 2.1 SQL injection
Every query in `src/persistence/storage.py` uses parameterized placeholders
(`?`) via `sqlite3`'s standard parameter-substitution API — none of the 9
`execute()` calls in that file build SQL with string formatting or
concatenation of caller-supplied values. Verified by direct inspection
(`grep -n "execute\|INSERT\|SELECT" src/persistence/storage.py`) on
2026-09-08: every `INSERT`/`SELECT` statement is a literal triple-quoted
string with `?` placeholders and a separate parameter tuple.

### 2.2 Prompt injection (Phase 3 agentic layer)
The retrieved-chunk text fed into the LLM narrative prompt
(`src/agentic/narrative.py`) originates from a company's own SEC filing —
technically untrusted, attacker-influenceable input the moment this
pipeline is pointed at an adversarial or compromised filer (a company could
in principle embed adversarial text in a public risk-factors section).
Two layers of defense, deliberately not relying on the LLM alone:

1. **Prompt-level instruction**: `SYSTEM_PROMPT` explicitly tells the model
   to treat every retrieved passage strictly as data to cite, never as
   instructions, and to ignore any text that looks like an instruction.
   This is a real but incomplete defense — prompt instructions can
   sometimes be overridden by sufficiently adversarial injected text, which
   is exactly why layer 2 exists.
2. **Deterministic, non-LLM grounding critic** (`src/agentic/critic.py`):
   after generation, `check_grounding()` regex-parses every
   `[[metric:NAME]]` / `[[chunk:ID]]` citation the model actually emitted
   and checks it against the *real* set of metric names and chunk ids that
   were actually retrieved for this request. An injected instruction in a
   filing might change what the model chooses to *say*, but it cannot make
   the critic accept a citation to a chunk id that was never actually
   retrieved, because the critic's valid-id set comes from the retrieval
   step's own output, not from anything the model claims. A memo whose
   critic report fails is routed to `CRITIC_FAILED` status by
   `src/agentic/approval.py` and cannot be approved without a human
   reviewer supplying an explicit, recorded override reason
   (`approve(..., override_reason=...)`) — there is no code path that lets
   a failed-critic memo silently become "approved."
3. This is tested with a scripted adversarial case in
   `tests/unit/test_narrative_and_approval.py::test_draft_narrative_with_hallucinating_llm_fails_critic`
   and `tests/unit/test_critic.py`, which construct narratives containing
   exactly the failure modes described above (a citation to a chunk id that
   doesn't exist, an uncited numeric claim) and assert the critic catches
   them — this was verified against a fake, scripted LLM client, not a live
   model, since this project's own development sandbox cannot reach a real
   LLM (see below). The critic's regex-based logic is itself deterministic
   Python, independently unit-tested, and does not depend on the LLM
   behaving well.
4. **ADR-011 update — the critic matters more, not less, with a local
   model.** The default real backend (`OllamaLLMClient`) runs a small,
   free, locally-hosted model rather than a large hosted one. Smaller
   models instruction-follow less reliably — they are *more* likely to
   occasionally skip a citation or phrase a claim loosely than a large
   hosted model would. This makes the deterministic critic's job more
   important, not less: it catches a citation-format slip from a small
   local model exactly the same way it would catch a large model's
   hallucination, and routes either case to the same human-approval gate.
   This is a genuine argument for keeping the critic non-LLM-based
   regardless of which model backend is in use.

**Finding**: none required fixing — this was designed in from Phase 3
(ADR-006) rather than retrofitted. **Residual risk**: the critic checks
*citation grounding*, not narrative *correctness of tone/emphasis* — a
technically-well-cited narrative could still subtly mischaracterize a
metric's significance. This is why `docs/00_business_case.md` and
`ADR-006` both position the narrative output as a human-reviewed draft, not
an autonomous output, and the approval workflow enforces a human decision
point before any memo is considered final.

## 3. Network-facing client hardening

- **ADR-011 note on data exposure**: `OllamaLLMClient` (the default real
  LLM backend) calls `http://localhost:11434` by default — a server on the
  *same machine*, not a third party. Filing text, computed metrics, and
  generated narratives never leave the machine running this project. This
  is a strictly stronger privacy posture than any cloud LLM API, where
  that same content would be sent to a third-party service's servers.
  `OllamaLLMClient` also has no embedded credential of any kind (there is
  nothing to leak) since Ollama's local API requires none.
- `SecEdgarClient` sends a mandatory, descriptive `User-Agent` header
  (SEC's own published requirement) and self-throttles to 4 req/s by
  default, well under SEC's documented ~10 req/s limit
  (`src/config.py`), specifically to avoid ever tripping SEC's automated
  blocking — a reliability property, not just courtesy.
- All outbound calls use `requests` with an explicit `timeout` (never an
  unbounded/hanging request) and a bounded retry count with exponential
  backoff (`src/ingestion/sec_edgar_client.py`, `src/ingestion/fred_client.py`)
  — a transient failure cannot spin forever.
- **Bug found and fixed in this phase**: `SecEdgarClient._get()` retried
  correctly on network exceptions (`requests.RequestException`) but, after
  exhausting all retries, re-raised the *raw* `requests` exception instead
  of wrapping it in `SecEdgarError` — inconsistent with the 403/429 path,
  which already wrapped its failure in `SecEdgarRateLimited`. This breaks
  the contract callers (`xbrl_facts.py`, `cli.py`) rely on when they
  specifically catch `SecEdgarError`, meaning a real, persistent network
  outage would have propagated as an unhandled exception type instead of
  hitting the intended graceful error/data-quality-issue path. Fixed by
  wrapping any un-wrapped exception in `SecEdgarError` immediately before
  the final `raise`. Regression test:
  `tests/unit/test_sec_edgar_client_retry.py::test_persistent_network_error_eventually_raises`
  (previously failing, now passing — see the 6-test retry suite added this
  phase, all using a scripted fake `requests.Session`, no real network or
  real sleeping, so they run in ~1 second and deterministically).
- HTTPS is used for every outbound call (`https://data.sec.gov`,
  `https://api.stlouisfed.org`, `https://api.anthropic.com`); no client
  disables TLS certificate verification anywhere in the codebase (verified:
  no `verify=False` appears in `src/`).

## 4. Dependency vulnerability scan

Run with `pip-audit` (2026-09-08), against this project's actual
`requirements.txt`:

**Before fix**: 1 known vulnerability — `pytest 8.4.2`, advisory
`PYSEC-2026-1845`, fixed in `pytest>=9.0.3`. This was a real finding from a
real scan, not a hypothetical.

**Fix applied**: `requirements.txt`'s pytest constraint was raised from
`>=8.0,<9.0` to `>=9.0.3,<10.0`; the project's venv was upgraded to
`pytest 9.1.1` and the full test suite (54 tests, see Phase 5's regression
run below) was re-run to confirm no breakage from the upgrade.

**After fix**: `pip-audit -r requirements.txt` → `No known vulnerabilities
found` (all of: requests, pydantic, python-dotenv, scikit-learn, numpy,
scipy, streamlit, pytest, pytest-cov, and their transitive dependencies).

This scan only covers Python dependencies actually declared in
`requirements.txt`; it does not cover the optional `anthropic` SDK (not
installed in this environment, since no live LLM call was made) — a user
who installs it should re-run `pip-audit` themselves, which `README.md`
now notes.

## 5. Data handling and provenance integrity

- Every financial figure in this project is a typed `FactPoint`
  (`src/ingestion/models.py`) carrying its source XBRL tag, filing
  accession number, and filed date — never a bare float divorced from its
  source (ADR-004). This is a data-integrity control as much as an
  engineering-quality one: it makes it possible to audit exactly which SEC
  filing produced every number in a memo.
- The system never fabricates a missing figure. Both `altman_z.py` and
  `piotroski.py` raise `InsufficientDataError` (or, in the snapshot layer,
  record a `DataQualityIssue`) rather than defaulting a missing concept to
  zero — a defaulted zero in a leverage or liquidity ratio would silently
  produce a wrong, confidently-stated risk score, which in this domain is
  worse than a visible failure.

## 6. Explicitly out of scope for this project's threat model

- **Multi-tenant authentication/authorization**: this is a single-analyst
  local tool, not a hosted multi-user service. If it were ever deployed as
  a shared service, an auth layer would need to be added — not attempted
  here, and not claimed to exist.
- **Denial-of-service resilience at scale**: the rate-limiting logic
  protects *this client* from getting blocked by SEC/FRED; it is not
  designed to defend a hosted deployment against a hostile flood of
  requests.
- **Supply-chain signing/SBOM verification**: `pip-audit` checks known
  CVEs/advisories against declared versions; it does not verify package
  signatures or build provenance. Reasonable for a research tool; would
  need strengthening for a regulated production deployment.

## Summary

One real bug (exception-wrapping inconsistency in the SEC EDGAR retry path)
and one real dependency vulnerability (pytest CVE) were found and fixed
during this review, both verified by tests/scans actually run in this
environment, not asserted. No SQL injection, secret-leakage, or unbounded
network path issues were found in the current codebase. The one significant
residual risk — prompt injection via filing text partially defeating the
LLM's own instruction-following — is mitigated by a second, independent,
non-LLM verification layer (the grounding critic) by design, not left to
the model's good behavior alone.
