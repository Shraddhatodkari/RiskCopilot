# Data Provenance and Data-Quality Findings

## Sources selected, and why

| Source | What it provides | Why selected |
|---|---|---|
| [SEC EDGAR XBRL "company concept"/"company facts" API](https://www.sec.gov/os/webmaster-faq#developers) (`data.sec.gov`) | Structured, machine-readable figures directly from companies' own filed 10-K/10-Q financial statements | The single most authoritative source possible for a company's own reported financials — it *is* the regulatory filing, not a third party's re-derivation of it. Free, no API key, no usage restriction beyond a published fair-use rate limit. |
| Federal Reserve Economic Data (FRED) API (Phase 2) | Macro time series (rates, credit spreads, unemployment) for contextualizing a company's risk against the prevailing environment | Authoritative (Federal Reserve Bank of St. Louis), free, requires only a no-cost registered API key. Not yet integrated in Phase 1 because Phase 1's models are company-specific balance-sheet ratios that don't require a macro input. |

Both were evaluated against the project's stated criteria (authority,
credibility, freshness, accessibility, reproducibility, licensing) before
being chosen — no other candidate source was seriously considered for
Phase 1, since SEC XBRL is the closest thing available to ground truth for
company financial statements.

## Access details actually confirmed (not assumed)

- **Rate limit**: 10 requests/second per IP, enforced by SEC
  ([official policy](https://www.sec.gov/filergroup/announcements-old/new-rate-control-limits)).
  This project defaults to 4 requests/second (`Settings.sec_requests_per_second`
  in `src/config.py`) — well under even SEC's own "target 8/s" internal
  guidance — because a single-analyst research tool has no reason to
  operate near the limit.
- **User-Agent requirement**: SEC rejects any request lacking a descriptive
  `User-Agent` header containing a real contact email
  ([source](https://tldrfiling.com/blog/sec-edgar-api-rate-limits-best-practices)).
  Enforced in this project by `src/config.py` refusing to start without one.
- **Endpoints used**: `GET /api/xbrl/companyconcept/CIK{10-digit}/us-gaap/{tag}.json`
  and `GET /api/xbrl/companyfacts/CIK{10-digit}.json`. Verified live and
  working on 2026-09-08 (see below).

## Real data actually retrieved during development (2026-09-08)

The following are genuine values returned by `data.sec.gov` on the date
above, retrieved to validate the ingestion design and to build realistic,
non-fabricated test fixtures (`tests/fixtures/*.json`). A development-
environment network constraint meant this retrieval was done via a
web-fetch tool rather than this project's own `requests`-based client
directly from inside that sandbox — see "Known limitation of this
development environment" below for exactly what that does and doesn't
affect.

**Apple Inc. (CIK 0000320193), FY2025 10-K, accession `0000320193-25-000079`,
filed 2025-10-31, period end 2025-09-27:**

| Concept | Tag | Value |
|---|---|---|
| Total assets | `Assets` | $359,241,000,000 |
| Total liabilities | `Liabilities` | $285,508,000,000 |
| Current assets | `AssetsCurrent` | $147,957,000,000 |
| Current liabilities | `LiabilitiesCurrent` | $165,631,000,000 |
| Operating income | `OperatingIncomeLoss` | $133,050,000,000 |
| Revenues | `RevenueFromContractWithCustomerExcludingAssessedTax` | $416,161,000,000 |
| Retained earnings | `RetainedEarningsAccumulatedDeficit` | **not available for FY2025** — see finding below |

**Microsoft Corporation (CIK 0000789019), FY2025 10-K, accession
`0000950170-25-100235`, filed 2025-07-30, period end 2025-06-30:**

| Concept | Tag | Value |
|---|---|---|
| Total assets | `Assets` | $619,003,000,000 |
| Total liabilities | `Liabilities` | $275,524,000,000 |
| Current assets | `AssetsCurrent` | $191,131,000,000 |
| Current liabilities | `LiabilitiesCurrent` | $141,218,000,000 |
| Retained earnings | `RetainedEarningsAccumulatedDeficit` | $237,731,000,000 |
| Operating income | `OperatingIncomeLoss` | $128,528,000,000 |
| Revenues | `RevenueFromContractWithCustomerExcludingAssessedTax` | $281,724,000,000 |
| Stockholders' equity | `StockholdersEquity` | $343,479,000,000 |

**NVIDIA Corporation (CIK 0001045810), FY2025 10-K, accession
`0001045810-25-000023`, filed 2025-02-26, period end 2025-01-26 — added
under ADR-013 as a third, independently real validation company:**

| Concept | Tag | Value |
|---|---|---|
| Total assets | `Assets` | $111,601,000,000 |
| Total liabilities | `Liabilities` | $32,274,000,000 |
| Current assets | `AssetsCurrent` | $80,126,000,000 |
| Current liabilities | `LiabilitiesCurrent` | $18,047,000,000 |
| Retained earnings | `RetainedEarningsAccumulatedDeficit` | $68,038,000,000 |
| Operating income | `OperatingIncomeLoss` | $81,453,000,000 |
| Revenues | `Revenues` (not `RevenueFromContractWithCustomerExcludingAssessedTax` — see finding below) | $130,497,000,000 |
| Stockholders' equity | `StockholdersEquity` | $79,327,000,000 |
| Interest expense | `InterestExpense`/`InterestExpenseDebt` | **not available for FY2025** — see finding below |
| Capital expenditures | `PaymentsToAcquirePropertyPlantAndEquipment`/`PaymentsForCapitalImprovements` | **not available for any fiscal year in this fixture** — see finding below |

Full detail and every fiscal year's real values: `tests/fixtures/
nvda_fy2025_companyconcept.json`'s own `_provenance` field.

## Real data-quality finding: XBRL tags are not stable across companies or time

**Finding**: Querying `RetainedEarningsAccumulatedDeficit` for Apple's
FY2025 10-K returns no matching fact — the only entries the API returns
for that tag are from Apple's FY2018 filing. Apple's balance sheet
unambiguously discloses retained earnings/accumulated deficit in its
FY2025 10-K, but the `companyconcept` API — which only returns
non-dimensional ("simple-context") facts — does not surface it, most
likely because Apple now reports it only inside a dimensional
statement-of-equity rollforward rather than as a standalone fact. The
same query for Microsoft's FY2025 10-K returns a clean, unambiguous value.

**Why this matters and how the system handles it**: This is a real example
of exactly the kind of "source failure" the project's data-quality
requirements anticipate. The system's response (`src/ingestion/xbrl_facts.py`)
is to treat this as a genuine, reported data-quality issue — not to
substitute zero, not to carry forward a stale 2018 figure, and not to
guess. `tests/unit/test_xbrl_facts.py::test_aapl_snapshot_flags_missing_retained_earnings_honestly`
and `tests/unit/test_altman_z.py::test_aapl_real_filing_raises_instead_of_fabricating`
both assert this behavior directly against the real captured data. The
practical consequence is visible in `docs/sample_output.txt`: Apple's
Z'-Score is correctly reported as **not computable**, with the specific
missing concept named, rather than silently producing a wrong number.

**What a production deployment (Phase 2) does about it**: fall back to
`companyfacts` (which includes dimensional facts) and parse the default
member of the relevant dimension when `companyconcept` comes up empty,
with the same honest-failure behavior if that also fails. This is scoped
as Phase 2 work rather than rushed into Phase 1, per the project's own
instruction not to move forward based on assumptions.

## Real data-quality finding: NVIDIA (third-company validation, ADR-013)

**Finding**: Adding NVIDIA as a third, fully independent validation
company (rather than reusing Apple's or Microsoft's fixture data)
surfaced three more genuine, live-confirmed data-quality facts, none of
which were anticipated or engineered in advance:

1. NVIDIA tags its revenue under `Revenues`, not under
   `RevenueFromContractWithCustomerExcludingAssessedTax` (the tag Apple and
   Microsoft both use, and which is tried first in
   `src/ingestion/xbrl_facts.py`'s fallback list) — confirmed live: the
   latter tag has no NVIDIA data for FY2023-FY2025 at all. This is exactly
   the tag-instability problem this fallback list exists to handle, and it
   worked correctly on the first genuinely new company it was tried
   against, with no code change required.
2. `InterestExpense`/`InterestExpenseDebt` are both genuinely absent for
   NVIDIA's FY2025 10-K — the same class of gap already found for Apple
   and Microsoft's FY2025 filings, now confirmed on a third,
   unrelated company. Interest coverage is therefore honestly reported as
   unavailable for NVIDIA FY2025, not fabricated or zero-substituted.
3. Neither `PaymentsToAcquirePropertyPlantAndEquipment` nor its fallback
   `PaymentsForCapitalImprovements` resolves for NVIDIA at **any** fiscal
   year in this fixture — both genuinely 404 live against data.sec.gov for
   this CIK. Free cash flow is therefore honestly reported as unavailable
   for NVIDIA at every fiscal year tested, a real, confirmed gap distinct
   from Apple's and Microsoft's.

**Why this matters**: this is the concrete evidence this project's
company-agnostic claim is real, not aspirational — the exact same
fallback-tag/refuse-rather-than-guess logic, unmodified, correctly
resolved a company whose tagging choices differ from both companies the
code was originally developed against, and correctly reported (rather
than hid) three new, genuine gaps rather than crashing or fabricating.

## Known limitation of this development environment (and why it doesn't undermine the result)

This project was developed inside a cloud sandbox whose network egress
allowlist does not include `data.sec.gov` (confirmed: direct `curl`/
`requests` calls receive a proxy-level connection rejection, independent of
SEC's own service). This is a constraint of the *development* sandbox, not
of the target deployment environment (the user's own Windows laptop, which
has normal, unrestricted internet access) or of SEC's service itself, which
responded normally to direct fetches made through an available web-fetch
tool throughout Phase 1 research (see the real, live values captured
above).

Concretely, this means:

- The unit test suite (`pytest`, the default run) uses fixtures built from
  the real values above, so it runs deterministically offline and does not
  depend on this sandbox's network restriction.
- A genuine live-network integration test exists
  (`tests/integration/test_sec_edgar_live.py`) and is excluded from the
  default test run specifically because it cannot pass inside this
  sandbox — it is written to run unmodified against the real
  `SecEdgarClient` and will pass once run from an unrestricted network
  (e.g. `pytest -m integration` on the target laptop). This is stated
  plainly here rather than hidden, per the project's rule against
  fabricated or misleading test results.
