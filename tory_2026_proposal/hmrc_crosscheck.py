# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Cost of the Conservative family home proposal from HMRC's published tables, with no survey data.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

TOTAL_TAX = 7_030e6
RATE = 0.4
RNRB = 175_000.0
P_DESC = 0.80         # share of taxpaying estates with a home passing to children or grandchildren
P_TWO_BANDS = 0.42    # table 12.2a: 22,300 of 53,600 estates in net chargeable value used a transferred band

# (lower, upper, estates with gross capital, estates with residential, residential £m), table 12.3b 2023-24
BANDS = [
    (1, 25_000, 5_580, 4_510, 1_760),
    (25_000, 50_000, 4_160, 3_430, 1_390),
    (50_000, 100_000, 5_880, 4_900, 2_230),
    (100_000, 200_000, 6_190, 5_200, 2_840),
    (200_000, 300_000, 3_010, 2_560, 1_620),
    (300_000, 500_000, 2_730, 2_280, 1_750),
    (500_000, 1_000_000, 1_780, 1_480, 1_500),
    (1_000_000, None, 1_050, 883, 1_640),
]
# Share of the RNRB still available after the £2m taper, by band of tax bill. A £300k bill is a taxable amount of
# £750k, so an estate of about £1.4m to £1.8m with bands: mostly untapered. A £500k bill is £1.25m taxable, an
# estate over £2m: mostly tapered. Bills over £1m are estates over £3m: no RNRB.
TAPER_FACTOR = [1.0, 1.0, 1.0, 1.0, 1.0, 0.6, 0.2, 0.0]
LABELS = ["Under £25k", "£25k to £50k", "£50k to £100k", "£100k to £200k", "£200k to £300k",
          "£300k to £500k", "£500k to £1m", "Over £1m"]


def band_tax() -> list[float]:
    closed = [(lo + hi) / 2 * n for lo, hi, n, _, _ in BANDS if hi]
    return closed + [TOTAL_TAX - sum(closed)]


def run(cv: float = 0.6, main_res_share: float = 1.0, seed: int = 1, draws: int = 200_000) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for (lo, hi, n, n_res, res_m), tax, taper in zip(BANDS, band_tax(), TAPER_FACTOR):
        avg_tax = tax / n
        if hi:
            t = np.exp(rng.uniform(np.log(lo), np.log(hi), draws))
            t *= avg_tax / t.mean()
        else:
            alpha = avg_tax / (avg_tax - lo)
            t = lo * (1 - rng.uniform(size=draws)) ** (-1 / alpha)
        taxable = t / RATE
        has_home = rng.uniform(size=draws) < n_res / n
        avg_home = res_m * 1e6 / n_res * main_res_share
        sigma = np.sqrt(np.log(1 + cv**2))
        home = np.where(has_home, rng.lognormal(np.log(avg_home) - sigma**2 / 2, sigma, draws), 0.0)
        desc = rng.uniform(size=draws) < P_DESC
        bands = np.where(rng.uniform(size=draws) < P_TWO_BANDS, 2.0, 1.0)
        rnrb_cap = RNRB * bands * taper
        rnrb_used = np.where(desc, np.minimum(home, rnrb_cap), 0.0)

        save_a = RATE * np.minimum(np.where(desc, np.maximum(home - rnrb_used, 0.0), 0.0), taxable)
        save_b = RATE * np.minimum(np.where(desc, home, 0.0) + RNRB * bands - rnrb_used, taxable)
        save_c = RATE * np.minimum(RNRB * bands - rnrb_used, taxable)
        rows.append({
            "band": LABELS[len(rows)], "estates": n, "tax_gbp_m": tax / 1e6,
            "rnrb_used_gbp_m": rnrb_used.mean() * n / 1e6,
            "A_home_only_gbp_m": save_a.mean() * n / 1e6,
            "B_announced_design_gbp_m": save_b.mean() * n / 1e6,
            "C_threshold_only_gbp_m": save_c.mean() * n / 1e6,
            "estates_paying_nothing_after_B": float((save_b >= t - 1).mean()) * n,
        })
    return pd.DataFrame(rows)


def widow_example() -> None:
    """The reviewer's check: widow, two sets of bands, £1m home, £1m investments, to children."""
    estate, home, nrb, rnrb = 2_000_000, 1_000_000, 650_000, 350_000
    now = RATE * (estate - nrb - rnrb)
    a = RATE * max(0, estate - home - nrb)
    b = RATE * max(0, estate - home - 1_000_000)
    print(f"  widow example: tax now £{now:,.0f}; home exempt, bands unchanged £{a:,.0f} (saving £{now-a:,.0f}); "
          f"announced design £{b:,.0f} (saving £{now-b:,.0f})")


if __name__ == "__main__":
    pd.set_option("display.width", 220)
    widow_example()
    out = []
    for share in (1.0, 0.9, 0.85):
        for cv in (0.6,):
            df = run(cv, share)
            tot = df[["rnrb_used_gbp_m", "A_home_only_gbp_m", "B_announced_design_gbp_m", "C_threshold_only_gbp_m",
                      "estates_paying_nothing_after_B"]].sum()
            print(f"\nmain residence = {share:.0%} of HMRC residential, CV {cv}")
            print(df.round(0).to_string(index=False))
            print(f"  RNRB in use (simulated, taxpaying estates): £{tot.rnrb_used_gbp_m:,.0f}m  [HMRC 12.2a: £7,790m across 31,000 estates above the NRB]")
            print(f"  A home exempt alone:    £{tot.A_home_only_gbp_m:,.0f}m = {tot.A_home_only_gbp_m/7030:.1%} of yield")
            print(f"  B announced design:     £{tot.B_announced_design_gbp_m:,.0f}m = {tot.B_announced_design_gbp_m/7030:.1%} of yield; "
                  f"estates paying nothing after: {tot.estates_paying_nothing_after_B:,.0f} of 30,400")
            print(f"  C threshold half alone: £{tot.C_threshold_only_gbp_m:,.0f}m = {tot.C_threshold_only_gbp_m/7030:.1%} of yield")
            df["main_res_share"] = share
            out.append(df)
    pd.concat(out).to_csv(Path(__file__).parent / "output" / "hmrc_crosscheck.csv", index=False)
