"""
Deterministic risk-factor categorization (ADR-013).

Tags a retrieved filing-evidence chunk (heading + body text) with one or
more categories from a fixed taxonomy, using a keyword-rule table — never
an LLM classifying "what kind of risk is this." This is a documented
heuristic (stated plainly below, not hidden): a real 10-K's Item 1A
sub-heading is usually a strong, explicit signal (e.g. "Cybersecurity"),
but keyword matching over free text is inherently approximate — a chunk
can legitimately match more than one category (litigation risk arising
from a cybersecurity breach, say), and a chunk that discusses a risk
using unusual vocabulary this table hasn't seen may be tagged
"uncategorized" rather than forced into the nearest guess. That is a
deliberate, honest design choice: this module's job is to help a reviewer
scan filing evidence by theme, not to claim a definitive legal
classification of each risk factor.

Categories (fixed, documented — not learned or LLM-invented):
liquidity, leverage_refinancing, litigation, cybersecurity, supply_chain,
customer_concentration, competition, regulation, geopolitical,
technology_ai, margin_pressure, going_concern, m_and_a.
"""
from __future__ import annotations

import re

RISK_CATEGORIES = (
    "liquidity",
    "leverage_refinancing",
    "litigation",
    "cybersecurity",
    "supply_chain",
    "customer_concentration",
    "competition",
    "regulation",
    "geopolitical",
    "technology_ai",
    "margin_pressure",
    "going_concern",
    "m_and_a",
)

# Each category's keyword list is matched case-insensitively as whole
# substrings against "heading. text" — deliberately simple and inspectable
# in full here, not tuned per company. Order doesn't matter; a chunk can
# match zero, one, or several categories.
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "liquidity": ("liquidity", "working capital", "cash on hand", "short-term financing", "cash flow may"),
    "leverage_refinancing": (
        "refinance", "refinancing", "refinanced", "leverage", "credit rating",
        "debt covenant", "maturities of", "our indebtedness", "ability to service",
        "access to capital",
    ),
    "litigation": (
        "litigation", "lawsuit", "legal proceeding", "class action", "regulatory investigation",
        "intellectual property claims", "settlement", "legal claims",
    ),
    "cybersecurity": (
        "cybersecurity", "cyber-attack", "cyber-attacks", "cyberattack", "cyberattacks",
        "data breach", "data breaches", "security breach", "security breaches",
        "malware", "ransomware", "denial-of-service", "information security", "unauthorized access",
    ),
    "supply_chain": (
        "supply chain", "supplier", "suppliers", "foundry", "foundries",
        "manufacture", "manufacturing", "manufacturer", "manufacturers",
        "component shortage", "raw material", "subcontractor", "logistics",
    ),
    "customer_concentration": (
        "concentration of", "limited number of customers", "limited number of partners",
        "significant customer", "customer concentration", "few customers", "key customers",
    ),
    "competition": (
        "competitor", "competition", "competitive", "market share", "new entrants",
    ),
    "regulation": (
        "regulation", "regulations", "regulatory", "compliance with laws",
        "government policies", "legislation", "antitrust", "privacy law", "tax law",
    ),
    "geopolitical": (
        "geopolitical", "export control", "trade restriction", "tariff", "sanctions",
        "china", "taiwan", "war", "political instability", "foreign government",
    ),
    "technology_ai": (
        "artificial intelligence", "ai", "machine learning", "emerging technology",
        "rapid technological change", "new technology", "ai services",
    ),
    "margin_pressure": (
        "gross margin", "operating margin", "pricing pressure", "declining prices",
        "cost of revenue", "margins may",
    ),
    "going_concern": (
        "going concern", "substantial doubt", "ability to continue as a going concern",
        "recurring losses", "negative cash flow from operations",
    ),
    "m_and_a": (
        "acquisition", "merger", "divestiture", "integration of acquired",
        "business combination",
    ),
}


def _matches(keyword: str, haystack: str) -> bool:
    """Whole-word/whole-phrase match, not a bare substring check — a naive
    `kw in haystack` check would (and, caught during this module's own
    testing against real filing text, did) false-positive on things like
    "war" inside "malware." `\\b` word boundaries around the whole
    (possibly multi-word) keyword close that class of bug for both
    single words and phrases."""
    pattern = r"\b" + re.escape(keyword.strip()) + r"\b"
    return re.search(pattern, haystack) is not None


def categorize_chunk(heading: str, text: str) -> list[str]:
    """Return every category (from RISK_CATEGORIES) whose keywords appear
    in this chunk's heading or text, case-insensitively, as whole words/
    phrases. Returns an empty list — never a guessed default — if nothing
    matches; callers should display that as "uncategorized," not silently
    drop the chunk."""
    haystack = f"{heading}. {text}".lower()
    matched = [
        cat for cat, keywords in _KEYWORDS.items()
        if any(_matches(kw, haystack) for kw in keywords)
    ]
    return matched
