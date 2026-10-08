# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Calculate each unit's tax with and without descendants, weighted by cohort childlessness probabilities. Save both branches to parquet for headline and validation calculations."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import (BUILD, LIABILITY_THRESHOLDS, RULES_2023_24,
                        RULES_2026_27, RULES_2027_28, RuleSet)
from lib.iht_rules import Estate, compute_tax
from model.build_units import PROVENANCE_PATH, UNITS_PATH

RESULTS_PATH = BUILD / "liabilities.parquet"

SCENARIOS: dict[str, RuleSet] = {
    "current": RULES_2026_27,
    "april_2027": RULES_2027_28,
    "validation_2023_24": RULES_2023_24,
}


def estate_from_row(row, has_descendants: bool) -> Estate:
    """Build an estate from a unit. Capitalised DB and in-payment pension wealth is excluded upstream."""
    return Estate(
        property_wealth=row.property_wealth,
        financial_wealth=row.financial_wealth,
        physical_wealth=row.physical_wealth,
        dc_pension_wealth=row.dc_pension_wealth,
        business_agricultural=row.business_agricultural,
        # Without descendants the home remains taxable but attracts no residence band.
        residence_value=row.residence_value if has_descendants else 0.0,
        has_direct_descendants=has_descendants,
        nil_rate_bands=row.nil_rate_bands,
        residence_bands=row.residence_bands,
        apr_bpr_allowances=row.apr_bpr_allowances,
        taper_thresholds=row.taper_thresholds,
    )


def run_scenario(units: pd.DataFrame, rules: RuleSet) -> pd.DataFrame:
    """Liability for every unit under one rule set, both descendant branches."""
    tax_with, tax_without = [], []
    gross_with = []

    for row in units.itertuples():
        result_with = compute_tax(estate_from_row(row, True), rules)
        result_without = compute_tax(estate_from_row(row, False), rules)
        tax_with.append(result_with.tax)
        tax_without.append(result_without.tax)
        gross_with.append(result_with.gross_estate)

    frame = pd.DataFrame({
        "unit_id": units["unit_id"].to_numpy(),
        "weight": units["weight"].to_numpy(),
        "tax_with_descendants": tax_with,
        "tax_without_descendants": tax_without,
        "gross_estate": gross_with,
        "p_has_descendants": units["p_has_descendants"].to_numpy(),
    })

    # The expected bill, averaging over whether the unit has descendants.
    frame["expected_tax"] = (
        frame["p_has_descendants"] * frame["tax_with_descendants"]
        + (1 - frame["p_has_descendants"]) * frame["tax_without_descendants"]
    )

    # Threshold probabilities: sum weight * p_over_0 for expected exposed units.
    for threshold in LIABILITY_THRESHOLDS:
        over_with = (frame["tax_with_descendants"] > threshold).astype(float)
        over_without = (frame["tax_without_descendants"] > threshold).astype(float)
        frame[f"p_over_{int(threshold)}"] = (
            frame["p_has_descendants"] * over_with
            + (1 - frame["p_has_descendants"]) * over_without
        )

    return frame


def main() -> None:
    units = pd.read_parquet(UNITS_PATH)

    frames = []
    for name, rules in SCENARIOS.items():
        frame = run_scenario(units, rules)
        frame["scenario"] = name
        frames.append(frame)

    results = pd.concat(frames, ignore_index=True)
    results.to_parquet(RESULTS_PATH)

    print(f"\n  Written to {RESULTS_PATH}  ({len(results):,} rows)\n")

    total_units = units["weight"].sum()
    for name in SCENARIOS:
        subset = results[results["scenario"] == name]
        print(f"  {name}")
        for threshold in LIABILITY_THRESHOLDS:
            exposed = (subset[f"p_over_{int(threshold)}"] * subset["weight"]).sum()
            label = "any bill" if threshold == 0 else f"£{int(threshold):,}+"
            print(f"    units with {label:<10} {exposed / 1e6:5.2f}m "
                  f"({exposed / total_units * 100:4.1f}% of units)")
        total_tax = (subset["expected_tax"] * subset["weight"]).sum()
        print(f"    latent tax in the stock  £{total_tax / 1e9:,.0f}bn\n")


if __name__ == "__main__":
    main()
