# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Compare modelled APR/BPR claims with HMRC Table 12.2 under 2023-24 uncapped rules.

Scale HMRC targets by the model/HMRC taxable-estate ratio. This assumes business and agricultural assets decumulate in proportion to other wealth. Qualification is not observed in WAS; relief_distribution.py maps all holders to HMRC's distribution.

Everything here is valued in 2023-24 money, which is the year HMRC's tables cover, for the same reason validate.py does it: the headline model is in April 2027 money, and comparing that against claims made three years earlier would credit inflation to the estates."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import (ASSUMPTIONS, RELIEF_CLAIMS_2023_24, RULES_2023_24,
                        VALIDATION_2023_24)
from model.administrative import deaths_65_plus_by_marital_status
from model.validate import (band_mortality_rates, deaths_at_65_plus,
                            load_mortality_rates, units_at_validation_prices)


def death_weights(units: pd.DataFrame) -> pd.Series:
    """Expected chargeable deaths per unit, with the same marital mortality adjustment as validate.py."""
    mortality = load_mortality_rates()
    oldest = max(mortality)
    band_rates = band_mortality_rates()

    qx = units["age"].map(band_rates)
    qx = qx.fillna(units["age"].clip(upper=oldest).map(mortality)).astype(float)

    exposure = (units["marital_status"] != "Married").astype(float)
    uncorrected = units["weight"] * qx * exposure

    by_status = deaths_65_plus_by_marital_status()
    total = sum(by_status.values())
    observed = sum(v for k, v in by_status.items()
                   if k != "Married or civil partnered") / total

    units = units.assign(_qx=qx)
    all_deaths = deaths_at_65_plus(units)
    modelled = uncorrected.sum() / all_deaths
    return uncorrected * (observed / modelled)


def claim_counts(values: pd.Series, deaths: pd.Series,
                 reporting: pd.Series,
                 bands: list[tuple[int, int, float]]) -> dict:
    """Count weighted claims by band only for estates above the reporting threshold."""
    live = (values > 0) & reporting
    total = float(deaths[live].sum())

    edges = [lower for lower, _, _ in bands] + [float("inf")]
    counted = []
    for low, high in zip(edges[:-1], edges[1:]):
        inside = live & (values >= low) & (values < high)
        counted.append(float(deaths[inside].sum()))

    return {"claims": total,
            "value": float((deaths * values)[live].sum()),
            "bands": counted}


def main() -> None:
    units, liabilities = units_at_validation_prices()

    deaths = death_weights(units)

    # Scale HMRC claims by modelled taxable estates / HMRC's all-age UK total, allowing for both the stock-to-flow gap and population coverage.
    subset = liabilities[liabilities["scenario"] == "validation_2023_24"]
    subset = subset.set_index("unit_id")
    p_over_0 = units.set_index("unit_id").index.map(subset["p_over_0"])
    modelled_estates = float((deaths.to_numpy() * np.asarray(p_over_0)).sum())
    ratio = modelled_estates / VALIDATION_2023_24["taxpaying_estates"]

    print(f"\n  Model produces {modelled_estates:,.0f} taxable estates against "
          f"HMRC's {VALIDATION_2023_24['taxpaying_estates']:,} across all ages")
    print(f"  Relief targets multiplied by {ratio:.2f} to match that basis.")

    # No pension pots: RULES_2023_24 keeps them outside the estate, and this
    # test decides which estates had to file in 2023-24. Counting them here
    # inflated the modelled business claims by a third.
    estate = (units["property_wealth"] + units["financial_wealth"]
              + units["physical_wealth"] + units["business_agricultural"])
    if RULES_2023_24.pensions_in_estate:
        raise ValueError("RULES_2023_24 now puts pensions in the estate, so "
                         "the filing test above needs to include them again.")
    threshold = RULES_2023_24.nil_rate_band * units["nil_rate_bands"]

    for asset in ("agricultural", "business"):
        spec = RELIEF_CLAIMS_2023_24[asset]
        target = spec["claims"] * ratio
        target_value = spec["value_gbp"] * ratio

        values = units["business_property" if asset == "business"
                       else "agricultural_property"]
        reporting = estate > threshold
        got = claim_counts(values, deaths, reporting, spec["bands"])

        holders = values > 0
        print(f"\n  {asset.upper()}")
        print(f"    stock      {units['weight'][holders].sum():>11,.0f} households "
              f"hold qualifying property "
              f"({units['weight'][holders].sum() / units['weight'].sum() * 100:.2f}% "
              f"of all units)")
        print(f"    claims     modelled {got['claims']:>9,.0f}   "
              f"target {target:>9,.0f}   ratio {got['claims'] / target:>5.2f}")
        print(f"    value      modelled £{got['value'] / 1e9:>6.2f}bn   "
              f"target £{target_value / 1e9:>6.2f}bn   "
              f"ratio {got['value'] / target_value:>5.2f}")
        print(f"    by claim size band (lower limit, modelled vs target):")
        for (low, count, _), modelled in zip(spec["bands"], got["bands"]):
            print(f"      >= £{low:>9,}  {modelled:>9,.0f}  vs  {count * ratio:>9,.0f}")

    print()


if __name__ == "__main__":
    main()
