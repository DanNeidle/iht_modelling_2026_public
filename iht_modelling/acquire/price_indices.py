# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Build annual house price and consumer price series for the housing-gain calculation.

Use regional UK HPI where available, backcast on national growth before regional coverage starts. Regional starts vary from 1968 to 1995, so early gains depend on this assumption. The national series uses Nationwide via the Bank of England before 1969, back to 1952.

Splice Bank of England CPI to ONS D7BT, both on 2015 = 100, checking their overlap."""

from __future__ import annotations

import csv
import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import (BUILD, MODEL_DATE, ONS_DIR, SURVEY_WINDOW,
                        VALIDATION_WINDOW)

BOE_XLSX = ONS_DIR / "boe_millennium.xlsx"
HPI_CSV = ONS_DIR / "uk_hpi_average_prices.csv"
CPI_JSON = ONS_DIR / "ons_cpi_d7bt.json"

HPI_OUT = BUILD / "house_price_index.csv"
CPI_OUT = BUILD / "cpi.csv"
UPRATING_OUT = BUILD / "uprating.json"

CPI_URL = ("https://www.ons.gov.uk/economy/inflationandpriceindices"
           "/timeseries/d7bt/mm23/data")

# WAS-to-HPI region names; distinguish West Midlands region from county.
WAS_REGION_TO_HPI = {
    1: "North East",
    2: "North West",
    4: "Yorkshire and The Humber",
    5: "East Midlands",
    6: "West Midlands Region",
    7: "East of England",
    8: "London",
    9: "South East",
    10: "South West",
    11: "Wales",
    12: "Scotland",
}

NATIONAL = "United Kingdom"
GB = "Great Britain"

# Start in 1969: official 1968 coverage is incomplete, so use Nationwide for it.
HPI_FIRST_YEAR = 1969


MONTH_NUMBER = {name: number for number, name in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


def cpi_payload() -> dict:
    """ONS CPI series D7BT. Cached, because it rarely changes."""
    if not CPI_JSON.exists():
        request = urllib.request.Request(
            CPI_URL, headers={"User-Agent": "Tax Policy Associates research"})
        with urllib.request.urlopen(request, timeout=90) as response:
            CPI_JSON.write_bytes(response.read())
    return json.loads(CPI_JSON.read_text())


def fetch_cpi() -> dict[int, float]:
    """ONS CPI, annual, 2015 = 100."""
    return {int(row["year"]): float(row["value"])
            for row in cpi_payload().get("years", []) if row.get("value")}


def fetch_cpi_monthly() -> dict[str, float]:
    """ONS CPI by month, keyed YYYY-MM, so it lines up with the house price series."""
    rows = cpi_payload().get("months", [])
    out = {}
    for row in rows:
        if not row.get("value") or row.get("month") not in MONTH_NUMBER:
            continue
        out[f"{row['year']}-{MONTH_NUMBER[row['month']]:02d}"] = float(row["value"])
    if not out:
        raise ValueError(f"{CPI_JSON} has no monthly observations, so there is "
                         "nothing to uprate savings and pensions with.")

    # A silent partial parse is the dangerous failure here, not an empty one.
    # If ONS ever changes how it spells a month, the dropped rows would come out
    # as a shorter series that still looks perfectly usable.
    if len(out) < len(rows) - 1:
        raise ValueError(
            f"Only {len(out)} of {len(rows)} monthly CPI rows parsed from "
            f"{CPI_JSON}. That means the month names are not what this expects, "
            f"so the series has holes in it.")
    return out


def boe_series(sheet: str, column: int, first_data_row: int) -> dict[int, float]:
    """One column of the Bank of England's long-run dataset, by year."""
    workbook = openpyxl.load_workbook(BOE_XLSX, read_only=True, data_only=True)
    worksheet = workbook[sheet]
    out = {}
    for row in worksheet.iter_rows(min_row=first_data_row, values_only=True):
        year, value = row[0], row[column]
        if isinstance(year, (int, float)) and isinstance(value, (int, float)):
            out[int(year)] = float(value)
    workbook.close()
    return out


def official_hpi() -> dict[str, dict[int, float]]:
    """Average price by region and year, from the UK House Price Index."""
    wanted = set(WAS_REGION_TO_HPI.values()) | {NATIONAL}
    monthly: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))

    with HPI_CSV.open() as handle:
        for row in csv.DictReader(handle):
            region = row.get("Region_Name")
            price = row.get("Average_Price")
            if region in wanted and price:
                monthly[region][int(row["Date"][:4])].append(float(price))

    return {region: {year: sum(values) / len(values)
                     for year, values in years.items()
                     if year >= HPI_FIRST_YEAR}
            for region, years in monthly.items()}


def carry_forward(monthly: dict[str, float], label: str) -> dict:
    """Carry a monthly series from the survey window average to the model date.

    Both of the model's uprating factors are built this way, so that neither
    gets a kinder treatment than the other. The base is the mean of the series
    across the two years of fieldwork, which is the money the survey answers are
    actually denominated in. Past the last real observation we extend at the
    rate of the preceding twelve months rather than picking a forecast."""
    start, end = SURVEY_WINDOW
    window = [v for m, v in monthly.items() if start <= m <= end]
    if len(window) < 12:
        raise ValueError(f"Only {len(window)} months of {label} inside the "
                         f"survey window {start} to {end}.")
    base = sum(window) / len(window)

    latest = max(monthly)
    a_year_before = f"{int(latest[:4]) - 1}-{latest[5:]}"
    if a_year_before not in monthly:
        raise ValueError(f"{label} has no observation for {a_year_before}, so "
                         "there is no twelve-month growth rate to extend on.")
    annual = monthly[latest] / monthly[a_year_before] - 1.0

    months = ((int(MODEL_DATE[:4]) - int(latest[:4])) * 12
              + int(MODEL_DATE[5:7]) - int(latest[5:7]))
    if months < 0:
        raise ValueError(
            f"{label} already runs past {MODEL_DATE} (latest {latest}), so this "
            f"would deflate backwards at the trailing growth rate. Move "
            f"MODEL_DATE forward, or read the actual observation instead.")
    projected = monthly[latest] * (1.0 + annual) ** (months / 12.0)

    return {
        "base": base,
        "latest_month": latest,
        "latest_value": monthly[latest],
        "annual_growth_last_12_months": annual,
        "months_projected": months,
        "projected": projected,
        "factor": projected / base,
        "factor_if_held_flat": monthly[latest] / base,
    }


def rebase(monthly: dict[str, float], window: tuple[str, str], label: str) -> float:
    """The same series carried to the middle of a past tax year instead of the model date.

    validate.py needs this. Comparing April 2027 wealth against estates that
    were actually taxed in 2023-24 would bank four years of inflation as if it
    were evidence about how much people spend before they die."""
    start, end = SURVEY_WINDOW
    base = [v for m, v in monthly.items() if start <= m <= end]
    target = [v for m, v in monthly.items() if window[0] <= m <= window[1]]
    if len(target) < 12:
        raise ValueError(f"Only {len(target)} months of {label} inside the "
                         f"validation window {window[0]} to {window[1]}.")
    return (sum(target) / len(target)) / (sum(base) / len(base))


def write_uprating() -> None:
    """Write the two factors that bring survey-date money to the model date.

    Property moves on the Great Britain house price index. Everything else,
    which in practice means savings, investments, pension pots and possessions,
    moves on consumer prices. Neither is a claim about what any particular
    household's assets did: they are the exchange rate between survey money and
    model-date money."""
    house_prices: dict[str, float] = {}
    with HPI_CSV.open() as handle:
        for row in csv.DictReader(handle):
            if row.get("Region_Name") == GB and row.get("Average_Price"):
                house_prices[row["Date"][:7]] = float(row["Average_Price"])

    consumer_monthly = fetch_cpi_monthly()
    property_ = carry_forward(house_prices, f"{GB} house prices")
    consumer = carry_forward(consumer_monthly, "ONS CPI D7BT")

    start, end = SURVEY_WINDOW
    payload = {
        "survey_window": [start, end],
        "model_date": MODEL_DATE,
        "base_price": round(property_["base"], 2),
        "latest_actual_month": property_["latest_month"],
        "latest_actual_price": property_["latest_value"],
        "annual_growth_last_12_months": round(
            property_["annual_growth_last_12_months"], 5),
        "months_projected": property_["months_projected"],
        "projected_price": round(property_["projected"], 2),
        "uprate_property": round(property_["factor"], 4),
        "uprate_property_if_held_flat": round(property_["factor_if_held_flat"], 4),
        "cpi_base": round(consumer["base"], 4),
        "cpi_latest_actual_month": consumer["latest_month"],
        "cpi_latest_actual_value": consumer["latest_value"],
        "cpi_annual_growth_last_12_months": round(
            consumer["annual_growth_last_12_months"], 5),
        "cpi_months_projected": consumer["months_projected"],
        "cpi_projected": round(consumer["projected"], 4),
        "uprate_other": round(consumer["factor"], 4),
        "uprate_other_if_held_flat": round(consumer["factor_if_held_flat"], 4),
        "validation_window": list(VALIDATION_WINDOW),
        "uprate_property_validation": round(
            rebase(house_prices, VALIDATION_WINDOW, f"{GB} house prices"), 4),
        "uprate_other_validation": round(
            rebase(consumer_monthly, VALIDATION_WINDOW, "ONS CPI D7BT"), 4),
    }
    UPRATING_OUT.write_text(json.dumps(payload, indent=2))

    print(f"\n  Uprating from the survey window {start} to {end}, "
          f"out to {MODEL_DATE}:")
    print(f"    property, {GB} house prices")
    print(f"      base £{property_['base']:,.0f}, latest actual "
          f"{property_['latest_month']} £{property_['latest_value']:,.0f}")
    print(f"      extended {property_['months_projected']} months at "
          f"{property_['annual_growth_last_12_months'] * 100:+.1f}% a year "
          f"to £{property_['projected']:,.0f}")
    print(f"      factor {property_['factor']:.4f}  "
          f"(held flat instead: {property_['factor_if_held_flat']:.4f})")
    print(f"    everything else, ONS CPI")
    print(f"      base {consumer['base']:.1f}, latest actual "
          f"{consumer['latest_month']} {consumer['latest_value']:.1f}")
    print(f"      extended {consumer['months_projected']} months at "
          f"{consumer['annual_growth_last_12_months'] * 100:+.1f}% a year "
          f"to {consumer['projected']:.1f}")
    print(f"      factor {consumer['factor']:.4f}  "
          f"(held flat instead: {consumer['factor_if_held_flat']:.4f})")
    print(f"    consumer prices ran "
          f"{(consumer['factor'] / property_['factor'] - 1) * 100:.0f}% ahead "
          f"of house prices over the period")
    print(f"    re-based to {VALIDATION_WINDOW[0]}-{VALIDATION_WINDOW[1]} for "
          f"validation: property {payload['uprate_property_validation']:.4f}, "
          f"other {payload['uprate_other_validation']:.4f}")
    print(f"    written to {UPRATING_OUT.name}")


def main() -> None:
    print("\nBuilding the long-run price indices")

    # consumer prices
    boe_cpi = boe_series("A47. Wages and prices", column=3, first_data_row=7)
    ons_cpi = fetch_cpi()

    overlap = sorted(set(boe_cpi) & set(ons_cpi))
    worst = max(abs(boe_cpi[y] - ons_cpi[y]) / ons_cpi[y] for y in overlap)
    print(f"  CPI: Bank of England {min(boe_cpi)}-{max(boe_cpi)}, "
          f"ONS {min(ons_cpi)}-{max(ons_cpi)}")
    print(f"       overlap {len(overlap)} years, worst disagreement "
          f"{worst * 100:.2f}%")
    if worst > 0.02:
        raise ValueError("The two consumer price series disagree by more than "
                         "2% somewhere in their overlap, so splicing them "
                         "would hide a real inconsistency.")

    cpi = {y: v for y, v in boe_cpi.items() if y not in ons_cpi}
    cpi.update(ons_cpi)

    with CPI_OUT.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["year", "cpi_2015_100", "source"])
        for year in sorted(cpi):
            source = "ONS D7BT" if year in ons_cpi else "BoE millennium"
            writer.writerow([year, round(cpi[year], 4), source])
    print(f"       written to {CPI_OUT.name} ({min(cpi)}-{max(cpi)})")

    # house prices
    official = official_hpi()
    nationwide = boe_series("A32. Property prices & rent", column=8,
                            first_data_row=5)

    # Join official HPI and Nationwide at 1969; use this national path for regional backcasts.
    national = dict(official[NATIONAL])
    join = min(national)
    for year in sorted(y for y in nationwide if y < join):
        national[year] = national[join] * (nationwide[year] / nationwide[join])

    print(f"  House prices: official UK HPI from {join}, "
          f"Nationwide splice {min(national)}-{join - 1}")

    rows = []
    for was_code, region in WAS_REGION_TO_HPI.items():
        series = dict(official[region])
        starts = min(series)

        for year in sorted(y for y in national if y < starts):
            series[year] = series[starts] * (national[year] / national[starts])

        for year in sorted(series):
            source = ("UK HPI" if year >= starts
                      else f"backcast from {starts}")
            rows.append([was_code, region, year, round(series[year], 2), source])
        if starts > join:
            print(f"       {region}: published from {starts}, "
                  f"backcast {min(series)}-{starts - 1}")

    with HPI_OUT.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["was_region", "region", "year", "average_price",
                         "source"])
        writer.writerows(rows)

    years = sorted({r[2] for r in rows})
    print(f"       written to {HPI_OUT.name} "
          f"({len(WAS_REGION_TO_HPI)} regions, {min(years)}-{max(years)})")

    # Show the implied gains.
    print("\n  Average price, selected years and regions:")
    lookup = {(r[0], r[2]): r[3] for r in rows}
    print(f"    {'':22}" + "".join(f"{y:>10}" for y in (1960, 1975, 1990, 2005, 2021)))
    for code in (8, 9, 1, 11):
        name = WAS_REGION_TO_HPI[code]
        line = "".join(f"{lookup[(code, y)]:>10,.0f}"
                       for y in (1960, 1975, 1990, 2005, 2021))
        print(f"    {name:22}{line}")

    write_uprating()

    print("\n  House prices against consumer prices since 1970, UK:")
    hpi_1970 = lookup[(8, 1970)], lookup[(1, 1970)]
    for code in (8, 1):
        grew = lookup[(code, 2021)] / lookup[(code, 1970)]
        print(f"    {WAS_REGION_TO_HPI[code]:22} house prices "
              f"{grew:6.1f}x")
    print(f"    {'consumer prices':22} {cpi[2021] / cpi[1970]:6.1f}x")


if __name__ == "__main__":
    main()
