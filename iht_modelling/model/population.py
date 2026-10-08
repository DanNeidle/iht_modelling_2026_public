# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Build GB adult and 65-plus population denominators. Northern Ireland is excluded to match WAS coverage."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import (ADULT_AGE, AGE_THRESHOLDS, POPULATION_BASE_YEAR,
                        POPULATION_PROJECTIONS_XLSX, POPULATION_XLSX,
                        POPULATION_YEAR)

# Use geography codes to distinguish England from England and Wales.
GB_CODE = "K03000001"
UK_CODE = "K02000001"
COUNTRY_CODES = {
    "England": "E92000001",
    "Wales": "W92000004",
    "Scotland": "S92000003",
    "Northern Ireland": "N92000002",
}

# The oldest single-year column in MYE2. Everyone older is in the 90+ bucket.
TOP_AGE = 90


def _age_label(cell) -> int | None:
    """Read the lower age from projection labels. Older groups are summed into 90-plus."""
    text = str(cell).strip()
    if text.isdigit():
        return int(text)
    head = text.split()[0]
    return int(head) if head.isdigit() else None


def projection_growth(target_year: int,
                      base_year: int = POPULATION_BASE_YEAR,
                      path: Path = POPULATION_PROJECTIONS_XLSX) -> np.ndarray:
    """Age-specific growth from ONS 2024-based principal projections. Apply UK growth rates to GB levels; this assumes similar growth in the two populations."""
    if target_year == base_year:
        return None

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    worksheet = workbook["Population"]

    rows = worksheet.iter_rows(values_only=True)
    header = next(rows)
    years = [str(c) for c in header]
    try:
        base_col = years.index(str(base_year))
        target_col = years.index(str(target_year))
    except ValueError as exc:
        raise ValueError(f"{path.name} does not cover {base_year} and "
                         f"{target_year}") from exc

    base: dict[int, float] = {}
    target: dict[int, float] = {}
    for row in rows:
        if row[0] not in ("Males", "Females") or row[1] is None:
            continue
        age = _age_label(row[1])
        if age is None:
            continue
        base[age] = base.get(age, 0.0) + float(row[base_col] or 0.0)
        target[age] = target.get(age, 0.0) + float(row[target_col] or 0.0)
    workbook.close()

    oldest = max(base)
    factors = np.ones(oldest + 1)
    for age in base:
        if base[age] > 0:
            factors[age] = target[age] / base[age]
    return factors


def load_population_by_age(path: Path = POPULATION_XLSX,
                           area_code: str = GB_CODE,
                           year: int = POPULATION_YEAR) -> np.ndarray:
    """Population by age and area, projected from mid-2024 if needed. Age 90 includes everyone older."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["MYE2 - Persons"]

    rows = ws.iter_rows(values_only=True)
    header = None
    for row in rows:
        if row and row[0] == "Code":
            header = row
            break
    if header is None:
        raise ValueError(f"could not find the header row in {path}")

    # Ages start in the column after 'All ages'.
    first_age_col = list(header).index("All ages") + 1

    for row in rows:
        if not row or row[0] != area_code:
            continue
        values = []
        for cell in row[first_age_col:]:
            if cell is None or cell == "":
                break
            values.append(float(cell))
        wb.close()

        counts = np.array(values)
        factors = projection_growth(year)
        if factors is None:
            return counts

        # Apply growth for the whole 90-plus group.
        grown = counts * factors[:len(counts)]
        top = len(counts) - 1
        grown[top] = counts[top] * _open_band_growth(year, top)
        return grown

    wb.close()
    raise ValueError(f"area code {area_code} not found in {path}")


def _open_band_growth(target_year: int, from_age: int,
                      base_year: int = POPULATION_BASE_YEAR,
                      path: Path = POPULATION_PROJECTIONS_XLSX) -> float:
    """Growth of everyone above an age, for the estimates' open top band."""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    worksheet = workbook["Population"]
    rows = worksheet.iter_rows(values_only=True)
    header = [str(c) for c in next(rows)]
    base_col, target_col = header.index(str(base_year)), header.index(str(target_year))

    base = target = 0.0
    for row in rows:
        if row[0] not in ("Males", "Females") or row[1] is None:
            continue
        age = _age_label(row[1])
        if age is not None and age >= from_age:
            base += float(row[base_col] or 0.0)
            target += float(row[target_col] or 0.0)
    workbook.close()
    return target / base if base else 1.0


def summarise(area_code: str = GB_CODE) -> dict:
    """Headline population counts for the model."""
    by_age = load_population_by_age(area_code=area_code)
    ages = np.arange(len(by_age))

    out = {
        "total": float(by_age.sum()),
        "adults": float(by_age[ages >= ADULT_AGE].sum()),
    }
    for threshold in AGE_THRESHOLDS:
        out[f"aged_{threshold}_plus"] = float(by_age[ages >= threshold].sum())

    out["by_age"] = by_age
    return out


def main() -> None:
    gb = summarise(GB_CODE)
    uk = summarise(UK_CODE)

    def m(x: float) -> str:
        return f"{x / 1e6:,.2f}m"

    print("Mid-2024 population, ONS")
    print(f"  Great Britain, all ages      {m(gb['total'])}")
    print(f"  Great Britain, adults (18+)  {m(gb['adults'])}   <- headline denominator")
    for threshold in AGE_THRESHOLDS:
        key = f"aged_{threshold}_plus"
        share = gb[key] / gb["adults"] * 100
        print(f"  Great Britain, aged {threshold}+       {m(gb[key])}"
              f"   ({share:.1f}% of adults)")
    print(f"  United Kingdom, all ages     {m(uk['total'])}")
    print(f"  Northern Ireland is excluded throughout: "
          f"{m(uk['total'] - gb['total'])} people, "
          f"{(uk['total'] - gb['total']) / uk['total'] * 100:.1f}% of the UK.")


if __name__ == "__main__":
    main()
