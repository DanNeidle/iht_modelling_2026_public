# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Hand-checkable relief tests, including estate totals and preservation of the main residence when reclassifying farmland."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import RELIEF_CLAIMS_2023_24, ModelAssumptions
from model.business_property import (BAND_MIDPOINT_BVAL, BAND_MIDPOINT_BWORTH,
                                     MIN_BUSINESS_AGE_BAND,
                                     business_by_household, land_by_household,
                                     winsorise)
from model.build_units import apply_relief_property
from model.relief_distribution import remap, target_curve

BUSINESS = RELIEF_CLAIMS_2023_24["business"]["bands"]
AGRICULTURAL = RELIEF_CLAIMS_2023_24["agricultural"]["bands"]


# The target curve
@pytest.mark.parametrize("bands", [BUSINESS, AGRICULTURAL])
def test_curve_is_monotone_and_never_negative(bands):
    """Larger claims must have smaller ranks."""
    value_at, total = target_curve(bands)
    values = value_at(np.linspace(0, total - 1e-9, 20_000))
    assert np.all(np.diff(values) <= 1e-6)
    assert np.all(values >= 0)


@pytest.mark.parametrize("bands", [BUSINESS, AGRICULTURAL])
def test_curve_hits_every_published_band_limit(bands):
    """The curve must pass through each published band boundary."""
    value_at, _ = target_curve(bands)
    above = np.cumsum([n for _, n, _ in bands][::-1])[::-1]
    for index, (lower, _, _) in enumerate(bands):
        if lower == 0:
            continue
        assert value_at(np.array([float(above[index])]))[0] == pytest.approx(
            float(lower), rel=1e-9)


@pytest.mark.parametrize("bands", [BUSINESS, AGRICULTURAL])
def test_curve_length_is_the_published_number_of_claims(bands):
    _, total = target_curve(bands)
    assert total == pytest.approx(sum(n for _, n, _ in bands))


@pytest.mark.parametrize("bands", [BUSINESS, AGRICULTURAL])
def test_open_bands_reproduce_their_published_means(bands):
    """Top and bottom bands are fitted to their published means."""
    value_at, total = target_curve(bands)
    above = np.cumsum([n for _, n, _ in bands][::-1])[::-1]

    top_lower, top_count, top_value = bands[-1]
    top = value_at(np.linspace(0, top_count - 1e-9, 5_000))
    assert top.mean() == pytest.approx(top_value / top_count, rel=1e-3)

    _, bottom_count, bottom_value = bands[0]
    bottom = value_at(np.linspace(above[1], total - 1e-9, 20_000))
    assert bottom.mean() == pytest.approx(bottom_value / bottom_count, rel=1e-2)


def test_curve_survives_a_band_with_no_claims():
    """A zero-count band must not open a gap or divide by zero."""
    value_at, total = target_curve([(0, 100, 5e6), (250_000, 0, 0.0),
                                    (500_000, 10, 8e6)])
    values = value_at(np.linspace(0, total - 1e-9, 5_000))
    assert np.all(np.isfinite(values))
    assert np.all(np.diff(values) <= 1e-6)


# The re-map
def test_remap_preserves_rank():
    values = pd.Series([10.0, 500_000.0, 200.0, 0.0, 90_000.0])
    weights = pd.Series([1_000.0] * 5)
    out = remap(values, weights, BUSINESS)
    order = values.sort_values(ascending=False).index
    assert list(out[order]) == sorted(out[order], reverse=True)
    assert out[3] == 0.0          # a non-holder stays a non-holder


def test_remap_assigns_every_holder_a_positive_value():
    """Every surveyed holder must retain a positive value."""
    values = pd.Series(np.linspace(1.0, 5_000_000.0, 400))
    weights = pd.Series(np.full(400, 1_000.0))
    out = remap(values, weights, AGRICULTURAL)
    assert (out > 0).all()


def test_remap_aggregate_matches_the_published_distribution():
    """Scale the published total by the ratio of survey holders to claims."""
    values = pd.Series(np.linspace(1.0, 2_000_000.0, 500))
    weights = pd.Series(np.full(500, 700.0))
    out = remap(values, weights, BUSINESS)

    _, total = target_curve(BUSINESS)
    stretch = weights.sum() / total
    published = sum(v for _, _, v in BUSINESS)
    assert float((out * weights).sum()) == pytest.approx(published * stretch,
                                                         rel=0.02)


def test_tied_reports_get_one_value():
    """Equal reported values must map to equal values, regardless of sort order."""
    values = pd.Series([300_000.0] * 16 + [50_000.0] * 4)
    weights = pd.Series([1_000.0] * 20)
    out = remap(values, weights, AGRICULTURAL)
    assert out[:16].nunique() == 1


def test_tie_averaging_leaves_the_aggregate_alone():
    values = pd.Series([300_000.0] * 16 + [50_000.0] * 4)
    weights = pd.Series(np.r_[np.full(16, 1_000.0), np.full(4, 2_500.0)])
    tied = float((remap(values, weights, AGRICULTURAL) * weights).sum())

    # Nudge each tied value apart so no averaging happens, and compare.
    spread = values + pd.Series(np.arange(20) * 1e-6)
    untied = float((remap(spread, weights, AGRICULTURAL) * weights).sum())
    assert tied == pytest.approx(untied, rel=1e-9)


def test_remap_handles_empty_and_degenerate_input():
    empty = pd.Series([0.0, 0.0])
    assert remap(empty, pd.Series([1.0, 1.0]), BUSINESS).sum() == 0.0

    one = pd.Series([123.0])
    assert remap(one, pd.Series([500.0]), BUSINESS).iloc[0] > 0

    assert remap(pd.Series([5.0]), pd.Series([0.0]), BUSINESS).iloc[0] == 0.0


def test_remap_refuses_misaligned_weights():
    """Reject mismatched weights before they produce NaNs."""
    values = pd.Series([100.0, 200.0], index=[0, 1])
    weights = pd.Series([1_000.0], index=[0])
    with pytest.raises(ValueError, match="not aligned"):
        remap(values, weights, BUSINESS)


# Reading the survey
def _person(**overrides) -> pd.DataFrame:
    row = {"CASER8": 1.0, "DVAge17R8": 15.0,
           "BVal1R8": -9.0, "BVal2R8": -9.0, "BVal3R8": -9.0, "BWorthR8": -9.0,
           "BValB1R8": -9.0, "BValB2R8": -9.0, "BValB3R8": -9.0,
           "BWorthBR8": -9.0}
    row.update(overrides)
    return pd.DataFrame([row])


def test_missing_codes_are_not_money():
    """Survey codes -8 and -9 must contribute no business value."""
    assert business_by_household(_person()).iloc[0] == 0.0


def test_an_exact_answer_beats_its_own_band():
    """A respondent who gave both must not be counted twice."""
    both = business_by_household(_person(BVal1R8=40_000.0, BValB1R8=5.0)).iloc[0]
    assert both == 40_000.0


def test_a_band_is_used_when_the_exact_answer_is_missing():
    only_banded = business_by_household(_person(BValB1R8=5.0)).iloc[0]
    assert only_banded == BAND_MIDPOINT_BVAL[5]


def test_every_banded_component_has_a_fallback():
    """Read banded fallbacks for all four business questions."""
    for exact, banded, midpoints in [
            ("BVal1R8", "BValB1R8", BAND_MIDPOINT_BVAL),
            ("BVal2R8", "BValB2R8", BAND_MIDPOINT_BVAL),
            ("BVal3R8", "BValB3R8", BAND_MIDPOINT_BVAL),
            ("BWorthR8", "BWorthBR8", BAND_MIDPOINT_BWORTH)]:
        got = business_by_household(_person(**{banded: 4.0})).iloc[0]
        assert got == midpoints[4], f"{banded} was not read"


def test_a_young_relatives_business_is_not_in_the_estate():
    """Exclude a younger adult child's business from the parent's estate."""
    young = business_by_household(
        _person(DVAge17R8=float(MIN_BUSINESS_AGE_BAND - 1), BVal1R8=1e6))
    assert young.iloc[0] == 0.0

    old = business_by_household(
        _person(DVAge17R8=float(MIN_BUSINESS_AGE_BAND), BVal1R8=1e6))
    assert old.iloc[0] == 1e6


def test_land_is_net_of_its_own_debt_and_never_negative():
    household = pd.DataFrame([{"DVLUKValR8_sum": 300_000.0,
                               "DVLOSValR8_sum": -9.0,
                               "DVLUKDebtR8_sum": 100_000.0,
                               "DVLOSDebtR8_sum": -9.0},
                              {"DVLUKValR8_sum": 50_000.0,
                               "DVLOSValR8_sum": 0.0,
                               "DVLUKDebtR8_sum": 90_000.0,
                               "DVLOSDebtR8_sum": 0.0}])
    land = land_by_household(household)
    assert land.iloc[0] == 200_000.0
    assert land.iloc[1] == 0.0


def test_winsorise_caps_the_top_without_touching_the_rest():
    """Compute the percentile among holders; use enough observations for the cap to bind."""
    holders = list(np.linspace(1e5, 2e6, 299)) + [3.4e8]
    values = pd.Series([0.0] * 200 + holders)
    capped = winsorise(values, 99.0)

    expected = float(np.percentile(holders, 99.0))
    assert capped.max() == pytest.approx(expected)
    assert capped.max() < 1e7
    # Nothing below the cap moves, and the non-holders stay at zero.
    below = values < expected
    assert capped[below].equals(values[below])


# The invariants that matter most
def _units(**overrides) -> pd.DataFrame:
    frame = pd.DataFrame({
        "weight": [1_000.0, 1_000.0, 1_000.0],
        "property_wealth": [400_000.0, 1_200_000.0, 900_000.0],
        "residence_value": [380_000.0, 500_000.0, 900_000.0],
        "business_raw": [0.0, 250_000.0, 0.0],
        "agricultural_raw": [120_000.0, 400_000.0, 80_000.0],
    })
    for key, value in overrides.items():
        frame[key] = value
    return frame


def test_farmland_adds_to_the_estate_rather_than_moving_within_it():
    """Land is extra wealth, so property wealth is left exactly where it was.

    The survey's net property total is the home plus other property less the
    mortgage on them. Land is not in it: for 407 of the 520 land-holding
    households that identity holds to the pound with land contributing nothing,
    and for none of them does it hold once land is added. So the model adds
    land as relievable property and takes nothing out to pay for it."""
    before = _units()
    after = apply_relief_property(before.copy(), ModelAssumptions())
    assert np.allclose(after["property_wealth"], before["property_wealth"])
    assert (after["agricultural_property"] >= 0).all()


def test_reclassifying_farmland_never_touches_the_home():
    """Keep the main residence and its nil-rate band when reclassifying farmland."""
    before = _units()
    after = apply_relief_property(before.copy(), ModelAssumptions())
    assert np.allclose(after["residence_value"], before["residence_value"])
    assert (after["property_wealth"] >= after["residence_value"] - 1e-6).all()
    # And the residence band cannot be eaten by farmland any more, because
    # nothing is taken out of property wealth to make room for it.
    assert np.allclose(after["property_wealth"], before["property_wealth"])


def test_a_household_whose_property_is_all_house_keeps_its_land():
    """Owning no property beyond the home says nothing about owning land.

    This used to be capped away on the view that land had to come out of the
    property total. It does not, so a household living in its whole £900,000 of
    property and also reporting farmland keeps both."""
    before = _units()
    after = apply_relief_property(before.copy(), ModelAssumptions())
    assert after["property_wealth"].iloc[2] == 900_000.0
    if before["agricultural_raw"].iloc[2] > 0:
        assert after["agricultural_property"].iloc[2] > 0


def test_business_property_adds_to_the_estate():
    """Business assets add wealth because WAS excludes them from its aggregates."""
    after = apply_relief_property(_units(), ModelAssumptions())
    assert after["business_property"].iloc[1] > 0
    assert np.allclose(after["business_agricultural"],
                       after["business_property"] + after["agricultural_property"])


def test_the_feature_can_be_switched_off_cleanly():
    before = _units()
    after = apply_relief_property(
        before.copy(), ModelAssumptions(include_business_agricultural=False))
    assert (after["business_agricultural"] == 0).all()
    assert np.allclose(after["property_wealth"], before["property_wealth"])
    assert np.allclose(after["residence_value"], before["residence_value"])


if __name__ == "__main__":
    # Run pytest when invoked directly by run_all.py.
    import pytest
    raise SystemExit(pytest.main(["-q", "--import-mode=importlib", __file__]))
