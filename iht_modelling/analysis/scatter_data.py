# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Join constituency exposure to 2024 Conservative vote share and winning party. Match on ONS codes to avoid name differences."""

from __future__ import annotations

import csv
import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import BUILD, OUTPUT

ELECTION = BUILD / "election_2024.csv"
ESTIMATES = OUTPUT / "constituency_exposure_2024.csv"
# Intermediate output for build_charts.py.
POINTS = OUTPUT / "scatter_points.json"
OUT_PATH = POINTS


def normalise(name: str) -> str:
    name = unicodedata.normalize("NFKD", name.lower().replace("&", " and "))
    return "".join(ch for ch in name
                   if ch.isalnum() and not unicodedata.combining(ch))


# The parties the election file carries a vote share for. "Other" is an
# aggregate of several candidates, so it is not one of them.
PARTIES = ("Labour", "Conservative", "Liberal Democrat", "Reform UK",
           "Green Party", "Scottish National Party", "Plaid Cymru")


def conservative_target(result: dict) -> float | None:
    """How far the Conservatives are behind, in seats where they are second.

    None if they won the seat, if somebody else is the closer challenger, or if
    the winner is an independent, whose vote is buried in the "Other" column and
    cannot be read off.

    Second place is judged against the named parties only. A seat where three
    independents between them out-poll the Conservatives still has the
    Conservatives as the party in second.
    """
    winner = result["winner"]
    if winner == "Conservative" or winner not in PARTIES:
        return None
    conservative = float(result["Conservative"])
    rivals = (float(result[p]) for p in PARTIES
              if p not in (winner, "Conservative"))
    if any(r > conservative for r in rivals):
        return None
    return round(float(result[winner]) - conservative, 1)


def main() -> None:
    election = list(csv.DictReader(ELECTION.open()))
    estimates = list(csv.DictReader(ESTIMATES.open()))

    by_code = {row["ons_code"]: row for row in election if row.get("ons_code")}
    by_name = {normalise(row["constituency"]): row for row in election}

    points, unmatched = [], []
    for seat in estimates:
        result = by_code.get(seat["code"]) or by_name.get(normalise(seat["constituency"]))
        if not result:
            unmatched.append(seat["constituency"])
            continue
        point = {
            "n": seat["constituency"],
            "x": round(float(result["Conservative"]), 1),
            "y": round(float(seat["pct_over_65_households_facing_a_bill"]), 1),
            "w": result["winner"],
            "s": float(seat["share_from_hmrc_suppressed_seats"]),
        }
        # How far behind the Conservatives are where they are the challenger,
        # which is what makes a seat one of their targets. Absent otherwise.
        gap = conservative_target(result)
        if gap is not None:
            point["g"] = gap
            # The majority in votes, which is the winner over the Conservatives
            # in exactly these seats, because these are the seats where the
            # Conservatives are second.
            point["m"] = (int(result["votes_winner"])
                          - int(result["votes_Conservative"]))
        # Include the map's bill-size distribution.
        for index in range(7):
            point[f"b{index}"] = float(seat[f"pct_band_{index}"])
        points.append(point)

    if unmatched:
        print(f"  {len(unmatched)} seats had no election result: "
              f"{unmatched[:5]}")

    POINTS.write_text(json.dumps({"points": points}, separators=(",", ":")))
    print(f"  {len(points)} points written to {POINTS.name}")

    # Report the relationship shown in the chart.
    xs = [p["x"] for p in points]
    ys = [p["y"] for p in points]
    n = len(points)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(xs, ys))
    var_x = sum((a - mean_x) ** 2 for a in xs)
    var_y = sum((b - mean_y) ** 2 for b in ys)
    r = cov / (var_x * var_y) ** 0.5
    slope = cov / var_x

    print(f"\n  correlation {r:.3f}")
    print(f"  slope: one extra point of Conservative vote goes with "
          f"{slope:.2f} points of exposure")

    by_winner: dict[str, list[float]] = {}
    for point in points:
        by_winner.setdefault(point["w"], []).append(point["y"])
    print("\n  mean exposure by winning party:")
    for party, values in sorted(by_winner.items(),
                                key=lambda kv: -sum(kv[1]) / len(kv[1])):
        if len(values) >= 3:
            print(f"    {sum(values) / len(values):5.1f}%  {party} "
                  f"({len(values)} seats)")


if __name__ == "__main__":
    main()
