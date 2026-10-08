# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Fetch NOMIS population denominators for England and Wales. TYPE460 matches HMRC's 2010 seats; TYPE172 supplies 2024 seats for the crosswalk."""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import BUILD

# NOMIS dataset NM_2014_1: small area population estimates by single year of
# age, 2021 census based.
BASE = "https://www.nomisweb.co.uk/api/v01/dataset/NM_2014_1.data.csv"

GEOGRAPHY_TYPES = {
    "pcon2010": "TYPE460",   # matches HMRC table 12.9
    "pcon2024": "TYPE172",   # current seats
}

AGE_CODES = {
    "all_ages": 200,
    "aged_16_plus": 202,      # the closest published cut to "adults"
    "aged_65_plus": 209,
}

YEAR = 2024
GENDER_ALL = 0
MEASURE_COUNT = 20100  # a headcount rather than a percentage


def fetch(geography_type: str, age_code: int) -> str:
    url = (f"{BASE}?geography={geography_type}"
           f"&date={YEAR}&gender={GENDER_ALL}&c_age={age_code}"
           f"&measures={MEASURE_COUNT}"
           f"&select=geography_code,geography_name,obs_value")
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read().decode("utf-8")


def main() -> None:
    out_dir = BUILD / "constituency"
    out_dir.mkdir(parents=True, exist_ok=True)

    for boundary, geo_type in GEOGRAPHY_TYPES.items():
        for age_label, age_code in AGE_CODES.items():
            csv_text = fetch(geo_type, age_code)
            path = out_dir / f"population_{boundary}_{age_label}.csv"
            path.write_text(csv_text)
            rows = csv_text.strip().count("\n")
            print(f"  {path.name}: {rows} rows")


if __name__ == "__main__":
    main()
