# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Estimate constituency exposure by fitting wealth multipliers to HMRC estate rates.

Run the national sample over a wealth grid under both outturn and headline rules. Match each seat's observed death-basis rate, then read its living-household exposure. Calibrate the baseline so seat shares average to the national result.

Impute suppressed seats from regional residuals and flag them. Coverage is England and Wales; the population source excludes Scotland and WAS excludes Northern Ireland. The map counts local households and their resident adults, not children living elsewhere.

Assumes geographical differences in deaths track wealth. Local mortality differences and the impact of post-2023-24 rule changes are not identified by HMRC's outturn."""

from __future__ import annotations

import csv
import json
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import (ADULT_AGE, BILL_BANDS, BUILD, HEADLINE_RULES, OUTPUT,
                        RULES_2023_24)
from model.population import load_population_by_age
from model.validate import deaths_at_65_plus
from model.build_units import UNITS_PATH
from model.simulate import run_scenario
from model.validate import band_mortality_rates

HMRC_DIR = BUILD / "hmrc"
POP_DIR = BUILD / "constituency"

# Use seat-level HMRC counts where population denominators exist. NOMIS covers England and Wales; WAS excludes Northern Ireland.
MAPPED_REGIONS = {
    "North East", "North West", "Yorkshire and the Humber", "East Midlands",
    "West Midlands", "East of England", "London", "South East", "South West",
    "Wales",
}

# Include exactly 1.0 to reproduce the national result. Clamp and flag rates outside the grid.
WEALTH_GRID = np.unique(np.concatenate([
    np.exp(np.linspace(np.log(0.25), np.log(5.0), 45)), [1.0]]))


def _adult_conversion() -> float:
    """Ratio of the 18+ population to the 16+ population, nationally."""
    population = load_population_by_age()
    return float(population[ADULT_AGE:].sum() / population[16:].sum())


ADULT_CONVERSION = _adult_conversion()


@dataclass
class Seat:
    name: str
    code: str
    region: str
    population_65_plus: float
    population_adults: float
    liable_estates: float
    imputed: bool

    @property
    def liable_per_1000_over_65(self) -> float:
        if self.population_65_plus <= 0:
            return 0.0
        return self.liable_estates / self.population_65_plus * 1000.0


# Loading
def normalise(name: str) -> str:
    """Normalise punctuation, conjunctions and accents for cross-source name matching."""
    name = unicodedata.normalize("NFKD", name.lower().replace("&", " and "))
    return "".join(ch for ch in name
                   if ch.isalnum() and not unicodedata.combining(ch))


def load_population(measure: str) -> dict[str, tuple[str, float]]:
    path = POP_DIR / f"population_pcon2010_{measure}.csv"
    out: dict[str, tuple[str, float]] = {}
    for row in csv.DictReader(path.open()):
        out[normalise(row["GEOGRAPHY_NAME"])] = (
            row["GEOGRAPHY_CODE"], float(row["OBS_VALUE"])
        )
    return out


def load_hmrc_constituencies() -> tuple[list[dict], dict[str, float]]:
    """Read Table 12.9 seat counts and regional totals. Blank counts are suppressed, not zero."""
    path = HMRC_DIR / "table_12_9.csv"
    rows: list[dict] = []
    regional_totals: dict[str, float] = {}

    with path.open() as handle:
        for record in csv.reader(handle):
            if len(record) < 3 or record[0] in ("Region", "") or record[0].startswith("["):
                continue
            region, seat, number = record[0], record[1], record[2]
            if region not in MAPPED_REGIONS:
                continue
            if seat == "All":
                if not number:
                    raise ValueError(
                        f"HMRC gives no total for {region!r}. Every suppressed "
                        f"seat in it would be handed a residual of zero, come "
                        f"out at the floor of the wealth grid, and be "
                        f"published as a share of about 1.7%.")
                regional_totals[region] = float(number)
                continue
            rows.append({"region": region, "name": seat,
                         "count": float(number) if number else None})

    return rows, regional_totals


def load_regional_totals() -> dict[str, float]:
    path = HMRC_DIR / "table_12_8.csv"
    out: dict[str, float] = {}
    with path.open() as handle:
        for record in csv.reader(handle):
            if len(record) < 2 or record[0].startswith("["):
                continue
            try:
                out[record[0]] = float(record[1])
            except ValueError:
                continue
    return out


def build_seats() -> list[Seat]:
    pop65 = load_population("aged_65_plus")
    pop_adults = load_population("aged_16_plus")
    hmrc_rows, regional_totals = load_hmrc_constituencies()
    all_regions = load_regional_totals()

    seats: list[Seat] = []

    for region in sorted(MAPPED_REGIONS):
        in_region = [r for r in hmrc_rows if r["region"] == region]
        known = [r for r in in_region if r["count"] is not None]
        suppressed = [r for r in in_region if r["count"] is None]

        residual = max(0.0, regional_totals.get(region, 0.0)
                       - sum(r["count"] for r in known))
        suppressed_pop = sum(pop65[normalise(r["name"])][1]
                             for r in suppressed if normalise(r["name"]) in pop65)

        for row in in_region:
            key = normalise(row["name"])
            if key not in pop65:
                raise KeyError(
                    f"No 65-plus population for {row['name']!r}. Dropping the "
                    f"seat here would leave it off the map with no complaint, "
                    f"which is the same failure the adult check below refuses.")
            code, p65 = pop65[key]
            if key not in pop_adults:
                raise ValueError(
                    f"No adult population for {row['name']}. Inventing one "
                    "would publish a share of a made-up denominator."
                )
            # Convert NOMIS 16-plus population to 18-plus using the national ratio.
            adults = pop_adults[key][1] * ADULT_CONVERSION

            if row["count"] is not None:
                count, imputed = row["count"], False
            elif suppressed_pop > 0:
                count, imputed = residual * p65 / suppressed_pop, True
            elif residual > 0:
                raise ValueError(
                    f"{region} has {residual:,.0f} estates left over after the "
                    f"published seats, but no suppressed seat with a population "
                    f"to spread them across. Assigning zero would publish a "
                    f"floor-of-the-grid share for a seat that has estates.")
            else:
                count, imputed = 0.0, True

            seats.append(Seat(row["name"], code, region, p65, adults, count, imputed))

    return seats


# The wealth-multiplier grid
def bill_distribution(units: pd.DataFrame, liabilities: pd.DataFrame) -> list[float]:
    """Expected household shares by bill band, weighting both descendant branches. The first band is zero tax."""
    weight = units["weight"].to_numpy()
    with_desc = liabilities["tax_with_descendants"].to_numpy()
    without_desc = liabilities["tax_without_descendants"].to_numpy()
    p_desc = units["p_has_descendants"].to_numpy()

    edges = list(BILL_BANDS) + [float("inf")]
    shares = []

    # Band 0 contains bills of exactly zero.
    shares.append(float((weight * (p_desc * (with_desc <= 0)
                                   + (1 - p_desc) * (without_desc <= 0))).sum()))

    for low, high in zip(edges[:-1], edges[1:]):
        def inside(tax):
            return (tax > low) & (tax <= high) if high != float("inf") else (tax > low)
        shares.append(float((weight * (p_desc * inside(with_desc)
                                       + (1 - p_desc) * inside(without_desc))).sum()))

    total = sum(shares)
    return [value / total for value in shares] if total else shares


# Sensitivity thresholds for the swing model, as a bill expressed in percent of
# the estate. A bill of £50,000 means one thing against an estate of £1.1m and
# something else entirely against £5m, so the swing model asks how big a bill
# has to be relative to what the household owns before anyone would notice.
WEALTH_SHARE_THRESHOLDS = tuple(range(1, 21))

# Children counted twice come off, exactly as they do in the headline: those
# claimed by two liable units because their divorced parents both re-partnered,
# and those who are themselves 65 or over and already in the pensioner count.
# analysis/headline.py measures both; this is the ratio they leave behind.
CHILD_NET_RATIO = 0.957


def members_above_thresholds(units: pd.DataFrame,
                             liabilities: pd.DataFrame) -> dict[int, float]:
    """Share of unit members whose bill exceeds each threshold.

    Members, not units, because the swing model counts voters and a couple has
    two of them. Both branches are averaged by the probability of having
    descendants, as everywhere else.

    The denominator is all unit members in over-65 units, so the result scales
    straight onto a seat's population aged 65 and over.
    """
    members = (units["weight"] * units["n_adults"]).to_numpy(dtype=float)
    gross = liabilities["gross_estate"].to_numpy(dtype=float)
    p_desc = units["p_has_descendants"].to_numpy(dtype=float)
    safe = np.where(gross > 0, gross, np.inf)

    with_desc = liabilities["tax_with_descendants"].to_numpy() / safe
    without_desc = liabilities["tax_without_descendants"].to_numpy() / safe

    total = members.sum()
    out = {}
    for threshold in WEALTH_SHARE_THRESHOLDS:
        cut = threshold / 100.0
        above = (p_desc * (with_desc > cut) + (1 - p_desc) * (without_desc > cut))
        out[threshold] = float((members * above).sum() / total) if total else 0.0
    return out


def children_above_thresholds(units: pd.DataFrame,
                              liabilities: pd.DataFrame) -> dict[int, float]:
    """Adult children of exposed units, per person aged 65 and over.

    Only the descendants branch is used, because a unit with no children has no
    children to count. The result is scaled by the 65-plus population rather
    than by units, so a seat's figure is its own population times this.

    The netting down matches the headline: children claimed by two separate
    liable units, because their divorced parents both re-partnered, and
    children who are themselves 65 or over and already counted as pensioners.
    """
    weight = units["weight"].to_numpy(dtype=float)
    p_desc = units["p_has_descendants"].to_numpy(dtype=float)
    # mean_living_adult_children averages the childless in at zero, so it is
    # already an unconditional figure. Dividing by the chance of having any
    # gives the count for a unit that does have children, which is the branch
    # this function is counting. Multiplying the unconditional figure by p_desc
    # instead would apply that probability a second time and lose 15% of the
    # children. headline.py does the same two steps, and the two have to agree.
    has_children = np.clip(1.0 - units["p_childless"].to_numpy(dtype=float), 0.01, None)
    children = (units["mean_living_adult_children"].to_numpy(dtype=float)
                / has_children)
    gross = liabilities["gross_estate"].to_numpy(dtype=float)
    safe = np.where(gross > 0, gross, np.inf)
    ratio = liabilities["tax_with_descendants"].to_numpy() / safe

    over_65 = (weight * units["members_65_plus"]).sum()
    out = {}
    for threshold in WEALTH_SHARE_THRESHOLDS:
        above = ratio > threshold / 100.0
        total = float((weight * p_desc * children * above).sum())
        out[threshold] = total * CHILD_NET_RATIO / over_65 if over_65 else 0.0
    return out


def build_grid(units: pd.DataFrame) -> pd.DataFrame:
    """Compute living-household exposure under headline rules and annual taxable estates under HMRC outturn rules at each wealth multiplier."""
    band_rates = band_mortality_rates()
    qx = units["age"].map(band_rates).astype(float)

    # Match validate.py: spouse-exempt married deaths; one combined bill per cohabiting unit.
    exposure = (units["marital_status"] != "Married").astype(float)

    # Apply validate.py's marital mortality correction to match the ONS share of deaths leaving no spouse.
    from model.administrative import deaths_65_plus_by_marital_status

    by_status = deaths_65_plus_by_marital_status()
    observed_share = (
        sum(v for k, v in by_status.items() if k != "Married or civil partnered")
        / sum(by_status.values()))
    all_deaths = deaths_at_65_plus(units.assign(_qx=qx))
    modelled_share = (units["weight"] * qx * exposure).sum() / all_deaths
    marital_correction = observed_share / modelled_share

    wealth_columns = ["property_wealth", "residence_value", "financial_wealth",
                      "physical_wealth", "dc_pension_wealth"]

    rows = []
    for multiplier in WEALTH_GRID:
        scaled = units.copy()
        for column in wealth_columns:
            scaled[column] = scaled[column] * multiplier

        today = run_scenario(scaled, HEADLINE_RULES)
        at_death = run_scenario(scaled, RULES_2023_24)

        weight = scaled["weight"]
        share_today = (weight * today["p_over_0"].to_numpy()).sum() / weight.sum()

        deaths = weight * qx * exposure * marital_correction
        estates = (deaths * at_death["p_over_0"].to_numpy()).sum()
        per_1000 = estates / (weight * scaled["members_65_plus"]).sum() * 1000.0

        row = {"multiplier": multiplier,
               "share_liable_today": share_today,
               "liable_estates_per_1000_over_65": per_1000}
        for index, share in enumerate(bill_distribution(scaled, today)):
            row[f"band_{index}"] = share
        for threshold, share in members_above_thresholds(scaled, today).items():
            row[f"members_over_{threshold}pc"] = share
        for threshold, per in children_above_thresholds(scaled, today).items():
            row[f"children_over_{threshold}pc"] = per
        rows.append(row)

    return pd.DataFrame(rows)


def multiplier_for_rate(grid: pd.DataFrame, observed_rate: float) -> float:
    """Interpolate the multiplier matching HMRC's estate rate. Values outside the grid clamp to an endpoint; the caller flags them."""
    x = grid["liable_estates_per_1000_over_65"].to_numpy()
    y = grid["multiplier"].to_numpy()
    order = np.argsort(x)
    return float(np.interp(observed_rate, x[order], y[order]))


def is_outside_grid(grid: pd.DataFrame, observed_rate: float) -> bool:
    rates = grid["liable_estates_per_1000_over_65"]
    return bool(observed_rate < rates.min() or observed_rate > rates.max())


def share_for_multiplier(grid: pd.DataFrame, multiplier: float) -> float:
    return float(np.interp(multiplier, grid["multiplier"].to_numpy(),
                           grid["share_liable_today"].to_numpy()))


def bands_for_multiplier(grid: pd.DataFrame, multiplier: float) -> list[float]:
    """The whole distribution of bill sizes at a given wealth level."""
    columns = [c for c in grid.columns if c.startswith("band_")]
    columns.sort(key=lambda c: int(c.split("_")[1]))
    return [float(np.interp(multiplier, grid["multiplier"].to_numpy(),
                            grid[c].to_numpy())) for c in columns]


def main() -> None:
    units = pd.read_parquet(UNITS_PATH)
    grid = build_grid(units)

    national_share = share_for_multiplier(grid, 1.0)
    national_rate = float(np.interp(
        1.0, grid["multiplier"].to_numpy(),
        grid["liable_estates_per_1000_over_65"].to_numpy()))

    seats = build_seats()

    # Apply the national households-per-65-plus-person ratio to every seat.
    households_per_over_65 = (units["weight"].sum()
                              / (units["weight"] * units["members_65_plus"]).sum())
    # Use adult counts among liable households.
    liability = run_scenario(units, HEADLINE_RULES)["p_over_0"].to_numpy()
    liable_weight = units["weight"].to_numpy() * liability
    # Count resident adults, including adult children and excluding absent separated spouses.
    adults_per_liable_household = (
        (liable_weight * units["adults_in_household"].to_numpy()).sum()
        / liable_weight.sum())

    # Find the multiplier reproducing HMRC's national rate before comparing local wealth levels.
    hmrc_national_rate = (sum(s.liable_estates for s in seats)
                          / sum(s.population_65_plus for s in seats) * 1000.0)

    # Solve the baseline so population-weighted seat exposure matches the national result. Non-linearity prevents a simple rate rescaling.
    def weighted_average_share(baseline: float) -> float:
        total = weight = 0.0
        for seat in seats:
            share = share_for_multiplier(
                grid, multiplier_for_rate(grid, seat.liable_per_1000_over_65) / baseline)
            total += share * seat.population_65_plus
            weight += seat.population_65_plus
        return total / weight if weight else 0.0

    low, high = 0.30, 3.0
    for _ in range(40):
        baseline_multiplier = (low + high) / 2.0
        if weighted_average_share(baseline_multiplier) < national_share:
            high = baseline_multiplier      # a smaller baseline lifts the shares
        else:
            low = baseline_multiplier
    baseline_multiplier = (low + high) / 2.0

    naive_baseline = multiplier_for_rate(grid, hmrc_national_rate)

    rows = []
    for seat in seats:
        raw = multiplier_for_rate(grid, seat.liable_per_1000_over_65)
        multiplier = raw / baseline_multiplier
        share = share_for_multiplier(grid, multiplier)

        households = seat.population_65_plus * households_per_over_65
        adults_in_liable = households * share * adults_per_liable_household

        bands = bands_for_multiplier(grid, multiplier)
        rows.append({
            "constituency": seat.name,
            "code": seat.code,
            "region": seat.region,
            "population_65_plus": round(seat.population_65_plus),
            "hmrc_liable_estates_2023_24": round(seat.liable_estates, 1),
            "hmrc_suppressed_and_imputed": "yes" if seat.imputed else "no",
            "liable_per_1000_over_65": round(seat.liable_per_1000_over_65, 3),
            "implied_wealth_multiplier": round(multiplier, 3),
            "outside_modelled_range": "yes" if is_outside_grid(
                grid, seat.liable_per_1000_over_65) else "no",
            "pct_over_65_households_facing_a_bill": round(share * 100, 1),
            "pct_of_adults_living_in_such_a_household": round(
                adults_in_liable / seat.population_adults * 100, 1),
            **{f"pct_band_{i}": round(v * 100, 2) for i, v in enumerate(bands)},
        })

    frame = pd.DataFrame(rows).sort_values(
        "pct_over_65_households_facing_a_bill", ascending=False)
    path = OUTPUT / "constituency_exposure.csv"
    frame.to_csv(path, index=False)

    # The grid itself, so analysis/swing_model.py can interpolate off it without
    # re-running the tax over every unit at every wealth multiplier again.
    grid_path = BUILD / "wealth_grid.csv"
    grid_path.parent.mkdir(parents=True, exist_ok=True)
    grid.to_csv(grid_path, index=False)
    print(f"Written to {grid_path}")

    weighted = float(np.average(frame["pct_over_65_households_facing_a_bill"],
                                weights=frame["population_65_plus"]))
    print(f"\nNational, from the survey model:        "
          f"{national_share * 100:.1f}% of over-65 households face a bill")
    print(f"Average across seats, population weighted: {weighted:.1f}%")
    gap = abs(national_share * 100 - weighted)
    if gap < 0.15:
        print("  The two agree, which they should: the seats are a decomposition")
        print("  of the national figure, not an independent estimate of it.")
    else:
        print(f"  The two differ by {gap:.1f} points. Some difference is expected,")
        print("  because the seats cover England and Wales while the national")
        print("  figure covers Great Britain, and averaging an S-shaped function")
        print("  across seats does not return its value at the average. More than")
        print("  about half a point would be worth investigating.")
    print(f"\nModel death-basis rate at national wealth: {national_rate:.2f} "
          f"per 1,000 over-65s")
    print(f"HMRC recorded rate, England and Wales:     {hmrc_national_rate:.2f}")
    print(f"Baseline that reproduces HMRC's own rate:  {naive_baseline:.3f}")
    print(f"Baseline solved to match the headline:     {baseline_multiplier:.3f}"
          f"   (used, so the map decomposes the headline)")
    # Write the national readout directly; averaging seat results would give a different bill distribution.
    national_bands = [float(np.interp(1.0, grid["multiplier"].to_numpy(),
                                      grid[f"band_{i}"].to_numpy()))
                      for i in range(len(BILL_BANDS) + 1)]
    (OUTPUT / "national_summary.json").write_text(json.dumps({
        "h": round(national_share * 100, 1),
        "bands": [round(v * 100, 2) for v in national_bands],
    }))

    print(f"\nSeats: {len(frame)}  "
          f"({(frame['hmrc_suppressed_and_imputed'] == 'yes').sum()} imputed)")
    print(f"Written to {path}\n")

    real = frame[frame["hmrc_suppressed_and_imputed"] == "no"]

    print("Most exposed seats (HMRC published figures only)\n")
    print(f"  {'':34} {'wealth':>7} {'% of 65+':>9} {'% of adults':>12}")
    print(f"  {'':34} {'vs nat':>7} {'liable':>9} {'in such a hh':>12}")
    for _, r in real.head(12).iterrows():
        print(f"  {r['constituency'][:32]:<34} {r['implied_wealth_multiplier']:>7.2f}"
              f" {r['pct_over_65_households_facing_a_bill']:>8.1f}%"
              f" {r['pct_of_adults_living_in_such_a_household']:>11.1f}%")

    print("\nLeast exposed of the published seats. The genuinely lowest are all")
    print("suppressed, so this is a floor on the published data.\n")
    for _, r in real.tail(8).iterrows():
        print(f"  {r['constituency'][:32]:<34} {r['implied_wealth_multiplier']:>7.2f}"
              f" {r['pct_over_65_households_facing_a_bill']:>8.1f}%"
              f" {r['pct_of_adults_living_in_such_a_household']:>11.1f}%")


if __name__ == "__main__":
    main()
