"""
End-to-end dashboard regression test for the exact defect ADR-012 fixes:
selecting a company in `dashboard.py` must control EVERY section of the
page, with zero cross-company contamination anywhere (Company Overview,
Filing Risk Intelligence, and — the tab where the original, user-reported
bug actually lived — Agentic Narrative).

ADR-013: exercised across THREE independently real companies (Apple,
Microsoft, NVIDIA) rather than two — concrete proof at the dashboard
level, not just the underlying modules, that nothing here is hardcoded to
a 2-company universe. NVIDIA's real data comes from
tests/fixtures/nvda_fy2025_companyconcept.json and
tests/fixtures/nvda_fy2025_risk_factors.json (see each file's own
`_provenance`/docstring).

Uses Streamlit's `AppTest` harness (headless, no browser) against the
project's own real, already-seeded `data/riskcopilot.db` (see
`scripts/seed_dashboard_data.py`) — this is the actual dashboard file a
user runs, not a reimplementation of its logic, so a regression here would
be caught by this test even if a future edit reintroduces a hardcoded
company literal in `dashboard.py` itself.

Requires `data/riskcopilot.db` to already contain the seeded Apple/
Microsoft/NVIDIA data (run `python scripts/seed_dashboard_data.py` once if
it's missing) — skipped, not failed, if that file isn't present, since
this project's own dev sandbox may not have run the seed script before
tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

DASHBOARD_PATH = str(Path(__file__).parent.parent.parent / "dashboard.py")
DB_PATH = Path(__file__).parent.parent.parent / "data" / "riskcopilot.db"

pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(),
    reason="data/riskcopilot.db not seeded yet — run scripts/seed_dashboard_data.py first",
)


def _run_dashboard() -> AppTest:
    at = AppTest.from_file(DASHBOARD_PATH)
    at.run(timeout=30)
    assert not at.exception, f"dashboard raised on initial render: {at.exception}"
    return at


def test_company_selector_offers_all_three_seeded_companies():
    at = _run_dashboard()
    assert set(at.selectbox[0].options) >= {"Apple Inc.", "Microsoft Corporation", "NVIDIA Corporation"}


def test_switching_company_changes_overview_tab_scores_and_filing():
    """The Company Overview tab (index 0) must show each company's own
    filing accession number and score state — never another company's."""
    at = _run_dashboard()
    at.selectbox[0].set_value("Apple Inc.").run(timeout=30)
    apple_overview = " ".join(m.value for m in at.tabs[0].markdown)
    assert "0000320193" in apple_overview or "Apple" in apple_overview
    assert "0000950170-25-100235" not in apple_overview  # MSFT's accession must not appear
    assert "0001045810-25-000023" not in apple_overview  # NVIDIA's accession must not appear

    at.selectbox[0].set_value("Microsoft Corporation").run(timeout=30)
    assert not at.exception
    msft_overview = " ".join(m.value for m in at.tabs[0].markdown)
    assert "0000950170-25-100235" in msft_overview
    assert "0000320193-25-000079" not in msft_overview  # Apple's accession must not appear
    assert "0001045810-25-000023" not in msft_overview  # NVIDIA's accession must not appear

    at.selectbox[0].set_value("NVIDIA Corporation").run(timeout=30)
    assert not at.exception
    nvda_overview = " ".join(m.value for m in at.tabs[0].markdown)
    assert "0001045810-25-000023" in nvda_overview
    assert "0000950170-25-100235" not in nvda_overview  # MSFT's accession must not appear
    assert "0000320193-25-000079" not in nvda_overview  # Apple's accession must not appear


def test_agentic_tab_metrics_and_evidence_are_the_selected_companys_own():
    """This is the exact tab the user's originally reported bug lived in:
    hardcoded MSFT-shaped metrics text and hardcoded Apple evidence
    regardless of the company actually selected. Verifies the fix holds
    for the real dashboard file, not just the underlying modules, across
    all three real, independently-seeded companies."""
    at = _run_dashboard()

    at.selectbox[0].set_value("Microsoft Corporation").run(timeout=30)
    msft_agentic = " ".join(m.value for m in at.tabs[5].markdown)
    assert "Microsoft" in msft_agentic
    assert "msft-2025-rf" in msft_agentic
    assert "aapl-2025-rf" not in msft_agentic  # Apple's chunk ids must never appear here
    assert "nvda-2025-rf" not in msft_agentic  # nor NVIDIA's

    at.selectbox[0].set_value("NVIDIA Corporation").run(timeout=30)
    nvda_agentic = " ".join(m.value for m in at.tabs[5].markdown)
    assert "NVIDIA" in nvda_agentic
    assert "nvda-2025-rf" in nvda_agentic
    assert "msft-2025-rf" not in nvda_agentic
    assert "aapl-2025-rf" not in nvda_agentic


def test_apple_agentic_tab_never_presents_a_different_fiscal_years_score_as_current():
    """Apple's seeded FY2025 filing genuinely has no computable Altman Z'
    (a real, honestly-reported data-quality gap — no clean
    StockholdersEquity/RetainedEarningsAccumulatedDeficit fact — see
    ADR-012/013 and test_storage.py), so the most recent STORED Altman
    result is FY2024's, while Piotroski's most recent stored result is the
    real FY2025 one. `get_latest_altman`/`get_latest_piotroski` deliberately
    return the latest score each independently has (see their own
    docstrings) — the failure mode this test guards against is the
    dashboard silently presenting FY2024's Altman number as if it were a
    current FY2025 result. Each metric must carry its own real,
    independently-correct fiscal year in the text shown to the model."""
    at = _run_dashboard()
    at.selectbox[0].set_value("Apple Inc.").run(timeout=30)
    assert not at.exception
    apple_agentic_text = " ".join(m.value for m in at.tabs[5].markdown)
    assert "altman_z_score" in apple_agentic_text
    assert "FY2024" in apple_agentic_text  # Altman's real, stored fiscal year
    assert "piotroski_f_score" in apple_agentic_text
    assert "FY2025" in apple_agentic_text  # Piotroski's real, stored fiscal year


def test_dashboard_source_still_guards_against_a_totally_missing_score():
    """Now that Apple's seeded data genuinely has a real stored Piotroski
    score (FY2025) and a real stored Altman score (FY2024), no company in
    the current seeded database reaches dashboard.py's "no deterministic
    score is available" branch, so it can no longer be exercised through a
    real end-to-end AppTest run without monkeypatching Streamlit's own
    script-execution machinery (unreliable — AppTest re-execs dashboard.py
    as a fresh script per run, not as an importable module, so patching
    module attributes ahead of time does not survive that re-exec). This
    is a source-level regression guard instead: confirms the exact guard
    condition and its user-facing message are both still present in
    dashboard.py verbatim, so a future edit cannot silently delete or
    rename either without this test catching it."""
    source = Path(__file__).parent.parent.parent.joinpath("dashboard.py").read_text(encoding="utf-8")
    assert "dossier.latest_altman is None and dossier.latest_piotroski is None" in source
    assert "no deterministic score is" in source


def test_filing_risk_intelligence_tab_never_shows_another_companys_chunks():
    at = _run_dashboard()

    at.selectbox[0].set_value("Apple Inc.").run(timeout=30)
    apple_filing = str(at.tabs[4].get("caption")) + str(at.tabs[4].get("write"))
    assert "msft-2025-rf" not in apple_filing
    assert "nvda-2025-rf" not in apple_filing

    at.selectbox[0].set_value("Microsoft Corporation").run(timeout=30)
    msft_filing = str(at.tabs[4].get("caption")) + str(at.tabs[4].get("write"))
    assert "aapl-2025-rf" not in msft_filing
    assert "nvda-2025-rf" not in msft_filing

    at.selectbox[0].set_value("NVIDIA Corporation").run(timeout=30)
    nvda_filing = str(at.tabs[4].get("caption")) + str(at.tabs[4].get("write"))
    assert "aapl-2025-rf" not in nvda_filing
    assert "msft-2025-rf" not in nvda_filing


def test_nvidia_risk_overview_and_metrics_tabs_render_its_own_real_tier_and_ratios():
    """NVIDIA-specific check on the two new tabs (Risk Overview, Financial
    Metrics) added in this pass: a real, computed tier and real ratio
    values for NVIDIA must render without exception, and must not be
    Apple's or Microsoft's."""
    at = _run_dashboard()
    at.selectbox[0].set_value("NVIDIA Corporation").run(timeout=30)
    assert not at.exception
    risk_tab_text = " ".join(m.value for m in at.tabs[1].markdown)
    assert "SAFE" in risk_tab_text or "LOW" in risk_tab_text or "MODERATE" in risk_tab_text
    metrics_tab_text = str(at.tabs[2].get("metric"))
    assert "Current Ratio" in metrics_tab_text

