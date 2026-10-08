# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Estimate nominal and inflation-adjusted gains on main homes.

HPriceR8 gives purchase cost; DVHValueR8 gives current value. Exclude inherited homes (cost reset at death) and shared ownership (incompatible price/value bases). Missing purchase prices contribute no gain; second homes and land are outside this calculation.

Infer purchase year from regional house price growth. This assumes the home tracked its region, ignoring improvements and discounted purchases. Under-reported costs inflate both gains: scaling costs by 0.8 raised real gain 8.7% and nominal gain 9.5%. Coverage omissions reduce the measured gain; purchase-year inference is not inherently conservative."""

from __future__ import annotations

import csv

import numpy as np
import pandas as pd

# Survey variables.
HH_PURCHASE_PRICE = "HPriceR8"
HH_PURCHASE_BANDED = "HPriceBR8"
HH_HOW_ACQUIRED = "HHOwnR8"
HH_SHARED_OWNERSHIP = "HShareR8"
HOUSING_GAIN_COLUMNS = [HH_PURCHASE_PRICE, HH_PURCHASE_BANDED, HH_HOW_ACQUIRED,
                        HH_SHARED_OWNERSHIP]

# HShareR8 == 1 means the household owns only part of the home.
OWNS_ONLY_A_SHARE = 1.0

# Inherited homes (HHOwnR8 code 3) have a reset base cost; reported purchase price is unsuitable.
ACQUIRED_BY_INHERITANCE = 3.0

# Use the survey midpoint year here; uprate property later.
REFERENCE_YEAR = 2021

# Floor purchase age at 18. This shortens inflation adjustment and raises real gain (about 0.03% in total).
MINIMUM_AGE_AT_PURCHASE = 18


def load_index(path) -> dict[int, dict[int, float]]:
    """Average house price by WAS region and year."""
    out: dict[int, dict[int, float]] = {}
    with open(path) as handle:
        for row in csv.DictReader(handle):
            out.setdefault(int(row["was_region"]), {})[int(row["year"])] = (
                float(row["average_price"]))
    if not out:
        raise ValueError(f"No house price index rows in {path}. Run "
                         f"acquire/price_indices.py first.")
    return out


def load_cpi(path) -> dict[int, float]:
    with open(path) as handle:
        return {int(row["year"]): float(row["cpi_2015_100"])
                for row in csv.DictReader(handle)}


def implied_purchase_year(price_paid: np.ndarray, value_now: np.ndarray,
                          region: np.ndarray,
                          index: dict[int, dict[int, float]]) -> np.ndarray:
    """Infer the latest purchase year matching the price ratio, interpolating between annual index values."""
    out = np.full(len(price_paid), np.nan)

    for code, series in index.items():
        rows = region == code
        if not rows.any():
            continue

        years = np.array(sorted(series))
        levels = np.array([series[y] for y in years])
        reference = series[REFERENCE_YEAR]

        target = reference * (price_paid[rows] / value_now[rows])
        # Use a running maximum for interpolation; plateaus select their latest year. This misdates some purchases during price falls and reduces total real gain about 2.2% against the unsmoothed series.
        monotone = np.maximum.accumulate(levels)
        # Nobody in this survey bought after it ended.
        out[rows] = np.minimum(np.interp(target, monotone, years), REFERENCE_YEAR)

    return out


def housing_gain(household: pd.DataFrame, region: pd.Series, age: pd.Series,
                 index: dict[int, dict[int, float]],
                 cpi: dict[int, float]) -> pd.DataFrame:
    """Return purchase cost, inferred year, counterfactual values and any exclusion reason."""
    price = household[HH_PURCHASE_PRICE].where(
        household[HH_PURCHASE_PRICE] > 0, 0.0).to_numpy(dtype=float)
    value = household["value_now"].to_numpy(dtype=float)
    how = household[HH_HOW_ACQUIRED].to_numpy(dtype=float)
    shared = household[HH_SHARED_OWNERSHIP].to_numpy(dtype=float) == OWNS_ONLY_A_SHARE

    usable = (price > 0) & (value > 0)
    inherited = how == ACQUIRED_BY_INHERITANCE

    year = implied_purchase_year(price, np.maximum(value, 1.0),
                                 region.to_numpy(dtype=float), index)

    # Hold the purchase to no earlier than the owner's eighteenth birthday.
    earliest = REFERENCE_YEAR - (age.to_numpy(dtype=float) - MINIMUM_AGE_AT_PURCHASE)
    too_early = np.isfinite(year) & (year < earliest)
    year = np.where(too_early, earliest, year)

    cpi_years = np.array(sorted(cpi))
    cpi_levels = np.array([cpi[y] for y in cpi_years])
    cpi_at_purchase = np.interp(np.nan_to_num(year, nan=REFERENCE_YEAR),
                                cpi_years, cpi_levels)
    inflation = cpi[REFERENCE_YEAR] / cpi_at_purchase

    # Cap counterfactual values at current value, so losses do not create negative gains.
    nominal = np.minimum(price, value)
    real = np.minimum(price * inflation, value)

    # Excluded households keep current value and contribute no measured gain.
    counted = usable & ~inherited & ~shared & np.isfinite(year)
    nominal = np.where(counted, nominal, value)
    real = np.where(counted, real, value)

    reason = np.where(counted, "counted",
              np.where(~usable, "no purchase price reported",
              np.where(shared, "owns only a share, price and value differ in basis",
              np.where(inherited, "inherited or gifted, base cost uplifted",
                       "no index for region"))))

    return pd.DataFrame({
        "purchase_price": np.where(counted, price, np.nan),
        "implied_purchase_year": np.where(counted, year, np.nan),
        "residence_value_if_nominal": nominal,
        "residence_value_if_real": real,
        "housing_gain_counted": counted,
        "housing_gain_reason": reason,
        "purchase_year_floored": too_early & counted,
    }, index=household.index)
