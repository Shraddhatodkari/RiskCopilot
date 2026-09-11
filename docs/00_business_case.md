# Business Case

## The problem

Credit analysts, corporate lenders, and M&A/financial due-diligence teams
routinely need to answer a narrow but high-stakes question about a
counterparty, borrower, or target: *is this company's financial position
deteriorating, and is that visible in what they've actually filed?*

Today that work is largely manual: pulling 10-Ks/10-Qs, rebuilding ratio
calculations in a spreadsheet, re-deriving them again next quarter to see
what changed, and reading footnotes and MD&A sections for qualitative red
flags. It is repetitive, error-prone (manual XBRL/footnote transcription is
a common source of spreadsheet mistakes), and the deterministic-ratio part
of it adds little analytical value relative to the time it consumes — the
real analytical value is in interpreting the result and deciding what to do
about it, not in re-typing balance sheet figures.

## Who would use this

- Credit analysts and corporate lenders performing ongoing counterparty
  monitoring.
- Private equity / investment banking associates doing financial
  due diligence on a target.
- Corporate development / treasury teams assessing supplier or customer
  concentration risk.
- (Consulting application) a restructuring or corporate-finance advisory
  team needing a fast, defensible first pass across a portfolio of
  counterparties before allocating senior analyst time.

## Why the current process is difficult

- Financial statement data is public but not analysis-ready: XBRL tagging
  is inconsistent across companies and across years for the same company
  (documented concretely in `docs/03_data_provenance.md`), so building a
  reliable pipeline requires real engineering, not just an API call.
  Manual analysts often don't reconcile this and unknowingly compare
  apples-to-oranges figures.
- Deterministic ratio calculation (Altman Z-Score, and Phase 2's Piotroski
  F-Score / Beneish M-Score) is mechanical but must be done correctly and
  consistently across a portfolio — a single sign error or unit
  mismatch (millions vs. dollars) can flip a conclusion.
- Qualitative red flags (going-concern language, covenant renegotiation
  disclosures, related-party transaction growth) are buried in long-form
  text that does not scale to manual review across a large counterparty
  book.

## What this system does about it

1. **Removes the mechanical burden without removing the analyst.** The
   deterministic layer (Phase 1, delivered) computes financial-distress
   indicators directly from a company's own filed XBRL data, with every
   number traced back to a specific filing, so an analyst can verify in
   seconds rather than re-deriving from scratch.
2. **Refuses to guess.** When required data is missing or ambiguous in the
   source filing (a real, observed condition — see
   `docs/03_data_provenance.md`), the system says so explicitly rather than
   silently defaulting to zero or a stale value. This is a deliberate
   design choice: a wrong number that looks confident is worse than a
   flagged gap.
3. **(Phase 3+) Surfaces qualitative risk signals with citations**, so an
   analyst reviews a short, sourced list of candidate red flags instead of
   the entire MD&A section, while an automated critic step rejects any
   generated claim not traceable to the retrieved text.
4. **Keeps a human in the loop where judgment is required** (Phase 3+
   approval gate before a memo is treated as final) — the system produces
   a defensible first draft, not an autonomous credit decision.

## What "good" looks like — KPIs an enterprise could actually track

- **Analyst time per counterparty review**: today's fully-manual baseline
  vs. time-to-first-draft-memo with this tool. This is directly
  measurable by timing the manual process against `python -m src.cli` on
  the same company.
- **Coverage/consistency**: percentage of a counterparty portfolio that can
  be refreshed automatically each quarter without manual re-entry, vs. the
  manual baseline (which typically only covers a prioritized subset due to
  analyst time constraints).
- **Data-quality transparency**: number of data-quality issues surfaced
  and resolved (vs. silently ignored) per review — a metric that is
  invisible in a manual process today and directly counts against the risk
  of an undetected data error driving a wrong conclusion.
- **(Phase 4) Historical backtest hit-rate**: of a set of companies with
  *known, real, publicly documented* financial distress events (Chapter 11
  filings, going-concern qualifications, major restatements), what
  fraction would this system's deterministic score have flagged as
  "grey" or "distress" in the fiscal year(s) before the event, and how many
  quarters of lead time did that provide? This is the project's actual
  accuracy claim, and it will be reported honestly — including where the
  model misses or is too conservative — rather than cherry-picked.

## ROI framing for an enterprise (illustrative, not claimed as achieved)

If a credit team spends, say, 3-4 analyst-hours manually pulling and
ratio-checking one counterparty's financials each quarter, and this
pipeline reduces that to review-and-judgment time on an auto-generated,
sourced draft, the time saved scales linearly with portfolio size — a
meaningful reduction is genuinely a matter of *how many hours are
currently spent on data assembly vs. judgment*, which varies by team and is
exactly the kind of number a real engagement would gather from the client
rather than assume. This is stated here as a framework for the ROI
conversation, deliberately not as a fabricated percentage, in keeping with
the project's rule against invented business impact.

## Risks and where humans must stay in the loop

- The deterministic models (Altman-family Z-Scores) were empirically
  validated on manufacturing/industrial firms; they are known to be
  imperfect for asset-light or high-growth businesses (see the Microsoft
  FY2025 result in `docs/sample_output.txt`, which lands in the "grey"
  zone despite Microsoft's investment-grade credit profile — a real,
  disclosed limitation, not a bug to hide). A score is a screening signal,
  not a verdict.
- Missing/ambiguous XBRL data must be resolved by a human before a memo is
  treated as final when the automated pipeline cannot resolve it itself.
- (Phase 3+) Any LLM-generated qualitative narrative requires the critic
  step to pass AND a human reviewer to approve before the memo is
  finalized — this is a hard gate in the design, not a suggestion.
