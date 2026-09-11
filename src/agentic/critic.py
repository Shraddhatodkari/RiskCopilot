"""
Deterministic grounding critic (ADR-006's "critic/validator step") for
LLM-generated risk narratives.

This is the load-bearing safety component of the Phase 3 design, and it is
intentionally NOT itself an LLM call: it is plain, tested Python string/
regex logic, for the same reason financial calculations are deterministic
(ADR-005) — a validator whose own judgment can hallucinate is not a
validator. Its job is narrow and mechanical:

1. Every citation marker in the generated text (format: `[[chunk:ID]]` for
   a retrieved filing passage, `[[metric:NAME]]` for a Phase 1/2 computed
   number) must reference something that was actually supplied to the
   narrative generator — never a citation to a chunk or metric that was
   never retrieved/computed.
2. Every sentence containing a genuine numeric claim must carry at least
   one citation marker somewhere in it — a heuristic, not a proof of
   correctness, but a real, mechanically-checkable proxy for "this
   specific figure claims a source" that catches the most dangerous
   failure mode: an LLM stating an invented number with unearned
   confidence. A bare 4-digit calendar year (e.g. "FY2025", "in 2025") is
   the one deliberate exception — it's contextual/event information, not
   a financial figure — so it alone never triggers this requirement; a
   dollar amount, percentage, ratio, or decimal is never exempted, even
   when it happens to contain 4 digits (see `_is_contextual_year_
   reference`'s own docstring for exactly where that line is drawn).

A narrative that fails either check is not discarded silently — the full
`CriticReport` (which citations were bad, which sentences lacked one) is
attached to the memo so a human reviewer sees exactly what to fix, per the
project's human-in-the-loop requirement.

3. Optional, narrower exemption for TRUSTED DETERMINISTIC METRICS: a
   sentence that does nothing but restate a metric RiskCopilot itself
   computed and handed to the LLM (e.g. "an Altman Z-score of 2.01") is
   allowed to skip the citation marker for that number, because the
   number's source of truth is RiskCopilot's own deterministic
   calculation (ADR-005), not the LLM. This is deliberately narrow —
   see `_sentence_metric_status`'s docstring for exactly what does and
   does not qualify — and only ever loosens rule 2 above; rule 1 (every
   citation must reference something real) is completely unaffected.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

_CITATION_PATTERN = re.compile(r"\[\[(chunk|metric):([A-Za-z0-9_\-]+)\]\]")
# A conservative "this sentence makes a numeric claim" detector: any
# sequence of digits (with optional $, %, commas, decimal point). Still
# intentionally over-inclusive rather than under-inclusive as a general
# rule — a false positive here just means "ask for a citation on a
# sentence that arguably didn't need one," while a false negative would
# let an uncited real number through.
_NUMERIC_CLAIM_PATTERN = re.compile(r"[\$]?\d[\d,]*(\.\d+)?%?")
# The one deliberate exception to that over-inclusive rule: a BARE 4-digit
# calendar year (e.g. the "2025" inside "FY2025" or "tariffs announced in
# 2025") is contextual/event/fiscal-year information, not a financial
# numeric claim, and forcing a citation on an otherwise purely qualitative
# sentence just because it names a year is a real false positive, not
# caution. This exemption is narrow by construction: the match must be
# the ENTIRE token with nothing else attached, so it never fires on a
# dollar amount, percentage, ratio, or decimal that merely happens to
# contain 4 digits — "$2025", "2025%", "20.25", and "8/9" all still match
# as genuine numeric claims below, exactly as before this exemption.
_BARE_YEAR_PATTERN = re.compile(r"^(?:19|20)\d{2}$")
_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+")


def _is_contextual_year_reference(token: str) -> bool:
    """True only for a matched token that is ENTIRELY a plain 1900-2099
    4-digit number — nothing else attached. A dollar sign, percent sign,
    comma, or decimal point anywhere in the same token means it isn't a
    bare year and this returns False, so `$2025`, `2025%`, `20.25`, and
    any longer number like `136,162,000,000` are never exempted."""
    return bool(_BARE_YEAR_PATTERN.match(token))


def _sentence_has_genuine_numeric_claim(sentence: str) -> bool:
    return any(
        not _is_contextual_year_reference(match.group(0))
        for match in _NUMERIC_CLAIM_PATTERN.finditer(sentence)
    )


# --- Trusted deterministic metric exemption -------------------------------
#
# `trusted_metrics` (an optional, additive parameter on check_grounding) is
# the exact {metric_name: description} dict RiskCopilot handed to the LLM
# for this narrative (e.g. {"altman_z_score": "2.01 (grey zone, FY2025)"}).
# The helpers below use it to recognize a sentence that is purely restating
# one of those already-trusted numbers, so rule 2 above doesn't demand a
# citation marker for something whose correctness never depended on the
# LLM in the first place. Everything here is additive: when no
# trusted_metrics are supplied, every helper is a no-op and behavior is
# byte-for-byte identical to the original citation-only check.
_METRIC_KEYWORD_ALIASES: dict[str, tuple[str, ...]] = {
    "altman_z_score": ("altman", "z-score", "z'-score", "z score"),
    "piotroski_f_score": ("piotroski", "f-score", "f score"),
}
# Filing-derived risk language: a sentence that pairs a trusted metric
# value with any of these is making a claim ABOUT the filing (a risk,
# a relationship, a cause), not just reporting a number — so it still
# needs a real [[chunk:ID]] citation. This is what keeps a narrative like
# "the Piotroski F-score of 6/9 suggests heightened cybersecurity risk"
# failing exactly as before: the trusted number alone is fine, but the
# unsupported relationship to a filing risk factor is not.
_RISK_TOPIC_KEYWORDS = (
    "risk", "cybersecurity", "cyberattack", "security", "privacy",
    "regulatory", "compliance", "litigation", "legal", "competition",
    "covenant", "lawsuit", "breach", "vulnerab", "reputation",
)
# Same shape as _NUMERIC_CLAIM_PATTERN but also matches an "X/9"-style
# fraction (the Piotroski F-Score's native format) as one atomic token,
# so "6/9" is compared as a whole rather than as the two numbers 6 and 9.
_VALUE_TOKEN_PATTERN = re.compile(r"\d+/\d+|[\$]?\d[\d,]*(?:\.\d+)?%?")
_FISCAL_YEAR_PATTERN = re.compile(r"FY\s?(\d{4})", re.IGNORECASE)
_BARE_YEAR_FINDALL_PATTERN = re.compile(r"(?:19|20)\d{2}")


def _metric_keywords(metric_name: str) -> tuple[str, ...]:
    """Known aliases for the two metrics this project actually computes,
    with a generic fallback (underscores -> spaces) for any future
    deterministic metric added to the engine."""
    return _METRIC_KEYWORD_ALIASES.get(metric_name, (metric_name.replace("_", " "),))


def _genuine_value_tokens(text: str) -> list[str]:
    """Every number-like token in `text` that isn't a bare calendar year
    (an "X/9" fraction is never mistaken for one)."""
    tokens = []
    for match in _VALUE_TOKEN_PATTERN.finditer(text):
        token = match.group(0)
        if "/" in token or not _is_contextual_year_reference(token):
            tokens.append(token)
    return tokens


def _expected_value_and_year(description: str) -> tuple[str | None, str | None]:
    """Pulls the one real value token (e.g. '2.01' or '6/9') and the
    fiscal year (e.g. '2025') out of a trusted-metric description string
    such as '2.01 (grey zone, FY2025)' or '6/9 (FY2025)'."""
    year_match = _FISCAL_YEAR_PATTERN.search(description)
    fiscal_year = year_match.group(1) if year_match else None
    values = _genuine_value_tokens(description)
    value = values[0] if values else None
    return value, fiscal_year


def _sentence_metric_status(sentence: str, trusted_metrics: dict[str, str]) -> tuple[bool, bool]:
    """Returns (is_pure_trusted_restatement, has_mismatch) for one sentence
    against the exact trusted deterministic metrics passed to the LLM.

    is_pure_trusted_restatement: every genuine numeric token in the
    sentence is explained by a trusted metric's real value, the fiscal
    year (if stated) matches, and the sentence carries no filing-risk
    language — so this sentence needs no citation for its number(s).

    has_mismatch: a recognized metric keyword appears together with a
    value or fiscal year that CONTRADICTS the trusted metric. This is a
    hard failure regardless of citation — a wrong deterministic figure is
    worse than an uncited one, and a citation marker doesn't excuse it.

    A metric never supplied in `trusted_metrics` (an invented metric, or
    one genuinely unavailable this fiscal year) gets no special treatment
    at all here — it falls straight through to the ordinary citation
    check in check_grounding, exactly as before this exemption existed.
    """
    if not trusted_metrics:
        return False, False

    sentence_lower = sentence.lower()
    sentence_tokens = _genuine_value_tokens(sentence)
    if not sentence_tokens:
        return False, False

    mentioned_any_metric = False
    has_mismatch = False
    matched_values: set[str] = set()

    for metric_name, description in trusted_metrics.items():
        if not any(kw in sentence_lower for kw in _metric_keywords(metric_name)):
            continue
        mentioned_any_metric = True
        expected_value, expected_year = _expected_value_and_year(description)

        if expected_value and expected_value in sentence_tokens:
            matched_values.add(expected_value)
        elif expected_value:
            has_mismatch = True  # named metric, but no matching value present

        if expected_year:
            fy_years = {m.group(1) for m in _FISCAL_YEAR_PATTERN.finditer(sentence)}
            bare_years = set(_BARE_YEAR_FINDALL_PATTERN.findall(sentence))
            stated_years = fy_years | bare_years
            if stated_years and expected_year not in stated_years:
                has_mismatch = True  # right score, wrong fiscal year

    if has_mismatch or not mentioned_any_metric:
        return False, has_mismatch

    leftover = [tok for tok in sentence_tokens if tok not in matched_values]
    has_risk_topic = any(kw in sentence_lower for kw in _RISK_TOPIC_KEYWORDS)
    is_pure_restatement = not leftover and not has_risk_topic
    return is_pure_restatement, False


class CriticReport(BaseModel):
    passed: bool
    ungrounded_citations: list[str]  # cited but not in the valid set
    uncited_numeric_sentences: list[str]  # contain a number, no citation marker
    all_citations_found: list[str]


def check_grounding(
    narrative_text: str,
    valid_chunk_ids: set[str],
    valid_metric_names: set[str],
    trusted_metrics: dict[str, str] | None = None,
) -> CriticReport:
    """`trusted_metrics` is optional and additive: the exact {metric_name:
    description} dict passed to the LLM for this narrative (see
    src/agentic/narrative.py). Omitting it (or passing None/{}) reproduces
    the original citation-only behavior exactly — every existing caller
    that doesn't yet pass it keeps working unchanged."""
    trusted_metrics = trusted_metrics or {}
    found_citations = _CITATION_PATTERN.findall(narrative_text)
    all_citations = [f"{kind}:{ident}" for kind, ident in found_citations]

    ungrounded = []
    for kind, ident in found_citations:
        valid_set = valid_chunk_ids if kind == "chunk" else valid_metric_names
        if ident not in valid_set:
            ungrounded.append(f"{kind}:{ident}")

    uncited_numeric_sentences = []
    for sentence in _SENTENCE_SPLIT_PATTERN.split(narrative_text):
        is_pure_restatement, has_mismatch = _sentence_metric_status(sentence, trusted_metrics)
        if has_mismatch:
            # A named deterministic metric was misstated (wrong value or
            # wrong fiscal year) — always a failure, citation or not: the
            # figure's correctness is what's actually at stake here, and
            # a citation marker doesn't make a wrong number right.
            uncited_numeric_sentences.append(sentence.strip())
            continue
        if is_pure_restatement:
            # A plain, correct restatement of a trusted deterministic
            # value needs no citation — RiskCopilot's own computation is
            # already its source of truth (ADR-005).
            continue
        if _sentence_has_genuine_numeric_claim(sentence) and not _CITATION_PATTERN.search(sentence):
            uncited_numeric_sentences.append(sentence.strip())

    passed = not ungrounded and not uncited_numeric_sentences
    return CriticReport(
        passed=passed,
        ungrounded_citations=ungrounded,
        uncited_numeric_sentences=uncited_numeric_sentences,
        all_citations_found=all_citations,
    )
