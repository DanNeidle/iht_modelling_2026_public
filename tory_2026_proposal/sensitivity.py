# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Sensitivity of the model's cost share for the announced design to (a) survey valuation biases and (b) decumulation between the survey and death.

The model measures the living. It over-predicts taxpaying estates against HMRC by about 1.6x and we use only the proportional change in tax when the rules change. That proportion is reliable only if decumulation before death does not change the share of the estate that is housing. Two tests:

  Valuation: homes marked down (self-reported values run high), financial wealth marked up (the survey under-records it).
  Decumulation: Oxford Economics' assumptions, "consistent with those used by the IFS": 1% a year real drawdown of property, 3% of financial assets, 5% of pension pots, applied for 5 and 10 years. These run housing's share UP, since property is drawn down slowest. The opposite case, which HMRC's age gradient suggests (residential property is 41% of taxpaying estates at 65 to 74 and 33% at 85+), is homes scaled down relative to everything else.

Run from the project root: python3 code/main_residence/sensitivity.py (in the public repository: python3 tory_2026_proposal/sensitivity.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _paths  # noqa: E402,F401  (puts the household model on the path)

from lib.config import RULES_2023_24  # noqa: E402
from model import validate  # noqa: E402
from static_cost import VARIANTS, run_variant  # noqa: E402

CASES = [
    # name, home scale, financial scale, pension scale
    ("as modelled", 1.0, 1.0, 1.0),
    ("homes 95%", 0.95, 1.0, 1.0),
    ("homes 95%, financial 125%", 0.95, 1.25, 1.0),
    ("homes 90%, financial 150%", 0.90, 1.50, 1.0),
    ("OE drawdown 5 years (property 0.99^5, financial 0.97^5, pensions 0.95^5)", 0.99**5, 0.97**5, 0.95**5),
    ("OE drawdown 10 years", 0.99**10, 0.97**10, 0.95**10),
    ("HMRC age gradient: homes 80% relative to the rest", 0.80, 1.0, 1.0),
]


def main():
    units, _ = validate.units_at_validation_prices()
    base_v, design = VARIANTS[0], VARIANTS[1]
    rows = []
    for name, hs, fs, ps in CASES:
        u = units.copy()
        u["property_wealth"] = u["property_wealth"] - u["residence_value"] * (1 - hs)
        u["residence_value"] = u["residence_value"] * hs
        u["financial_wealth"] = u["financial_wealth"] * fs
        u["dc_pension_wealth"] = u["dc_pension_wealth"] * ps
        base = validate.simulate_year_of_deaths(u, run_variant(u, RULES_2023_24, base_v), scenario="x")
        new = validate.simulate_year_of_deaths(u, run_variant(u, RULES_2023_24, design), scenario="x")
        rows.append({"case": name, "baseline_tax_bn": base["modelled_tax_gbp"] / 1e9,
                     "design_tax_bn": new["modelled_tax_gbp"] / 1e9,
                     "cost_share": 1 - new["modelled_tax_gbp"] / base["modelled_tax_gbp"],
                     "estates_share_lost": 1 - new["modelled_taxpaying_estates"] / base["modelled_taxpaying_estates"],
                     "ratio_to_hmrc_estates": base["modelled_taxpaying_estates"] / validate.hmrc_taxpaying_estates_aged_65_plus()})
    df = pd.DataFrame(rows)
    df.to_csv(HERE / "output" / "sensitivity.csv", index=False)
    pd.set_option("display.width", 200)
    print(df.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
