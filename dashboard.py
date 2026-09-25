"""
RiskCopilot dashboard — a Streamlit read/act surface over data this
project's own deterministic pipeline (Phases 1-2), expanded ratio/trend/
risk-tier engine (ADR-013), evaluation harness (Phase 4), and agentic
layer (Phase 3, ADR-011) have computed and persisted. This file contains
NO financial calculation of its own — every score comes from
`src/analysis/*.py` via `src/reporting/company_dossier.py` (ADR-012/013),
and every piece of company-specific data is scoped by CIK end to end so
that selecting one company can never surface another company's facts,
scores, evidence, or narrative (see docs/02_architecture_decision_
record.md's ADR-012/013 and tests/unit/test_company_dossier.py,
tests/unit/test_evidence_registry.py, tests/unit/test_dashboard_isolation.py).

Run: streamlit run dashboard.py
(First run: `python scripts/seed_dashboard_data.py` populates
data/riskcopilot.db with real, already-verified MSFT/AAPL results, or use
the "Analyze a new company" panel below to fetch any real ticker live —
requires your own SEC_EDGAR_USER_AGENT in .env — see README.md. There is
nothing in this file's logic specific to any one company: the same code
path runs for AAPL, MSFT, or any other real US-listed ticker you enter.)
"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from src.config import ConfigError, load_settings
from src.ingestion.risk_categorizer import categorize_chunk
from src.ingestion.sec_edgar_client import SecEdgarClient
from src.persistence.storage import connect, list_companies
from src.reporting.company_dossier import build_company_dossier, filing_source_url
from src.reporting.evidence_registry import evidence_available, load_evidence_index, registered_ciks
from src.reporting.live_ingest import LiveIngestError, analyze_and_persist

DB_PATH = "data/riskcopilot.db"
BACKTEST_FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "distress_backtest_cases.json"

# Deterministic display metadata only (icons/labels) — never a source of
# calculation. The underlying tier/direction/status enums are computed
# entirely in src/analysis/*.py; this dict just maps an already-computed
# enum value to something readable.
_TIER_ICON = {
    "low": "🟢", "moderate": "🟡", "elevated": "🟠", "high": "🔴", "insufficient_data": "⚪",
}
_TREND_ICON = {
    "improving": "📈", "deteriorating": "📉", "stable": "➡️", "insufficient_data": "❓",
}
_RATIO_LABELS = {
    "current_ratio": "Current Ratio", "quick_ratio": "Quick Ratio",
    "debt_to_equity": "Debt / Equity", "debt_to_assets": "Debt / Assets",
    "interest_coverage": "Interest Coverage",
    "return_on_assets": "Return on Assets", "return_on_equity": "Return on Equity",
    "operating_margin": "Operating Margin", "net_margin": "Net Margin",
    "operating_cash_flow": "Operating Cash Flow", "free_cash_flow": "Free Cash Flow",
    "altman_z_score": "Altman Z'-Score", "piotroski_f_score": "Piotroski F-Score",
}
_RATIO_CATEGORY_ORDER = ("liquidity", "leverage", "profitability", "cash_flow")
_RATIO_CATEGORY_LABELS = {
    "liquidity": "Liquidity", "leverage": "Leverage / Solvency",
    "profitability": "Profitability", "cash_flow": "Cash Flow",
}


def _format_ratio_value(r: dict) -> str:
    """Display-only formatting (no calculation change): the 'cash_flow'
    category holds real dollar amounts (operating/free cash flow), not
    decimal ratios, so formatting them with the same 4-decimal-place ratio
    format used for current_ratio/ROE/etc. produces an unreadable raw
    float like '136162000000.0000'. This mirrors the comma-separated,
    whole-dollar formatting already used elsewhere on this page for the
    same underlying values (see the 'Real source values used' list
    below)."""
    if r["category"] == "cash_flow":
        return f"{r['value']:,.0f}"
    return f"{r['value']:.4f}"

st.set_page_config(page_title="RiskCopilot", layout="wide")

st.title("RiskCopilot — Company Risk Intelligence")
st.caption(
    "Every score and ratio below was computed deterministically by src/analysis/*.py from real "
    "SEC XBRL data — never invented or estimated by an LLM (ADR-005/ADR-013). The selected "
    "company below controls every section on this page; no section ever substitutes another "
    "company's data (ADR-012). This dashboard has no hardcoded company list — enter any real "
    "US-listed ticker below."
)

Path("data").mkdir(exist_ok=True)
conn = connect(DB_PATH)
companies = list_companies(conn)

# ============================================================================
# Single source of context: ONE company selector drives the entire page.
# ============================================================================
st.subheader("Company")
col_pick, col_new = st.columns([2, 1])

with col_pick:
    if companies:
        names = {c["entity_name"]: c["cik"] for c in companies}
        picked_name = st.selectbox("Select an analyzed company", sorted(names.keys()))
        selected_cik = names[picked_name]
        selected_name = picked_name
    else:
        st.info("No companies analyzed yet. Use 'Analyze a new company' or run "
                "`python scripts/seed_dashboard_data.py`.")
        selected_cik = None
        selected_name = None

with col_new:
    with st.popover("➕ Analyze a new company"):
        st.caption(
            "Fetches REAL SEC EDGAR data live for any ticker/CIK — not limited to any "
            "predefined list. Requires SEC_EDGAR_USER_AGENT in your .env (see README.md). "
            "This project's own cloud sandbox cannot reach data.sec.gov — this works from "
            "your own machine."
        )
        new_ticker = st.text_input("Ticker (any US-listed company)", "")
        new_fy = st.number_input("Fiscal year", min_value=2010, max_value=2030, value=2025, step=1)
        new_with_piotroski = st.checkbox("Also compute Piotroski F-Score (fetches prior year too)", value=True)
        new_fetch_evidence = st.checkbox(
            "Also fetch this company's real Item 1A risk-factor evidence "
            "(needed for Filing Risk Intelligence / Agentic Narrative)",
            value=True,
        )
        if st.button("Fetch & score", key="fetch_new_company"):
            if not new_ticker.strip():
                st.error("Enter a ticker first.")
            else:
                try:
                    settings = load_settings()
                    client = SecEdgarClient(settings)
                    with st.spinner(f"Fetching real SEC data for {new_ticker.upper()}..."):
                        result = analyze_and_persist(
                            conn, client, ticker=new_ticker.strip(),
                            fiscal_year=int(new_fy), settings=settings,
                            with_piotroski=new_with_piotroski,
                            fetch_evidence=new_fetch_evidence,
                        )
                    st.success(f"Fetched and scored {result.entity_name} (CIK {result.cik}), FY{result.fiscal_year}.")
                    if result.altman_error:
                        st.warning(f"Altman Z': {result.altman_error}")
                    if result.piotroski_error:
                        st.warning(f"Piotroski F: {result.piotroski_error}")
                    if new_fetch_evidence:
                        if result.evidence_cached:
                            st.success("Real filing risk-factor evidence fetched and cached for this company.")
                        elif result.evidence_error:
                            st.info(f"Filing evidence not cached: {result.evidence_error}")
                    st.rerun()
                except ConfigError as e:
                    st.error(f"Configuration error: {e}")
                except LiveIngestError as e:
                    st.error(f"Could not fetch data: {e}")

if selected_cik is None:
    conn.close()
    st.stop()

dossier = build_company_dossier(conn, selected_cik, selected_name)

(
    tab_overview, tab_risk, tab_metrics, tab_trends, tab_filing,
    tab_agentic, tab_backtest, tab_quality,
) = st.tabs(
    [
        "Company Overview", "Risk Overview", "Financial Metrics", "Historical Trends",
        "Filing Risk Intelligence", "Agentic Narrative", "Historical Validation", "Data Quality",
    ]
)

# ============================================================================
# Company Overview
# ============================================================================
with tab_overview:
    st.subheader(f"{dossier.entity_name}  (CIK {dossier.cik})")

    if dossier.filing_metadata:
        fm = dossier.filing_metadata
        url = filing_source_url(dossier.cik, fm["accession_number"])
        st.write(
            f"**Source filing:** {fm['form']} filed {fm['filed']} — "
            f"accession `{fm['accession_number']}` · fiscal year {dossier.current_fiscal_year} "
            f"· [{fm['fact_count']} facts extracted from this filing]({url})"
        )
    else:
        st.write("No filing metadata available yet for this company.")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if dossier.latest_altman:
            zone = dossier.latest_altman["zone"]
            zone_icon = {"safe": "🟢", "grey": "🟡", "distress": "🔴"}.get(zone, "")
            # Each score carries its OWN fiscal year: Altman's and Piotroski's
            # latest stored results can belong to different years than each
            # other and than the source filing shown above.
            st.metric(
                "Altman Z'-Score",
                f"{dossier.latest_altman['z_score']:.4f} (FY{dossier.latest_altman['fiscal_year']})",
                zone.upper() + " " + zone_icon,
            )
        else:
            st.metric("Altman Z'-Score", "N/A", "not computable — see Data Quality")
    with col2:
        if dossier.latest_piotroski:
            st.metric("Piotroski F-Score", f"{dossier.latest_piotroski['f_score']}/9 (FY{dossier.latest_piotroski['fiscal_year']})")
        else:
            st.metric("Piotroski F-Score", "N/A", "not computable — see Data Quality")
    with col3:
        if dossier.risk_rating:
            tier = dossier.risk_rating["tier"]
            st.metric("Overall Risk Tier", tier.replace("_", " ").upper(), _TIER_ICON.get(tier, ""))
    with col4:
        st.metric("Filing risk-factor evidence", "Available" if dossier.has_risk_factor_evidence else "Not ingested")

    st.write(f"**Overall (deterministic, not LLM-generated):** {dossier.overall_risk_note}")

    if dossier.data_quality_issues:
        st.warning(
            f"{len(dossier.data_quality_issues)} data quality issue(s) affect FY{dossier.current_fiscal_year} "
            f"— see the Data Quality tab for what's missing and why."
        )

# ============================================================================
# Risk Overview: the deterministic 🟢🟡🟠🔴 tier, with its full documented
# derivation shown alongside it (never a black box — see src/analysis/
# risk_rating.py's own docstring for the fixed rule table this reads back).
# ============================================================================
with tab_risk:
    st.subheader(f"Deterministic risk tier — {dossier.entity_name}")
    st.caption(
        "Computed by src/analysis/risk_rating.py from the Altman Z' zone, the Piotroski F-Score "
        "bucket, and a current-ratio liquidity flag, using a fixed, documented rule table — never "
        "an LLM judgment call. 'Why?' below shows the exact inputs and rule that produced this "
        "tier for this company."
    )
    if dossier.risk_rating:
        tier = dossier.risk_rating["tier"]
        icon = _TIER_ICON.get(tier, "")
        st.markdown(f"## {icon} {tier.replace('_', ' ').upper()}")
        if dossier.risk_rating["liquidity_flag"]:
            st.warning("Liquidity flag: current ratio is below 1.0 (current liabilities exceed current assets).")
        with st.expander("Why? (full deterministic derivation)"):
            st.write(dossier.risk_rating["basis"])
    else:
        st.info("No risk tier available for this company/year.")

    st.divider()
    st.write("**Component scores feeding this tier:**")
    c1, c2 = st.columns(2)
    with c1:
        if dossier.latest_altman:
            st.write(f"Altman Z'-Score (FY{dossier.latest_altman['fiscal_year']}): "
                     f"**{dossier.latest_altman['z_score']:.4f}** "
                     f"({dossier.latest_altman['zone'].upper()} zone)")
        else:
            st.write("Altman Z'-Score: **not computable** for the current fiscal year.")
    with c2:
        if dossier.latest_piotroski:
            st.write(f"Piotroski F-Score (FY{dossier.latest_piotroski['fiscal_year']}): "
                     f"**{dossier.latest_piotroski['f_score']}/9**")
        else:
            st.write("Piotroski F-Score: **not computable** for the current fiscal year.")

# ============================================================================
# Financial Metrics: the expanded deterministic ratio engine (ADR-013),
# grouped by category, every ratio individually honest about availability.
# ============================================================================
with tab_metrics:
    st.subheader(f"Financial ratios — {dossier.entity_name}, FY{dossier.current_fiscal_year}")
    st.caption(
        "Computed by src/analysis/financial_ratios.py directly from this company's real, sourced "
        "SEC filing facts. A ratio that cannot be computed from real data shows 'Insufficient "
        "data' with the specific missing concept — never a fabricated or estimated value."
    )
    if not dossier.current_ratios:
        st.info("No financial ratios available for this company's current fiscal year.")
    else:
        ratios = dossier.current_ratios["ratios"]
        available = sum(1 for r in ratios if r["status"] == "available")
        st.write(f"**{available} / {len(ratios)}** ratios computable from real data for this fiscal year.")

        by_category: dict[str, list[dict]] = {}
        for r in ratios:
            by_category.setdefault(r["category"], []).append(r)

        for category in _RATIO_CATEGORY_ORDER:
            cat_ratios = by_category.get(category, [])
            if not cat_ratios:
                continue
            st.write(f"### {_RATIO_CATEGORY_LABELS[category]}")
            cols = st.columns(len(cat_ratios))
            for col, r in zip(cols, cat_ratios):
                with col:
                    label = _RATIO_LABELS.get(r["name"], r["name"])
                    if r["status"] == "available":
                        st.metric(label, _format_ratio_value(r))
                    else:
                        st.metric(label, "N/A", r["status"].replace("_", " "))
                    with st.expander("Why?"):
                        st.write(f"**Formula:** `{r['formula']}`")
                        st.write(f"**Source concepts:** {', '.join(r['source_concepts'])}")
                        # r['status'] is a RatioStatus (str-backed enum); an
                        # f-string calls its __str__, which renders the
                        # qualified enum name ("RatioStatus.AVAILABLE")
                        # rather than the plain value ("available") an
                        # analyst should see — normalize explicitly rather
                        # than leaking that internal representation.
                        status_value = getattr(r["status"], "value", r["status"])
                        st.write(f"**Status:** {status_value.replace('_', ' ')}")
                        if r.get("detail"):
                            st.write(r["detail"])
                        if r["status"] == "available" and dossier.source_facts:
                            st.write("**Real source values used:**")
                            for f in dossier.source_facts:
                                if f["concept"] in r["source_concepts"]:
                                    st.write(
                                        f"- {f['concept']} = {f['value']:,.0f} {f['unit']} "
                                        f"(tag `{f['tag_used']}`, {f['form']} filed {f['filed']})"
                                    )

# ============================================================================
# Historical Trends: multi-year deterministic trend classification
# (ADR-013) for every ratio and both distress scores.
# ============================================================================
with tab_trends:
    st.subheader(f"Multi-year trends — {dossier.entity_name}")
    st.caption(
        "Computed by src/analysis/trend.py by comparing the earliest and latest real fiscal "
        "year this company has stored data for — a fixed 5% threshold distinguishes 'stable' "
        "from a real move; never an artificial or interpolated data point (see the module's own "
        "docstring for the full, documented rule)."
    )

    if dossier.altman_history and len(dossier.altman_history) >= 1:
        st.write("**Altman Z'-Score history**")
        # Fiscal years are cast to str here purely so Streamlit/Altair render
        # the x-axis as clean categorical labels ("2023", "2024", "2025")
        # instead of a quantitative numeric axis, which on a tiny integer
        # range (e.g. 2023-2025) produces malformed, over-precise tick
        # labels like "2,025.000000012345". This is a display-only change —
        # the underlying fiscal_year and z_score values are untouched.
        st.line_chart({str(r["fiscal_year"]): r["z_score"] for r in dossier.altman_history})
    if dossier.piotroski_history and len(dossier.piotroski_history) >= 1:
        st.write("**Piotroski F-Score history**")
        st.line_chart({str(r["fiscal_year"]): r["f_score"] for r in dossier.piotroski_history})

    if not dossier.metric_trends:
        st.info("No trend data available for this company yet.")
    else:
        st.write("### Trend classification by metric")
        for name, trend in dossier.metric_trends.items():
            label = _RATIO_LABELS.get(name, name)
            icon = _TREND_ICON.get(trend["direction"], "")
            with st.container(border=True):
                st.write(f"**{label}** — {icon} {trend['direction'].replace('_', ' ').upper()}")
                st.caption(trend["detail"])

# ============================================================================
# Filing Risk Intelligence: company-specific retrieval, categorized by a
# fixed deterministic taxonomy, or an explicit unavailable state — never
# another company's evidence (ADR-012).
# ============================================================================
with tab_filing:
    st.subheader(f"Item 1A Risk Factors — {dossier.entity_name}")

    if not dossier.has_risk_factor_evidence:
        st.error(
            f"No company-specific filing risk-factor evidence has been ingested for "
            f"{dossier.entity_name} (CIK {dossier.cik}) yet. This project currently has real, "
            f"provenance-carrying evidence only for the companies registered in "
            f"src/reporting/evidence_registry.py (curated fixtures, plus any company you fetched "
            f"live via 'Analyze a new company' with evidence fetching enabled). Per this "
            f"project's design, another company's evidence is never substituted here — see "
            f"ADR-012/013."
        )
    else:
        index = load_evidence_index(dossier.cik)
        query = st.text_input(
            "Search this company's real Item 1A text",
            "competition regulatory risk data security",
            key="filing_query",
        )
        retrieved = index.search(query, top_k=5)
        if not retrieved:
            st.info("No passages matched this query (TF-IDF exact-term matching — try different words).")

        all_categories: set[str] = set()
        chunk_categories: dict[str, list[str]] = {}
        for r in retrieved:
            cats = categorize_chunk(r.chunk.heading, r.chunk.text)
            chunk_categories[r.chunk.chunk_id] = cats
            all_categories.update(cats)
        if all_categories:
            st.caption(
                "Categories tagged below by src/ingestion/risk_categorizer.py's fixed keyword "
                "taxonomy (deterministic, never LLM-classified): " + ", ".join(sorted(all_categories))
            )

        for r in retrieved:
            with st.container(border=True):
                st.write(f"**{r.chunk.heading}** — relevance {r.score:.3f}")
                cats = chunk_categories.get(r.chunk.chunk_id, [])
                if cats:
                    st.write(" ".join(f"`{c}`" for c in cats))
                else:
                    st.write("`uncategorized` — no fixed taxonomy keyword matched this passage")
                st.write(r.chunk.text)
                url = filing_source_url(r.chunk.cik, r.chunk.accession_number)
                st.caption(
                    f"chunk `{r.chunk.chunk_id}` · {r.chunk.entity_name} FY{r.chunk.fiscal_year} · "
                    f"[source filing]({url})"
                )

# ============================================================================
# Agentic Narrative: real Ollama call by default, company-scoped end to
# end — real metrics AND real evidence for the SAME selected company.
# ============================================================================
with tab_agentic:
    st.subheader(f"Grounded narrative — {dossier.entity_name}")
    st.caption(
        "ADR-011: real LLM backend is a locally-run Ollama model — zero API cost, no API key, "
        "nothing leaves this machine. Retrieval, prompt construction, the deterministic "
        "grounding critic, and the approval state machine are unchanged from Phase 3."
    )

    if not dossier.has_risk_factor_evidence:
        st.error(
            f"Cannot generate a grounded narrative for {dossier.entity_name}: no company-specific "
            f"filing evidence is available (see Filing Risk Intelligence tab). This project does "
            f"NOT substitute another company's evidence to make this tab appear to work — "
            f"companies with real evidence today: "
            f"{', '.join(registered_ciks())}."
        )
    elif dossier.latest_altman is None and dossier.latest_piotroski is None:
        st.error(
            f"Cannot generate a narrative for {dossier.entity_name}: no deterministic score is "
            f"available to ground it in (see Data Quality tab for why). The narrative layer only "
            f"describes real computed metrics — it never invents one."
        )
    else:
        from src.agentic.approval import create_memo
        from src.agentic.llm_client import FakeLLMClient, OllamaConnectionError, OllamaError, OllamaLLMClient
        from src.agentic.narrative import draft_risk_narrative

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            model_name = st.text_input("Ollama model", value=OllamaLLMClient.DEFAULT_MODEL, key="ollama_model")
        with col_b:
            base_url = st.text_input("Ollama base URL", value=OllamaLLMClient.DEFAULT_BASE_URL, key="ollama_url")
        with col_c:
            timeout_seconds = st.number_input(
                "Timeout (seconds)", min_value=10.0, value=OllamaLLMClient.DEFAULT_TIMEOUT, step=30.0,
                key="ollama_timeout",
                help="Raised from an earlier 120s default: on a CPU-only machine, the "
                "*first* call after `ollama serve` starts pays the cost of loading the "
                "model into RAM before generating anything, which alone can exceed 120s. "
                "The model then stays loaded (OLLAMA_KEEP_ALIVE) so later calls are fast.",
            )

        reachable = OllamaLLMClient.is_reachable(base_url=base_url)
        if reachable:
            st.success(f"✅ Connected to Ollama at {base_url}")
            available_models = OllamaLLMClient.list_models(base_url=base_url)
            if available_models:
                st.caption(f"Models pulled on this server: {', '.join(available_models)}")
                if model_name not in available_models:
                    st.warning(f"'{model_name}' is not pulled — run `ollama pull {model_name}` first.")
                elif len(available_models) > 1:
                    st.caption(
                        "Still timing out with the default model? Try one of the other "
                        "already-pulled models above (a smaller one loads and generates "
                        "faster on a CPU-only machine) — no new download required."
                    )
        else:
            st.error(
                f"❌ Could not reach Ollama at {base_url}. Expected if this dashboard runs on a "
                f"different machine than Ollama (e.g. this project's own cloud sandbox). On the "
                f"machine that should run the model: `ollama serve`, `ollama pull {model_name}`."
            )

        demo_mode = st.checkbox(
            "Use the scripted fake LLM instead (offline demo — no Ollama needed)",
            value=not reachable, key="demo_mode",
        )

        index = load_evidence_index(dossier.cik)
        query = st.text_input(
            "Retrieval query for the narrative's evidence",
            "competition regulatory risk data security",
            key="agentic_query",
        )
        retrieved = index.search(query, top_k=3)
        st.write(f"**Retrieved passages (real {dossier.entity_name} filing text):**")
        for r in retrieved:
            st.write(f"- `{r.chunk.chunk_id}` (score {r.score:.3f}): {r.chunk.heading}")

        metrics: dict[str, str] = {}
        if dossier.latest_altman:
            la = dossier.latest_altman
            metrics["altman_z_score"] = f"{la['z_score']:.2f} ({la['zone']} zone, FY{la['fiscal_year']})"
        if dossier.latest_piotroski:
            lp = dossier.latest_piotroski
            metrics["piotroski_f_score"] = f"{lp['f_score']}/9 (FY{lp['fiscal_year']})"
        st.write(f"**Metrics passed to the model (real, {dossier.entity_name}'s own):** {metrics}")

        button_label = "Generate narrative (scripted demo)" if demo_mode else f"Generate narrative (real call to {model_name})"
        if st.button(button_label, key="generate_narrative"):
            if demo_mode:
                scripted_response = (
                    f"{dossier.entity_name}'s Altman Z'-Score {metrics.get('altman_z_score', 'N/A')} "
                    "[[metric:altman_z_score]] warrants continued monitoring. Its Piotroski F-Score "
                    f"{metrics.get('piotroski_f_score', 'N/A')} [[metric:piotroski_f_score]] reflects "
                    "its fundamental trend. Filed disclosures note "
                    + (retrieved[0].chunk.heading.lower() if retrieved else "no specific evidence")
                    + " [[chunk:" + (retrieved[0].chunk.chunk_id if retrieved else "none") + "]]."
                )
                llm_client = FakeLLMClient(responses=[scripted_response])
            else:
                llm_client = OllamaLLMClient(model=model_name, base_url=base_url, timeout=float(timeout_seconds))

            try:
                with st.spinner("Running scripted demo..." if demo_mode else f"Calling {model_name}..."):
                    draft = draft_risk_narrative(llm_client, metrics, retrieved)
            except OllamaConnectionError as e:
                st.error(f"Could not reach Ollama: {e}")
            except OllamaError as e:
                st.error(f"Ollama call failed: {e}")
            else:
                memo_fy = dossier.current_fiscal_year or 0
                memo = create_memo(
                    cik=dossier.cik, entity_name=dossier.entity_name, fiscal_year=memo_fy,
                    narrative_text=draft.narrative_text, critic_report=draft.critic_report,
                )
                source_label = "scripted fake LLM (demo)" if demo_mode else f"real local Ollama ({model_name})"
                st.write(f"**Narrative** — {dossier.entity_name}, source: {source_label}:")
                st.write(draft.narrative_text)
                st.write(f"**Grounding critic:** {'✅ PASSED' if draft.critic_report.passed else '❌ FAILED'}")
                st.write(f"**Memo status:** `{memo.status.value}`")
                if not draft.critic_report.passed:
                    st.write(f"Ungrounded citations: {draft.critic_report.ungrounded_citations}")
                    st.write(f"Uncited numeric sentences: {draft.critic_report.uncited_numeric_sentences}")
                    st.info(
                        "A memo whose critic fails is blocked from approval unless a human reviewer "
                        "supplies an explicit override reason — it never silently becomes final."
                    )

# ============================================================================
# Historical Validation — company-agnostic (a fixed, real backtest study)
# ============================================================================
with tab_backtest:
    st.subheader("Does the Altman Z'-Score actually flag real bankruptcies?")
    st.caption("Full methodology and limitations: docs/06_historical_backtest.md")
    if BACKTEST_FIXTURE.exists():
        from src.evaluation.backtest import run_backtest

        cases = json.loads(BACKTEST_FIXTURE.read_text())["cases"]
        summary = run_backtest(cases)

        m1, m2, m3 = st.columns(3)
        m1.metric("Snapshots filed BEFORE bankruptcy (real predictions)", summary.predictive_snapshots)
        m2.metric(
            "Flagged in advance (grey or distress)",
            f"{summary.predictive_flagged_count} of {summary.predictive_snapshots} "
            f"({summary.predictive_flagged_rate:.0%})",
        )
        m3.metric("Missed in advance (safe zone)", summary.predictive_missed_count)
        st.caption(
            f"Only filings that were public before the bankruptcy petition count as predictions. "
            f"{summary.total_snapshots - summary.predictive_snapshots} of the {summary.total_snapshots} "
            f"snapshots below were filed after the company had already filed for bankruptcy, so they "
            f"are shown for completeness but not counted as advance warnings."
        )

        for case in summary.cases:
            st.write(f"### {case.company_name} — {case.known_event} ({case.known_event_date})")
            for s in case.snapshots:
                filed_note = "before" if s.filed_before_event else "**after**"
                flag_note = "🔴 FLAGGED" if s.flagged_for_review else "⚪ missed"
                st.write(
                    f"FY{s.fiscal_year} (period end {s.period_end}, filed {filed_note} the event): "
                    f"Z'={s.z_score:.4f} → **{s.zone.value.upper()}** | "
                    f"lead time {s.lead_time_days} days | {flag_note}"
                )
        st.info(
            "Honest N=2 company backtest (2 real advance-warning snapshots) — not a statistically powered accuracy "
            "claim. See docs/06_historical_backtest.md for the full discussion."
        )
    else:
        st.write("Backtest fixture not found.")

# ============================================================================
# Data Quality — current-state only (ADR-012's delete-and-replace
# semantics mean this list can never show a stale, already-resolved
# issue), extended (ADR-013) to also report every new ratio's honest
# availability status and this company's filing-evidence state.
# ============================================================================
with tab_quality:
    st.subheader(f"Data quality — {dossier.entity_name}, FY{dossier.current_fiscal_year}")
    st.caption(
        "This reflects only the most recent ingestion run for this company/fiscal year "
        "(ADR-012) — an issue that has since been resolved (the concept was found on a later "
        "run) is deleted, never shown alongside the fresh fact as if both were current. A "
        "previously-valid Altman/Piotroski score that could not be reproduced on the most recent "
        "recomputation is explicitly invalidated (severity 'invalidated') rather than left "
        "displayed as if it were still current (ADR-013 — see src/persistence/storage.py's "
        "invalidate_altman_result/invalidate_piotroski_result)."
    )
    if dossier.data_quality_issues:
        for issue in dossier.data_quality_issues:
            st.write(f"- **`{issue['concept']}`** ({issue['severity']}): {issue['detail']}")
    else:
        st.success(f"No data quality issues recorded for {dossier.entity_name}'s current fiscal year.")

    st.write("### Financial ratio availability")
    if dossier.current_ratios:
        for r in dossier.current_ratios["ratios"]:
            label = _RATIO_LABELS.get(r["name"], r["name"])
            if r["status"] == "available":
                st.write(f"- ✅ **{label}**: available (`{_format_ratio_value(r)}`)")
            else:
                st.write(f"- ⚠️ **{label}**: {r['status'].replace('_', ' ')} — {r['detail']}")
    else:
        st.info("No ratio data computed for this company/year.")

    st.write("### Filing risk-factor evidence")
    if dossier.has_risk_factor_evidence:
        st.write(f"✅ Real, provenance-carrying Item 1A evidence is registered for CIK {dossier.cik}.")
    else:
        st.write(
            f"⚠️ No filing risk-factor evidence is registered for CIK {dossier.cik}. Use "
            f"'Analyze a new company' with evidence fetching enabled to fetch it live, or it may "
            f"not be available if this filing's Item 1A section could not be located/parsed."
        )

    if dossier.source_facts:
        with st.expander(f"All {len(dossier.source_facts)} sourced facts for FY{dossier.current_fiscal_year} (full provenance)"):
            for f in dossier.source_facts:
                st.write(
                    f"- **{f['concept']}** = {f['value']:,.0f} {f['unit']} — tag `{f['tag_used']}`, "
                    f"{f['form']} filed {f['filed']}, period end {f['period_end']}, "
                    f"accession `{f['accession_number']}`"
                )

conn.close()

