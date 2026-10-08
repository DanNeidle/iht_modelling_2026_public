# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""How many homes, and how much savings, the Conservative design would affect.
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
from lib.iht_rules import compute_tax  # noqa: E402
from model.build_units import UNITS_PATH  # noqa: E402
from static_cost import VARIANTS, estate_with_exemption, rules_for_variant  # noqa: E402

DESIGN = VARIANTS[1]


def main():
    u = pd.read_parquet(UNITS_PATH)
    rules = rules_for_variant(RULES_2027_28, DESIGN)
    rows = []
    for row in u.itertuples():
        home = float(row.residence_value)
        kept = estate_with_exemption(row, True, DESIGN)
        t_kept = compute_tax(kept, rules)
        sold = dataclasses.replace(kept, financial_wealth=kept.financial_wealth + home)
        t_sold = compute_tax(sold, rules).tax
        half = dataclasses.replace(kept, financial_wealth=kept.financial_wealth + home / 2)
        t_half = compute_tax(half, rules).tax
        rows.append((row.weight * row.p_has_descendants, home, t_kept.tax, t_kept.taxable_amount,
                     t_sold - t_kept.tax, t_half - t_kept.tax, row.financial_wealth))
    f = pd.DataFrame(rows, columns=["w", "home", "tax_kept", "taxable_kept", "pen_all", "pen_half", "fin"])
    own = f.home > 0
    W = f.w
    n_own = W[own].sum()
    print(f"65-plus homeowner units with descendants (GB, weighted): {n_own/1e6:.2f}m; all 65-plus units {u.weight.sum()/1e6:.2f}m")
    for lab, col in (("selling up entirely", "pen_all"), ("moving to a home worth half as much", "pen_half")):
        for thr in (1, 50_000, 100_000):
            m = own & (f[col] >= thr)
            print(f"  {lab}: penalty >= £{thr:,}: {W[m].sum()/1e6:.2f}m households ({W[m].sum()/n_own:.0%} of owners); "
                  f"median penalty £{np.median(np.repeat(f[col][m], (W[m]/50).round().astype(int))):,.0f}; homes worth £{(f.home*W)[m].sum()/1e9:,.0f}bn")
    m = own & (f.pen_half >= 1)
    # downsizing flow: 2% of older owners move a year, 36% to 47% of those downsize
    for d in (0.36, 0.47):
        print(f"  downsizing moves a year by these households (2% move, {d:.0%} downsize): {W[m].sum()*0.02*d:,.0f}")

    # savings
    pay = own & (f.taxable_kept > 0)
    print(f"\nhomeowners still paying after reform: {W[pay].sum()/1e6:.2f}m; their financial wealth £{(f.fin*W)[pay].sum()/1e9:,.0f}bn; "
          f"taxable excess £{(f.taxable_kept*W)[pay].sum()/1e9:,.0f}bn")
    for s in (0.075, 0.15, 0.30):
        moved = np.minimum(s * f.fin, f.taxable_kept).clip(lower=0).where(pay, 0.0)
        print(f"  {s:.1%} of financial wealth moved, capped at taxable excess: £{(moved*W).sum()/1e9:,.0f}bn "
              f"(£{(moved*W).sum()/W[pay].sum():,.0f} per household)")
    print(f"total financial wealth of all 65-plus units: £{(u.financial_wealth*u.weight).sum()/1e9:,.0f}bn")

    # HMRC: estates with no house
    b = {"65-74": (3850, 3460, 1790), "75-84": (8330, 7240, 4220), "85+": (15600, 12300, 7610)}
    tot_n = tot_v = 0
    for k, (n, nres, res) in b.items():
        tot_n += n - nres; tot_v += (n - nres) * res / nres * 1e6
    print(f"\nHMRC: {tot_n:,} taxpaying estates aged 65+ a year with no house; at band-average values £{tot_v/1e9:.1f}bn")
    # The gross missing-home count is not the population with an incentive:
    # exclude non-descendant cases and estates sheltered by the new allowances.
    try:
        from review_calculations import retention
    except ModuleNotFoundError:
        sys.path.insert(0, str(HERE.parent / 'residence_codex'))
        from review_calculations import retention
    retained = retention()
    lo = retained[0]['eligible_homes_per_year'] * .5
    hi = retained[-1]['eligible_homes_per_year'] * .8
    print(f"  After descendants and remaining-tax limits, 50% to 80% response: {lo:,.0f} to {hi:,.0f} homes a year")


if __name__ == "__main__":
    main()
