# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Check unit size follows partnership status. Other adults in the household are not part of the estate unit."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from lib.iht_rules import bands_for_marital_status
from model.build_units import (COUPLE_STATUSES, MARITAL_CODE, SINGLE_STATUSES,
                               unit_size)


def _sizes(*statuses):
    return list(unit_size(pd.Series(list(statuses))))


def test_a_living_partner_makes_two():
    assert _sizes("Married", "Cohabiting") == [2, 2]


def test_nobody_left_makes_one():
    assert _sizes("Widowed", "Single", "Divorced") == [1, 1, 1]


def test_separated_still_counts_as_a_couple():
    """Separation preserves spouse exemption and transferable bands."""
    assert _sizes("Separated") == [2]


def test_every_marital_code_the_survey_can_return_is_classified():
    """Every WAS status must have an explicit classification."""
    for code, status in MARITAL_CODE.items():
        assert unit_size(pd.Series([status])).iloc[0] in (1, 2), code


def test_an_unclassified_status_raises_rather_than_counting_as_single():
    """Unknown statuses must raise, not default to one member."""
    with pytest.raises(ValueError, match="no unit size"):
        unit_size(pd.Series(["Married", "Civil Partner Separated"]))


def test_a_civil_partner_is_a_couple_and_a_former_one_is_not():
    assert unit_size(pd.Series([MARITAL_CODE[8]])).iloc[0] == 2
    assert unit_size(pd.Series([MARITAL_CODE[9]])).iloc[0] == 1


def test_two_living_people_means_two_sets_of_allowances():
    """Unit size and tax allowances must agree on living-partner status."""
    for status in COUPLE_STATUSES:
        assert unit_size(pd.Series([status])).iloc[0] == 2
        assert bands_for_marital_status(status)[0] == 2.0


def test_one_living_person_means_one_set_unless_they_inherited_another():
    """Widows can inherit a second set of bands; single and divorced people have one."""
    assert unit_size(pd.Series(["Widowed"])).iloc[0] == 1
    assert bands_for_marital_status("Widowed")[0] > 1.5

    for status in SINGLE_STATUSES - {"Widowed"}:
        assert unit_size(pd.Series([status])).iloc[0] == 1
        assert bands_for_marital_status(status)[0] == 1.0


def test_the_result_is_an_integer_series():
    """Return integer counts for downstream arithmetic."""
    out = unit_size(pd.Series(["Married", "Widowed"]))
    assert out.dtype.kind == "i"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main(["-q", "--import-mode=importlib", __file__]))
