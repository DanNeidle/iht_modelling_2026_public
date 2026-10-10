"""Add aggregate diagnostics to a completed comparison, creating new files only."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd

from compare import CODE, RULES_2027_28, VARIANTS, run_variant, write_json
from distribution import death_weights


def describe(path: Path, round_number: int) -> dict:
    units = pd.read_parquet(path / f"round{round_number}_2027_28_units.parquet")
    weights = death_weights(units)
    baseline = run_variant(units, RULES_2027_28, VARIANTS[0])
    package = run_variant(units, RULES_2027_28, VARIANTS[1])
    home = run_variant(units, RULES_2027_28, VARIANTS[5])
    thresholds = run_variant(units, RULES_2027_28, VARIANTS[3])
    saving = baseline.expected_tax - package.expected_tax
    total_saving = float((saving * weights).sum())
    baseline_tax = float((baseline.expected_tax * weights).sum())
    records = []
    for region in sorted(units.region.unique()):
        mask = units.region == region
        records.append({
            "region": int(region),
            "annual_saving_gbp": float((saving[mask] * weights[mask]).sum()),
            "share_of_annual_saving": float((saving[mask] * weights[mask]).sum() / total_saving),
            "share_of_households": float(units.loc[mask, "weight"].sum() / units.weight.sum()),
        })
    regional = pd.DataFrame(records)
    regional.to_csv(path / f"round{round_number}_annual_regional.csv", index=False, mode="x")
    payers = baseline.p_over_0
    annual_payers = weights * payers
    asset_columns = ["residence_value", "financial_wealth", "dc_pension_wealth",
                     "physical_wealth", "business_agricultural", "property_wealth"]
    asset_means = {col: float(np.average(units[col], weights=annual_payers))
                   for col in asset_columns}
    result = {
        "round": round_number,
        "home_only_cost_share": float(((baseline.expected_tax - home.expected_tax) * weights).sum() / baseline_tax),
        "threshold_only_cost_share": float(((baseline.expected_tax - thresholds.expected_tax) * weights).sum() / baseline_tax),
        "package_cost_share": total_saving / baseline_tax,
        "london_south_east_share_of_annual_saving": float(
            regional.loc[regional.region.isin([8, 9]), "share_of_annual_saving"].sum()),
        "mean_assets_of_baseline_taxpaying_estates_gbp": asset_means,
    }
    expected = package.p_has_descendants
    penalty_results = {}
    for label, fraction in (("sell", 1.0), ("halve", 0.5)):
        moved = units.copy(deep=True)
        amount = moved.residence_value * fraction
        moved.residence_value -= amount
        moved.property_wealth -= amount
        moved.financial_wealth += amount
        tax_after_move = run_variant(moved, RULES_2027_28, VARIANTS[1])
        # A household with descendants and a positive tax penalty counts once,
        # weighted by its probability of having descendants.
        penalty = tax_after_move.tax_with_descendants - package.tax_with_descendants
        penalty_results[label] = float((units.weight * expected * (penalty > 1e-6)).sum())
    result["households_with_selling_or_downsizing_penalty"] = penalty_results
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    output = [describe(args.directory, wave) for wave in (7, 8)]
    write_json(args.directory / "diagnostics.json", output)
    for record in output:
        print(record)


if __name__ == "__main__":
    main()
