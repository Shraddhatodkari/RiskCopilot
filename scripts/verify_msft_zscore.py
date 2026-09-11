"""
Standalone, dependency-free re-implementation of the Altman Z'-Score
arithmetic, used ONLY to independently verify src/analysis/altman_z.py
against the same real Microsoft FY2025 10-K figures, without importing the
module under test. This is what test_altman_z.py's expected value
(2.0060) was derived from — run it yourself:

    python scripts/verify_msft_zscore.py

Figures below are the real values captured live from
https://data.sec.gov/api/xbrl/companyconcept/CIK0000789019/us-gaap/... on
2026-09-08, all from Microsoft's FY2025 10-K, accession 0000950170-25-100235,
filed 2025-07-30, period end 2025-06-30.
"""

total_assets = 619_003_000_000
total_liabilities = 275_524_000_000
current_assets = 191_131_000_000
current_liabilities = 141_218_000_000
retained_earnings = 237_731_000_000
operating_income = 128_528_000_000
revenues = 281_724_000_000
stockholders_equity = 343_479_000_000

working_capital = current_assets - current_liabilities
x1 = working_capital / total_assets
x2 = retained_earnings / total_assets
x3 = operating_income / total_assets
x4 = stockholders_equity / total_liabilities
x5 = revenues / total_assets

z_prime = 0.717 * x1 + 0.847 * x2 + 3.107 * x3 + 0.420 * x4 + 0.998 * x5

if __name__ == "__main__":
    print(f"X1 (working capital / total assets)      = {x1:.6f}")
    print(f"X2 (retained earnings / total assets)     = {x2:.6f}")
    print(f"X3 (operating income / total assets)      = {x3:.6f}")
    print(f"X4 (book equity / total liabilities)      = {x4:.6f}")
    print(f"X5 (revenues / total assets)               = {x5:.6f}")
    print(f"Z' = 0.717*X1 + 0.847*X2 + 3.107*X3 + 0.420*X4 + 0.998*X5")
    print(f"Z' = {z_prime:.4f}")
    zone = "safe" if z_prime > 2.90 else ("grey" if z_prime >= 1.23 else "distress")
    print(f"Zone = {zone}")
