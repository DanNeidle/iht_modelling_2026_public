# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Test household probability arithmetic. analysis/households.py checks child counts against the adults model."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model.household_stake import (AGE_BANDS, A_GENERATION, band_probability,
                                   household_stake, stake_probability_by_age)

# Band 9 is 40-44, band 10 is 45-49, band 14 is 65-69, band 17 is 80+.
BY_BAND = {b: 0.0 for b in AGE_BANDS}
BY_BAND.update({9: 0.15, 10: 0.15, 11: 0.10, 14: 0.02})


def test_a_household_nobody_can_claim_in_is_out():
    assert household_stake([6], 0.0, BY_BAND) == 0.0


def test_a_liable_pensioner_household_is_in_whatever_else_is_true():
    assert household_stake([17], 1.0, BY_BAND) == pytest.approx(1.0)


def test_one_adult_gets_that_adult_s_probability():
    assert household_stake([9], 0.0, BY_BAND) == pytest.approx(0.15)


def test_two_adults_are_more_likely_than_one_but_not_twice_as_likely():
    """Two independent 15% chances combine to 27.75%."""
    one = household_stake([9], 0.0, BY_BAND)
    two = household_stake([9, 10], 0.0, BY_BAND)
    assert two > one
    assert two < 2 * one
    assert two == pytest.approx(1 - 0.85 * 0.85)


def test_adding_an_adult_can_never_reduce_the_chance():
    base = household_stake([9], 0.0, BY_BAND)
    for extra in (6, 10, 11, 12):
        assert household_stake([9, extra], 0.0, BY_BAND) >= base - 1e-12


def test_own_bill_and_children_combine_without_double_counting():
    """Own and parental exposure still count as one household."""
    got = household_stake([10], 0.3, BY_BAND)
    assert got == pytest.approx(1 - 0.7 * 0.85)
    assert got < 0.3 + 0.15


def test_a_child_living_with_the_liable_parent_is_not_counted_twice():
    """A co-resident adult child is already covered by the parent's household."""
    with_parent = household_stake([17, 10], 0.4, BY_BAND)
    assert with_parent == pytest.approx(0.4)

    # Same-generation adults both contribute.
    couple = household_stake([10, 11], 0.0, BY_BAND)
    assert couple > 0.15


def test_the_generation_rule_is_applied_on_the_gap_not_on_being_old():
    """Two people 25 years apart is a generation; twenty is not."""
    gap = AGE_BANDS[14][0] - AGE_BANDS[9][0]      # 65 - 40
    assert gap >= A_GENERATION
    near = AGE_BANDS[13][0] - AGE_BANDS[9][0]     # 60 - 40
    assert near < A_GENERATION


def test_the_result_is_always_a_probability():
    for bands in ([9], [9, 10], [9, 10, 11], [17, 9], [6]):
        for p_own in (0.0, 0.5, 1.0):
            got = household_stake(bands, p_own, BY_BAND)
            assert 0.0 <= got <= 1.0


# The numerator
def test_the_step_family_haircut_comes_off_the_numerator():
    children = np.array([0.0, 100.0, 100.0])
    population = np.array([1000.0, 1000.0, 1000.0])
    plain = stake_probability_by_age(children, population, 0.0)
    cut = stake_probability_by_age(children, population, 0.04)
    assert plain[1] == pytest.approx(0.10)
    assert cut[1] == pytest.approx(0.096)


def test_a_probability_can_never_exceed_one():
    """Clamp probabilities to one if implied children exceed population."""
    children = np.array([5000.0])
    population = np.array([1000.0])
    assert stake_probability_by_age(children, population, 0.0)[0] == 1.0


def test_an_empty_age_has_no_probability():
    out = stake_probability_by_age(np.array([10.0]), np.array([0.0]), 0.0)
    assert out[0] == 0.0


def test_bands_are_averaged_by_population_not_by_midpoint():
    """Population weighting should favour the densely populated end of a band."""
    by_age = np.zeros(60)
    population = np.zeros(60)
    by_age[40:45] = [0.0, 0.0, 0.0, 0.0, 1.0]
    population[40:45] = [1000.0, 1000.0, 1000.0, 1000.0, 10.0]
    bands = band_probability(by_age, population)
    assert bands[9] == pytest.approx(10.0 / 4010.0, rel=1e-6)
    assert bands[9] < 0.2          # nowhere near the midpoint reading


# Grandchildren, the wider definition
from lib.config import MODEL_YEAR                      # noqa: E402
from model.children import build_cohort_table          # noqa: E402
from model.household_stake import (adult_children_schedule,      # noqa: E402
                                   household_stake_wide,
                                   lineage_probability)

GRAND = {b: 0.0 for b in AGE_BANDS}
GRAND.update({5: 0.20, 6: 0.16, 7: 0.12, 8: 0.08})


def test_the_schedule_covers_ages_the_cohort_table_does_not():
    """Younger cohorts missing from the completed-fertility table can still have adult children."""
    table = build_cohort_table()
    schedule = adult_children_schedule(table)
    youngest_in_table = MODEL_YEAR - max(table)
    assert youngest_in_table > 40                     # the gap is real
    assert sum(schedule[youngest_in_table - 1].values()) > 0
    assert sum(schedule[30].values()) >= 0


def test_the_extrapolation_joins_the_table_without_a_step():
    """A one-year age difference must not produce a jump at the boundary."""
    table = build_cohort_table()
    schedule = adult_children_schedule(table)
    edge = MODEL_YEAR - max(table)
    inside = sum(schedule[edge].values())
    outside = sum(schedule[edge - 1].values())
    assert outside < inside
    assert outside > inside * 0.7


def test_younger_parents_have_fewer_adult_children():
    """A mismatched model year breaks continuity at the cohort-table join."""
    table = build_cohort_table()
    schedule = adult_children_schedule(table)
    totals = [sum(schedule[a].values()) for a in range(30, 60)]
    assert all(b >= a - 1e-9 for a, b in zip(totals, totals[1:]))


def test_one_ancestor_line_gives_back_the_share_liable():
    """One living ancestor unit gives the liable share directly."""
    liable = np.array([16.0]); everyone = np.array([100.0])
    population = np.array([100.0])
    assert lineage_probability(liable, everyone, population)[0] == pytest.approx(0.16)


def test_two_ancestor_lines_are_less_than_twice_as_likely():
    """Two independent 16% chances combine to 29.4%, counting both lineages once."""
    liable = np.array([32.0]); everyone = np.array([200.0])
    population = np.array([100.0])
    got = lineage_probability(liable, everyone, population)[0]
    assert got == pytest.approx(1 - 0.84 ** 2)
    assert got < 0.32


def test_a_lineage_probability_is_always_a_probability():
    for liable, everyone, pop in ((5.0, 1.0, 1.0), (0.0, 10.0, 10.0),
                                  (10.0, 10.0, 0.0), (1.0, 100.0, 50.0)):
        got = lineage_probability(np.array([liable]), np.array([everyone]),
                                  np.array([pop]))[0]
        assert 0.0 <= got <= 1.0


def test_widening_can_only_add_households():
    for bands in ([5], [9], [5, 6], [9, 10], [17, 10]):
        narrow = household_stake(bands, 0.0, BY_BAND)
        wide = household_stake_wide(bands, 0.0, BY_BAND, GRAND)
        assert wide >= narrow - 1e-12


def test_being_both_a_child_and_a_grandchild_counts_once():
    """Parental and grandparental exposure still count as one household."""
    got = household_stake_wide([9], 0.0, {9: 0.15}, {9: 0.10})
    assert got == pytest.approx(1 - 0.85 * 0.90)
    assert got < 0.25


def test_a_grandchild_living_with_their_parents_adds_no_household():
    """Suppress co-resident grandchildren already covered through their parent."""
    assert household_stake_wide([12, 5], 0.0, BY_BAND, GRAND) == pytest.approx(0.0)


if __name__ == "__main__":
    # Run pytest when invoked directly by run_all.py.
    import pytest
    raise SystemExit(pytest.main(["-q", "--import-mode=importlib", __file__]))
