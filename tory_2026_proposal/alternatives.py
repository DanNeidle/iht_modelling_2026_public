# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""What else £7.7bn of inheritance tax cuts would buy, in 2029-30.
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _paths  # noqa: E402,F401  (puts the household model on the path)

from lib.config import RULES_2027_28  # noqa: E402
from model.build_units import UNITS_PATH  # noqa: E402
from model import validate  # noqa: E402
from static_cost import Variant, VARIANTS, run_variant  # noqa: E402
import hmrc_crosscheck as hc  # noqa: E402

OBR_2029_30 = 13.7e9
COST = 7.7e9
TARGET = COST / OBR_2029_30


def model_tax(units, rules, v):
    return validate.simulate_year_of_deaths(units, run_variant(units, rules, v), scenario="x")["modelled_tax_gbp"]


def hmrc_cut_for_nrb(nrb: float, main_res_share: float = 0.9, seed: int = 1, draws: int = 200_000) -> float:
    rng = np.random.default_rng(seed)
    d = nrb - 325_000
    total = 0.0
    for (lo, hi, n, n_res, res_m), tax, taper in zip(hc.BANDS, hc.band_tax(), hc.TAPER_FACTOR):
        avg_tax = tax / n
        if hi:
            t = np.exp(rng.uniform(np.log(lo), np.log(hi), draws)); t *= avg_tax / t.mean()
        else:
            alpha = avg_tax / (avg_tax - lo); t = lo * (1 - rng.uniform(size=draws)) ** (-1 / alpha)
        taxable = t / 0.4
        has_home = rng.uniform(size=draws) < n_res / n
        avg_home = res_m * 1e6 / n_res * main_res_share
        sigma = np.sqrt(np.log(1 + 0.6**2))
        home = np.where(has_home, rng.lognormal(np.log(avg_home) - sigma**2 / 2, sigma, draws), 0.0)
        desc = rng.uniform(size=draws) < hc.P_DESC
        bands = np.where(rng.uniform(size=draws) < hc.P_TWO_BANDS, 2.0, 1.0)
        rnrb_used = np.where(desc, np.minimum(home, hc.RNRB * bands * taper), 0.0)
        saving = np.clip(0.4 * np.minimum(d * bands - rnrb_used, taxable), -np.inf, t)
        total += saving.mean() * n
    return total / hc.TOTAL_TAX


def solve(f, lo, hi, target, tol=500):
    for _ in range(40):
        mid = (lo + hi) / 2
        if f(mid) < target: lo = mid
        else: hi = mid
        if hi - lo < tol: break
    return (lo + hi) / 2


def main():
    print(f"target cut: £{COST/1e9:.1f}bn of £{OBR_2029_30/1e9:.1f}bn = {TARGET:.1%}")
    units = pd.read_parquet(UNITS_PATH)
    rules = RULES_2027_28
    base = model_tax(units, rules, VARIANTS[0])
    cons = model_tax(units, rules, VARIANTS[1])
    print(f"model: Conservative design cuts {1-cons/base:.1%}")

    def model_cut(nrb):
        return 1 - model_tax(units, rules, Variant("x", exempt=False, nil_rate_band=nrb, drop_rnrb=True)) / base
    rows = []
    for nrb in (500_000, 600_000, 700_000, 800_000, 900_000, 1_000_000):
        m, h = model_cut(nrb), hmrc_cut_for_nrb(nrb)
        rows.append({"nrb": nrb, "model_cut": m, "hmrc_cut": h})
        print(f"  NRB £{nrb:,} (no RNRB): model cuts {m:.1%}, HMRC route {h:.1%}  -> 2029-30 cost £{m*OBR_2029_30/1e9:.1f}bn / £{h*OBR_2029_30/1e9:.1f}bn")
    x_model = solve(model_cut, 500_000, 1_200_000, TARGET, tol=2_000)
    x_hmrc = solve(hmrc_cut_for_nrb, 500_000, 1_500_000, TARGET, tol=2_000)
    print(f"NRB giving a {TARGET:.1%} cut: model £{x_model:,.0f}, HMRC route £{x_hmrc:,.0f}")
    # couples check: what share of the remaining tax, estates still paying
    for nrb in (round(x_model, -4), round(x_hmrc, -4)):
        res = validate.simulate_year_of_deaths(units, run_variant(units, rules, Variant("x", exempt=False, nil_rate_band=nrb, drop_rnrb=True)), scenario="x")
        b = validate.simulate_year_of_deaths(units, run_variant(units, rules, VARIANTS[0]), scenario="x")
        c = validate.simulate_year_of_deaths(units, run_variant(units, rules, VARIANTS[1]), scenario="x")
        print(f"  NRB £{nrb:,.0f}: taxpaying estates fall {1-res['modelled_taxpaying_estates']/b['modelled_taxpaying_estates']:.0%} "
              f"(Conservative design {1-c['modelled_taxpaying_estates']/b['modelled_taxpaying_estates']:.0%})")

    # Rate cut: static tax is proportional to the rate, check in model
    r = 0.40 * (1 - TARGET)
    t_r = model_tax(units, dataclasses.replace(rules, rate=r), VARIANTS[0])
    print(f"rate cut: {r:.1%} gives a model cut of {1-t_r/base:.1%}")
    pd.DataFrame(rows).to_csv(HERE / "output" / "alternatives.csv", index=False)


if __name__ == "__main__":
    main()
