# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Estimate households with own-estate or parental IHT exposure, counting each once.

Derive p_child(age) from the adults model's children of liable units divided by ONS population. Use liability on the descendants branch and apply the step-family correction. Combine with own exposure as 1 - (1 - p_own) * product(1 - p_child).

Assume adults' parental exposure is independent within a household; clustering would lower the count. Parental exposure depends only on age, omitting correlations with own wealth. Exclude likely co-resident children from the product. Grandchildren form a separate wider measure."""

from __future__ import annotations

import numpy as np
import pandas as pd

from lib.config import MODEL_YEAR
from model.children import build_cohort_table, children_for_unit

# WAS DVAge17R8 age bands.
AGE_BANDS = {
    1: (0, 4), 2: (5, 9), 3: (10, 14), 4: (15, 19), 5: (20, 24),
    6: (25, 29), 7: (30, 34), 8: (35, 39), 9: (40, 44), 10: (45, 49),
    11: (50, 54), 12: (55, 59), 13: (60, 64), 14: (65, 69), 15: (70, 74),
    16: (75, 79), 17: (80, 100),
}

# Treat an adult a generation younger than the oldest as a co-resident child, already covered by p_own.
A_GENERATION = 25

# The survey and the adults model both treat 18 as adulthood.
ADULT_AGE_FOR_STAKE = 18


def children_by_age(units: pd.DataFrame, exposure: np.ndarray,
                    max_age: int) -> tuple[np.ndarray, np.ndarray]:
    """Return age-indexed counts of living adult children of (liable units, all units)."""
    table = build_cohort_table()
    liable = np.zeros(max_age)
    everyone = np.zeros(max_age)

    for row, p in zip(units.itertuples(), exposure):
        record = children_for_unit(int(row.female_birth_cohort), table)
        for age, count in record.child_age_distribution.items():
            if 0 <= age < max_age:
                everyone[age] += row.weight * count
                liable[age] += row.weight * count * p

    return liable, everyone


def stake_probability_by_age(children: np.ndarray, population: np.ndarray,
                             double_counted: float) -> np.ndarray:
    """Probability of parental exposure by age. Apply the adults model's step-family double-count share uniformly across ages."""
    scaled = children * (1.0 - double_counted)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(population > 0, scaled / np.maximum(population, 1.0), 0.0)
    return np.clip(out, 0.0, 1.0)


def band_probability(by_age: np.ndarray, population: np.ndarray) -> dict[int, float]:
    """Average age-specific probabilities within WAS bands, weighted by population."""
    out = {}
    for band, (low, high) in AGE_BANDS.items():
        top = min(high + 1, len(by_age))
        if low >= len(by_age):
            out[band] = 0.0
            continue
        weight = population[low:top]
        values = by_age[low:top]
        out[band] = float((values * weight).sum() / weight.sum()) if weight.sum() else 0.0
    return out


def household_stake(bands: np.ndarray, p_own: float,
                    by_band: dict[int, float]) -> float:
    """Combine own and parental exposure probabilities for one household."""
    oldest = max((AGE_BANDS[b][0] for b in bands), default=0)

    survives = 1.0 - p_own
    for band in bands:
        # Omit likely co-resident children already represented by p_own.
        if oldest - AGE_BANDS[band][0] >= A_GENERATION:
            continue
        survives *= 1.0 - by_band.get(band, 0.0)

    return 1.0 - survives


# Grandchildren
# Grandchildren are a separate wider measure. Extrapolate younger parents from the youngest completed fertility cohort, counting only children already adult.
# Convert expected ancestor counts to probabilities with 1 - (1 - r) ** L, where L is living ancestor units and r is their liable share.


def adult_children_schedule(cohort_table, model_year: int = MODEL_YEAR,
                            max_parent_age: int = 110) -> dict[int, dict[int, float]]:
    """Living adult children by current parental age. Extrapolate below the youngest cohort; model_year must match the cohort table."""
    youngest = max(cohort_table)
    schedule: dict[int, dict[int, float]] = {}

    for age in range(15, max_parent_age + 1):
        cohort = model_year - age
        if cohort in cohort_table:
            schedule[age] = dict(cohort_table[cohort].child_age_distribution)
            continue

        # Shift the youngest cohort's child-age schedule for younger parents.
        shift = (model_year - youngest) - age
        base = cohort_table[youngest].child_age_distribution
        schedule[age] = {a - shift: n for a, n in base.items()
                         if a - shift >= ADULT_AGE_FOR_STAKE}

    return schedule


def grandchildren_by_age(units: pd.DataFrame, exposure: np.ndarray,
                         max_age: int, model_year: int = MODEL_YEAR
                         ) -> tuple[np.ndarray, np.ndarray]:
    """Compose parent-child schedules twice to count living adult grandchildren by age."""
    table = build_cohort_table()
    schedule = adult_children_schedule(table, model_year)

    liable = np.zeros(max_age)
    everyone = np.zeros(max_age)

    for row, p in zip(units.itertuples(), exposure):
        record = children_for_unit(int(row.female_birth_cohort), table)
        for child_age, count in record.child_age_distribution.items():
            own = schedule.get(int(round(child_age)))
            if not own:
                continue
            for age, n in own.items():
                if 0 <= age < max_age:
                    everyone[age] += row.weight * count * n
                    liable[age] += row.weight * count * n * p

    return liable, everyone


def lineage_probability(liable: np.ndarray, everyone: np.ndarray,
                        population: np.ndarray) -> np.ndarray:
    """Estimate the chance of at least one liable ancestor unit as 1 - (1 - r) ** L."""
    with np.errstate(divide="ignore", invalid="ignore"):
        share = np.where(everyone > 0, liable / np.maximum(everyone, 1e-9), 0.0)
        count = np.where(population > 0, everyone / np.maximum(population, 1.0), 0.0)

    share = np.clip(share, 0.0, 1.0)
    return np.clip(1.0 - np.power(1.0 - share, count), 0.0, 1.0)


def household_stake_wide(bands, p_own: float, by_band: dict[int, float],
                         grand_by_band: dict[int, float]) -> float:
    """Combine own, parental and grandparental exposure, counting each household once."""
    oldest = max((AGE_BANDS[b][0] for b in bands), default=0)

    survives = 1.0 - p_own
    for band in bands:
        if oldest - AGE_BANDS[band][0] >= A_GENERATION:
            continue
        survives *= 1.0 - by_band.get(band, 0.0)
        survives *= 1.0 - grand_by_band.get(band, 0.0)

    return 1.0 - survives
