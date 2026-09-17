# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Build map GeoJSON from constituency estimates and July 2024 boundaries. Short property keys and four-decimal coordinates reduce download size. Version filenames to refresh year-long caches."""

from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import DOCS, OUTPUT

BOUNDARIES = DOCS / "geo" / "pcon2024_buc.geojson"
ESTIMATES = OUTPUT / "constituency_exposure_2024.csv"
WEB = Path(__file__).resolve().parents[1] / "web"

# Bump on every rebuild whose output changes. See the note above about caching.
VERSION = 14

COORDINATE_PLACES = 4


def round_coords(geometry):
    """Round every coordinate in place, whatever the nesting depth."""
    def walk(node):
        if isinstance(node, (int, float)):
            return round(float(node), COORDINATE_PLACES)
        return [walk(child) for child in node]

    return {"type": geometry["type"], "coordinates": walk(geometry["coordinates"])}


def main() -> None:
    estimates = {}
    with ESTIMATES.open() as handle:
        for row in csv.DictReader(handle):
            estimates[row["code"]] = row

    boundaries = json.loads(BOUNDARIES.read_text())

    features, missing = [], []
    for feature in boundaries["features"]:
        props = feature["properties"]
        code = props.get("PCON24CD") or props.get("pcon24cd")
        name = props.get("PCON24NM") or props.get("pcon24nm")
        row = estimates.get(code)
        if row is None:
            missing.append((code, name))
            continue

        features.append({
            "type": "Feature",
            "geometry": round_coords(feature["geometry"]),
            "properties": {
                "c": code,
                "n": name,
                "h": round(float(row["pct_over_65_households_facing_a_bill"]), 1),
                "a": round(float(row["pct_of_adults_living_in_such_a_household"]), 1),
                "s": round(float(row["share_from_hmrc_suppressed_seats"]), 3),
                **{f"b{i}": round(float(row[f"pct_band_{i}"]), 2)
                   for i in range(7)},
            },
        })

    # Only Scottish (S14) and Northern Irish (N05) seats may lack estimates.
    unexpected = [name for code, name in missing
                  if not code.startswith(("S14", "N05"))]
    if unexpected:
        raise ValueError(f"{len(unexpected)} seats in England or Wales had no "
                         f"estimate, starting with {unexpected[:5]}")

    # Use the national readout from constituency.py, not an average of seat results.
    national = json.loads((OUTPUT / "national_summary.json").read_text())

    out = {"type": "FeatureCollection", "features": features, "national": national}
    path = WEB / f"constituencies.v{VERSION}.json"
    path.write_text(json.dumps(out, separators=(",", ":")))

    # Copy the downloadable table byte for byte to preserve CSV line endings.
    csv_out = WEB / "inheritance-tax-by-constituency.csv"
    shutil.copyfile(ESTIMATES, csv_out)
    print(f"Written to {csv_out}")

    print(f"\n  {len(features):,} seats mapped, {len(missing)} left unshaded")
    print(f"  England and Wales: {national['h']}% of pensioner households")
    print(f"  Written to {path.name} ({path.stat().st_size / 1e6:.1f}MB)")
    print(f"  Remember to point index.html at v{VERSION} and clear the cache.")


if __name__ == "__main__":
    main()
