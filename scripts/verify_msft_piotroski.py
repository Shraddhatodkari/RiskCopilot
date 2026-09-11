"""
Standalone, dependency-free re-implementation of the Piotroski F-Score
arithmetic, used ONLY to independently verify src/analysis/piotroski.py
against real Microsoft FY2023/FY2024 10-K figures, without importing the
module under test.

Run: python scripts/verify_msft_piotroski.py
"""

fy2023 = dict(
    assets=411_976_000_000, current_assets=184_257_000_000, current_liabilities=104_149_000_000,
    net_income=72_361_000_000, cfo=87_582_000_000, lt_debt=41_990_000_000, shares=7_432_000_000,
    revenues=211_915_000_000, cogs=65_863_000_000,
)
fy2024 = dict(
    assets=512_163_000_000, current_assets=159_734_000_000, current_liabilities=125_286_000_000,
    net_income=88_136_000_000, cfo=118_548_000_000, lt_debt=42_688_000_000, shares=7_434_000_000,
    revenues=245_122_000_000, cogs=74_114_000_000,
)


def roa(y):
    return y["net_income"] / y["assets"]


def leverage(y):
    return y["lt_debt"] / y["assets"]


def current_ratio(y):
    return y["current_assets"] / y["current_liabilities"]


def gross_margin(y):
    return (y["revenues"] - y["cogs"]) / y["revenues"]


def asset_turnover(y):
    return y["revenues"] / y["assets"]


if __name__ == "__main__":
    signals = {
        "roa_positive": roa(fy2024) > 0,
        "cfo_positive": fy2024["cfo"] > 0,
        "delta_roa_positive": roa(fy2024) > roa(fy2023),
        "accruals_quality": fy2024["cfo"] > fy2024["net_income"],
        "leverage_decreased": leverage(fy2024) < leverage(fy2023),
        "liquidity_increased": current_ratio(fy2024) > current_ratio(fy2023),
        "no_new_shares": fy2024["shares"] <= fy2023["shares"],
        "gross_margin_increased": gross_margin(fy2024) > gross_margin(fy2023),
        "asset_turnover_increased": asset_turnover(fy2024) > asset_turnover(fy2023),
    }
    for name, passed in signals.items():
        print(f"{name}: {passed}")
    print(f"F-Score = {sum(signals.values())} / 9")
