"""
Tests for the deterministic grounding critic. These are the ones that
matter most in Phase 3: they must actually catch a hallucinated citation
and an uncited number, not just pass a well-behaved example.
"""
from __future__ import annotations

from src.agentic.critic import check_grounding

VALID_CHUNKS = {"aapl-2025-rf-5", "aapl-2025-rf-7"}
VALID_METRICS = {"altman_z_score", "piotroski_f_score"}


def test_well_grounded_narrative_passes():
    text = (
        "The company's Altman Z'-Score is 2.01 [[metric:altman_z_score]], placing it "
        "in the grey zone. A key qualitative risk is supply chain concentration in "
        "single-source components [[chunk:aapl-2025-rf-5]]."
    )
    report = check_grounding(text, VALID_CHUNKS, VALID_METRICS)
    assert report.passed
    assert report.ungrounded_citations == []
    assert report.uncited_numeric_sentences == []


def test_catches_a_hallucinated_metric_citation():
    """The model cites a metric that was never actually provided to it —
    exactly the failure mode this critic exists to catch."""
    text = "The company's EBITDA margin improved to 34% [[metric:ebitda_margin]] this year."
    report = check_grounding(text, VALID_CHUNKS, VALID_METRICS)
    assert not report.passed
    assert "metric:ebitda_margin" in report.ungrounded_citations


def test_catches_a_hallucinated_chunk_citation():
    text = "Management disclosed a material weakness in internal controls [[chunk:fabricated-999]]."
    report = check_grounding(text, VALID_CHUNKS, VALID_METRICS)
    assert not report.passed
    assert "chunk:fabricated-999" in report.ungrounded_citations


def test_catches_an_uncited_specific_number():
    """A real, dangerous failure mode: a plausible-sounding but entirely
    invented figure, stated with no citation at all."""
    text = "Revenue grew 12% year over year, driven by strong iPhone demand."
    report = check_grounding(text, VALID_CHUNKS, VALID_METRICS)
    assert not report.passed
    assert len(report.uncited_numeric_sentences) == 1
    assert "12%" in report.uncited_numeric_sentences[0]


def test_sentence_with_number_and_citation_passes():
    text = "The current ratio is 1.27 [[metric:altman_z_score]], indicating adequate short-term liquidity."
    report = check_grounding(text, VALID_CHUNKS, VALID_METRICS)
    assert report.uncited_numeric_sentences == []


def test_multiple_citations_all_tracked():
    text = (
        "Two risks stand out: supply concentration [[chunk:aapl-2025-rf-5]] and "
        "data security [[chunk:aapl-2025-rf-7]], alongside a Z'-Score of 2.01 "
        "[[metric:altman_z_score]]."
    )
    report = check_grounding(text, VALID_CHUNKS, VALID_METRICS)
    assert report.passed
    assert set(report.all_citations_found) == {
        "chunk:aapl-2025-rf-5",
        "chunk:aapl-2025-rf-7",
        "metric:altman_z_score",
    }


def test_bare_fiscal_year_reference_does_not_require_a_citation():
    """Regression test for a real false positive observed on a live Ollama
    narrative: a purely qualitative sentence that only names a year (e.g.
    "tariffs announced in 2025 and potential retaliatory measures creates
    an environment of significant risk") was being flagged as an uncited
    numeric claim, blocking an otherwise well-grounded memo. A bare
    4-digit calendar year is contextual/event information, not a
    financial figure, and must not by itself force a citation
    requirement."""
    text = (
        "The uncertainty surrounding new U.S. tariffs announced in 2025 and "
        "potential retaliatory measures creates an environment of "
        "significant risk for the company's supply chain."
    )
    report = check_grounding(text, VALID_CHUNKS, VALID_METRICS)
    assert report.uncited_numeric_sentences == []
    assert report.passed


TRUSTED_METRICS = {
    "altman_z_score": "2.01 (grey zone, FY2025)",
    "piotroski_f_score": "6/9 (FY2025)",
}


def test_trusted_deterministic_metric_passes_without_a_citation_marker():
    """Regression test for the real live-dashboard failure: a well-behaved
    narrative restates RiskCopilot's own computed scores in plain prose
    (no [[metric:...]] marker), and the critic must NOT flag that as an
    uncited numeric claim — the number's source of truth is RiskCopilot's
    deterministic calculation, not the LLM."""
    text = (
        "As part of our financial due diligence analysis, we have computed "
        "several key metrics for Microsoft Corporation, including an Altman "
        "Z-score of 2.01 and a Piotroski F-score of 6/9 for the FY2025 period."
    )
    report = check_grounding(text, VALID_CHUNKS, VALID_METRICS, trusted_metrics=TRUSTED_METRICS)
    assert report.uncited_numeric_sentences == []
    assert report.passed


def test_exact_metric_value_mismatch_fails_even_if_cited():
    """The LLM misquoting its own trusted score is worse than an uncited
    one — this must fail regardless of whether a citation is attached."""
    uncited = "The company's Altman Z-score is 9.99, well outside the grey zone."
    cited = "The company's Altman Z-score is 9.99 [[metric:altman_z_score]]."
    for text in (uncited, cited):
        report = check_grounding(text, VALID_CHUNKS, VALID_METRICS, trusted_metrics=TRUSTED_METRICS)
        assert not report.passed, f"expected a mismatched trusted metric to fail: {text!r}"
        assert report.uncited_numeric_sentences == [text]


def test_fiscal_year_mismatch_on_a_trusted_metric_fails():
    """Right score, wrong year — still a misstatement of the trusted fact."""
    text = "The company's Altman Z-score was 2.01 in FY2023."
    report = check_grounding(text, VALID_CHUNKS, VALID_METRICS, trusted_metrics=TRUSTED_METRICS)
    assert not report.passed
    assert report.uncited_numeric_sentences == [text]


def test_invented_metric_not_in_trusted_set_still_requires_citation():
    """A metric RiskCopilot never computed/supplied (only Altman and
    Piotroski were) gets no exemption at all — it's just an ordinary
    uncited numeric claim, exactly as before this feature existed."""
    text = "The company's current ratio of 3.7 indicates strong liquidity."
    report = check_grounding(text, VALID_CHUNKS, VALID_METRICS, trusted_metrics=TRUSTED_METRICS)
    assert not report.passed
    assert report.uncited_numeric_sentences == [text]


def test_trusted_metric_paired_with_unsupported_filing_claim_still_fails():
    """The exemption is narrow: pairing a correct trusted number with a
    filing-risk relationship the retrieved evidence doesn't support must
    still fail — this is the exact 'NVIDIA-type' false negative the
    exemption must not reopen."""
    text = "Given a Piotroski F-score of 6/9, the company faces heightened cybersecurity risk."
    report = check_grounding(text, VALID_CHUNKS, VALID_METRICS, trusted_metrics=TRUSTED_METRICS)
    assert not report.passed
    assert report.uncited_numeric_sentences == [text]


def test_properly_cited_filing_claim_still_passes_alongside_trusted_metrics():
    """A real chunk citation for a qualitative risk claim keeps working
    exactly as before, even when trusted_metrics is supplied."""
    text = (
        "The company's Altman Z-score of 2.01 places it in the grey zone. "
        "A material risk factor is single-source component reliance "
        "[[chunk:aapl-2025-rf-5]]."
    )
    report = check_grounding(text, VALID_CHUNKS, VALID_METRICS, trusted_metrics=TRUSTED_METRICS)
    assert report.uncited_numeric_sentences == []
    assert report.passed


def test_no_trusted_metrics_supplied_falls_back_to_original_behavior():
    """Insufficient-data companies (no computable Altman/Piotroski this
    fiscal year) pass no trusted metrics at all — the exemption must stay
    completely inert, so any numeric claim is still held to the original,
    stricter citation-only standard rather than silently let through."""
    text = "The company's Altman Z-score is 2.01, indicating moderate risk."
    report = check_grounding(text, VALID_CHUNKS, VALID_METRICS, trusted_metrics={})
    assert not report.passed
    assert report.uncited_numeric_sentences == [text]


def test_genuine_numeric_claims_remain_protected_even_near_a_bare_year():
    """The year exemption must be narrow: a dollar amount, a decimal
    ratio, a fraction-style score, and a percentage must all still
    require a citation, including in the same sentence as a bare year, and
    even when a number happens to be 4 digits but is decorated (a dollar
    amount, percentage, or decimal) rather than a plain calendar year."""
    cases = [
        "Operating cash flow was $136,162,000,000 this year.",
        "The Altman Z'-Score of 2.35 places the company in the grey zone.",
        "The Piotroski F-Score of 8/9 indicates a strong credit profile.",
        "Revenue grew 45.6% year over year.",
        "In 2025, revenue grew 12% year over year.",  # bare year + genuine claim together
        "The company reported $2025 in one-time fees during the period.",
        "The margin reached 2025% due to a reporting anomaly.",
    ]
    for text in cases:
        report = check_grounding(text, VALID_CHUNKS, VALID_METRICS)
        assert report.uncited_numeric_sentences == [text], (
            f"expected a genuine numeric claim to still require a citation: {text!r}"
        )
        assert not report.passed


PRECISE_TRUSTED_METRICS = {
    "altman_z_score": "2.1054 (grey zone, FY2025)",
    "piotroski_f_score": "6/9 (FY2025)",
}


def test_correctly_rounded_trusted_metric_passes():
    """Regression test for the reported false alarm: the critic blocked
    'Altman Z-score of 2.11' although 2.11 is the computed 2.1054 rounded
    to two decimals. Coarser correct roundings must pass too."""
    for stated in ("2.11", "2.1", "2", "2.105", "2.1054"):
        text = f"The company's Altman Z-score of {stated} places it in the grey zone."
        report = check_grounding(
            text, VALID_CHUNKS, VALID_METRICS, trusted_metrics=PRECISE_TRUSTED_METRICS
        )
        assert report.passed, f"expected a correct rounding to pass: {stated!r}"
        assert report.uncited_numeric_sentences == []


def test_wrong_value_at_rounded_precision_still_fails():
    """Rounding tolerance must not become 'any nearby number': 2.1054 rounds
    to 2.11, so 2.10 and 2.15 are misstatements, as is 2.2 (it rounds to
    2.1), and 21.05 (a shifted decimal point)."""
    for stated in ("2.10", "2.15", "2.2", "21.05"):
        text = f"The company's Altman Z-score of {stated} places it in the grey zone."
        report = check_grounding(
            text, VALID_CHUNKS, VALID_METRICS, trusted_metrics=PRECISE_TRUSTED_METRICS
        )
        assert not report.passed, f"expected a wrong value to fail: {stated!r}"
        assert report.uncited_numeric_sentences == [text]


def test_more_precision_than_supplied_is_not_a_rounding():
    """The dashboard hands the model '2.11'. If the narrative claims
    '2.1054', those extra digits were not supplied to it, so they cannot be
    verified against the trusted value and must fail."""
    text = "The company's Altman Z-score of 2.1054 places it in the grey zone."
    report = check_grounding(text, VALID_CHUNKS, VALID_METRICS, trusted_metrics={
        "altman_z_score": "2.11 (grey zone, FY2025)",
    })
    assert not report.passed


def test_exact_half_accepts_either_standard_rounding():
    """For a value exactly on a half (2.125), writers round either way
    (2.13 half-up, 2.12 half-even); both are honest roundings."""
    trusted = {"altman_z_score": "2.125 (grey zone, FY2025)"}
    for stated in ("2.13", "2.12"):
        text = f"The company's Altman Z-score of {stated} places it in the grey zone."
        report = check_grounding(text, VALID_CHUNKS, VALID_METRICS, trusted_metrics=trusted)
        assert report.passed, stated


def test_fraction_scores_still_require_an_exact_match():
    """A Piotroski F-score has no meaningful rounding: '6/9' is only
    matched by '6/9'."""
    text = "The company's Piotroski F-score of 7/9 indicates a moderate profile."
    report = check_grounding(
        text, VALID_CHUNKS, VALID_METRICS, trusted_metrics=PRECISE_TRUSTED_METRICS
    )
    assert not report.passed
    assert report.uncited_numeric_sentences == [text]


def test_rounded_value_with_wrong_fiscal_year_still_fails():
    """Rounding tolerance applies to the value only, never to the year."""
    text = "The company's Altman Z-score was 2.11 in FY2023."
    report = check_grounding(
        text, VALID_CHUNKS, VALID_METRICS, trusted_metrics=PRECISE_TRUSTED_METRICS
    )
    assert not report.passed
