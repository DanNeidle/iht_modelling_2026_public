# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Rebuild the model under alternative assumptions and report changes in the headline measures."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import (ASSUMPTIONS, LIABILITY_THRESHOLDS, OUTPUT,
                        RULES_2026_27, RULES_2027_28, RuleSet)
from analysis.headline import compute
from model import build_units as bu
from model.population import summarise
from model.simulate import run_scenario

# Each entry is a label, the assumptions to use, and the rules to apply.
SCENARIOS: list[tuple[str, dict, RuleSet]] = [
    ("HEADLINE: April 2027 rules", {}, RULES_2027_28),
    ("Today's rules, pensions outside estates", {}, RULES_2026_27),

    # Pension inclusion from April 2027.
    ("Pensions: cohort-shifted DC pots",
     {"pension_cohort_basis": "cohort_shifted"}, RULES_2027_28),

    # The survey's missing rich households.
    ("No correction for the missing top tail",
     {"apply_pareto_tail": False}, RULES_2027_28),
    ("Heavier top tail (Pareto alpha 1.5)",
     {"pareto_alpha": 1.5}, RULES_2027_28),

    # Bringing survey-window values to April 2027. The base case moves property
    # on house prices and everything else on CPI; these turn each off in turn.
    ("Savings and pensions left at survey values",
     {"uprate_financial": 1.0, "uprate_pension": 1.0}, RULES_2027_28),
    ("Possessions uprated with everything else",
     {"uprate_physical": None}, RULES_2027_28),
    ("Property left at survey values",
     {"uprate_property": 1.0}, RULES_2027_28),
    ("No uprating at all",
     {"uprate_property": 1.0, "uprate_financial": 1.0,
      "uprate_pension": 1.0}, RULES_2027_28),

    # Physical wealth at replacement cost versus resale value.
    ("Physical wealth excluded entirely",
     {"physical_wealth_haircut": 1.0}, RULES_2027_28),
    ("Physical wealth at full replacement cost",
     {"physical_wealth_haircut": 0.0}, RULES_2027_28),

    # Alternative cohort, allowance and population-weighting assumptions.
    ("Cohabiting couples given one set of allowances",
     {"cohabiting_band_sets": 1.0}, RULES_2027_28),
    ("Weights raked per household, not per person",
     {"post_stratify_per_person": False}, RULES_2027_28),
    ("Multi-generational households excluded",
     {"exclude_multi_generational": True}, RULES_2027_28),

    # Business assets add to estates; identifying agricultural land allows relief.
    ("No business or agricultural property at all",
     {"include_business_agricultural": False}, RULES_2027_28),
    ("Business and agricultural property at survey values",
     {"remap_to_published_distribution": False}, RULES_2027_28),
    ("Business values uncapped (no winsorisation)",
     {"business_winsorise_percentile": 100.0}, RULES_2027_28),

    # Widows and their inherited nil-rate bands.
    ("Only 80% of widows have a transferred band",
     {"widow_transferred_band_share": 0.80}, RULES_2027_28),
    ("All widows have a full transferred band",
     {"widow_transferred_band_share": 1.0}, RULES_2027_28),
]


def run_one(overrides: dict, rules: RuleSet) -> dict:
    assumptions = dataclasses.replace(ASSUMPTIONS, **overrides)
    units = bu.load_was_units(assumptions).frame

    liabilities = run_scenario(units, rules)
    liabilities["scenario"] = "s"

    all_adults = summarise()["adults"]
    out = {}
    for threshold in LIABILITY_THRESHOLDS:
        h = compute(units, liabilities, "s", threshold, all_adults)
        out[threshold] = h
    return out


def main() -> None:
    rows = []
    print(f"\n{'scenario':<46} {'units':>8} {'adults':>8} {'£100k+':>8}")
    print(f"{'':<46} {'liable':>8} {'w/ stake':>8} {'adults':>8}")
    print("-" * 74)

    for label, overrides, rules in SCENARIOS:
        result = run_one(overrides, rules)
        any_bill = result[0.0]
        over_10k = result[10_000.0]
        over_100k = result[100_000.0]

        rows.append({
            "scenario": label,
            "units_liable_m": round(any_bill.exposed_units / 1e6, 3),
            "adults_any_bill_pct": round(any_bill.share_of_adults * 100, 1),
            "adults_over_10k_pct": round(over_10k.share_of_adults * 100, 1),
            "adults_over_100k_pct": round(over_100k.share_of_adults * 100, 1),
            "latent_tax_gbn": round(any_bill.latent_tax / 1e9),
        })
        print(f"{label:<46} {any_bill.exposed_units / 1e6:>7.2f}m "
              f"{any_bill.share_of_adults * 100:>7.1f}% "
              f"{over_100k.share_of_adults * 100:>7.1f}%")

    frame = pd.DataFrame(rows)
    path = OUTPUT / "sensitivity.csv"
    frame.to_csv(path, index=False)
    print(f"\nWritten to {path}")

    base = frame.iloc[0]["adults_any_bill_pct"]
    spread = frame["adults_any_bill_pct"]
    print(f"\nHeadline under the central assumptions: {base}% of GB adults.")
    print(f"Across every sensitivity: {spread.min()}% to {spread.max()}%.")


if __name__ == "__main__":
    main()
