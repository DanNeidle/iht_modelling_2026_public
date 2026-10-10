"""Tests for price-basis isolation and output protection in the round comparison."""

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

import compare


def raw_frame():
    return pd.DataFrame({
        "property_wealth": [100.0], "residence_value": [80.0],
        "housing_gain_nominal": [30.0], "housing_gain_real": [10.0],
        "financial_wealth": [-25.0], "dc_pension_wealth": [40.0],
        "dc_pension_cohort_shifted": [45.0], "db_pension_wealth": [200.0],
        "business_raw": [50.0], "agricultural_raw": [60.0],
        "physical_wealth_raw": [5.0], "weight": [1000.0],
        "case_id": [123], "members_band_14": [2],
    })


BRIDGE = {"property": {"round7_to_round8": 1.2},
          "other": {"round7_to_round8": 1.1}}


def test_round7_prices_change_without_changing_weights_or_households():
    source = raw_frame()
    original = source.copy(deep=True)
    changed = compare.common_price_frame(source, 7, BRIDGE)
    assert changed.loc[0, "residence_value"] == 96
    assert changed.loc[0, "financial_wealth"] == pytest.approx(-27.5)
    assert changed.loc[0, "dc_pension_wealth"] == 44
    assert changed.loc[0, "physical_wealth_raw"] == 5.5
    assert changed.loc[0, "weight"] == 1000
    assert changed.loc[0, "members_band_14"] == 2
    pd.testing.assert_frame_equal(source, original)


def test_round8_is_an_identity_and_returns_a_copy():
    source = raw_frame()
    changed = compare.common_price_frame(source, 8, BRIDGE)
    pd.testing.assert_frame_equal(source, changed)
    changed.loc[0, "weight"] = 0
    assert source.loc[0, "weight"] == 1000


def test_physical_sensitivity_only_changes_physical_wealth():
    standard = compare.common_price_frame(raw_frame(), 7, BRIDGE)
    frozen = compare.common_price_frame(raw_frame(), 7, BRIDGE, bridge_physical=False)
    assert frozen.loc[0, "physical_wealth_raw"] == 5
    pd.testing.assert_frame_equal(standard.drop(columns="physical_wealth_raw"),
                                  frozen.drop(columns="physical_wealth_raw"))


def test_changed_pension_valuation_fields_are_all_excluded_from_estates():
    assert set(compare.EXCLUDED_PENSION_ALIASES).issubset(compare.bu.HH_NON_ESTATE_PENSION)
    assert not set(compare.EXCLUDED_PENSION_ALIASES).intersection(compare.bu.HH_DC_COMPONENTS)


def test_output_directory_cannot_be_reused(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    with patch.object(compare, "ROOT", tmp_path):
        created = compare.create_output(str(notes / "run"))
        marker = created / "preserve.txt"
        marker.write_text("original")
        with pytest.raises(FileExistsError):
            compare.create_output(str(created))
        assert marker.read_text() == "original"


def test_json_writer_cannot_replace_an_existing_file(tmp_path):
    target = tmp_path / "result.json"
    compare.write_json(target, {"first": True})
    with pytest.raises(FileExistsError):
        compare.write_json(target, {"second": True})
    assert '"first": true' in target.read_text()
