# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Retention, housing, fixed-cost planning and totals for the October 2026 article.

All behavioural choices below are scenarios, not fitted estimates. HMRC retention
uses the tax-bill distribution of estates without residential property, assumes
it also applies to those aged 65+, and pairs it with age-band mean home values.
Existing downsizing relief is unknown: report none, half and all eligible estates
claiming it. Scale historical revenue amounts by 13.7/7.03, not by a price index.

The fixed-cost scenario assumes planning shelters 20% of the potential bill and
costs a fixed sum. It measures the revenue from abandoning planning when the
post-reform saving falls below that cost. It does not identify actual take-up.

Run from the public repository root:
python3 tory_2026_proposal/review_calculations.py --budget-bn 7.7
In the research project the script is in code/residence_codex/.
Only aggregate results are printed. --output saves JSON; --hmrc-only skips WAS.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

import numpy as np
import pandas as pd

from behaviour import (ROOT, UNITS_PATH, RULES_2027_28, compute_tax,
                       estate_from_row, reformed_tax, death_weights)
import hmrc_crosscheck as hc
from alternatives import model_tax, solve
from static_cost import Variant
from downsizing_channel import article_estimate

OBR = 13.7e9
HISTORICAL_TAX = hc.TOTAL_TAX
SCALE = OBR / HISTORICAL_TAX
AGE_BANDS = [(390, 1790e6 / 3460), (1090, 4220e6 / 7240),
             (3300, 7610e6 / 12300)]


def retention_saving(old_bill, bands, old_relief, home):
    """Additional saving from keeping the home, after the static reform.

    The old bill is already net of old allowances. Restore any old downsizing
    relief, subtract the increase in ordinary bands, then cap the housing saving.
    """
    after_sale_tax = np.maximum(0, old_bill - .4 * (175_000 * bands - old_relief))
    return np.minimum(.4 * home, after_sale_tax)


def retention(draws=200_000):
    rng = np.random.default_rng(20261008)
    no_home_count = sum(n - nr for _, _, n, nr, _ in hc.BANDS)
    scenarios = []
    for old_relief_share in (0.0, 0.5, 1.0):
        tax_at_risk = eligible_homes = eligible_values = 0.0
        for (lo, hi, n, nr, _), total_tax in zip(hc.BANDS, hc.band_tax()):
            avg = total_tax / n
            if hi:
                bills = np.exp(rng.uniform(np.log(lo), np.log(hi), draws))
                bills *= avg / bills.mean()
            else:
                alpha = avg / (avg - lo)
                bills = lo * (1 - rng.uniform(size=draws)) ** (-1 / alpha)
            for bands, bp in ((1, 1 - hc.P_TWO_BANDS), (2, hc.P_TWO_BANDS)):
                # Infer the old chargeable estate from its bill and allowances.
                # At full downsizing relief, invert the RNRB taper exactly in
                # the simplifying absence of other reliefs in this reconstruction.
                z = bills / 0.4 + 325_000 * bands
                maximum_rnrb = 175_000 * bands
                rnrb = np.where(z + maximum_rnrb <= 2e6, maximum_rnrb,
                                np.maximum(0, (maximum_rnrb - .5 * (z - 2e6)) / 1.5))
                for relief, rp in ((np.zeros_like(bills), 1 - old_relief_share),
                                   (rnrb, old_relief_share)):
                    for age_count, home in AGE_BANDS:
                        weight = age_count * (n - nr) / no_home_count * bp * rp * hc.P_DESC
                        saving = retention_saving(bills, bands, relief, home)
                        tax_at_risk += weight * saving.mean()
                        eligible_homes += weight * (saving > 0).mean()
                        eligible_values += weight * (saving > 0).mean() * home
        scenarios.append(dict(old_downsizing_relief_share=old_relief_share,
                              tax_at_risk_2023_24=tax_at_risk,
                              tax_at_risk_2029_scale=tax_at_risk * SCALE,
                              eligible_homes_per_year=eligible_homes,
                              eligible_home_values=eligible_values,
                              response_50pct_2029=tax_at_risk * SCALE * .5,
                              response_80pct_2029=tax_at_risk * SCALE * .8))
    return scenarios


def fixed_cost_offset(before, after, fee, saving_share=.2):
    """Revenue restored per participant who stops a fixed-cost arrangement."""
    if saving_share * before > fee and saving_share * after <= fee:
        return saving_share * after
    return 0.0


def fixed_cost_and_housing(units):
    dd = death_weights(units).to_numpy()
    base = 0.0
    fees = (5_000, 10_000, 20_000)
    participation = (.25, .5)
    scenarios = {fee: dict(switchers=0.0, offset=0.0) for fee in fees}
    homes = []
    for i, row in enumerate(units.itertuples()):
        home = max(0, row.residence_value)
        for desc, p in ((True, row.p_has_descendants), (False, 1 - row.p_has_descendants)):
            est = estate_from_row(row, desc)
            before = compute_tax(est, RULES_2027_28).tax
            after = reformed_tax(est, home, RULES_2027_28)
            w = dd[i] * p
            base += w * before
            for fee in fees:
                if .2 * before > fee and .2 * after.tax <= fee:
                    scenarios[fee]['switchers'] += w
                    scenarios[fee]['offset'] += w * fixed_cost_offset(before, after.tax, fee)
            if desc and home > 0:
                sold = dataclasses.replace(est, property_wealth=est.property_wealth-home,
                                           residence_value=0,
                                           financial_wealth=est.financial_wealth+home)
                half = dataclasses.replace(est, property_wealth=est.property_wealth-home/2,
                                           residence_value=home/2,
                                           financial_wealth=est.financial_wealth+home/2)
                homes.append(dict(w=row.weight*p, home=home, after=after.tax,
                                  taxable=after.taxable_amount, fin=max(0, est.financial_wealth),
                                  pen_all=reformed_tax(sold, 0, RULES_2027_28).tax-after.tax,
                                  pen_half=reformed_tax(half, home/2, RULES_2027_28).tax-after.tax))
    f = pd.DataFrame(homes)
    out = dict(owners_with_descendants=float(f.w.sum()))
    for col in ('pen_all', 'pen_half'):
        out[col] = {str(thr): float(f.loc[f[col] > thr, 'w'].sum()) for thr in (0, 50_000, 100_000)}
        m = f.loc[f[col] > 0].sort_values(col)
        out[col]['median'] = float(m.loc[m.w.cumsum() >= m.w.sum()/2, col].iloc[0])
    pay = f.after > 0
    out['remaining_payer_households'] = float(f.loc[pay, 'w'].sum())
    out['remaining_financial'] = float((f.loc[pay, 'w']*f.loc[pay, 'fin']).sum())
    out['remaining_taxable'] = float((f.loc[pay, 'w']*f.loc[pay, 'taxable']).sum())
    out['all_financial'] = float((units.weight * units.financial_wealth).sum())
    out['portfolio'] = []
    for share in (.075, .15, .3):
        moved = np.minimum(share * f.fin, f.taxable).where(pay, 0)
        total = float((moved*f.w).sum())
        out['portfolio'].append(dict(share=share, moved=total,
                                    per_household=total/out['remaining_payer_households'],
                                    share_all_financial=total/out['all_financial']))
    return dict(baseline_annual_tax=base, housing=out,
                fixed_cost=[dict(fee=fee, take_up=takeup,
                                 model_switchers=v['switchers']*takeup,
                                 offset_2029=v['offset']*takeup/base*OBR)
                            for fee,v in scenarios.items() for takeup in participation])


def article_totals(retained, downsizing):
    """Reconcile the published rounded components and their unrounded inputs.

    Static £7bn to £9bn, portfolio £0.3bn to £0.6bn and marginal offset
    £0.02bn to £0.07bn are the article's rounded scenario ranges. The separate
    fixed-cost sensitivity is excluded, as it is in the article's headline.
    """
    unrounded_low = 7 + retained[0]['response_50pct_2029']/1e9 + downsizing['low_2029']/1e9 + .3 - .07
    unrounded_high = 9 + retained[-1]['response_80pct_2029']/1e9 + downsizing['high_2029']/1e9 + .6 - .02
    return dict(basis='2029-30 receipts scale; GBP billions',
                published_components=dict(static=[7, 9], retention=[.25, .7],
                                          downsizing=[.25, .65], portfolio=[.3, .6],
                                          marginal_offset=[.02, .07]),
                total_from_published_components=[7+.25+.25+.3-.07, 9+.7+.65+.6-.02],
                total_with_unrounded_housing=[unrounded_low, unrounded_high],
                headline=[round(unrounded_low, 1), round(unrounded_high, 1)],
                fixed_cost_sensitivity_included=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--budget-bn', type=float, default=None)
    parser.add_argument('--hmrc-only', action='store_true',
                        help='Retention, downsizing and headline arithmetic without survey inputs')
    args = parser.parse_args()
    if args.hmrc_only and args.budget_bn is not None:
        parser.error('--budget-bn requires the survey; omit it with --hmrc-only')
    if args.budget_bn is not None and not 0 < args.budget_bn * 1e9 < OBR:
        parser.error('--budget-bn must be greater than zero and below 13.7')
    out = {}
    if not args.hmrc_only:
        units = pd.read_parquet(UNITS_PATH)
        out = fixed_cost_and_housing(units)
    out['retention'] = retention()
    out['downsizing'] = article_estimate()
    out['article_totals'] = article_totals(out['retention'], out['downsizing'])
    if not args.hmrc_only:
        n = out['housing']['pen_half']['0']
        retained_homes = [out['retention'][0]['eligible_homes_per_year']*.5,
                          out['retention'][-1]['eligible_homes_per_year']*.8]
        fewer_downsizes = [n*.02*.36*.5, n*.02*.47*.5]
        out['housing_flows'] = dict(retained_homes_per_year=retained_homes,
                                   fewer_downsizes_per_year=fewer_downsizes,
                                   combined_postponed_sales_per_year=[a+b for a,b in zip(retained_homes, fewer_downsizes)],
                                   note='Postponed sales; no permanent transaction-loss estimate')
    if args.budget_bn is not None:
        target = args.budget_bn*1e9/OBR
        def cut(nrb):
            return 1-model_tax(units, RULES_2027_28,
                        Variant('alternative', exempt=False, nil_rate_band=nrb, drop_rnrb=True))/out['baseline_annual_tax']
        nrb = solve(cut, 500_000, 3_000_000, target, tol=500)
        out['alternative'] = dict(budget_bn=args.budget_bn, nrb=nrb,
                                  rate=.4*(1-target), reduction=target)
    text = json.dumps(out, indent=2)+'\n'
    if args.output:
        dest = args.output.resolve()
        if (ROOT/'notes').is_dir() and not dest.is_relative_to(ROOT/'notes'):
            parser.error('Derived results must be saved under notes/')
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open('x') as handle:
            handle.write(text)
    print(text)


if __name__ == '__main__':
    main()
