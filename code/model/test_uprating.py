# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Check the machinery that carries survey money to the model date.

Survey fieldwork ran to March 2022 and the rules being modelled start in April
2027, so every pound in the survey has to be restated before it is tested
against a threshold. Getting a factor wrong moves the headline by more than a
point, and none of it shows up as an exception, so these are the checks that
have to catch it."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acquire.price_indices import carry_forward, rebase
from lib.config import ASSUMPTIONS, SURVEY_WINDOW, VALIDATION_WINDOW
from model.build_units import (UPRATING_SOURCE, blended_estate_uprating,
                               counterfactual_home_value, resolve_uprating,
                               uprating_between_validation_year_and_model_date)


def flat_window(value: float, start_year: int) -> dict[str, float]:
    """Twenty four months at one level, matching the survey window."""
    return {f"{start_year + (month - 1) // 12}-{(month - 1) % 12 + 1:02d}": value
            for month in range(1, 25)}


def rising_window(first: float, step: float) -> dict[str, float]:
    """The twenty four fieldwork months, climbing by a fixed step each month."""
    months = [f"{2020 + (3 + n) // 12}-{(3 + n) % 12 + 1:02d}" for n in range(24)]
    return {month: first + step * n for n, month in enumerate(months)}


def test_the_base_is_the_mean_across_the_whole_fieldwork_window():
    """Not a midpoint month. The answers were collected across two years.

    The series climbs, so a window mean and any single month give different
    answers, which is the only way this test can tell them apart."""
    monthly = rising_window(100.0, 10.0)
    assert sorted(monthly)[0] == SURVEY_WINDOW[0]
    assert sorted(monthly)[-1] == SURVEY_WINDOW[1]
    monthly["2025-04"] = 500.0
    monthly["2026-04"] = 500.0

    out = carry_forward(monthly, "test series")
    # 100 to 330 in steps of ten averages 215, where the middle months are 210
    # and 220 and the last month of fieldwork is 330.
    assert out["base"] == pytest.approx(215.0)
    assert out["base"] != pytest.approx(monthly["2021-03"])
    assert out["base"] != pytest.approx(monthly["2021-04"])


def test_the_projection_compounds_the_trailing_year_over_the_gap():
    """Past the last real observation, extend at the preceding twelve months' growth."""
    monthly = flat_window(100.0, 2020)
    monthly["2025-04"] = 100.0
    monthly["2026-04"] = 110.0

    out = carry_forward(monthly, "test series")
    assert out["latest_month"] == "2026-04"
    assert out["annual_growth_last_12_months"] == pytest.approx(0.10)
    # April 2026 to April 2027 is twelve months, so one whole year of growth.
    assert out["months_projected"] == 12
    assert out["projected"] == pytest.approx(121.0)
    assert out["factor"] == pytest.approx(1.21)
    assert out["factor_if_held_flat"] == pytest.approx(1.10)


def test_a_short_survey_window_stops_rather_than_guessing():
    with pytest.raises(ValueError, match="inside the survey window"):
        carry_forward({"2020-04": 100.0, "2021-06": 100.0, "2022-04": 110.0,
                       "2023-04": 120.0}, "test series")


def test_a_missing_comparator_stops_rather_than_guessing():
    """Without the same month a year earlier there is no growth rate to extend on."""
    monthly = flat_window(100.0, 2020)
    monthly["2026-04"] = 110.0
    with pytest.raises(ValueError, match="no observation for 2025-04"):
        carry_forward(monthly, "test series")


def test_rebasing_to_the_survey_window_itself_is_a_no_op():
    monthly = flat_window(100.0, 2020)
    monthly.update({f"2023-{m:02d}": 130.0 for m in range(4, 13)})
    monthly.update({f"2024-{m:02d}": 130.0 for m in range(1, 4)})
    assert rebase(monthly, SURVEY_WINDOW, "test series") == pytest.approx(1.0)
    assert rebase(monthly, VALIDATION_WINDOW, "test series") == pytest.approx(1.3)


def test_rebasing_stops_on_a_short_target_window():
    with pytest.raises(ValueError, match="validation window"):
        rebase(flat_window(100.0, 2020), ("2023-04", "2023-06"), "test series")


@pytest.mark.parametrize("field", sorted(UPRATING_SOURCE))
def test_an_override_wins_and_never_reads_the_file(field):
    """A sensitivity run says what it wants and gets exactly that."""
    assumptions = dataclasses.replace(ASSUMPTIONS, **{field: 1.0})
    assert resolve_uprating(assumptions, field) == 1.0


def test_every_wealth_column_has_a_factor_behind_it():
    """A new class of wealth must be given a source, not silently left at one."""
    assert set(UPRATING_SOURCE) == {"uprate_property", "uprate_financial",
                                    "uprate_pension", "uprate_physical"}
    assert set(UPRATING_SOURCE.values()) == {"uprate_property", "uprate_other"}


def test_the_measured_factors_carry_survey_money_forward_not_back():
    """A live check on the built file: prices rose over this period."""
    for field in ("uprate_property", "uprate_financial", "uprate_pension"):
        assert resolve_uprating(ASSUMPTIONS, field) > 1.0


def test_possessions_are_held_at_their_survey_values():
    """A deliberate choice, not an oversight, so it gets a test of its own.

    Physical wealth enters as a replacement cost. Second-hand furniture and
    cars do not track an index driven by energy and food, and they wear out, so
    carrying them forward on consumer prices would flatter the answer."""
    assert ASSUMPTIONS.uprate_physical == 1.0
    assert resolve_uprating(ASSUMPTIONS, "uprate_physical") == 1.0
    # The plumbing still works, so putting them back on CPI is one override.
    import dataclasses
    on_cpi = dataclasses.replace(ASSUMPTIONS, uprate_physical=None)
    assert resolve_uprating(on_cpi, "uprate_physical") > 1.0


def test_the_validation_year_is_a_waypoint_on_the_same_journey():
    """Survey to 2023-24, then 2023-24 to April 2027, is survey to April 2027."""
    from model.build_units import _uprating_factors

    factors = _uprating_factors()
    whole = factors["uprate_other"]
    first_leg = factors["uprate_other_validation"]
    second_leg = uprating_between_validation_year_and_model_date(ASSUMPTIONS)
    assert first_leg * second_leg == pytest.approx(whole, rel=1e-6)
    assert first_leg < whole, "2023-24 sits between the survey and the model date"


def test_a_frozen_sensitivity_freezes_the_hmrc_leg_too():
    """Holding savings still and then uprating HMRC's claim values would be incoherent."""
    frozen = dataclasses.replace(ASSUMPTIONS, uprate_financial=1.0)
    assert uprating_between_validation_year_and_model_date(frozen) == 1.0


def test_the_blended_factor_is_the_aggregate_the_parts_imply():
    """Half the estate on a factor of two and half on one gives one and a half."""
    frame = pd.DataFrame({
        "weight": [1.0, 1.0],
        "property_wealth": [100.0, 0.0],
        "financial_wealth": [0.0, 100.0],
        "physical_wealth": [0.0, 0.0],
        "dc_pension_wealth": [0.0, 0.0],
    })
    assumptions = dataclasses.replace(
        ASSUMPTIONS, uprate_property=2.0, uprate_financial=1.0,
        uprate_pension=1.0, uprate_physical=1.0)
    assert blended_estate_uprating(frame, assumptions) == pytest.approx(1.5)


def test_the_blended_factor_is_weighted_by_wealth_not_by_household():
    """One large property household outweighs one small savings household."""
    frame = pd.DataFrame({
        "weight": [1.0, 1.0],
        "property_wealth": [900.0, 0.0],
        "financial_wealth": [0.0, 100.0],
        "physical_wealth": [0.0, 0.0],
        "dc_pension_wealth": [0.0, 0.0],
    })
    assumptions = dataclasses.replace(
        ASSUMPTIONS, uprate_property=2.0, uprate_financial=1.0,
        uprate_pension=1.0, uprate_physical=1.0)
    assert blended_estate_uprating(frame, assumptions) == pytest.approx(1.9)


def test_the_blended_factor_stops_on_an_empty_estate_base():
    frame = pd.DataFrame({"weight": [1.0], "property_wealth": [0.0],
                          "financial_wealth": [0.0], "physical_wealth": [0.0],
                          "dc_pension_wealth": [0.0]})
    with pytest.raises(ValueError, match="sums to nothing"):
        blended_estate_uprating(frame, ASSUMPTIONS)


def test_nothing_uprated_leaves_the_pareto_threshold_where_it_started():
    """The threshold names a point in the distribution, so a still distribution does not move it."""
    frame = pd.DataFrame({"weight": [1.0], "property_wealth": [500.0],
                          "financial_wealth": [500.0], "physical_wealth": [0.0],
                          "dc_pension_wealth": [0.0]})
    still = dataclasses.replace(
        ASSUMPTIONS, uprate_property=1.0, uprate_financial=1.0,
        uprate_pension=1.0, uprate_physical=1.0)
    assert blended_estate_uprating(frame, still) == pytest.approx(1.0)


def counterfactual_inputs(price_grown_to_survey_date, value_now, counted=True):
    """One household, as housing_gain.py hands it over: everything in survey money."""
    gains = pd.DataFrame({
        "residence_value_if_nominal": [min(price_grown_to_survey_date, value_now)],
        "residence_value_if_real": [min(price_grown_to_survey_date, value_now)],
        "housing_gain_counted": [counted],
    })
    return gains, pd.Series([float(value_now)])


def test_the_nominal_world_home_does_not_grow_at_all():
    """"Still worth what was paid for it" is a cash sum, and cash does not move.

    The frame is multiplied by the property factor after this, so the value
    handed back has to be divided by it to come out flat."""
    assumptions = dataclasses.replace(
        ASSUMPTIONS, uprate_property=2.0, uprate_financial=4.0)
    gains, residence = counterfactual_inputs(100.0, 1000.0)
    out = counterfactual_home_value(gains, residence, "nominal", assumptions)
    assert float(out.iloc[0]) * 2.0 == pytest.approx(100.0)


def test_the_real_world_home_grows_on_consumer_prices_not_house_prices():
    """A world where homes tracked inflation cannot have them tracking house prices."""
    assumptions = dataclasses.replace(
        ASSUMPTIONS, uprate_property=2.0, uprate_financial=4.0)
    gains, residence = counterfactual_inputs(100.0, 10_000.0)
    out = counterfactual_home_value(gains, residence, "real", assumptions)
    assert float(out.iloc[0]) * 2.0 == pytest.approx(400.0)


def test_the_counterfactual_home_is_never_worth_more_than_the_real_one():
    """Otherwise a home that lagged inflation would show a negative gain."""
    assumptions = dataclasses.replace(
        ASSUMPTIONS, uprate_property=1.0, uprate_financial=4.0)
    gains, residence = counterfactual_inputs(900.0, 1000.0)
    out = counterfactual_home_value(gains, residence, "real", assumptions)
    assert float(out.iloc[0]) == pytest.approx(1000.0)


def test_a_household_with_no_purchase_price_contributes_no_gain():
    """Uncounted households keep their real home, in every world."""
    assumptions = dataclasses.replace(
        ASSUMPTIONS, uprate_property=2.0, uprate_financial=4.0)
    for world in ("nominal", "real"):
        gains, residence = counterfactual_inputs(100.0, 1000.0, counted=False)
        gains[f"residence_value_if_{world}"] = [1000.0]
        out = counterfactual_home_value(gains, residence, world, assumptions)
        assert float(out.iloc[0]) == pytest.approx(1000.0), world


def test_an_unknown_counterfactual_world_stops():
    assumptions = ASSUMPTIONS
    gains, residence = counterfactual_inputs(100.0, 1000.0)
    with pytest.raises(ValueError, match="Unknown housing counterfactual"):
        counterfactual_home_value(gains, residence, "sideways", assumptions)


if __name__ == "__main__":
    raise SystemExit(pytest.main(["-q", "--import-mode=importlib", __file__]))
