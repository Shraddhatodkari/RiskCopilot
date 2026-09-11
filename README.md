# RiskCopilot — Company-Agnostic Financial-Risk Intelligence Platform

**Status: Complete and verified.**

RiskCopilot is a real-data financial risk intelligence platform for credit analysts, lenders, investment committees, and financial due-diligence teams.

## What this is

Given **any real US-listed company** — entered as a ticker, CIK, or company name — RiskCopilot retrieves that company's actual SEC-filed financial data through the SEC EDGAR XBRL API and computes deterministic financial-risk indicators:

- **Altman Z'-Score**
- **Piotroski F-Score**
- **11 financial ratios** covering liquidity, leverage, profitability, and cash flow
- **Multi-year financial trends**
- **Company-specific SEC Item 1A risk-factor intelligence**
- **Deterministic overall risk tier**
- **Grounded AI risk narrative**
- **Human approval workflow**
- **Historical bankruptcy validation**

Financial calculations are performed by deterministic Python logic — **not by an LLM**. When required filing data is missing or ambiguous, RiskCopilot reports insufficient data rather than estimating or fabricating a value.

The AI layer is used only for qualitative explanation. It retrieves company-specific filing evidence before generating a narrative, and a deterministic grounding critic verifies the narrative's metric and filing citations before it can move to human approval.

The dashboard uses a single company-scoped `CompanyDossier` read path across all sections, preventing one company's data from appearing in another company's tab.

AAPL, MSFT, and NVDA are used throughout the repository as **validation cases**. They are not a hardcoded supported universe. The same code path can analyze other real US-listed companies.

---

## Quickstart

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
# source .venv/bin/activate

pip install -r requirements.txt

