# Historical Distress Backtest (Phase 4)

## Method

The business case (`docs/00_business_case.md`) commits to an honest,
falsifiable accuracy claim rather than an assumed one: does the Altman
Z'-Score (`src/analysis/altman_z.py`) actually flag companies that are now,
as a matter of real public record, known to have filed for Chapter 11
bankruptcy?

Two real companies were selected because their bankruptcy filings are
well-documented, unambiguous public events with a specific date:

- **Bed Bath & Beyond Inc.** (CIK 0000886158) — filed Chapter 11 on
  **2023-04-23**.
- **Party City Holdco Inc.** (CIK 0001592058) — filed Chapter 11 on
  **2023-01-17**.

For each, the last two available annual 10-Ks before/around the filing were
pulled directly from `data.sec.gov`'s real XBRL API and run through the
unmodified `compute_altman_z_prime` function — the exact same code path
used everywhere else in this project, not a separate "backtest version" of
the model.

This is a small, real N=2 study, not a statistically powered claim. It is
reported as such. A larger backtest is scoped as future work below.

## A real data-extraction error, caught and corrected

While assembling this fixture, an initial fetch reported Party City's
FY2021 current liabilities as $713,877,000. Cross-checking against the
*complete, unfiltered* raw JSON array for that XBRL tag showed this was
actually the **FY2020** comparative figure reported inside the same
FY2021 10-K — the real FY2021 value is $570,059,000. The error was caught
specifically because this project's own working method (documented in
`tests/fixtures/distress_backtest_cases.json`'s provenance note) requires
cross-checking a filtered/summarized answer against the complete raw data
before trusting it for anything that feeds a financial calculation.

This is worth stating plainly rather than quietly fixing: it is a live
demonstration of exactly the failure mode `src/ingestion/xbrl_facts.py`'s
strict, code-based filtering (form + fiscal year + fiscal period + a
plausible date range, never free-text summarization) exists to prevent in
the production pipeline. Manually assembling backtest fixtures by reading
API responses is more error-prone than the automated pipeline itself — a
genuinely useful, if slightly uncomfortable, finding about this project's
own methodology.

## Results

| Company | Fiscal Year | Period End | Filed | vs. Bankruptcy Filing | Z'-Score | Zone | Result |
|---|---|---|---|---|---|---|---|
| Bed Bath & Beyond | 2021 | 2022-02-26 | 2022-04-21 (before) | 421 days early | 2.93 | **Safe** | **Missed** |
| Bed Bath & Beyond | 2022 | 2023-02-25 | 2023-06-14 (**after**) | 57 days early | -2.08 | **Distress** | Flagged |
| Party City | 2021 | 2021-12-31 | 2022-02-28 (before) | 382 days early | 0.76 | **Distress** | Flagged |
| Party City | 2022 | 2022-12-31 | 2024-03-28 (**after**) | 17 days early | -1.48 | **Distress** | Flagged |

**3 of 4 real pre-event snapshots (75%) were flagged** as grey-or-distress
zone; all 3 flags were the strongest ("distress") classification, not a
borderline "grey" call. **1 of 4 was a genuine miss**: Bed Bath & Beyond's
FY2021 10-K, filed about 14 months before the actual bankruptcy, scored
2.93 — just barely in the "safe" zone.

Two real filing-timing facts worth noting for interview defensibility:
first, both companies' *final* pre-bankruptcy 10-K was actually filed
*after* their Chapter 11 petition date (companies in acute distress
routinely delay financial reporting) — the period the filing *covers* is
still pre-petition, but a real production deployment could not have relied
on that specific filing to warn an analyst in advance, only earlier ones.
Second, Party City's earlier (FY2021) filing genuinely was available well
in advance (382 days) and correctly flagged distress — a real example of
useful lead time this system can provide.

## Why the miss happened (a real, investigated limitation, not hand-waved)

Bed Bath & Beyond's FY2021 retained-earnings figure was $9.67 billion
against total assets of only $5.13 billion — an X2 (retained
earnings/total assets) ratio of 1.88, which single-handedly pushed the
score positive despite clearly deteriorating operating performance (a
$408M operating loss that year). This is a real, known limitation of
retained-earnings-based distress models applied to mature retailers with
decades of accumulated historical profit sitting alongside an enormous,
debt-funded treasury stock balance (BBBY's well-documented history of large
share buybacks): retained earnings is a backward-looking cumulative
figure, not a leading indicator, and can stay large and positive for years
after a business starts to deteriorate. This is exactly the kind of
model limitation `docs/00_business_case.md` commits to surfacing rather
than hiding, and it is a legitimate, specific talking point for an
interview about why a single deterministic ratio model is a screening
signal, not a verdict — reinforcing this project's human-in-the-loop
design (ADR-006).

## Limitations of this backtest itself

- N=2 companies, 4 snapshots — far too small to claim a general accuracy
  rate. It demonstrates the evaluation *methodology* works and produces
  one real, honest, mixed result; it does not establish a validated hit
  rate.
- No control group of similarly-sized companies that did **not** go
  bankrupt is included yet, so this cannot yet distinguish "correctly
  flags real distress" from "flags most retailers as distressed
  regardless." Adding one (e.g., running the same model against a
  size-matched retailer that remained solvent over the same period) is the
  single highest-value next step for this evaluation and is scoped into
  Phase 4.x of `docs/04_roadmap.md`.
- Only the Altman Z'-Score was backtested. Piotroski F-Score requires two
  consecutive fiscal years of clean data, which — consistent with this
  project's honest-failure design — was not reliably available for these
  two distressed companies in the time available for this phase; extending
  the backtest to Piotroski is also scoped as future work.
