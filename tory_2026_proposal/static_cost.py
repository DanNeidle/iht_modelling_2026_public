# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Static cost of exempting the main residence from inheritance tax.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _paths  # noqa: E402,F401  (puts the household model on the path)

from lib.config import (BUILD, LIABILITY_THRESHOLDS, RULES_2023_24,  # noqa: E402
                        RULES_2026_27, RULES_2027_28, RuleSet)
from lib.iht_rules import Estate, compute_tax  # noqa: E402
from model.build_units import UNITS_PATH  # noqa: E402
from model.simulate import estate_from_row  # noqa: E402
from model import validate  # noqa: E402

OUT_DIR = HERE / "output"
OUT_DIR.mkdir(exist_ok=True)


@dataclasses.dataclass(frozen=True)
class Variant:
    name: str
    exempt: bool = True
    cap: float | None = None          # cap on exempt home value, None = uncapped
    descendants_only: bool = False    # exemption only where the home passes to direct descendants
    keep_rnrb_when_not_exempt: bool = True
    nil_rate_band: float | None = None  # override the per-person nil-rate band (the Conservative design: £500,000)
    drop_rnrb: bool = False             # withdraw the residence nil-rate band for everyone, not just where the home is exempt


VARIANTS = [
    Variant("baseline (current law)", exempt=False),
    Variant("Conservative design: home exempt to descendants, NRB £500k each, no RNRB",
            descendants_only=True, nil_rate_band=500_000.0, drop_rnrb=True),
    Variant("as Conservative design but home exempt whoever inherits",
            descendants_only=False, nil_rate_band=500_000.0, drop_rnrb=True),
    Variant("threshold half only: NRB £500k each, no RNRB, home taxable",
            exempt=False, nil_rate_band=500_000.0, drop_rnrb=True),
    Variant("full exemption, uncapped, any heir"),
    Variant("full exemption, uncapped, direct descendants only", descendants_only=True),
    Variant("exemption capped at £500k of home value", cap=500_000.0),
    Variant("exemption capped at £1m of home value", cap=1_000_000.0),
    Variant("exemption capped at £2m of home value", cap=2_000_000.0),
]


def estate_with_exemption(row, has_descendants: bool, v: Variant) -> Estate:
    """Build the estate, removing the exempt part of the home."""
    est = estate_from_row(row, has_descendants)
    if v.drop_rnrb:
        est.residence_value = 0.0
    if not v.exempt:
        return est
    if v.descendants_only and not has_descendants:
        return est
    home = float(row.residence_value)
    exempt_amount = home if v.cap is None else min(home, v.cap)
    est.property_wealth = est.property_wealth - exempt_amount
    # Any part of the home above the cap stays taxable and could in principle still carry the RNRB. We assume the RNRB is withdrawn whenever the exemption applies, since the Conservative proposal replaces it.
    est.residence_value = 0.0
    return est


def rules_for_variant(rules: RuleSet, v: Variant) -> RuleSet:
    """Apply policy-level allowances as well as changes to the estate's assets."""
    if v.nil_rate_band is not None:
        rules = dataclasses.replace(rules, nil_rate_band=v.nil_rate_band)
    return rules


def run_variant(units: pd.DataFrame, rules: RuleSet, v: Variant) -> pd.DataFrame:
    rules = rules_for_variant(rules, v)
    tax_with, tax_without = [], []
    for row in units.itertuples():
        tax_with.append(compute_tax(estate_with_exemption(row, True, v), rules).tax)
        tax_without.append(compute_tax(estate_with_exemption(row, False, v), rules).tax)
    f = pd.DataFrame({
        "unit_id": units["unit_id"].to_numpy(),
        "weight": units["weight"].to_numpy(),
        "tax_with_descendants": tax_with,
        "tax_without_descendants": tax_without,
        "p_has_descendants": units["p_has_descendants"].to_numpy(),
    })
    p = f["p_has_descendants"]
    f["expected_tax"] = p * f["tax_with_descendants"] + (1 - p) * f["tax_without_descendants"]
    for t in LIABILITY_THRESHOLDS:
        f[f"p_over_{int(t)}"] = (p * (f["tax_with_descendants"] > t) + (1 - p) * (f["tax_without_descendants"] > t))
    f["scenario"] = "x"
    return f


def summarise(units, rules, label):
    rows = []
    base = None
    for v in VARIANTS:
        f = run_variant(units, rules, v)
        stock_tax = float((f["expected_tax"] * f["weight"]).sum())
        stock_exposed = float((f["p_over_0"] * f["weight"]).sum())
        deaths = validate.simulate_year_of_deaths(units, f, scenario="x")
        rec = {
            "price_basis": label,
            "variant": v.name,
            "stock_latent_tax_gbp_bn": stock_tax / 1e9,
            "stock_units_with_any_bill": stock_exposed,
            "year_taxpaying_estates_65plus": deaths["modelled_taxpaying_estates"],
            "year_tax_65plus_gbp_bn": deaths["modelled_tax_gbp"] / 1e9,
        }
        if base is None:
            base = rec
        rec["tax_share_of_baseline_year"] = rec["year_tax_65plus_gbp_bn"] / base["year_tax_65plus_gbp_bn"]
        rec["cost_share_of_baseline_year"] = 1 - rec["tax_share_of_baseline_year"]
        rec["estates_share_of_baseline_year"] = rec["year_taxpaying_estates_65plus"] / base["year_taxpaying_estates_65plus"]
        rec["cost_share_of_baseline_stock"] = 1 - rec["stock_latent_tax_gbp_bn"] / base["stock_latent_tax_gbp_bn"]
        rows.append(rec)
    return pd.DataFrame(rows)


def main() -> None:
    results = []

    # 1. Validation basis: 2023-24 prices, rules and population.
    units_v, _ = validate.units_at_validation_prices()
    hmrc_tax = validate.hmrc_tax_aged_65_plus()
    hmrc_estates = validate.hmrc_taxpaying_estates_aged_65_plus()
    df_v = summarise(units_v, RULES_2023_24, "2023-24 validation basis")
    df_v["hmrc_tax_65plus_gbp_bn"] = hmrc_tax / 1e9
    df_v["hmrc_estates_65plus"] = hmrc_estates
    df_v["implied_cost_on_hmrc_65plus_gbp_bn"] = df_v["cost_share_of_baseline_year"] * hmrc_tax / 1e9
    results.append(df_v)

    # 2. Headline basis: April 2027 prices, 2027-28 rules (pensions in estates), and 2026-27 rules for comparison.
    units_h = pd.read_parquet(UNITS_PATH)
    results.append(summarise(units_h, RULES_2027_28, "April 2027, pensions in estate"))
    results.append(summarise(units_h, RULES_2026_27, "April 2027 prices, pensions outside estate"))

    out = pd.concat(results, ignore_index=True)
    out.to_csv(OUT_DIR / "static_cost.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    print(out.round(3).to_string())


if __name__ == "__main__":
    main()
