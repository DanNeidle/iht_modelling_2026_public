# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Compare modelled wealth with ONS aggregates and a simulated year of deaths with HMRC.

The living-estate model omits subsequent spending, gifts and planning. Report its gap from observed estates without fitting it away. Compare over-65s only and use total deaths as the denominator: reporting rules changed for non-taxpaying estates in 2022.

This rebuilds the units from the survey files rather than reading the parquet, because it needs them valued in HMRC's year rather than the model's. Inside run_all.py that costs nothing; run on its own it needs the raw WAS data and takes a couple of minutes."""

from __future__ import annotations

import csv
import dataclasses
import json
import sys
from pathlib import Path

import openpyxl
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import (ASSUMPTIONS, BUILD, LIFE_TABLES_XLSX, RULES_2023_24,
                        VALIDATION_2023_24, VALIDATION_WINDOW)
from model.build_units import AGE_BAND_MIDPOINT, UPRATING_JSON

HMRC_DIR = BUILD / "hmrc"

# The population year that goes with VALIDATION_WINDOW. The ONS estimates are
# based on mid-2024, which is inside the 2023-24 tax year, so this needs no
# projection at all.
VALIDATION_POPULATION_YEAR = 2024


def load_mortality_rates(path: Path = LIFE_TABLES_XLSX) -> dict[int, float]:
    """Annual mortality by age from the latest ONS period table. A simple sex average slightly overstates deaths in the mostly female 65-plus population."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    years = sorted(int(n) for n in wb.sheetnames if n.strip().isdigit())
    ws = wb[str(years[-1])]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header_idx = next(i for i, r in enumerate(rows)
                      if r and str(r[0]).strip().lower() == "age")
    header = [str(c).strip().lower() if c is not None else "" for c in rows[header_idx]]

    male_qx = header.index("qx")
    male_lx = header.index("lx")
    female_age = header.index("age", male_qx)
    female_qx = header.index("qx", female_age)
    female_lx = header.index("lx", female_age)

    out: dict[int, float] = {}
    survivors: dict[int, tuple[float, float]] = {}
    per_sex: dict[int, tuple[float, float]] = {}
    for r in rows[header_idx + 1:]:
        if not r or not isinstance(r[0], (int, float)):
            continue
        age = int(r[0])
        out[age] = (float(r[male_qx]) + float(r[female_qx])) / 2.0
        per_sex[age] = (float(r[male_qx]), float(r[female_qx]))
        survivors[age] = (float(r[male_lx]), float(r[female_lx]))

    # The population series stops at a 90-plus bucket, so that bucket needs the
    # average rate for everybody in it rather than the rate for someone who has
    # just turned 90. The two are a long way apart: 0.15 against 0.21, because
    # the group runs to well past 100. Weight each single-year rate above 90 by
    # the life table's own survivors, which is the same population weighting the
    # other bands get, using the only population figures that exist that far up.
    out[OPEN_AGE_BAND] = open_band_rate(per_sex, survivors)
    return out


# Where the ONS population series stops splitting by single year.
OPEN_AGE_BAND = 90


def open_band_rate(per_sex: dict[int, tuple[float, float]],
                   survivors: dict[int, tuple[float, float]]) -> float:
    """The average annual mortality of everyone aged 90 and over, by sex then averaged.

    Truncating at the top of the table loses the handful still alive past 100 and so shades the rate down a little. It is worth about a thousandth, against the 40% error from using the single-year rate."""
    ages = sorted(a for a in per_sex if a >= OPEN_AGE_BAND)
    if not ages:
        raise ValueError("The life table has no ages at or above "
                         f"{OPEN_AGE_BAND}, so the open band has no rate.")
    rates = []
    for sex in (0, 1):
        weight = sum(survivors[a][sex] for a in ages)
        if weight <= 0:
            raise ValueError("No survivors above "
                             f"{OPEN_AGE_BAND} in the life table.")
        rates.append(sum(survivors[a][sex] * per_sex[a][sex] for a in ages) / weight)
    return sum(rates) / len(rates)


AGE_65_PLUS_COLUMNS = ("65 to 74 total", "75 to 84 total", "85+ total")


def sum_wanted_columns(header: list, values: list, path) -> float:
    """Add up the three 65-plus columns, and stop if any of them is not there.

    Matching on a label and quietly skipping what does not match is how a
    renamed or newly suppressed column turns into a smaller total with no
    complaint. Losing the 85-plus column alone would take the published ratio
    from 1.6 to 3.6, and losing the youngest would take it somewhere that still
    looks perfectly reasonable."""
    seen, total = set(), 0.0
    for label, value in zip(header, values):
        name = label.split("[")[0].strip()
        if name in AGE_65_PLUS_COLUMNS:
            if not value:
                raise ValueError(f"Column {name!r} in {path} is empty, so the "
                                 f"65-plus total would be short by a band.")
            seen.add(name)
            total += float(value)
    missing = set(AGE_65_PLUS_COLUMNS) - seen
    if missing:
        raise ValueError(f"{path} has no column for {sorted(missing)}. The "
                         f"table's headings have changed, and the total would "
                         f"silently come back too small.")
    return total


def hmrc_taxpaying_estates_aged_65_plus() -> float:
    """2023-24 taxpaying estates aged 65-plus, summed from HMRC Table 12.5c."""
    path = HMRC_DIR / "table_12_5.csv"
    with path.open() as handle:
        records = list(csv.reader(handle))

    # Find the combined sheet, then its header and the row of estate counts.
    start = next(i for i, r in enumerate(records)
                 if r and r[0].startswith("[Sheet] 12_5c"))
    header = next(r for r in records[start:] if r and r[0] == "Asset Type")
    counts = next(r for r in records[start:]
                  if r and r[0].startswith("Number of estates containing a gross capital"))

    return sum_wanted_columns(header, counts, path) * GB_SHARE_OF_UK_ESTATES


def hmrc_tax_aged_65_plus() -> float:
    """2023-24 tax on estates aged 65-plus, in pounds, from the same HMRC table as estate counts."""
    path = HMRC_DIR / "table_12_5.csv"
    rows = list(csv.reader(path.open()))
    start = next(i for i, r in enumerate(rows)
                 if r and r[0].startswith("[Sheet] 12_5c"))
    header = next(r for r in rows[start:] if r and r[0] == "Asset Type")
    liability = next(r for r in rows[start:]
                     if r and r[0].startswith("Tax liability"))

    return (sum_wanted_columns(header, liability, path)
            * 1e6 * GB_SHARE_OF_UK_ESTATES)


# Convert UK HMRC totals to GB. Table 12.8 has 352 Northern Irish and 2,170 unknown-address estates out of 30,400. Use the midpoint of GB shares 91.8% to 98.8%, reflecting uncertainty over unknown addresses. Only validation uses this adjustment.
GB_SHARE_OF_UK_ESTATES = 0.953


def band_mortality_rates() -> dict[int, float]:
    """Population-weighted mortality within each survey age band, including the open 80-plus band."""
    from model.population import load_population_by_age

    population = load_population_by_age()
    mortality = load_mortality_rates()
    oldest_age = max(mortality)

    bands = {14: (65, 69), 15: (70, 74), 16: (75, 79), 17: (80, 120)}
    midpoint_to_band = {67: 14, 72: 15, 77: 16, 84: 17}

    out: dict[int, float] = {}
    for band, (low, high) in bands.items():
        top = min(high, len(population) - 1)
        weights, rates = [], []
        for age in range(low, top + 1):
            weights.append(population[age])
            rates.append(mortality[min(age, oldest_age)])
        total = sum(weights)
        out[band] = sum(w * r for w, r in zip(weights, rates)) / total

    return {midpoint: out[band] for midpoint, band in midpoint_to_band.items()}



def deaths_at_65_plus(units: pd.DataFrame) -> float:
    """Sum weighted 65-plus deaths using each member's own age band and mortality rate. Exclude younger partners."""
    band_rates = band_mortality_rates()
    weight = units["weight"].to_numpy(dtype=float)
    total = 0.0
    for band, midpoint in AGE_BAND_MIDPOINT.items():
        column = f"members_band_{band}"
        if column not in units.columns:
            raise KeyError(
                f"{column} missing from the units file. The per-band member"
                " counts are what make this denominator hit the ONS control"
                " total, so there is no sensible fallback.")
        total += float((weight * units[column].to_numpy() * band_rates[midpoint]).sum())
    return total
def simulate_year_of_deaths(units: pd.DataFrame, liabilities: pd.DataFrame,
                            scenario: str = "validation_2023_24") -> dict:
    """Turn the stock of units into a year's flow of taxable estates."""
    mortality = load_mortality_rates()
    oldest = max(mortality)
    band_rates = band_mortality_rates()

    subset = liabilities[liabilities["scenario"] == scenario].set_index("unit_id")
    joined = units.set_index("unit_id").join(
        subset[["p_over_0", "expected_tax"]]
    )

    # Assume married first deaths are spouse-exempt. Count each cohabiting unit once because its bill combines two estates; this understates estate counts by one per pair.
    not_married = joined["marital_status"] != "Married"
    exposure = not_married.astype(float)

    # Adjust mortality to match ONS's 62.1% share of 65-plus deaths leaving no spouse.
    from model.administrative import deaths_65_plus_by_marital_status

    by_status = deaths_65_plus_by_marital_status()
    total_deaths = sum(by_status.values())
    observed_not_married_share = (
        sum(v for k, v in by_status.items() if k != "Married or civil partnered")
        / total_deaths
    )

    # Use band rates for survey midpoints, single-year rates otherwise.
    qx = joined["age"].map(band_rates)
    qx = qx.fillna(joined["age"].clip(upper=oldest).map(mortality)).astype(float)

    uncorrected = joined["weight"] * qx * exposure
    joined["_qx"] = qx
    all_deaths = deaths_at_65_plus(joined)
    modelled_share = uncorrected.sum() / all_deaths

    # Scale unmarried deaths to the observed ONS share.
    deaths = uncorrected * (observed_not_married_share / modelled_share)
    taxable_estates = (deaths * joined["p_over_0"]).sum()
    tax_collected = (deaths * joined["expected_tax"]).sum()

    return {
        "not_married_share_observed": observed_not_married_share,
        "not_married_share_before_correction": modelled_share,
        "modelled_deaths_65_plus_final": float(deaths.sum()),
        "modelled_taxpaying_estates": float(taxable_estates),
        "modelled_tax_gbp": float(tax_collected),
    }


def units_at_validation_prices() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rebuild the model with wealth valued in the year HMRC's outturn covers.

    The model puts everything in April 2027 money, because that is when the
    rules being modelled start. HMRC's figures are for 2023-24. Compare the two
    directly and a fifth of the gap is just the inflation between those dates,
    which tells you nothing about how estates shrink before death. So for this
    one purpose we wind the clock back."""
    from model import build_units as bu
    from model.simulate import run_scenario

    if not UPRATING_JSON.exists():
        raise FileNotFoundError(
            f"{UPRATING_JSON} is missing, so the validation year's price level "
            f"is unknown. Run acquire/price_indices.py first.")
    factors = json.loads(UPRATING_JSON.read_text())

    assumptions = dataclasses.replace(
        ASSUMPTIONS,
        uprate_property=factors["uprate_property_validation"],
        uprate_financial=factors["uprate_other_validation"],
        uprate_pension=factors["uprate_other_validation"],
        # Possessions are held still in the headline model, so they are held
        # still here too. Re-basing them would move them the wrong way.
        uprate_physical=ASSUMPTIONS.uprate_physical,
        # Prices are not the only thing three years out. The 65-plus population
        # grows 6% between HMRC's year and the model's, so the weights have to
        # come back too or the comparison banks that growth as decumulation.
        population_year=VALIDATION_POPULATION_YEAR)

    units = bu.load_was_units(assumptions).frame
    liabilities = run_scenario(units, RULES_2023_24)
    liabilities["scenario"] = "validation_2023_24"
    return units, liabilities


def main() -> int:
    units, liabilities = units_at_validation_prices()
    modelled = simulate_year_of_deaths(units, liabilities)
    hmrc_65_plus = hmrc_taxpaying_estates_aged_65_plus()

    print()
    print("  Validation against HMRC 2023-24 outturn\n")
    print(f"    Wealth re-based to {VALIDATION_WINDOW[0]} to "
          f"{VALIDATION_WINDOW[1]}, the year HMRC's figures cover,")
    print("    so that the gap below is about estates and not about inflation.\n")
    print(f"    HMRC taxpaying estates, all ages      "
          f"{VALIDATION_2023_24['taxpaying_estates']:>10,}")
    print(f"    HMRC taxpaying estates, aged 65+, GB  {hmrc_65_plus:>10,.0f}"
          f"   <- the comparable figure")
    print(f"    Model taxpaying estates, aged 65+     "
          f"{modelled['modelled_taxpaying_estates']:>10,.0f}")

    ratio = modelled["modelled_taxpaying_estates"] / hmrc_65_plus
    print(f"    Ratio                                 {ratio:>10.2f}")

    hmrc_tax_65_plus = hmrc_tax_aged_65_plus()
    print(f"\n    HMRC tax collected, all ages          "
          f"£{VALIDATION_2023_24['total_tax_gbp'] / 1e9:>9.2f}bn")
    print(f"    HMRC tax, aged 65+, GB                "
          f"£{hmrc_tax_65_plus / 1e9:>9.2f}bn   <- the comparable figure")
    print(f"    Model tax, aged 65+                   "
          f"£{modelled['modelled_tax_gbp'] / 1e9:>9.2f}bn")

    print(f"\n    Not-married share of deaths, ONS      "
          f"{modelled['not_married_share_observed'] * 100:>9.1f}%")
    print(f"    Same, model before correction         "
          f"{modelled['not_married_share_before_correction'] * 100:>9.1f}%")
    print(f"    Model final deaths aged 65+           "
          f"{modelled['modelled_deaths_65_plus_final']:>10,.0f}")
    print(f"    (deaths of people not survived by a spouse, which is the only "
          f"kind that\n     produces a bill)")

    print(f"\n    The model exceeds HMRC by {ratio:.2f}x on estates and "
          f"{modelled['modelled_tax_gbp'] / hmrc_tax_65_plus:.2f}x on tax.")
    print("\n    This is the decumulation gap, not an error. The model asks what")
    print("    today's over-65s would be taxed on if they died today. HMRC counts")
    print("    what was left when people actually died, after years of spending,")
    print("    care costs, gifts and tax planning. Both are right; they are")
    print("    answers to different questions.")
    print(f"\n    Read the other way: for every 100 households that would face a")
    print(f"    bill today, about {100 / ratio:.0f} actually generate one. The rest")
    print("    spend, give away or plan their way below the threshold, or their")
    print("    estate is eaten by care costs.")

    # Report a diagnostic range for the model/HMRC gap; do not fit the model to HMRC.
    if not 1.1 <= ratio <= 2.2:
        print("\n    WARNING: that gap is outside anything decumulation can")
        print("    explain. Check the pension split, the physical wealth")
        print("    haircut, and the share of widows assumed to carry a")
        print("    transferred nil-rate band before using these numbers.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
