# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Hand-checkable housing-gain tests. analysis/housing_gain.py checks that only housing changes across full model runs."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model.housing_gain import (ACQUIRED_BY_INHERITANCE, MINIMUM_AGE_AT_PURCHASE,
                                OWNS_ONLY_A_SHARE, REFERENCE_YEAR,
                                housing_gain, implied_purchase_year)

# Synthetic prices: housing doubles each decade; consumer prices rise more slowly.
YEARS = list(range(1960, REFERENCE_YEAR + 1))
INDEX = {1: {y: 1000.0 * 2 ** ((y - 1960) / 10.0) for y in YEARS}}
CPI = {y: 10.0 * 1.03 ** (y - 1960) for y in YEARS}


def _household(price, value, region=1, how=1.0, shared=2.0):
    return pd.DataFrame([{
        "HPriceR8": price, "HPriceBR8": -9.0, "HHOwnR8": how,
        "HShareR8": shared, "value_now": value,
    }])


def _gain(price, value, age=75.0, **kw):
    frame = _household(price, value, **kw)
    return housing_gain(frame, region=pd.Series([1.0]), age=pd.Series([age]),
                        index=INDEX, cpi=CPI).iloc[0]


# Recovering the year
def test_a_known_purchase_year_comes_back():
    """Fourfold growth at this rate implies a purchase twenty years ago."""
    year = implied_purchase_year(np.array([25_000.0]), np.array([100_000.0]),
                                 np.array([1.0]), INDEX)
    assert year[0] == pytest.approx(REFERENCE_YEAR - 20, abs=0.05)


def test_paying_todays_price_dates_the_purchase_to_today():
    year = implied_purchase_year(np.array([100_000.0]), np.array([100_000.0]),
                                 np.array([1.0]), INDEX)
    assert year[0] == pytest.approx(REFERENCE_YEAR, abs=0.05)


def test_the_year_can_never_land_after_the_survey():
    """A loss must not imply a future purchase date."""
    year = implied_purchase_year(np.array([500_000.0]), np.array([100_000.0]),
                                 np.array([1.0]), INDEX)
    assert year[0] <= REFERENCE_YEAR


def test_a_falling_stretch_dates_to_the_last_year_at_that_level():
    """A price plateau selects its latest year after applying the running maximum."""
    falling = {1: {2000: 100.0, 2005: 200.0, 2010: 150.0, 2015: 190.0,
                   REFERENCE_YEAR: 400.0}}
    year = implied_purchase_year(np.array([50.0]), np.array([100.0]),
                                 np.array([1.0]), falling)
    assert year[0] == pytest.approx(2015.0, abs=0.01)


# Which way the measurement errors push
def test_an_under_reported_price_raises_both_gains():
    """Lower purchase costs raise both gains here; earlier dating only partly offsets the real gain."""
    honest = _gain(25_000.0, 100_000.0)
    understated = _gain(20_000.0, 100_000.0)

    nominal_honest = 100_000.0 - honest["residence_value_if_nominal"]
    nominal_under = 100_000.0 - understated["residence_value_if_nominal"]
    assert nominal_under > nominal_honest

    real_honest = 100_000.0 - honest["residence_value_if_real"]
    real_under = 100_000.0 - understated["residence_value_if_real"]
    assert real_under > real_honest


def test_the_real_gain_is_never_larger_than_the_nominal_one():
    row = _gain(25_000.0, 100_000.0)
    assert row["residence_value_if_real"] >= row["residence_value_if_nominal"]


# Who is counted
def test_a_shared_owner_is_left_out():
    """Purchase cost can cover a share while current value covers the whole home."""
    row = _gain(50_000.0, 100_000.0, shared=OWNS_ONLY_A_SHARE)
    assert not row["housing_gain_counted"]
    assert row["residence_value_if_nominal"] == 100_000.0
    assert row["residence_value_if_real"] == 100_000.0


def test_an_inherited_home_is_left_out():
    """Reported purchase cost is unsuitable after the base-cost uplift at inheritance."""
    row = _gain(25_000.0, 100_000.0, how=ACQUIRED_BY_INHERITANCE)
    assert not row["housing_gain_counted"]
    assert row["residence_value_if_nominal"] == 100_000.0


def test_no_reported_price_means_no_gain():
    row = _gain(-9.0, 100_000.0)
    assert not row["housing_gain_counted"]
    assert row["residence_value_if_nominal"] == 100_000.0
    assert row["residence_value_if_real"] == 100_000.0


def test_an_uncounted_household_keeps_its_home_whole():
    """Exclusion must leave the home's value unchanged."""
    for kwargs in ({"shared": OWNS_ONLY_A_SHARE},
                   {"how": ACQUIRED_BY_INHERITANCE},
                   {"price": -8.0}):
        price = kwargs.pop("price", 25_000.0)
        row = _gain(price, 100_000.0, **kwargs)
        assert row["residence_value_if_nominal"] == 100_000.0
        assert row["residence_value_if_real"] == 100_000.0


# Bounds
def test_a_loss_is_not_turned_into_a_gain():
    """A loss contributes no gain."""
    row = _gain(150_000.0, 100_000.0)
    assert row["residence_value_if_nominal"] <= 100_000.0
    assert row["residence_value_if_real"] <= 100_000.0
    assert 100_000.0 - row["residence_value_if_nominal"] >= 0


def test_the_age_floor_dates_the_purchase_later_and_raises_the_gain():
    """The age floor shortens inflation adjustment and raises real gain."""
    young = _gain(1_000.0, 100_000.0, age=70.0)
    old = _gain(1_000.0, 100_000.0, age=95.0)

    assert young["implied_purchase_year"] > old["implied_purchase_year"]
    assert young["implied_purchase_year"] == pytest.approx(
        REFERENCE_YEAR - (70.0 - MINIMUM_AGE_AT_PURCHASE), abs=0.05)
    assert young["purchase_year_floored"]
    assert young["residence_value_if_real"] < old["residence_value_if_real"]


def test_every_counted_household_gets_a_year():
    row = _gain(25_000.0, 100_000.0)
    assert row["housing_gain_counted"]
    assert np.isfinite(row["implied_purchase_year"])
    assert row["purchase_price"] == 25_000.0


if __name__ == "__main__":
    # Run pytest when invoked directly by run_all.py.
    import pytest
    raise SystemExit(pytest.main(["-q", "--import-mode=importlib", __file__]))
