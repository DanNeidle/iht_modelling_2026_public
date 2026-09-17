# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Build 65-plus estate units from WAS round 8: one person or a couple with at least one member aged 65-plus.

Include DC pension pots, excluding capitalised DB and in-payment pension wealth. Discount physical wealth from replacement cost to resale value. Missing microdata or wealth components stop the run."""

from __future__ import annotations

import functools
import sys
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model.business_property import (HH_LAND_COLUMNS, PERSON_BUSINESS_COLUMNS,
                                     business_by_household, land_by_household,
                                     winsorise)
from model.housing_gain import (HOUSING_GAIN_COLUMNS, REFERENCE_YEAR,
                                housing_gain, load_cpi, load_index)
from model.relief_distribution import remap
from lib.config import (ASSUMPTIONS, BUILD, CPI_INDEX, HEADLINE_AGE,
                        HOUSE_PRICE_INDEX, RELIEF_CLAIMS_2023_24,
                        UPRATING_JSON, WAS_DIR,
                        ModelAssumptions)
from lib.iht_rules import bands_for_marital_status
from model.children import build_cohort_table, children_for_unit, female_cohort_for_unit

UNITS_PATH = BUILD / "units.parquet"
PROVENANCE_PATH = BUILD / "units_provenance.txt"

# The WAS round 8 variables, confirmed against the actual file
# Variable names checked against round 8 SPSS labels.

HOUSEHOLD_FILE = "was_round_8_hhold_eul_may_2025_230525.sav"
PERSON_FILE = "was_round_8_person_eul_may_2025_230525.sav"

HH_ID = "CASER8"
HH_WEIGHT = "R8xshhwgt"

# Wealth that forms an estate.
HH_PROPERTY = "HPropWR8"            # Total net property wealth
HH_RESIDENCE = "DVHValueR8"         # Value of main residence
HH_FINANCIAL = "HFINWNTR8_Sum"      # Household net financial wealth
HH_PHYSICAL = "HphysWR8"            # Total physical wealth, at replacement cost

# DC funds, including unaccessed and drawdown pots.
HH_DC_COMPONENTS = [
    "DVValDCosR8_aggr",          # current defined contribution occupational
    "DVPPValR8_aggr",            # current personal pension
    "DVPAVCUVR8_aggr",           # additional voluntary contributions
    "DVRetDC_noaccessR8_aggr",   # retained DC, nothing accessed
    "DVRetDC_accessR8_aggr",     # retained DC, partly accessed or in drawdown
]

# Load other pension components to verify their exclusion from estates.
HH_NON_ESTATE_PENSION = [
    "dvvaldbt_scaper8_aggr",           # current defined benefit
    "dvretdb_noaccess_scaper8_aggr",   # retained DB, nothing accessed
    "DVRetDB_accessR8_aggr",           # retained DB, partly accessed
    "dvpinpval_scaper8_aggr",          # pensions already in payment
    "dvspen_scaper8_aggr",             # expected from a former spouse
]

# The household reference person's status, kept only as a fallback. The unit is
# the oldest adult, and in about one household in eight the reference person is
# younger and in a different band, so their partnership status is the wrong one
# to hand an elderly estate its nil-rate bands on.
HH_MARITAL = "HRPDVMrDfR8"
PERSON_MARITAL = "DVMrDfR8"
HH_TENURE = "Ten1R8"
HH_REGION = "GORR8"
HH_ADULTS = "NumAdultR8"

PERSON_ID = "CASER8"
PERSON_AGE_BAND = "DVAge17R8"       # five-year bands, topped at 80+
PERSON_SEX = "SexR8"

# Age-band midpoints; the End User Licence data has no finer ages.
AGE_BAND_MIDPOINT = {14: 67, 15: 72, 16: 77, 17: 84}
AGE_BAND_LOWER = {14: 65, 15: 70, 16: 75, 17: 80}

# Map WAS marital codes to tax status.
MARITAL_CODE = {
    1: "Married",
    2: "Cohabiting",
    3: "Single",
    4: "Widowed",
    5: "Divorced",
    6: "Separated",
    7: "Cohabiting",   # 'Same sex couple', the unmarried analogue of code 2
    8: "Married",      # 'Civil Partner', which the tax treats as marriage
    9: "Divorced",     # 'Former / separated Civil Partner'
}

# Separated people still have a living spouse and transferable bands.
COUPLE_STATUSES = {"Married", "Cohabiting", "Separated"}
SINGLE_STATUSES = {"Widowed", "Single", "Divorced"}


def unit_size(marital: "pd.Series") -> "pd.Series":
    """Two members where a partner is alive, otherwise one. Other household adults belong to separate units."""
    unknown = set(marital.dropna().unique()) - COUPLE_STATUSES - SINGLE_STATUSES
    if unknown:
        raise ValueError(
            f"Marital statuses with no unit size: {sorted(unknown)}. Falling "
            "back to one would hand the estate a single set of nil-rate bands, "
            "so add them to COUPLE_STATUSES or SINGLE_STATUSES deliberately.")
    return marital.isin(COUPLE_STATUSES).map({True: 2, False: 1}).astype(int)


@dataclass
class UnitsTable:
    """The output of this module, plus enough provenance to interpret it."""

    frame: pd.DataFrame
    backend: str
    notes: list[str]


# Building the units
def was_dir() -> Path | None:
    """Locate the extracted round 8 SPSS files."""
    for candidate in WAS_DIR.rglob(HOUSEHOLD_FILE):
        return candidate.parent
    return None


def load_was_units(assumptions: ModelAssumptions = ASSUMPTIONS) -> UnitsTable:
    """Build 65-plus units from WAS round 8. Raise if a required wealth component is missing."""
    import pyreadstat

    folder = was_dir()
    if folder is None:
        raise FileNotFoundError(
            f"Round 8 SPSS files not found under {WAS_DIR}. Run "
            "code/acquire/download_was.py."
        )

    hh_cols = ([HH_ID, HH_WEIGHT, HH_PROPERTY, HH_RESIDENCE, HH_FINANCIAL,
                HH_PHYSICAL, HH_MARITAL, HH_TENURE, HH_REGION, HH_ADULTS]
               + HH_DC_COMPONENTS + HH_NON_ESTATE_PENSION + HH_LAND_COLUMNS
               + HOUSING_GAIN_COLUMNS)
    household, _ = pyreadstat.read_sav(str(folder / HOUSEHOLD_FILE), usecols=hh_cols)
    person, _ = pyreadstat.read_sav(
        str(folder / PERSON_FILE),
        usecols=[PERSON_ID, PERSON_AGE_BAND, PERSON_SEX, PERSON_MARITAL]
                + PERSON_BUSINESS_COLUMNS)

    missing = [c for c in hh_cols if c not in household.columns]
    if missing:
        raise ValueError(f"WAS household file is missing {missing}")

    # Remove missing codes (-8, -9), preserving genuine negative financial wealth and equity.
    MISSING_CODES = (-8.0, -9.0)
    for col in ([HH_PROPERTY, HH_RESIDENCE, HH_FINANCIAL, HH_PHYSICAL]
                + HH_DC_COMPONENTS + HH_NON_ESTATE_PENSION):
        household[col] = household[col].mask(household[col].isin(MISSING_CODES), 0.0)

    # Home values, physical wealth and pension pots cannot be negative.
    for col in ([HH_RESIDENCE, HH_PHYSICAL]
                + HH_DC_COMPONENTS + HH_NON_ESTATE_PENSION):
        household[col] = household[col].clip(lower=0.0)

    # Age structure of each household, from the person file.
    person = person[person[PERSON_AGE_BAND] > 0].copy()
    adults = person[person[PERSON_AGE_BAND] >= 5]          # aged 20 and over
    by_hh = adults.groupby(PERSON_ID)[PERSON_AGE_BAND].agg(["max", "count"])
    by_hh.columns = ["oldest_band", "adults_counted"]

    # Count actual 65-plus members for population weighting; younger partners do not count.
    over_65 = adults[adults[PERSON_AGE_BAND] >= 14]
    by_hh["members_65_plus"] = over_65.groupby(PERSON_ID)[PERSON_AGE_BAND].count()
    by_hh["members_65_plus"] = by_hh["members_65_plus"].fillna(0)

    # Count members in their own age bands for post-stratification.
    for band in (14, 15, 16, 17):
        counts = (over_65[over_65[PERSON_AGE_BAND] == band]
                  .groupby(PERSON_ID)[PERSON_AGE_BAND].count())
        by_hh[f"members_band_{band}"] = counts
        by_hh[f"members_band_{band}"] = by_hh[f"members_band_{band}"].fillna(0)

    # Sex and marital status of the oldest adult. The unit is that person's
    # estate, so it is their partnership status that decides how many nil-rate
    # bands it gets. The household reference person is somebody younger in about
    # one household in eight, and in a few hundred of those the two disagree.
    oldest_person = (adults.sort_values(PERSON_AGE_BAND)
                           .groupby(PERSON_ID).tail(1)
                           .set_index(PERSON_ID))
    oldest_rows = oldest_person[PERSON_SEX]
    oldest_marital = oldest_person[PERSON_MARITAL].rename("oldest_marital")

    # Business values come from the person file; land is household level and is
    # extra wealth rather than part of the survey's net property total.
    business_raw = business_by_household(person)
    land_raw = land_by_household(household).rename("agricultural_raw")
    land_raw.index = household[HH_ID]

    frame = (household.set_index(HH_ID)
             .join(by_hh)
             .join(oldest_rows.rename("oldest_sex"))
             .join(oldest_marital)
             .join(business_raw.rename("business_raw"))
             .join(land_raw))
    frame["business_raw"] = frame["business_raw"].fillna(0.0)
    frame["agricultural_raw"] = frame["agricultural_raw"].fillna(0.0)
    frame = frame.dropna(subset=["oldest_band"])

    # Measure DC wealth in all age bands before filtering, for the cohort-shift sensitivity.
    dc_all = frame[HH_DC_COMPONENTS].sum(axis=1)
    band_mean_dc = {}
    for band in range(13, 18):
        rows = frame["oldest_band"] == band
        if rows.any():
            band_mean_dc[band] = float(np.average(dc_all[rows],
                                                  weights=frame.loc[rows, HH_WEIGHT]))
    cohort_ratio = {
        band: (band_mean_dc[band - 1] / band_mean_dc[band])
        for band in range(14, 18)
        if band in band_mean_dc and band - 1 in band_mean_dc and band_mean_dc[band] > 0
    }

    # Keep households whose oldest adult is 65 or over.
    frame = frame[frame["oldest_band"] >= 14].copy()

    # Flag households with more than two adults for optional exclusion.
    frame["multi_generation"] = frame["adults_counted"] > 2

    dc_pension = frame[HH_DC_COMPONENTS].sum(axis=1)
    other_pension = frame[HH_NON_ESTATE_PENSION].sum(axis=1)

    # The oldest adult's own status where the person file has one, and the
    # reference person's only where it does not.
    marital = (frame["oldest_marital"].map(MARITAL_CODE)
               .fillna(frame[HH_MARITAL].map(MARITAL_CODE)))
    if marital.isna().any():
        unmapped = sorted(frame.loc[marital.isna(), HH_MARITAL].unique())
        raise ValueError(
            f"Unmapped marital status codes in WAS: {unmapped}. Silently "
            "treating them as single would change the number of nil-rate "
            "bands, so add them to MARITAL_CODE deliberately."
        )
    # Unit size excludes other adults, such as a widowed parent's adult child.
    n_adults = unit_size(marital)

    # Keep raw holdings until finalise() supplies weights for relief remapping.
    business_raw = winsorise(frame["business_raw"],
                             assumptions.business_winsorise_percentile)

    # Calculate purchase-cost home values in survey-date money before uprating.
    gains = housing_gain(
        frame.assign(value_now=frame[HH_RESIDENCE].clip(lower=0)),
        region=frame[HH_REGION],
        age=frame["oldest_band"].map(AGE_BAND_MIDPOINT).astype(float),
        index=load_index(HOUSE_PRICE_INDEX),
        cpi=load_cpi(CPI_INDEX))

    residence = frame[HH_RESIDENCE].clip(lower=0)
    property_wealth = frame[HH_PROPERTY]

    counterfactual_homes = {
        world: counterfactual_home_value(gains, residence, world, assumptions)
        for world in ("nominal", "real")}

    # Remove home gains from total property wealth too.
    mode = assumptions.housing_gain_counterfactual
    if mode != "none":
        counterfactual = counterfactual_homes[mode]
        gain = (residence - counterfactual).clip(lower=0)
        # Cap the deduction at available net property wealth. Gross gains can exceed equity; an uncapped deduction would erase the residence band. Check other property stays unchanged in analysis/housing_gain.py.
        gain = np.minimum(gain, property_wealth.clip(lower=0))
        residence = residence - gain
        property_wealth = property_wealth - gain

    out = pd.DataFrame({
        "unit_id": np.arange(len(frame)),
        # Retain the WAS household ID for joins.
        "case_id": frame.index.to_numpy(),
        "age": frame["oldest_band"].map(AGE_BAND_MIDPOINT).astype(float),
        "age_band_lower": frame["oldest_band"].map(AGE_BAND_LOWER).astype(int),
        "partner_age": np.where(n_adults == 2,
                                frame["oldest_band"].map(AGE_BAND_MIDPOINT) - 2,
                                np.nan),
        "sex": np.where(frame["oldest_sex"] == 2, "female", "male"),
        "marital_status": marital.to_numpy(),
        "n_adults": n_adults.to_numpy(),
        # The map counts all resident adults, including those outside the estate unit.
        "adults_in_household": frame["adults_counted"].to_numpy(),
        "weight": frame[HH_WEIGHT].to_numpy(),
        "region": frame[HH_REGION].to_numpy(),
        "owns_home": (frame[HH_TENURE].isin([1, 2, 3])).to_numpy(),
        "multi_generation": frame["multi_generation"].to_numpy(),
        "members_65_plus_actual": frame["members_65_plus"].to_numpy(),
        "members_band_14": frame["members_band_14"].to_numpy(),
        "members_band_15": frame["members_band_15"].to_numpy(),
        "members_band_16": frame["members_band_16"].to_numpy(),
        "members_band_17": frame["members_band_17"].to_numpy(),
        "property_wealth": property_wealth.to_numpy(),
        # Approximate net home equity by capping gross residence value at net property wealth.
        "residence_value": np.minimum(
            residence, property_wealth.clip(lower=0)).to_numpy(),
        "financial_wealth": frame[HH_FINANCIAL].to_numpy(),
        "physical_wealth_raw": frame[HH_PHYSICAL].to_numpy(),
        "dc_pension_wealth": dc_pension.to_numpy(),
        "dc_pension_cohort_shifted": (
            dc_pension * frame["oldest_band"].map(cohort_ratio).fillna(1.0)
        ).to_numpy(),
        "db_pension_wealth": other_pension.to_numpy(),
        # Keep gains for reporting and coverage checks.
        "housing_gain_counted": gains["housing_gain_counted"].to_numpy(),
        "housing_gain_nominal": (residence - counterfactual_homes["nominal"]
                                 ).clip(lower=0).to_numpy(),
        "housing_gain_real": (residence - counterfactual_homes["real"]
                              ).clip(lower=0).to_numpy(),
        "implied_purchase_year": gains["implied_purchase_year"].to_numpy(),
        # As reported by the survey, before qualification and re-mapping.
        "business_raw": business_raw.to_numpy(),
        "agricultural_raw": frame["agricultural_raw"].to_numpy(),
    })

    # Report the weighted exclusion share.
    multi_share = float(np.average(out["multi_generation"],
                                   weights=out["weight"]))
    notes = [
        f"Wealth and Assets Survey round 8, April 2020 to March 2022.",
        f"Households with an adult aged 65 or over: {len(out):,} unweighted.",
        f"DC pension wealth, which enters estates from April 2027: mean "
        f"£{dc_pension.mean():,.0f} per household.",
        f"DB, annuitised and in-payment pension wealth, excluded because none of "
        f"it is inheritable: mean £{other_pension.mean():,.0f} per household. "
        f"That is {other_pension.mean() / max(1.0, dc_pension.mean()):.1f} times "
        f"the DC figure, which is the scale of the error avoided.",
        f"DC cohort ratios (how much more DC each band holds now than the "
        f"survey recorded, from the band five years younger): "
        f"{ {k: round(v, 2) for k, v in cohort_ratio.items()} }.",
        f"Multi-generational households (more than two adults): "
        f"{multi_share * 100:.1f}% by weight, modelled rather than dropped, "
        f"with the household's wealth credited to the senior members.",
        f"Business property, from the WAS person file and outside its wealth "
        f"aggregates, so it adds to estates: "
        f"{(business_raw > 0).sum():,} households report some. Agricultural "
        f"land, from the household file and already inside net property "
        f"wealth, so identifying it grants relief rather than adding wealth: "
        f"{(frame['agricultural_raw'] > 0).sum():,} report some. Both are "
        f"re-mapped onto the claim distribution HMRC publishes in table 12.2; "
        f"see model/relief_distribution.py.",
    ]

    if assumptions.exclude_multi_generational:
        out = out[~out["multi_generation"]].reset_index(drop=True)
        out["unit_id"] = np.arange(len(out))

    return finalise(UnitsTable(frame=out, backend="was", notes=notes), assumptions)


# Raking repeats until every band is within this of its control total.
RAKING_PASSES = 20
RAKING_TOLERANCE = 1e-6


# Which computed factor each assumption falls back to when it is left at None.
UPRATING_SOURCE = {
    "uprate_property": "uprate_property",
    "uprate_financial": "uprate_other",
    "uprate_pension": "uprate_other",
    "uprate_physical": "uprate_other",
}


@functools.lru_cache(maxsize=1)
def _uprating_factors() -> dict:
    """The measured factors, read once. A sensitivity run asks for these 72 times."""
    if not UPRATING_JSON.exists():
        raise FileNotFoundError(
            f"{UPRATING_JSON} is missing, so there are no uprating factors. "
            f"Run acquire/price_indices.py first.")
    return json.loads(UPRATING_JSON.read_text())


def resolve_uprating(assumptions: ModelAssumptions, field: str) -> float:
    """The factor for one class of wealth: the computed one, or an override.

    Sensitivity runs set these to fixed numbers to answer questions like "what
    if savings had not moved at all". Everything else leaves them at None and
    gets whatever acquire/price_indices.py measured."""
    override = getattr(assumptions, field)
    if override is not None:
        return float(override)
    return float(_uprating_factors()[UPRATING_SOURCE[field]])


def uprating_between_validation_year_and_model_date(
        assumptions: ModelAssumptions) -> float:
    """Consumer price growth from HMRC's 2023-24 tables to the model date.

    Values taken from HMRC's published claim distribution arrive already in
    2023-24 money, so they need the rest of the journey rather than all of it.
    An override on the financial factor means a sensitivity run is deliberately
    holding prices still, and this follows it rather than quietly uprating
    anyway."""
    if assumptions.uprate_financial is not None:
        return float(assumptions.uprate_financial)

    factors = _uprating_factors()
    return factors["uprate_other"] / factors["uprate_other_validation"]


def counterfactual_home_value(gains: pd.DataFrame, residence: pd.Series,
                              mode: str,
                              assumptions: ModelAssumptions) -> pd.Series:
    """What the home would be worth in the counterfactual world, in survey money.

    The counterfactual values come out of housing_gain.py as the purchase price
    carried to the survey window: literally the price paid for the nominal
    world, and the price grown by consumer prices for the real one. Everything
    in this frame is later multiplied by the property factor, which is the one
    index that must not apply here. The whole point of these worlds is that the
    home did not follow house prices, so the counterfactual home would keep
    rising on the house price index in a world where by construction it does
    not.

    So each world's value is divided by the property factor first, and
    multiplied by the index that world actually implies. "Still worth what was
    paid for it" means the cash sum, which does not grow at all. "Only kept pace
    with inflation" means consumer prices all the way to April 2027, not
    consumer prices to 2021 and house prices after that.

    Households with no usable purchase price keep the real home value and
    contribute no gain, so they are left alone."""
    if mode not in ("real", "nominal"):
        raise ValueError(f"Unknown housing counterfactual {mode!r}.")

    value = gains[f"residence_value_if_{mode}"]
    to_model_date = (1.0 if mode == "nominal"
                     else resolve_uprating(assumptions, "uprate_financial"))
    rescaled = value * (to_model_date
                        / resolve_uprating(assumptions, "uprate_property"))

    counted = gains["housing_gain_counted"].to_numpy()
    return pd.Series(np.where(counted, np.minimum(rescaled, residence), residence),
                     index=value.index)


def finalise(units: UnitsTable, assumptions: ModelAssumptions) -> UnitsTable:
    """Apply wealth adjustments, tax bands, child probabilities and population weights."""
    frame = units.frame
    cohort_table = build_cohort_table()

    # The physical wealth haircut, explained at the top of this file.
    frame["physical_wealth"] = frame["physical_wealth_raw"] * (
        1.0 - assumptions.physical_wealth_haircut
    )

    # Bring survey-date money to the model date. Fieldwork ran to March 2022 and
    # the rules we are applying start in April 2027, so a pound in the survey is
    # not a pound against the thresholds. Property moves on house prices, which
    # rose 19% over that stretch. Savings and pension pots move on consumer
    # prices, which rose 31%: the gap is the 2022 energy shock, which showed up
    # in the shops long before it showed up in house prices. Possessions stay
    # where they are, for the reason given in config.py.
    uprate_property = resolve_uprating(assumptions, "uprate_property")
    estate_uprating = blended_estate_uprating(frame, assumptions)
    frame["property_wealth"] *= uprate_property
    frame["residence_value"] *= uprate_property

    # The gains are a difference between two home values that have already been
    # put on the same footing, so they travel with the home.
    for column in ("housing_gain_nominal", "housing_gain_real"):
        if column in frame.columns:
            frame[column] *= uprate_property

    frame["financial_wealth"] *= resolve_uprating(assumptions, "uprate_financial")
    frame["physical_wealth"] *= resolve_uprating(assumptions, "uprate_physical")
    frame["dc_pension_wealth"] *= resolve_uprating(assumptions, "uprate_pension")

    # Pension cohort adjustment, if asked for. See config.py.
    if (assumptions.pension_cohort_basis == "cohort_shifted"
            and "dc_pension_cohort_shifted" in frame.columns):
        frame["dc_pension_wealth"] = frame["dc_pension_cohort_shifted"]

    # Which female birth cohort's fertility applies to each unit.
    cohorts = [
        female_cohort_for_unit(age=int(a), sex=s, has_partner=(n == 2))
        for a, s, n in zip(frame["age"], frame["sex"], frame["n_adults"])
    ]

    p_childless, mean_children, p_child_65 = [], [], []
    for year in cohorts:
        record = children_for_unit(year, cohort_table)
        p_childless.append(record.p_childless)
        mean_children.append(record.mean_living_adult_children)
        p_child_65.append(record.p_child_aged_65_plus)

    frame["female_birth_cohort"] = cohorts
    frame["p_childless"] = p_childless
    frame["mean_living_adult_children"] = mean_children
    frame["p_child_aged_65_plus"] = p_child_65

    # Carry descendant probabilities for expected tax, avoiding random draws.
    frame["p_has_descendants"] = 1.0 - frame["p_childless"]

    bands = [bands_for_marital_status(status, assumptions.widow_transferred_band_share,
                                      assumptions.cohabiting_band_sets)
             for status in frame["marital_status"]]
    frame["nil_rate_bands"] = [b[0] for b in bands]
    frame["residence_bands"] = [b[1] for b in bands]
    frame["apr_bpr_allowances"] = [b[2] for b in bands]
    frame["taper_thresholds"] = [b[3] for b in bands]

    # Population controls count 65-plus people, including both eligible partners.
    if "members_65_plus_actual" in frame.columns:
        frame["members_65_plus"] = frame["members_65_plus_actual"].clip(lower=1)
    else:
        partner_over_65 = (frame["partner_age"].fillna(0) >= HEADLINE_AGE).astype(int)
        frame["members_65_plus"] = 1 + partner_over_65 * (frame["n_adults"] == 2)

    # Post-stratify WAS weights to ONS age totals. The survey is older and excludes communal establishments, with a larger shortfall at older ages.
    from model.population import load_population_by_age

    population = load_population_by_age(year=assumptions.population_year)
    band_ranges = {67: (65, 69), 72: (70, 74), 77: (75, 79), 84: (80, 120)}

    # Rake to every band's control total. A household with members in two bands
    # gets a blend of the two factors, which is not either band's factor, so one
    # pass leaves the bands out by up to three per cent even though the total
    # comes right. Repeating it converges, and the provenance note claims the
    # controls are met, so it had better meet them.
    band_codes = {67: 14, 72: 15, 77: 16, 84: 17}
    members_by_band = {}
    for midpoint in band_ranges:
        column = f"members_band_{band_codes[midpoint]}"
        if column not in frame.columns or not assumptions.post_stratify_per_person:
            # Household-weighted alternative: use the oldest member's band.
            members_by_band[midpoint] = (
                frame["members_65_plus"].where(frame["age"] == midpoint, 0.0))
        else:
            members_by_band[midpoint] = frame[column]

    targets = {midpoint: population[low:min(high + 1, len(population))].sum()
               for midpoint, (low, high) in band_ranges.items()}

    weight = frame["weight"].copy()
    scales = {}
    for _ in range(RAKING_PASSES):
        worst = 0.0
        for midpoint, (low, high) in band_ranges.items():
            members = members_by_band[midpoint]
            represented = (weight * members).sum()
            scale = targets[midpoint] / represented if represented > 0 else 1.0
            scales[f"{low}-{high if high < 120 else '+'}"] = round(scale, 3)
            worst = max(worst, abs(scale - 1.0))

            share = members / frame["members_65_plus"].clip(lower=1)
            weight = weight * (1.0 + share * (scale - 1.0))
        if worst < RAKING_TOLERANCE:
            break
    else:
        raise ValueError(
            f"Post-stratification still out by {worst * 100:.2f}% after "
            f"{RAKING_PASSES} passes. The weights would not meet the "
            f"population controls the provenance note says they meet.")

    frame["weight"] = weight

    # Business and agricultural property, now that the weights are final.
    frame = apply_relief_property(frame, assumptions)

    # Fit the wealth tail using final post-stratified weights.
    frame = apply_pareto_tail(frame, assumptions, estate_uprating)

    achieved = {}
    for midpoint, (low, high) in band_ranges.items():
        column = f"members_band_{band_codes[midpoint]}"
        if column in frame.columns:
            achieved[f"{low}-{high if high < 120 else '+'}"] = round(
                (frame["weight"] * frame[column]).sum() / 1e6, 2)

    units.notes.append(
        f"Weights post-stratified to the ONS mid-2024 population, one age band "
        f"at a time and counted per person rather than per household. Band "
        f"factors: {scales}. Population achieved, millions: {achieved}. The "
        f"factors are largest for the youngest and oldest bands: the survey "
        f"under-covers both, the old because it excludes care homes."
    )

    units.frame = frame
    return units


# Business and agricultural property
def apply_relief_property(frame: pd.DataFrame,
                          assumptions: ModelAssumptions) -> pd.DataFrame:
    """Add business wealth and reclassify agricultural land for relief.

HMRC remapping preserves survey ranks. Land stays within existing property wealth and outside the main home; business assets are additional."""
    if not assumptions.include_business_agricultural:
        zero = pd.Series(0.0, index=frame.index)
        frame["business_property"] = zero
        frame["agricultural_property"] = zero
        frame["business_agricultural"] = zero
        return frame

    business = frame["business_raw"]
    agricultural = frame["agricultural_raw"]

    # This step runs after the rest of the frame has been carried to April 2027,
    # so whatever comes out of it has to be carried there too. The two routes
    # start from different years. The survey's own answers are in survey money
    # like everything else. HMRC's claim bands are 2023-24, so they need only
    # the remaining stretch. Getting this wrong would leave farms and family
    # companies as the one asset class still priced in an earlier decade.
    if assumptions.remap_to_published_distribution:
        business = remap(business, frame["weight"],
                         RELIEF_CLAIMS_2023_24["business"]["bands"])
        agricultural = remap(agricultural, frame["weight"],
                             RELIEF_CLAIMS_2023_24["agricultural"]["bands"])
        carry = uprating_between_validation_year_and_model_date(assumptions)
    else:
        carry = resolve_uprating(assumptions, "uprate_financial")

    business = business * carry
    agricultural = agricultural * carry

    # Land is not inside the survey's net property wealth, so it is not taken
    # out of it. HPropWR8 is the home plus other property less the mortgage on
    # them: for 407 of the 520 land-holding households that identity holds to
    # the pound with land contributing nothing, and for none of them does it
    # hold once land is added in. Treating land as a slice of property wealth
    # subtracted an asset that was never counted, and then capped what was left
    # at the property outside the home, which threw away three quarters of the
    # farmland and left it contributing nothing to any estate.
    frame["business_property"] = business
    frame["agricultural_property"] = agricultural
    frame["business_agricultural"] = business + agricultural
    return frame


# The missing top tail
def blended_estate_uprating(frame: pd.DataFrame,
                            assumptions: ModelAssumptions) -> float:
    """How much the estate base as a whole grows between the survey and the model date.

    Each class of wealth moves on its own index, so there is no single number
    that describes the estate. This is the one the aggregate implies: each
    factor weighted by how much of the estate base that class holds. It exists
    for the Pareto threshold, which needs to name the same point in the
    distribution before and after uprating rather than the same number of
    pounds. Call it before anything in the frame has been uprated."""
    parts = {
        "property_wealth": "uprate_property",
        "financial_wealth": "uprate_financial",
        "physical_wealth": "uprate_physical",
        "dc_pension_wealth": "uprate_pension",
    }
    weight = frame["weight"].to_numpy(dtype=float)
    before = after = 0.0
    for column, field in parts.items():
        total = float((weight * frame[column].to_numpy(dtype=float)).sum())
        before += total
        after += total * resolve_uprating(assumptions, field)
    if before <= 0:
        raise ValueError("The estate base sums to nothing before uprating, so "
                         "there is no factor to derive for the Pareto tail.")
    return after / before


def apply_pareto_tail(frame: pd.DataFrame,
                      assumptions: ModelAssumptions,
                      estate_uprating: float = 1.0) -> pd.DataFrame:
    """Increase top estates using a Pareto tail, preserving ranks and weights. See Advani, Bangham and Leslie (Fiscal Studies, 2021).

This changes wealthy households' assets without adding households. It should raise tax much more than the share exposed.

The threshold is a point in the distribution Advani, Bangham and Leslie fitted, not a number of pounds. The frame reaching this function has been carried to the model date, so the threshold is carried with it. Left nominal it would quietly reach further down the distribution every year prices rise, and the correction would grow for no reason connected to what the survey misses."""
    if not assumptions.apply_pareto_tail:
        frame["pareto_uplift"] = 0.0
        return frame

    threshold = assumptions.pareto_threshold * estate_uprating
    alpha = assumptions.pareto_alpha

    estate = (frame["property_wealth"] + frame["financial_wealth"]
              + frame["physical_wealth"] + frame["dc_pension_wealth"]
              + frame.get("business_agricultural", 0.0))

    above = estate > threshold
    if above.sum() < 20:
        frame["pareto_uplift"] = 0.0
        return frame

    # Rank households above the threshold, richest first, by weighted count.
    subset = frame.loc[above].copy()
    subset["estate"] = estate[above]
    subset = subset.sort_values("estate", ascending=False)

    weights = subset["weight"].to_numpy()
    # Use the midpoint of each household's weight.
    cumulative = np.cumsum(weights) - weights / 2.0
    total_above = weights.sum()

    # Pareto survival implies value = threshold * (N / rank) ** (1 / alpha).
    fitted = threshold * (total_above / cumulative) ** (1.0 / alpha)

    # Keep reported estate values as floors.
    uplift = np.maximum(fitted - subset["estate"].to_numpy(), 0.0)

    frame["pareto_uplift"] = 0.0
    frame.loc[subset.index, "pareto_uplift"] = uplift

    # Assign added wealth to financial assets without relief. This overstates tax where missing wealth would qualify for business relief.
    frame["financial_wealth"] = frame["financial_wealth"] + frame["pareto_uplift"]

    return frame


def load_units() -> UnitsTable:
    """Load and finalise WAS units; fail if microdata is missing."""
    return load_was_units()


def main() -> None:
    units = load_units()
    units.frame.to_parquet(UNITS_PATH)

    lines = [f"backend: {units.backend}"] + units.notes
    PROVENANCE_PATH.write_text("\n".join(lines))

    frame = units.frame
    print()
    for note in units.notes:
        print(f"  {note}")
    print(f"\n  Written to {UNITS_PATH}")
    print(f"  Units: {len(frame):,}")
    print(f"  Couples: {(frame['n_adults'] == 2).mean() * 100:.0f}%  "
          f"Widowed: {(frame['marital_status'] == 'Widowed').mean() * 100:.0f}%")
    print(f"  Median total wealth: £{(frame['property_wealth'] + frame['financial_wealth'] + frame['physical_wealth'] + frame['dc_pension_wealth']).median():,.0f}")
    print(f"  Mean living adult children per unit: "
          f"{frame['mean_living_adult_children'].mean():.2f}")
    print(f"  Represents {frame['weight'].sum() / 1e6:.2f}m units containing "
          f"{(frame['weight'] * frame['members_65_plus']).sum() / 1e6:.2f}m "
          f"people aged {HEADLINE_AGE}+ and "
          f"{(frame['weight'] * frame['n_adults']).sum() / 1e6:.2f}m adults in total.")


if __name__ == "__main__":
    main()
