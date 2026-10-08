# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Compare IHT with homes at current value, inflation-adjusted purchase cost and nominal purchase cost.

Re-run tax on the same households so thresholds respond to the changed values. Hold Pareto financial-wealth uplifts fixed; only housing may change. This decomposes observed estates without modelling behavioural responses to different house prices.

Only measurable main-home gains are removed. Other property and missing purchase costs reduce coverage; under-reported costs can inflate gains. See model/housing_gain.py."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import ASSUMPTIONS, HEADLINE_RULES, LIABILITY_THRESHOLDS, OUTPUT
from analysis.headline import compute
from model import build_units as bu
from model.population import summarise
from model.simulate import run_scenario


WORLDS = [
    ("actual", "none", "As things are"),
    ("real", "real", "If homes had only kept pace with inflation"),
    ("nominal", "nominal", "If homes were still worth what was paid for them"),
]


def run_world(mode: str, frozen_uplift=None):
    """Build one counterfactual, reusing frozen_uplift for financial wealth if supplied instead of refitting the Pareto tail."""
    assumptions = dataclasses.replace(
        ASSUMPTIONS, housing_gain_counterfactual=mode,
        apply_pareto_tail=(frozen_uplift is None))
    units = bu.load_was_units(assumptions).frame

    if frozen_uplift is not None:
        import numpy as np
        if not np.array_equal(units["unit_id"].to_numpy(),
                              frozen_uplift.index.to_numpy()):
            raise ValueError("The counterfactual produced a different set of "
                             "units from the actual world, so the frozen "
                             "Pareto uplift cannot be lined up against it.")
        units["financial_wealth"] = (units["financial_wealth"].to_numpy()
                                     + frozen_uplift.to_numpy())

    liabilities = run_scenario(units, HEADLINE_RULES)
    liabilities["scenario"] = "s"
    return units, liabilities


def asset_comparison() -> dict:
    """Gross assets potentially affected by the death uplift. Use OthpropvalR8_sum for all other property, including buy-to-let. Exclude pensions and ISAs, because duh not taxed."""
    import pyreadstat

    folder = bu.was_dir()
    household, _ = pyreadstat.read_sav(
        str(folder / bu.HOUSEHOLD_FILE),
        usecols=["CASER8", "R8xshhwgt", "DVHValueR8", "OthpropvalR8_sum",
                 "DVLUKValR8_sum", "DVLOSValR8_sum", "DVFShUKVR8_aggr",
                 "DVFShOSVR8_aggr"])
    person, _ = pyreadstat.read_sav(str(folder / bu.PERSON_FILE),
                                    usecols=["CASER8", "DVAge17R8"])

    oldest = person.groupby("CASER8")["DVAge17R8"].max()
    frame = household.set_index("CASER8").join(oldest.rename("oldest"))
    frame = frame[frame["oldest"] >= 14]
    weight = frame["R8xshhwgt"]

    def gross(*columns):
        total = sum(frame[c].where(frame[c] > 0, 0.0) for c in columns)
        return float((total * weight).sum())

    parts = {
        "main_residence": gross("DVHValueR8"),
        "other_property": gross("OthpropvalR8_sum"),
        "land": gross("DVLUKValR8_sum", "DVLOSValR8_sum"),
        "shares": gross("DVFShUKVR8_aggr", "DVFShOSVR8_aggr"),
    }
    parts["main_residence_share"] = parts["main_residence"] / sum(parts.values())
    return parts


def check_only_housing_moved(frames) -> None:
    """Assert that only housing changes. Preserve other assets and reduce total property wealth by exactly the reduction in home value."""
    import numpy as np

    base, _ = frames["actual"]
    for key in ("real", "nominal"):
        other, _ = frames[key]
        for column in ("financial_wealth", "physical_wealth",
                       "dc_pension_wealth", "business_agricultural"):
            if not np.allclose(base[column], other[column], atol=1.0):
                worst = float(np.abs(base[column] - other[column]).max())
                raise ValueError(
                    f"{column} moved between the actual world and '{key}' "
                    f"(worst difference £{worst:,.0f}). Only the home may "
                    f"differ, so something else is being attributed to house "
                    f"prices.")

        outside = base["property_wealth"] - base["residence_value"]
        outside_other = other["property_wealth"] - other["residence_value"]
        if not np.allclose(outside, outside_other, atol=1.0):
            raise ValueError(f"Property outside the main residence moved "
                             f"between the actual world and '{key}'.")

        fell = base["property_wealth"] - other["property_wealth"]
        home_fell = base["residence_value"] - other["residence_value"]
        if not np.allclose(fell, home_fell, atol=1.0):
            raise ValueError(f"Property wealth and the home fell by different "
                             f"amounts between the actual world and '{key}'.")

    print("  Checked: nothing but the home differs between the three worlds.")


def main() -> None:
    all_adults = summarise()["adults"]
    results, frames = {}, {}

    print("\nRunning the model three times over the same households")
    frozen = None
    for key, mode, label in WORLDS:
        units, liabilities = run_world(mode, frozen)
        if key == "actual":
            # Fitted once, here, and held still for the other two.
            frozen = units.set_index("unit_id")["pareto_uplift"]
        frames[key] = (units, liabilities)
        results[key] = {
            threshold: compute(units, liabilities, "s", threshold, all_adults)
            for threshold in LIABILITY_THRESHOLDS
        }
        head = results[key][0.0]
        households = (units["weight"] * liabilities.set_index("unit_id")
                      .loc[units["unit_id"], "p_over_0"].to_numpy()).sum()
        print(f"  {label:44s} {households / 1e6:5.2f}m households, "
              f"{head.share_of_adults * 100:4.1f}% of adults, "
              f"£{head.latent_tax / 1e9:.0f}bn")

    check_only_housing_moved(frames)

    # Compare the same households in the same order to measure exposure lost when gains are removed.
    base_units, base_liab = frames["actual"]
    weight = base_units["weight"].to_numpy()
    total_households = weight.sum()

    def exposure(key: str):
        units, liabilities = frames[key]
        return (liabilities.set_index("unit_id")
                .loc[units["unit_id"], "p_over_0"].to_numpy())

    actual = exposure("actual")
    out = {"households_65_plus": float(total_households)}

    print("\nOf the households exposed as things are:")
    for key, _, label in WORLDS[1:]:
        counter = exposure(key)
        # Lost exposure is the fall in expected probability.
        only_because = float((weight * (actual - counter).clip(0)).sum())
        share = only_because / float((weight * actual).sum())
        tax_gone = (results["actual"][0.0].latent_tax
                    - results[key][0.0].latent_tax)
        print(f"  {share * 100:4.1f}% would face no bill at all {label.lower()}")
        print(f"        and £{tax_gone / 1e9:.0f}bn of the "
              f"£{results['actual'][0.0].latent_tax / 1e9:.0f}bn goes with them "
              f"({tax_gone / results['actual'][0.0].latent_tax * 100:.0f}%)")
        out[key] = {
            "households_exposed": float((weight * counter).sum()),
            "share_of_65_plus": float((weight * counter).sum() / total_households),
            "adults_pct": results[key][0.0].share_of_adults * 100,
            "latent_tax": results[key][0.0].latent_tax,
            "exposed_only_by_gain": only_because,
            "share_of_exposed_only_by_gain": share,
            "tax_from_gain": tax_gone,
            "share_of_tax_from_gain": tax_gone / results["actual"][0.0].latent_tax,
        }

    out["actual"] = {
        "households_exposed": float((weight * actual).sum()),
        "share_of_65_plus": float((weight * actual).sum() / total_households),
        "adults_pct": results["actual"][0.0].share_of_adults * 100,
        "latent_tax": results["actual"][0.0].latent_tax,
    }

    # how much of a home is gain, for the households that face a bill
    counted = base_units["housing_gain_counted"].to_numpy()
    exposed_weight = weight * actual
    for name in ("nominal", "real"):
        value = base_units["residence_value"].to_numpy()
        # Cap gross home gains at net residence value to keep the share's numerator and denominator consistent.
        gain = np.minimum(base_units[f"housing_gain_{name}"].to_numpy(), value)
        rows = counted & (value > 0)
        share = float((exposed_weight[rows] * gain[rows]).sum()
                      / (exposed_weight[rows] * value[rows]).sum())
        out[f"gain_share_of_home_{name}"] = share
        print(f"\n  Among exposed households, {share * 100:.0f}% of the value "
              f"of the home is {name} gain")

    out["coverage"] = float(
        weight[counted & (base_units["residence_value"] > 0)].sum()
        / weight[base_units["residence_value"] > 0].sum())
    print(f"\n  Purchase price known for {out['coverage'] * 100:.0f}% of "
          f"home-owning households; the rest contribute no gain.")

    assets = asset_comparison()
    out["assets"] = assets
    print(f"\n  Assets which can carry a gain the uplift would wipe, 65+ "
          f"households:")
    for name in ("main_residence", "other_property", "land", "shares"):
        print(f"    {name:16s} £{assets[name] / 1e9:7.0f}bn")
    print(f"    the main home is {assets['main_residence_share'] * 100:.0f}% "
          f"of that")

    path = OUTPUT / "housing_gain.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"\nWritten to {path}")


if __name__ == "__main__":
    main()
