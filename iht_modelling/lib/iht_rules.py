# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Calculate tax on an estate. Married couples are modelled at the second death with transferred bands; see test_iht_rules.py for worked examples."""

from __future__ import annotations

from dataclasses import dataclass

from .config import RuleSet


@dataclass
class Estate:
    """Estate when we murder everyone, in pounds net of secured debt. Property wealth is equity."""

    property_wealth: float = 0.0
    financial_wealth: float = 0.0
    physical_wealth: float = 0.0

    # Unused DC pots only; exclude capitalised DB and in-payment pension wealth.
    dc_pension_wealth: float = 0.0

    # Qualifying agricultural and business property, valued before relief.
    business_agricultural: float = 0.0

    # Residence band requires a home passing to direct descendants.
    residence_value: float = 0.0
    has_direct_descendants: bool = True

    # Available bands, including partial transfers from a late spouse.
    nil_rate_bands: float = 1.0
    residence_bands: float = 1.0

    # APR/BPR allowances, including transfers between spouses.
    apr_bpr_allowances: float = 1.0

    # How many £2m taper thresholds this unit is tested against. One for a
    # single person or a couple's second death, two where the unit stands for
    # two estates that are taxed separately.
    taper_thresholds: float = 1.0


@dataclass
class TaxResult:
    """A bill, with enough working shown to audit it."""

    tax: float
    gross_estate: float
    chargeable_estate: float
    business_relief: float
    nil_rate_band_available: float
    residence_band_available: float
    residence_band_tapered_away: float
    taxable_amount: float

    @property
    def effective_rate(self) -> float:
        """Tax divided by gross estate value."""
        if self.gross_estate <= 0:
            return 0.0
        return self.tax / self.gross_estate


def compute_business_relief(estate: Estate, rules: RuleSet) -> float:
    """Apply APR/BPR to combined qualifying property. An infinite allowance reproduces the uncapped rules."""
    qualifying = estate.business_agricultural
    if qualifying <= 0:
        return 0.0

    # Order matters with the uncapped rules: infinity times a zero multiplier is
    # NaN, and a NaN allowance would slide through the comparisons below and
    # hand back full relief rather than none.
    if rules.apr_bpr_allowance == float("inf"):
        return qualifying if estate.apr_bpr_allowances > 0 else 0.0

    allowance = rules.apr_bpr_allowance * estate.apr_bpr_allowances

    fully_relieved = min(qualifying, allowance)
    excess = max(0.0, qualifying - allowance)
    return fully_relieved + excess * rules.apr_bpr_relief_above_allowance


def compute_residence_band(estate: Estate, rules: RuleSet,
                           net_estate_for_taper: float) -> tuple[float, float]:
    """Return (residence band available, amount tapered away).

Requires direct descendants and is capped at the home value. Taper uses net estate before reliefs and exemptions.

The order matters, and it is the order in the statute rather than the obvious one. IHTA 1984 s8D(5) reduces the default allowance by the taper to give the adjusted allowance, and s8E(4) and (5) then set the residence nil-rate amount at the lesser of the value of the home closely inherited and that adjusted allowance. So the taper comes off the band first and the home value caps the result, not the other way round.

Capping first and tapering the capped figure would take the taper twice off anyone whose home is worth less than their headline band, which is the modest house with a large portfolio behind it. On a £2.2m estate with a £250,000 home it is the difference between £150,000 and £250,000 of band, so £40,000 of tax."""
    if not estate.has_direct_descendants or estate.residence_value <= 0:
        return 0.0, 0.0

    headline = rules.residence_nil_rate_band * estate.residence_bands

    # A cohabiting couple leaves two estates rather than one, so the threshold
    # this unit is tested against is two thresholds. A married couple's second
    # death really is a single estate and gets one.
    threshold = rules.rnrb_taper_threshold * estate.taper_thresholds
    excess_over_taper = max(0.0, net_estate_for_taper - threshold)
    taper = excess_over_taper * rules.rnrb_taper_rate

    adjusted = max(0.0, headline - taper)
    available = min(estate.residence_value, adjusted)

    # What the taper itself cost, which is the band before it less the band after.
    tapered_away = min(estate.residence_value, headline) - available

    return available, tapered_away


def compute_tax(estate: Estate, rules: RuleSet) -> TaxResult:
    """Calculate tax, testing the residence taper before deducting reliefs and bands."""
    pension = estate.dc_pension_wealth if rules.pensions_in_estate else 0.0

    gross_estate = (
        estate.property_wealth
        + estate.financial_wealth
        + estate.physical_wealth
        + estate.business_agricultural
        + pension
    )

    # The taper test uses the estate before reliefs.
    residence_band, tapered_away = compute_residence_band(
        estate, rules, net_estate_for_taper=gross_estate
    )

    business_relief = compute_business_relief(estate, rules)
    chargeable_estate = gross_estate - business_relief

    nil_rate_band = rules.nil_rate_band * estate.nil_rate_bands
    total_bands = nil_rate_band + residence_band

    taxable = max(0.0, chargeable_estate - total_bands)
    tax = taxable * rules.rate

    return TaxResult(
        tax=tax,
        gross_estate=gross_estate,
        chargeable_estate=chargeable_estate,
        business_relief=business_relief,
        nil_rate_band_available=nil_rate_band,
        residence_band_available=residence_band,
        residence_band_tapered_away=tapered_away,
        taxable_amount=taxable,
    )


def bands_for_marital_status(status: str, widow_transfer_share: float = 0.95,
                             cohabiting_band_sets: float = 2.0
                             ) -> tuple[float, float, float, float]:
    """Return (nil-rate bands, residence bands, business allowances, taper thresholds).

    The last of those is how many £2m thresholds the unit is tested against, which is one for everybody except a cohabiting couple, whose deaths are two separate estates rather than one."""
    status = (status or "").strip().lower()

    if status in ("married", "civil partnership", "civil partner"):
        return 2.0, 2.0, 2.0, 1.0

    if status in ("widowed", "surviving civil partner"):
        # The share stands for the part of a late spouse's nil-rate band that
        # was already used, so it belongs to the two nil-rate bands and not to
        # the agricultural and business allowance. That allowance only exists
        # from April 2026, and these widows were widowed before it did, so
        # there was nothing for their spouse to spend.
        n = 1.0 + widow_transfer_share
        return n, n, 2.0, 1.0

    if status == "cohabiting":
        # Combine two estates assuming equal ownership. Unequal ownership can waste allowances, so this may understate tax.
        n = cohabiting_band_sets
        return n, n, n, 2.0

    if status == "separated":
        # Separation preserves spouse exemption and transferable bands.
        return 2.0, 2.0, 2.0, 1.0

    # Single, divorced. One of everything.
    return 1.0, 1.0, 1.0, 1.0
