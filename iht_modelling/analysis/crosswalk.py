# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Transfer estimates to 2024 constituencies using LSOA population-weighted centroids and 65-plus populations.
A centroid turns out to be the centre of a geometric shape. I didn't know that before. 

Assign each centroid to old and new seats, then weight old-seat estimates by their share of each new seat's older population. New seats inherit averaged estimates, losing within-seat variation.

Coverage is England and Wales. Boundaries simplified to 500 metres can misassign centroids; only unassigned points are detected. An official LSOA lookup would avoid this for 2024 seats."""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from lib.config import BUILD, DOCS, OUTPUT

GEO = DOCS / "geo"
OLD_BOUNDARIES = GEO / "pcon_old_buc.geojson"       # December 2022, what HMRC uses
NEW_BOUNDARIES = GEO / "pcon2024_buc.geojson"       # July 2024, the current seats
CENTROIDS = GEO / "lsoa21_centroids.json"
LSOA_POPULATION = GEO / "lsoa_pop65.csv"


def load_polygons(path: Path, code_field: str, name_field: str):
    data = json.loads(path.read_text())
    geometries, codes, names = [], [], []
    for feature in data["features"]:
        if not feature.get("geometry"):
            continue
        geometries.append(shape(feature["geometry"]))
        codes.append(feature["properties"][code_field])
        names.append(feature["properties"][name_field])
    return geometries, codes, names


def assign(points, geometries, codes) -> list[str | None]:
    """Which polygon does each point fall in."""
    tree = STRtree(geometries)
    out: list[str | None] = []
    for point in points:
        found = None
        for index in tree.query(point):
            if geometries[index].contains(point):
                found = codes[index]
                break
        out.append(found)
    return out


def build_crosswalk() -> dict[str, dict[str, float]]:
    """New seat code -> {old seat code: share of its population aged 65+}."""
    centroids = json.loads(CENTROIDS.read_text())

    population: dict[str, float] = {}
    with LSOA_POPULATION.open() as handle:
        for row in csv.DictReader(handle):
            population[row["GEOGRAPHY_CODE"]] = float(row["OBS_VALUE"])

    lsoa_codes = [code for code in centroids if code in population]
    points = [Point(*centroids[code]) for code in lsoa_codes]
    print(f"  {len(points):,} LSOAs with a centroid and a population")

    old_geoms, old_codes, _ = load_polygons(OLD_BOUNDARIES, "PCON22CD", "PCON22NM")
    new_geoms, new_codes, _ = load_polygons(NEW_BOUNDARIES, "PCON24CD", "PCON24NM")

    old_assignment = assign(points, old_geoms, old_codes)
    new_assignment = assign(points, new_geoms, new_codes)

    unmatched = sum(1 for a, b in zip(old_assignment, new_assignment)
                    if a is None or b is None)
    print(f"  {unmatched:,} LSOAs could not be placed in both vintages "
          f"({unmatched / len(points) * 100:.1f}%), usually coastal centroids "
          f"just outside a generalised boundary")

    weights: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for code, old, new in zip(lsoa_codes, old_assignment, new_assignment):
        if old is None or new is None:
            continue
        weights[new][old] += population[code]

    crosswalk = {}
    for new, mix in weights.items():
        total = sum(mix.values())
        if total > 0:
            crosswalk[new] = {old: value / total for old, value in mix.items()}
    return crosswalk


def main() -> None:
    print("\nBuilding the population-weighted crosswalk")
    crosswalk = build_crosswalk()
    print(f"  {len(crosswalk)} new seats mapped")

    estimates = {}
    with (OUTPUT / "constituency_exposure.csv").open() as handle:
        for row in csv.DictReader(handle):
            estimates[row["code"]] = row

    _, new_codes, new_names = load_polygons(NEW_BOUNDARIES, "PCON24CD", "PCON24NM")
    name_by_code = dict(zip(new_codes, new_names))

    band_columns = sorted(
        (c for c in next(iter(estimates.values())) if c.startswith("pct_band_")),
        key=lambda c: int(c.rsplit("_", 1)[1]))

    rows = []
    for new_code, mix in sorted(crosswalk.items()):
        share_households = share_adults = multiplier = 0.0
        covered = imputed_share = 0.0
        bands = {c: 0.0 for c in band_columns}
        for old_code, weight in mix.items():
            source = estimates.get(old_code)
            if source is None:
                continue
            covered += weight
            share_households += weight * float(source["pct_over_65_households_facing_a_bill"])
            share_adults += weight * float(source["pct_of_adults_living_in_such_a_household"])
            multiplier += weight * float(source["implied_wealth_multiplier"])
            for column in band_columns:
                bands[column] += weight * float(source[column])
            if source["hmrc_suppressed_and_imputed"] == "yes":
                imputed_share += weight

        if covered < 0.5:
            # No estimates for these contributing old seats.
            continue

        rows.append({
            "code": new_code,
            "constituency": name_by_code.get(new_code, new_code),
            "pct_over_65_households_facing_a_bill": round(share_households / covered, 1),
            "pct_of_adults_living_in_such_a_household": round(share_adults / covered, 1),
            "implied_wealth_multiplier": round(multiplier / covered, 2),
            "share_from_hmrc_suppressed_seats": round(imputed_share / covered, 2),
            "old_seats_contributing": len(mix),
            # Record coverage; partial estimates are rescaled to the whole seat.
            "crosswalk_coverage": round(covered, 3),
            **{c: round(v / covered, 2) for c, v in bands.items()},
        })

    rows.sort(key=lambda r: -r["pct_over_65_households_facing_a_bill"])
    path = OUTPUT / "constituency_exposure_2024.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  {len(rows)} seats on 2024 boundaries -> {path}\n")
    print("  Highest")
    for row in rows[:10]:
        print(f"    {row['pct_over_65_households_facing_a_bill']:>5.1f}%  "
              f"{row['constituency']}")
    print("\n  Lowest")
    for row in rows[-10:]:
        print(f"    {row['pct_over_65_households_facing_a_bill']:>5.1f}%  "
              f"{row['constituency']}")


if __name__ == "__main__":
    main()
