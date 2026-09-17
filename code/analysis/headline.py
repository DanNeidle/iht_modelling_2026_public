# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Count adults in exposed units plus their living adult children. Deduct children with their own exposed unit and those counted through two liable "parental units". 
xclude grandchildren, in-laws and other beneficiaries. Because too messy."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import (HEADLINE_AGE, LIABILITY_THRESHOLDS, OUTPUT,
                        VALIDATION_2023_24)
from model.build_units import PROVENANCE_PATH, UNITS_PATH
from model.population import summarise
from model.simulate import RESULTS_PATH, SCENARIOS

# Assumed share of children with divorced parents who both re-partnered. Bounds step-family double counting; not directly measured.
STEP_FAMILY_DOUBLE_COUNT_RATE = 0.04


@dataclass
class Headline:
    scenario: str
    threshold: float
    exposed_units: float
    adults_in_exposed_units: float
    adult_children: float
    overlap_children_over_65: float
    overlap_step_families: float
    affected_adults: float
    all_adults: float
    latent_tax: float

    @property
    def share_of_adults(self) -> float:
        return self.affected_adults / self.all_adults

    @property
    def multiple_of_the_headline_figure(self) -> float:
        """Ratio to HMRC's share of deaths taxed, using the configured outturn."""
        return self.share_of_adults / (
            VALIDATION_2023_24["proportion_taxed_pct"] / 100.0)


def compute(units: pd.DataFrame, liabilities: pd.DataFrame,
            scenario: str, threshold: float, all_adults: float) -> Headline:
    subset = liabilities[liabilities["scenario"] == scenario].set_index("unit_id")
    joined = units.set_index("unit_id").join(subset[[
        f"p_over_{int(threshold)}", "expected_tax", "tax_with_descendants",
        "tax_without_descendants",
    ]])

    weight = joined["weight"]
    p_exposed = joined[f"p_over_{int(threshold)}"]

    exposed_units = (weight * p_exposed).sum()

    # Count unit members, including partners under 65.
    adults_in_units = (weight * p_exposed * joined["n_adults"]).sum()

    # Divide unconditional child counts by the probability of having children.
    has_children = (1.0 - joined["p_childless"]).clip(lower=0.01)
    children_given_any = joined["mean_living_adult_children"] / has_children

    # Count children only on the descendants branch, using that branch's liability.
    exposed_with_descendants = (
        joined["p_has_descendants"]
        * (joined["tax_with_descendants"] > threshold).astype(float)
    )
    adult_children = (weight * exposed_with_descendants * children_given_any).sum()

    # Subtract 65-plus children with an exposed unit, using the overall exposure rate.
    exposure_rate_among_over_65s = (
        (weight * p_exposed * joined["members_65_plus"]).sum()
        / (weight * joined["members_65_plus"]).sum()
    )
    overlap_over_65 = (
        weight * exposed_with_descendants * children_given_any
        * joined["p_child_aged_65_plus"] * exposure_rate_among_over_65s
    ).sum()

    # Second subtraction: step families, bounded rather than measured.
    overlap_step = adult_children * STEP_FAMILY_DOUBLE_COUNT_RATE

    affected = adults_in_units + adult_children - overlap_over_65 - overlap_step

    # Sum tax only for estates clearing this threshold, taking each descendants
    # branch on its own terms. expected_tax already averages the two branches, so
    # multiplying it by p_exposed, which averages the same two branches again,
    # squares the probability for every unit where the branches disagree about
    # whether there is a bill at all. That is 1.4m units, and it was costing
    # about £10bn of the total.
    latent_tax = (weight * (
        joined["p_has_descendants"]
        * joined["tax_with_descendants"]
        * (joined["tax_with_descendants"] > threshold)
        + (1.0 - joined["p_has_descendants"])
        * joined["tax_without_descendants"]
        * (joined["tax_without_descendants"] > threshold)
    )).sum()

    return Headline(
        scenario=scenario,
        threshold=threshold,
        exposed_units=exposed_units,
        adults_in_exposed_units=adults_in_units,
        adult_children=adult_children,
        overlap_children_over_65=overlap_over_65,
        overlap_step_families=overlap_step,
        affected_adults=affected,
        all_adults=all_adults,
        latent_tax=latent_tax,
    )


def main() -> None:
    units = pd.read_parquet(UNITS_PATH)
    liabilities = pd.read_parquet(RESULTS_PATH)
    all_adults = summarise()["adults"]

    rows = []
    for scenario in ("current", "april_2027"):
        print(f"\n{SCENARIOS[scenario].name}")
        print(f"  Denominator: {all_adults / 1e6:.2f}m adults in Great Britain\n")
        for threshold in LIABILITY_THRESHOLDS:
            h = compute(units, liabilities, scenario, threshold, all_adults)
            label = "any bill" if threshold == 0 else f"£{int(threshold):,} or more"

            print(f"  Estates facing {label}")
            print(f"    exposed units                 {h.exposed_units / 1e6:6.2f}m")
            print(f"    adults in those units         {h.adults_in_exposed_units / 1e6:6.2f}m")
            print(f"    their adult children          {h.adult_children / 1e6:6.2f}m")
            print(f"    less children over 65         {-h.overlap_children_over_65 / 1e6:6.2f}m")
            print(f"    less step-family double count {-h.overlap_step_families / 1e6:6.2f}m")
            print(f"    affected adults               {h.affected_adults / 1e6:6.2f}m"
                  f"   = {h.share_of_adults * 100:.1f}% of adults")
            print(f"    latent tax                    £{h.latent_tax / 1e9:.0f}bn\n")

            rows.append({
                "scenario": scenario,
                "rules": SCENARIOS[scenario].name,
                "threshold_gbp": threshold,
                "exposed_units_m": round(h.exposed_units / 1e6, 3),
                "adults_in_exposed_units_m": round(h.adults_in_exposed_units / 1e6, 3),
                "adult_children_m": round(h.adult_children / 1e6, 3),
                "overlap_children_over_65_m": round(h.overlap_children_over_65 / 1e6, 3),
                "overlap_step_families_m": round(h.overlap_step_families / 1e6, 3),
                "affected_adults_m": round(h.affected_adults / 1e6, 3),
                "share_of_gb_adults_pct": round(h.share_of_adults * 100, 2),
                "multiple_of_hmrc_4pt72_pct": round(h.multiple_of_the_headline_figure, 1),
                "latent_tax_gbn": round(h.latent_tax / 1e9, 1),
                "input": "WAS round 8",
            })

    path = OUTPUT / "headline.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    print(f"Written to {path}")


if __name__ == "__main__":
    main()
