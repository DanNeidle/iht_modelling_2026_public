# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Hand-checkable tax examples. Run with pytest or python3 code/lib/test_iht_rules.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import RULES_2026_27, RULES_2027_28, RuleSet
from lib.iht_rules import Estate, bands_for_marital_status, compute_tax


def approx(a: float, b: float, tol: float = 1.0) -> bool:
    return abs(a - b) <= tol


# The cases everyone quotes
def test_couple_with_a_million_pays_nothing():
    """Two sets of bands give £1m if a home worth at least £350,000 passes to descendants."""
    estate = Estate(
        property_wealth=400_000,
        financial_wealth=600_000,
        residence_value=400_000,
        has_direct_descendants=True,
        nil_rate_bands=2.0,
        residence_bands=2.0,
    )
    r = compute_tax(estate, RULES_2026_27)
    assert approx(r.nil_rate_band_available, 650_000)
    assert approx(r.residence_band_available, 350_000)
    assert approx(r.tax, 0.0), r.tax


def test_couple_just_over_the_line():
    """£1.4m, so £400,000 is taxable and the bill is £160,000."""
    estate = Estate(
        property_wealth=500_000,
        financial_wealth=900_000,
        residence_value=500_000,
        nil_rate_bands=2.0,
        residence_bands=2.0,
    )
    r = compute_tax(estate, RULES_2026_27)
    assert approx(r.taxable_amount, 400_000)
    assert approx(r.tax, 160_000), r.tax


def test_childless_couple_pays_more_on_the_same_money():
    """No residence band: £650,000 threshold and £140,000 more tax."""
    estate = Estate(
        property_wealth=400_000,
        financial_wealth=600_000,
        residence_value=400_000,
        has_direct_descendants=False,
        nil_rate_bands=2.0,
        residence_bands=2.0,
    )
    r = compute_tax(estate, RULES_2026_27)
    assert approx(r.residence_band_available, 0.0)
    assert approx(r.tax, 140_000), r.tax


def test_single_never_married_person():
    """One of everything, so the threshold is £500,000."""
    estate = Estate(
        property_wealth=300_000,
        financial_wealth=200_000,
        residence_value=300_000,
        nil_rate_bands=1.0,
        residence_bands=1.0,
    )
    r = compute_tax(estate, RULES_2026_27)
    assert approx(r.tax, 0.0)
    # Pin the bands themselves, so this cannot pass on any total of £500,000.
    assert approx(r.nil_rate_band_available, 325_000)
    assert approx(r.residence_band_available, 175_000)


def test_a_pound_over_the_single_threshold_is_taxed():
    """The boundary itself, which the test above cannot see."""
    estate = Estate(
        property_wealth=300_000,
        financial_wealth=200_001,
        residence_value=300_000,
        nil_rate_bands=1.0,
        residence_bands=1.0,
    )
    assert approx(compute_tax(estate, RULES_2026_27).tax, 0.4)


def test_an_estate_with_nothing_in_it_owes_nothing():
    """Negative financial wealth is real in the survey and must not become tax."""
    estate = Estate(property_wealth=0.0, financial_wealth=-65_000)
    r = compute_tax(estate, RULES_2026_27)
    assert approx(r.tax, 0.0)
    assert approx(r.residence_band_available, 0.0)


def test_residence_band_capped_at_the_value_of_the_house():
    """A £120,000 home caps the residence band at £120,000."""
    estate = Estate(
        property_wealth=120_000,
        financial_wealth=900_000,
        residence_value=120_000,
        nil_rate_bands=2.0,
        residence_bands=2.0,
    )
    r = compute_tax(estate, RULES_2026_27)
    assert approx(r.residence_band_available, 120_000)
    # £1,020,000 less £650,000 less £120,000 = £250,000 taxable.
    assert approx(r.tax, 100_000), r.tax


# The taper
def test_taper_bites_above_two_million():
    """At £2.35m a couple has lost half of its £350,000 residence band."""
    estate = Estate(
        property_wealth=600_000,
        financial_wealth=1_750_000,
        residence_value=600_000,
        nil_rate_bands=2.0,
        residence_bands=2.0,
    )
    r = compute_tax(estate, RULES_2026_27)
    assert approx(r.residence_band_available, 175_000), r.residence_band_available
    assert approx(r.residence_band_tapered_away, 175_000)


def test_the_taper_comes_off_the_band_before_the_house_caps_it():
    """IHTA 1984 s8D(5) and s8E(4): taper the allowance, then cap at the home.

    Doing it the other way round takes the taper twice from anyone whose home
    is worth less than their headline band. This couple has a £250,000 home and
    a £2.2m estate, so the taper is £100,000 and the adjusted allowance is
    £250,000. The home is worth exactly that, so the whole £250,000 survives.
    Capping first would have given £150,000 and charged £40,000 too much."""
    estate = Estate(
        property_wealth=250_000,
        financial_wealth=1_950_000,
        residence_value=250_000,
        nil_rate_bands=2.0,
        residence_bands=2.0,
    )
    r = compute_tax(estate, RULES_2026_27)
    assert approx(r.residence_band_available, 250_000), r.residence_band_available
    # £2.2m less £650,000 of nil-rate band less £250,000 of residence band.
    assert approx(r.tax, 0.4 * 1_300_000), r.tax


def test_a_home_smaller_than_the_tapered_allowance_still_caps_it():
    """The cap has not gone away, it just applies second."""
    estate = Estate(
        property_wealth=120_000,
        financial_wealth=2_080_000,
        residence_value=120_000,
        nil_rate_bands=2.0,
        residence_bands=2.0,
    )
    r = compute_tax(estate, RULES_2026_27)
    # Adjusted allowance is £350,000 less £100,000 of taper, but the home is £120,000.
    assert approx(r.residence_band_available, 120_000), r.residence_band_available
    assert approx(r.residence_band_tapered_away, 0.0), r.residence_band_tapered_away


def test_taper_removes_the_band_entirely():
    """Two residence bands are worth £350,000, so they are gone by £2.7m."""
    estate = Estate(
        property_wealth=700_000,
        financial_wealth=2_100_000,
        residence_value=700_000,
        nil_rate_bands=2.0,
        residence_bands=2.0,
    )
    r = compute_tax(estate, RULES_2026_27)
    assert approx(r.residence_band_available, 0.0), r.residence_band_available


# Pensions from April 2027
def test_pension_pot_is_outside_the_estate_today():
    estate = Estate(
        property_wealth=400_000,
        financial_wealth=200_000,
        dc_pension_wealth=500_000,
        residence_value=400_000,
        nil_rate_bands=2.0,
        residence_bands=2.0,
    )
    r = compute_tax(estate, RULES_2026_27)
    assert approx(r.gross_estate, 600_000)
    assert approx(r.tax, 0.0)


def test_pension_pot_is_inside_the_estate_from_2027():
    """Including the pension pot gives the same family a £100,000 bill."""
    estate = Estate(
        property_wealth=400_000,
        financial_wealth=200_000,
        dc_pension_wealth=500_000,
        residence_value=400_000,
        nil_rate_bands=2.0,
        residence_bands=2.0,
    )
    r = compute_tax(estate, RULES_2027_28)
    assert approx(r.gross_estate, 1_100_000)
    assert approx(r.taxable_amount, 100_000)
    assert approx(r.tax, 40_000), r.tax


# Business and agricultural property
def test_farm_under_the_couples_allowance_pays_nothing_on_the_farm():
    """A £4m farm is fully relieved, but its value removes the residence band."""
    estate = Estate(
        property_wealth=500_000,
        business_agricultural=4_000_000,
        residence_value=500_000,
        nil_rate_bands=2.0,
        residence_bands=2.0,
        apr_bpr_allowances=2.0,
    )
    r = compute_tax(estate, RULES_2026_27)
    assert approx(r.business_relief, 4_000_000)
    assert approx(r.residence_band_available, 0.0)
    # £4.5m less £4m relief = £500,000, which is below the £650,000 bands.
    assert approx(r.tax, 0.0), r.tax


def test_farm_above_the_allowance_gets_half_relief():
    """£2m above the couple's £5m allowance gets half relief, leaving £1m taxable."""
    estate = Estate(
        business_agricultural=7_000_000,
        nil_rate_bands=2.0,
        residence_bands=2.0,
        has_direct_descendants=True,
        residence_value=0.0,
        apr_bpr_allowances=2.0,
    )
    r = compute_tax(estate, RULES_2026_27)
    assert approx(r.business_relief, 6_000_000), r.business_relief
    assert approx(r.chargeable_estate, 1_000_000)
    assert approx(r.taxable_amount, 350_000)
    assert approx(r.tax, 140_000), r.tax


def test_uncapped_relief_under_the_old_rules():
    """Uncapped relief for the 2023-24 validation."""
    old = RuleSet(name="test", apr_bpr_allowance=float("inf"),
                  apr_bpr_relief_above_allowance=1.0)
    estate = Estate(business_agricultural=7_000_000, nil_rate_bands=2.0)
    r = compute_tax(estate, old)
    assert approx(r.business_relief, 7_000_000)
    assert approx(r.tax, 0.0)


# Marital status to bands
def test_marital_status_mapping():
    """Every status, and all four returned values."""
    for married in ("Married", "Civil partnership", "Separated"):
        assert bands_for_marital_status(married) == (2.0, 2.0, 2.0, 1.0), married
    for alone in ("Single", "Divorced", "", None):
        assert bands_for_marital_status(alone) == (1.0, 1.0, 1.0, 1.0), alone


def test_a_widow_keeps_the_whole_relief_allowance():
    """The transfer share is about a spent nil-rate band, not about relief.

    A late spouse can use part of their nil-rate band, which is what the 0.95
    stands for. They cannot have used an agricultural and business allowance
    that did not exist until April 2026, so that one transfers in full."""
    nil_rate, residence, relief, thresholds = bands_for_marital_status(
        "Widowed", widow_transfer_share=0.95)
    assert approx(nil_rate, 1.95)
    assert approx(residence, 1.95)
    assert approx(relief, 2.0)
    assert approx(thresholds, 1.0)


def test_a_cohabiting_couple_is_tested_against_two_taper_thresholds():
    """Their deaths are two estates, so the £2m threshold applies to each.

    A married couple's second death is one estate and gets one threshold."""
    assert bands_for_marital_status("Cohabiting")[3] == 2.0
    assert bands_for_marital_status("Married")[3] == 1.0

    # £2.4m between them is £1.2m each, so neither estate is near the threshold.
    shared = dict(property_wealth=400_000, financial_wealth=2_000_000,
                  residence_value=400_000, nil_rate_bands=2.0, residence_bands=2.0)
    cohabiting = compute_tax(Estate(**shared, taper_thresholds=2.0), RULES_2026_27)
    married = compute_tax(Estate(**shared, taper_thresholds=1.0), RULES_2026_27)
    assert approx(cohabiting.residence_band_available, 350_000)
    assert approx(married.residence_band_available, 150_000)


def test_pensions_count_towards_the_two_million_taper_threshold():
    """A deliberate reading, recorded because it decides the band for real units.

    From April 2027 an unused pension pot is part of the estate, and s8D tests
    the taper against the value of the estate, so the pot pushes the estate
    towards the £2m threshold like anything else. This couple is under £2m
    without the pot and over it with, and the band goes from whole to halved."""
    shared = dict(property_wealth=700_000, financial_wealth=1_100_000,
                  residence_value=700_000, dc_pension_wealth=400_000,
                  nil_rate_bands=2.0, residence_bands=2.0)
    today = compute_tax(Estate(**shared), RULES_2026_27)
    from_2027 = compute_tax(Estate(**shared), RULES_2027_28)
    assert approx(today.gross_estate, 1_800_000)
    assert approx(today.residence_band_available, 350_000)
    assert approx(from_2027.gross_estate, 2_200_000)
    assert approx(from_2027.residence_band_available, 250_000)


def test_the_validation_rule_set_really_is_uncapped():
    """RULES_2023_24 is what the HMRC comparison runs on, so pin it here.

    Building a lookalike RuleSet in the test would not notice if the real one
    changed underneath the validation."""
    from lib.config import RULES_2023_24

    estate = Estate(property_wealth=200_000, business_agricultural=6_000_000,
                    nil_rate_bands=1.0, residence_bands=0.0,
                    apr_bpr_allowances=1.0)
    r = compute_tax(estate, RULES_2023_24)
    assert approx(r.business_relief, 6_000_000), r.business_relief
    assert approx(r.tax, 0.0), r.tax
    assert RULES_2023_24.pensions_in_estate is False


def test_an_uncapped_allowance_with_no_multiplier_gives_no_relief():
    """Infinity times zero is NaN, and NaN must not be read as full relief."""
    from lib.config import RULES_2023_24

    estate = Estate(business_agricultural=1_000_000, apr_bpr_allowances=0.0)
    assert approx(compute_tax(estate, RULES_2023_24).business_relief, 0.0)


def _run_all() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  pass  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {name}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
