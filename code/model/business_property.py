# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Extract business and land holdings from WAS.

Business values come from BVal1R8 to BVal3R8 and BWorthR8 in the person file, with band midpoints for missing exact answers. WAS excludes these from wealth aggregates, so add them to estates. Land is already in net property wealth and must be removed there before adding it as relievable property.

WAS cannot identify qualifying passive shareholdings or farmhouses, so their relief is omitted. Some reported businesses and land will not qualify either. relief_distribution.py maps all reported holders onto HMRC's claim distribution; it does not test eligibility."""

from __future__ import annotations

import numpy as np
import pandas as pd

# Survey variables
PERSON_ID = "CASER8"
PERSON_AGE_BAND = "DVAge17R8"

# Business values from up to three jobs, plus active or sleeping partner shares.
PERSON_BUSINESS_EXACT = ["BVal1R8", "BVal2R8", "BVal3R8", "BWorthR8"]

# Use banded answers only where the corresponding exact value is missing.
BANDED_FALLBACK = {
    "BVal1R8": "BValB1R8",
    "BVal2R8": "BValB2R8",
    "BVal3R8": "BValB3R8",
    "BWorthR8": "BWorthBR8",
}
PERSON_BUSINESS_BANDED = list(BANDED_FALLBACK.values())

PERSON_BUSINESS_COLUMNS = PERSON_BUSINESS_EXACT + PERSON_BUSINESS_BANDED

# Land, and the debt secured on it. Both are household level.
#
# UK land only. Agricultural property relief was restricted to property in the
# United Kingdom from 6 April 2024, when the extension to the EEA, the Channel
# Islands and the Isle of Man was withdrawn, so by April 2027 no overseas land
# qualifies. It is not a rounding item: overseas holdings are the larger half of
# what the survey records. Overseas businesses are a different matter and stay,
# because business relief has never had a territorial restriction.
HH_LAND_VALUE = ["DVLUKValR8_sum"]
HH_LAND_DEBT = ["DVLUKDebtR8_sum"]
HH_LAND_COLUMNS = HH_LAND_VALUE + HH_LAND_DEBT

# Band midpoints; use a value above the lower limit for open top bands.
BAND_MIDPOINT_BVAL = {
    1: 50, 2: 5_000, 3: 30_000, 4: 75_000, 5: 175_000,
    6: 375_000, 7: 750_000, 8: 1_500_000, 9: 3_500_000, 10: 8_000_000,
}
BAND_MIDPOINT_BWORTH = {
    1: 50, 2: 5_000, 3: 17_500, 4: 37_500, 5: 75_000, 6: 150_000,
    7: 250_000, 8: 350_000, 9: 450_000, 10: 750_000, 11: 2_000_000,
}

# Include business owners aged 50-plus (band 11), excluding younger co-resident children's assets.
MIN_BUSINESS_AGE_BAND = 11


def _positive(series: pd.Series) -> pd.Series:
    """Replace negative survey codes (-8 and -9) with zero."""
    return series.where(series > 0, 0.0)


def business_by_household(person: pd.DataFrame) -> pd.Series:
    """Sum business values by household, using banded answers only for missing exact values."""
    missing = [c for c in PERSON_BUSINESS_COLUMNS if c not in person.columns]
    if missing:
        raise ValueError(f"WAS person file is missing business variables: {missing}")

    exact_total = sum(_positive(person[c]) for c in PERSON_BUSINESS_EXACT)

    banded_total = pd.Series(0.0, index=person.index)
    for exact, banded in BANDED_FALLBACK.items():
        midpoints = (BAND_MIDPOINT_BWORTH if exact == "BWorthR8"
                     else BAND_MIDPOINT_BVAL)
        banded_total = banded_total + (
            person[banded].map(midpoints).fillna(0.0)
            .where(_positive(person[exact]) == 0, 0.0))

    value = exact_total + banded_total
    value = value.where(person[PERSON_AGE_BAND] >= MIN_BUSINESS_AGE_BAND, 0.0)

    return value.groupby(person[PERSON_ID]).sum()


def land_by_household(household: pd.DataFrame) -> pd.Series:
    """Land net of secured debt. Subtract it from property wealth before adding it as relievable property."""
    missing = [c for c in HH_LAND_COLUMNS if c not in household.columns]
    if missing:
        raise ValueError(f"WAS household file is missing land variables: {missing}")

    value = sum(_positive(household[c]) for c in HH_LAND_VALUE)
    debt = sum(_positive(household[c]) for c in HH_LAND_DEBT)
    return (value - debt).clip(lower=0.0)


def winsorise(values: pd.Series, percentile: float) -> pd.Series:
    """Cap extreme business values. The 99th-percentile cap reduces the 65-plus total from £115bn to £99bn; it mainly matters when HMRC remapping is off."""
    holders = values[values > 0]
    if holders.empty:
        return values
    cap = float(np.percentile(holders, percentile))
    return values.clip(upper=cap)
