# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Estimate the national share of households with own-estate or parental exposure, using model/household_stake.py. No local estimates: the data does not locate adult children."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyreadstat

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import HEADLINE_RULES, OUTPUT
from model import build_units as bu
from model.build_units import UNITS_PATH
from lib.config import MODEL_YEAR
from model.household_stake import (AGE_BANDS, band_probability, children_by_age,
                                   grandchildren_by_age, household_stake,
                                   household_stake_wide, lineage_probability,
                                   stake_probability_by_age)
from model.population import load_population_by_age
from model.simulate import RESULTS_PATH

# The survey calls an adult 20 or over, which is what build_units uses too.
FIRST_ADULT_BAND = 5

SCENARIO = "april_2027"


def main() -> None:
    units = pd.read_parquet(UNITS_PATH)
    liabilities = pd.read_parquet(RESULTS_PATH)
    liabilities = liabilities[liabilities["scenario"] == SCENARIO].set_index("unit_id")
    exposure = liabilities.loc[units["unit_id"], "p_over_0"].to_numpy()

    # Use only the descendants branch, matching the adults model.
    with_descendants = (
        liabilities.loc[units["unit_id"], "tax_with_descendants"].to_numpy() > 0
    ).astype(float)

    population = load_population_by_age()

    print(f"\nHouseholds with a stake in inheritance tax, {HEADLINE_RULES.name}")

    # the child's side, inverted out of the first model
    liable, everyone = children_by_age(units, with_descendants, len(population))

    # Reuse the headline model's step-family double-count correction.
    headline = pd.read_csv(OUTPUT / "headline.csv")
    row = headline[(headline["scenario"] == SCENARIO)
                   & (headline["threshold_gbp"] == 0.0)].iloc[0]
    # The household formula already handles own/parental overlap.
    double_counted = float(row["overlap_step_families_m"] / row["adult_children_m"])

    by_age = stake_probability_by_age(liable, population, double_counted)
    by_band = band_probability(by_age, population)

    # Report adult grandchildren as a separate wider measure.
    grand_liable, grand_all = grandchildren_by_age(
        units, with_descendants, len(population), MODEL_YEAR)
    grand_by_age = lineage_probability(grand_liable, grand_all, population)
    grand_by_band = band_probability(grand_by_age, population)

    print(f"  Adult children of liable 65+ households: "
          f"{liable[18:].sum() / 1e6:.3f}m before corrections "
          f"(the adults model says {row['adult_children_m']:.3f}m), "
          f"{liable[18:].sum() * (1 - double_counted) / 1e6:.3f}m after "
          f"the step-family correction of {double_counted * 100:.1f}%")
    peak = int(np.argmax(by_age))
    print(f"  Chance an adult is the child of a liable household peaks at "
          f"{by_age[peak] * 100:.1f}% at age {peak}")

    # Check implied shares with a living 65-plus parent.
    share_any = everyone[18:].sum() / population[18:].sum()
    print(f"  Implied share of adults with a living 65+ parent: "
          f"{share_any * 100:.0f}% overall, peaking at "
          f"{(everyone / np.maximum(population, 1))[18:].max() * 100:.0f}%")

    # every household in the survey
    folder = bu.was_dir()
    household, _ = pyreadstat.read_sav(str(folder / bu.HOUSEHOLD_FILE),
                                       usecols=["CASER8", "R8xshhwgt"])
    person, _ = pyreadstat.read_sav(str(folder / bu.PERSON_FILE),
                                    usecols=["CASER8", "DVAge17R8"])

    adults = person[person["DVAge17R8"] >= FIRST_ADULT_BAND]
    bands_by_case = adults.groupby("CASER8")["DVAge17R8"].apply(
        lambda s: [int(x) for x in s])

    modelled = pd.Series(exposure, index=units["case_id"].to_numpy())

    household = household.set_index("CASER8")
    household["bands"] = bands_by_case
    household = household[household["bands"].notna()]
    household["oldest"] = household["bands"].apply(max)
    has_65 = household["oldest"] >= 14

    # Take each 65-plus household's own post-stratified weight from the model,
    # rather than scaling every survey weight by one factor. Post-stratification
    # moves weight between households, and it moves it off the liable ones: a
    # flat rescale left this file reporting 2.148m exposed households where the
    # headline reported 2.115m. Younger households keep their survey weight,
    # because the model never touches them.
    modelled_weight = pd.Series(units["weight"].to_numpy(),
                                index=units["case_id"].to_numpy())
    own = modelled_weight.reindex(household.index)
    household["weight"] = np.where(has_65 & own.notna(),
                                   own.fillna(0.0), household["R8xshhwgt"])
    factor = (household.loc[has_65, "weight"].sum()
              / household.loc[has_65, "R8xshhwgt"].sum())

    # Join each 65-plus household to its own modelled exposure.
    matched = modelled.reindex(household.index)
    household["p_own"] = matched.fillna(0.0)

    # Require complete coverage: an unmatched 65-plus household must not default to zero exposure.
    covered = has_65 & matched.notna()
    coverage = (household.loc[covered, "weight"].sum()
                / household.loc[has_65, "weight"].sum())
    if coverage < 0.995:
        raise SystemExit(
            f"Only {coverage * 100:.1f}% of 65+ households are in the units "
            f"file. The remainder would be counted as facing no bill at all. "
            f"Check exclude_multi_generational and the unit build.")
    print(f"  Post-stratification factor on 65+ households: {factor:.3f}")
    print(f"  Coverage of 65+ households by the units file: {coverage * 100:.1f}%")

    weight = household["weight"].to_numpy(dtype=float)
    stake = np.array([household_stake(b, p, by_band) for b, p
                      in zip(household["bands"], household["p_own"])])
    wide = np.array([household_stake_wide(b, p, by_band, grand_by_band) for b, p
                     in zip(household["bands"], household["p_own"])])

    total = weight.sum()
    own_only = (weight * household["p_own"].to_numpy()).sum()
    with_stake = (weight * stake).sum()

    print(f"\n  Households in the survey's population: {total / 1e6:.2f}m")
    print(f"    with a 65+ member who would face a bill: "
          f"{own_only / 1e6:.2f}m ({own_only / total * 100:.1f}%)")
    print(f"    with a stake either way:                 "
          f"{with_stake / 1e6:.2f}m ({with_stake / total * 100:.1f}%)")
    print(f"    so the children add:                     "
          f"{(with_stake - own_only) / 1e6:.2f}m")

    with_grandchildren = (weight * wide).sum()
    print(f"\n  Widening it to adult grandchildren as well:")
    print(f"    adult grandchildren of liable households: "
          f"{grand_liable[18:].sum() / 1e6:.2f}m")
    print(f"    chance an adult is one peaks at "
          f"{grand_by_age.max() * 100:.1f}% at age {int(np.argmax(grand_by_age))}")
    print(f"    households with a stake:                  "
          f"{with_grandchildren / 1e6:.2f}m "
          f"({with_grandchildren / total * 100:.1f}%)")
    print(f"    grandchildren add:                        "
          f"{(with_grandchildren - with_stake) / 1e6:.2f}m")

    # Test clustering of parental wealth within households; independence can overstate the count.
    print(f"\n  Sensitivity to couples pairing up rather than pairing at random:")
    for clustering, label in ((0.0, "none (as published)"), (0.25, "moderate"),
                              (0.5, "strong")):
        adjusted = {b: v for b, v in by_band.items()}
        clustered = np.array([
            household_stake_clustered(b, p, adjusted, clustering)
            for b, p in zip(household["bands"], household["p_own"])])
        value = (weight * clustered).sum()
        print(f"    {label:<22} {value / 1e6:.2f}m  ({value / total * 100:.1f}%)")

    out = {
        "scenario": SCENARIO,
        "households_total": float(total),
        "households_own_bill": float(own_only),
        "households_with_stake": float(with_stake),
        "share_own_bill": float(own_only / total),
        "share_with_stake": float(with_stake / total),
        "households_with_stake_wide": float(with_grandchildren),
        "share_with_stake_wide": float(with_grandchildren / total),
        "adult_grandchildren_m": float(grand_liable[18:].sum()),
        "peak_grandchild_probability": float(grand_by_age.max()),
        "children_of_liable_m": float(liable[18:].sum() * (1 - double_counted)),
        "double_counted_share": double_counted,
        "peak_child_probability": float(by_age.max()),
        "peak_child_age": peak,
        "p_child_by_band": {str(k): v for k, v in by_band.items()},
    }
    path = OUTPUT / "households.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"\nWritten to {path}")


def household_stake_clustered(bands, p_own, by_band, clustering: float) -> float:
    """Correlate adults' parental exposure: 0 means independence; 1 adds no probability after the first adult."""
    oldest = max((AGE_BANDS[b][0] for b in bands), default=0)
    eligible = [b for b in bands
                if oldest - AGE_BANDS[b][0] < 25]

    survives = 1.0 - p_own
    for index, band in enumerate(sorted(eligible, key=lambda b: -by_band.get(b, 0.0))):
        p = by_band.get(band, 0.0)
        if index > 0:
            p *= (1.0 - clustering)
        survives *= 1.0 - p
    return 1.0 - survives


if __name__ == "__main__":
    main()
