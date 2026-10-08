# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Estimate exposure from HMRC estate counts and ONS demography, independently of WAS microdata.

Divide liable estates by deaths leaving no surviving spouse. Adjust couples using the ONS couple-to-single pensioner wealth ratio of 2.27 and a lognormal wealth distribution. Report the unadjusted result too.

This uses 2023-24 deaths and rules, so it excludes the April 2027 pension change and differences in younger cohorts' wealth."""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import (ADULT_AGE, BUILD, HEADLINE_AGE, LIFE_TABLES_XLSX,
                        ONS_DIR, OUTPUT)
from model.children import build_cohort_table, children_for_unit
from model.population import load_population_by_age, summarise
from model.validate import (GB_SHARE_OF_UK_ESTATES,
                            load_mortality_rates)

MORTALITY_MARITAL_XLSX = ONS_DIR / "mortality_by_marital_status_2010_2019.xlsx"
POPULATION_MARITAL_XLSX = ONS_DIR / "population_marital_status_2002_2025.xlsx"
HMRC_DIR = BUILD / "hmrc"

# HMRC reports estates in these age bands, so everything here uses them.
AGE_BANDS = {
    "65 to 74": (65, 74),
    "75 to 84": (75, 84),
    "85+": (85, 120),
}

# ONS Table 2.9: median couple/single pensioner wealth ratio, April 2020 to March 2022.
COUPLE_TO_SINGLE_WEALTH_RATIO = 661_600 / 291_800

# Lognormal spread from single pensioners' £76,000 and £591,300 quartiles (Table 2.9).
SINGLE_PENSIONER_WEALTH_SIGMA = np.log(591_300 / 76_000) / 1.34898


@dataclass
class BandResult:
    band: str
    deaths_final: float
    taxpaying_estates: float
    rate_single: float
    rate_couple: float
    units_single: float
    units_couple: float


# Reading the sources
def deaths_65_plus_by_marital_status() -> dict[str, float]:
    """Read 2019 England and Wales deaths by marital status. Apply shares to current deaths; later changes in marital composition are omitted."""
    wb = openpyxl.load_workbook(MORTALITY_MARITAL_XLSX, read_only=True, data_only=True)

    totals: dict[str, float] = {}
    for sheet in ("Table 5", "Table 6"):     # males, then females
        ws = wb[sheet]
        rows = list(ws.iter_rows(values_only=True))
        header_idx = next(i for i, r in enumerate(rows)
                          if r and str(r[0]).strip() == "Year")
        header = [str(c) if c is not None else "" for c in rows[header_idx]]
        latest = max((r for r in rows[header_idx + 1:]
                      if r and isinstance(r[0], (int, float))), key=lambda r: r[0])

        for label, value in zip(header, latest):
            if "(Number of deaths)" not in label:
                continue
            status = label.split("\n")[0].strip()
            totals[status] = totals.get(status, 0.0) + float(value or 0)

    wb.close()
    return totals


def population_65_plus_by_band_and_status() -> pd.DataFrame:
    """Latest England and Wales population by legal marital status, grouped into HMRC age bands."""
    wb = openpyxl.load_workbook(POPULATION_MARITAL_XLSX, read_only=True, data_only=True)
    ws = wb["1"]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header_idx = next(i for i, r in enumerate(rows)
                      if r and isinstance(r[0], str) and r[0].startswith("Marital status"))
    header = [str(c) if c is not None else "" for c in rows[header_idx]]
    estimate_col = next(i for i, c in enumerate(header) if c.endswith("Estimate"))

    # Which five-year groups roll into which HMRC band.
    group_to_band = {
        "65 to 69": "65 to 74", "70 to 74": "65 to 74",
        "75 to 79": "75 to 84", "80 to 84": "75 to 84",
        "85 and over": "85+",
    }

    records = []
    for row in rows[header_idx + 1:]:
        if not row or not isinstance(row[0], str):
            continue
        group = str(row[1]).strip() if row[1] else ""
        if group not in group_to_band:
            continue
        value = row[estimate_col]
        if not isinstance(value, (int, float)):
            continue

        # Collapse the note-suffixed variants onto four clean categories.
        raw = row[0].split("[")[0].strip()
        if raw in ("Married", "Civil Partnered"):
            status = "Married or civil partnered"
        elif raw == "Widowed":
            status = "Widowed"
        elif raw == "Divorced":
            status = "Divorced"
        elif raw.startswith("Never married"):
            status = "Single"
        else:
            continue

        records.append({"band": group_to_band[group], "status": status,
                        "population": float(value)})

    frame = pd.DataFrame(records)
    return frame.groupby(["band", "status"], as_index=False)["population"].sum()


def hmrc_estates_by_band_and_status() -> pd.DataFrame:
    """Taxpaying estates in 2023-24 by age band and marital status, table 12.5c."""
    path = HMRC_DIR / "table_12_5.csv"
    rows = list(csv.reader(path.open()))

    start = next(i for i, r in enumerate(rows)
                 if r and r[0].startswith("[Sheet] 12_5c"))
    header = next(r for r in rows[start:] if r and r[0] == "Asset Type")
    counts = next(r for r in rows[start:]
                  if r and r[0].startswith("Number of estates containing a gross capital"))

    records = []
    for label, value in zip(header, counts):
        label = label.split("[")[0].strip()
        if not value:
            continue
        for band in AGE_BANDS:
            for status in ("Married", "Widowed", "Other"):
                if label == f"{band} {status}":
                    # HMRC counts the United Kingdom. Every denominator this
                    # gets divided by is Great Britain, so put it on that basis
                    # here rather than leaving a 5% mismatch in the rate.
                    records.append({"band": band, "status": status,
                                    "estates": float(value)
                                    * GB_SHARE_OF_UK_ESTATES})
    if not records:
        raise ValueError(
            f"No age-by-marital-status estate counts matched in {path}. The "
            f"column labels are not what this expects, so every rate below "
            f"would come out of a silently empty table.")
    return pd.DataFrame(records)


def tax_liability_threshold_shares() -> dict[float, float]:
    """Shares of taxpaying estates clearing each threshold, from HMRC Table 12.3b. Interpolate £10,000 within the £1 to £25,000 band on a log scale; apply all-age shares to every age band."""
    path = HMRC_DIR / "table_12_3.csv"
    rows = list(csv.reader(path.open()))

    start = next(i for i, r in enumerate(rows) if r and r[0].startswith("[Sheet] 12_3b"))
    header = next(r for r in rows[start:] if r and r[0] == "Asset Type")
    counts = next(r for r in rows[start:]
                  if r and r[0].startswith("Number of estates containing a gross capital"))

    bands: list[tuple[float, float]] = []
    for label, value in zip(header[1:], counts[1:]):
        label = label.strip()
        if label.lower() == "total" or not value:
            continue
        try:
            lower = float(label.replace("£", "").replace("+", "").replace(",", ""))
        except ValueError:
            continue
        bands.append((lower, float(value)))

    bands.sort()
    taxpaying = sum(n for lower, n in bands if lower >= 1)

    def share_at_least(threshold: float) -> float:
        total = 0.0
        for i, (lower, n) in enumerate(bands):
            if lower < 1:
                continue                      # the £0 band is not taxpaying
            upper = bands[i + 1][0] if i + 1 < len(bands) else lower * 4
            if lower >= threshold:
                total += n
            elif upper > threshold:
                # Interpolate within the band on a log scale.
                span = np.log(upper) - np.log(lower)
                above = np.log(upper) - np.log(threshold)
                total += n * (above / span)
        return total / taxpaying

    return {0.0: 1.0, 10_000.0: share_at_least(10_000.0),
            100_000.0: share_at_least(100_000.0)}


# Putting it together
def deaths_by_band() -> dict[str, float]:
    """Deaths in a year by HMRC age band, from population and mortality rates."""
    population = load_population_by_age()
    mortality = load_mortality_rates()
    oldest = max(mortality)

    out: dict[str, float] = {}
    for band, (low, high) in AGE_BANDS.items():
        total = 0.0
        for age in range(low, min(high, len(population) - 1) + 1):
            total += population[age] * mortality[min(age, oldest)]
        out[band] = total
    return out


def build() -> tuple[list[BandResult], dict]:
    deaths_status = deaths_65_plus_by_marital_status()
    population = population_65_plus_by_band_and_status()
    estates = hmrc_estates_by_band_and_status()
    band_deaths = deaths_by_band()

    # The marital-status table covers England and Wales. Everything it is
    # compared against, and the adult denominator these results end up over, is
    # Great Britain. Take the split by status from it, which is what it is good
    # for, and the level from the Great Britain population series.
    from model.population import load_population_by_age

    gb_65_plus = float(load_population_by_age()[HEADLINE_AGE:].sum())
    england_and_wales = float(population["population"].sum())
    if england_and_wales <= 0:
        raise ValueError("The marital status table summed to no population at "
                         "all, so there is nothing to scale to Great Britain.")
    population = population.assign(
        population=population["population"] * (gb_65_plus / england_and_wales))

    # Relative mortality = each status's share of deaths / share of population.
    pop_totals = population.groupby("status")["population"].sum()
    pop_share = pop_totals / pop_totals.sum()
    death_total = sum(deaths_status.values())
    death_share = {k: v / death_total for k, v in deaths_status.items()}

    relative_risk = {
        status: death_share.get(status, 0.0) / pop_share.get(status, 1e-9)
        for status in pop_share.index
    }

    results: list[BandResult] = []
    for band in AGE_BANDS:
        in_band = population[population["band"] == band].set_index("status")["population"]

        # Allocate band deaths using population and relative mortality, preserving the total.
        weights = {s: in_band.get(s, 0.0) * relative_risk.get(s, 1.0)
                   for s in in_band.index}
        total_weight = sum(weights.values())
        deaths = {s: band_deaths[band] * w / total_weight for s, w in weights.items()}

        # A "final death" leaves no surviving spouse.
        final_deaths = sum(v for s, v in deaths.items()
                           if s != "Married or civil partnered")

        band_estates = estates[estates["band"] == band].set_index("status")["estates"]
        # Non-married taxpaying estates: HMRC's Widowed plus Other.
        taxpaying = float(band_estates.get("Widowed", 0.0)
                          + band_estates.get("Other", 0.0))

        rate_single = taxpaying / final_deaths if final_deaths else 0.0

        # Apply the couple wealth ratio to the lognormal probability of clearing the threshold.
        shift = np.log(COUPLE_TO_SINGLE_WEALTH_RATIO) / SINGLE_PENSIONER_WEALTH_SIGMA
        from math import erf, sqrt

        def normal_cdf(x: float) -> float:
            return 0.5 * (1.0 + erf(x / sqrt(2.0)))

        def normal_inverse(p: float) -> float:
            # Rational approximation, plenty accurate for our range.
            from statistics import NormalDist
            return NormalDist().inv_cdf(min(max(p, 1e-9), 1 - 1e-9))

        rate_couple = normal_cdf(normal_inverse(rate_single) + shift) if rate_single > 0 else 0.0

        # Units in the band: couples counted once, everyone else singly.
        married = float(in_band.get("Married or civil partnered", 0.0))
        others = float(in_band.sum() - married)
        # Allow for over-65 married people with younger partners.
        units_couple = married / 1.75
        units_single = others

        results.append(BandResult(
            band=band,
            deaths_final=final_deaths,
            taxpaying_estates=taxpaying,
            rate_single=rate_single,
            rate_couple=rate_couple,
            units_single=units_single,
            units_couple=units_couple,
        ))

    context = {
        "deaths_by_status_2019": deaths_status,
        "relative_risk": relative_risk,
        "band_deaths": band_deaths,
        "couple_wealth_ratio": COUPLE_TO_SINGLE_WEALTH_RATIO,
        "single_sigma": SINGLE_PENSIONER_WEALTH_SIGMA,
    }
    return results, context


def affected_adults(results: list[BandResult], use_couple_adjustment: bool) -> dict:
    """Turn unit exposure rates into a share of the adult population."""
    cohorts = build_cohort_table()
    all_adults = summarise()["adults"]

    # Midpoint ages for each band, used to look up family size.
    band_midpoint = {"65 to 74": 70, "75 to 84": 80, "85+": 89}

    total_units = exposed_units = 0.0
    adults_in_units = adult_children = overlap_65 = 0.0

    for r in results:
        record = children_for_unit(2026 - band_midpoint[r.band], cohorts)
        children_if_any = record.mean_living_adult_children / max(
            1e-6, 1.0 - record.p_childless
        )
        p_has_children = 1.0 - record.p_childless

        for units, rate, size in (
            (r.units_single, r.rate_single, 1.0),
            (r.units_couple, r.rate_couple if use_couple_adjustment else r.rate_single, 2.0),
        ):
            total_units += units
            exposed = units * rate
            exposed_units += exposed
            adults_in_units += exposed * size
            children = exposed * p_has_children * children_if_any
            adult_children += children
            overlap_65 += children * record.p_child_aged_65_plus * rate

    # Step-family double counting, bounded as in headline.py.
    overlap_step = adult_children * 0.04

    affected = adults_in_units + adult_children - overlap_65 - overlap_step

    return {
        "total_units": total_units,
        "exposed_units": exposed_units,
        "unit_exposure_rate": exposed_units / total_units,
        "adults_in_exposed_units": adults_in_units,
        "adult_children": adult_children,
        "overlap": overlap_65 + overlap_step,
        "affected_adults": affected,
        "all_adults": all_adults,
        "share_of_adults": affected / all_adults,
    }


def main() -> None:
    results, context = build()

    print("\nDeaths at 65+ by marital status, England and Wales 2019 (ONS)\n")
    total = sum(context["deaths_by_status_2019"].values())
    not_married = 0.0
    for status, value in sorted(context["deaths_by_status_2019"].items(),
                                key=lambda kv: -kv[1]):
        if status != "Married or civil partnered":
            not_married += value
        print(f"    {status:<28} {value:>9,.0f}  ({value / total * 100:4.1f}%)")
    print(f"    {'NOT survived by a spouse':<28} {not_married:>9,.0f}  "
          f"({not_married / total * 100:4.1f}%)   <- the deaths that can be taxed")

    print("\n\nLiability among households that ended, by age band\n")
    print("    band        final deaths   taxpaying estates   rate (single)  "
          "rate (couple, adj)")
    for r in results:
        print(f"    {r.band:<12} {r.deaths_final:>10,.0f}   {r.taxpaying_estates:>15,.0f}"
              f"   {r.rate_single * 100:>12.1f}%   {r.rate_couple * 100:>15.1f}%")

    floor = affected_adults(results, use_couple_adjustment=False)
    central = affected_adults(results, use_couple_adjustment=True)

    print(f"\n\nOver-65 units in Great Britain: {floor['total_units'] / 1e6:.2f}m")
    print(f"All adults in Great Britain:    {floor['all_adults'] / 1e6:.2f}m\n")

    for label, r in (("FLOOR (couples treated as if they were widows)", floor),
                     ("CENTRAL (couples adjusted for their greater wealth)", central)):
        print(f"  {label}")
        print(f"    over-65 units liable        {r['exposed_units'] / 1e6:5.2f}m "
              f"({r['unit_exposure_rate'] * 100:4.1f}% of units)")
        print(f"    adults in those units       {r['adults_in_exposed_units'] / 1e6:5.2f}m")
        print(f"    their adult children        {r['adult_children'] / 1e6:5.2f}m")
        print(f"    less double counting        {-r['overlap'] / 1e6:5.2f}m")
        print(f"    AFFECTED ADULTS             {r['affected_adults'] / 1e6:5.2f}m"
              f"   = {r['share_of_adults'] * 100:.1f}% of GB adults")
        print(f"    against HMRC's headline     4.7% of deaths"
              f"   ({r['share_of_adults'] / 0.0472:.1f}x)\n")

    rows = [{
        "basis": label,
        "over_65_units_m": round(r["total_units"] / 1e6, 2),
        "units_liable_m": round(r["exposed_units"] / 1e6, 3),
        "unit_exposure_rate_pct": round(r["unit_exposure_rate"] * 100, 1),
        "adults_in_units_m": round(r["adults_in_exposed_units"] / 1e6, 3),
        "adult_children_m": round(r["adult_children"] / 1e6, 3),
        "affected_adults_m": round(r["affected_adults"] / 1e6, 3),
        "share_of_gb_adults_pct": round(r["share_of_adults"] * 100, 1),
    } for label, r in (("floor", floor), ("central", central))]

    path = OUTPUT / "administrative_estimate.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    print(f"Written to {path}")

    shares = tax_liability_threshold_shares()
    print("\nBy size of bill, applying HMRC table 12.3b\n")
    print("    threshold      share of taxpaying estates   units liable   "
          "affected adults")
    for threshold, share in sorted(shares.items()):
        label = "any bill" if threshold == 0 else f"£{int(threshold):,}+"
        units = central["unit_exposure_rate"] * share
        adults = central["share_of_adults"] * share
        print(f"    {label:<14} {share * 100:>20.0f}%   {units * 100:>11.1f}%   "
              f"{adults * 100:>13.1f}%")

    print("\n  Both figures are measured on people who died in 2023-24, under")
    print("  2023-24 rules. They exclude the April 2027 change that brings")
    print("  pension pots into estates, and they are based on the wealth of the")
    print("  1930s cohorts rather than today's richer 65-year-olds. The true")
    print("  figure for today's over-65s is above both.")


if __name__ == "__main__":
    main()
