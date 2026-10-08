# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""
Data for the swing model: what happens to the Commons if exposed pensioners
switch to the Conservatives.

The model itself runs in the browser. This script creates a lookup file 
so the browser has an easy life
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import BUILD, OUTPUT
from analysis.constituency import WEALTH_SHARE_THRESHOLDS
from analysis.scatter_data import normalise
from model.build_units import UNITS_PATH

WEB = Path(__file__).resolve().parents[1] / "web"
VERSION = 5
ELECTION = BUILD / "election_2024.csv"
GRID = BUILD / "wealth_grid.csv"
ESTIMATES = OUTPUT / "constituency_exposure_2024.csv"
# Population aged 65 and over on the 2024 boundaries, which the crosswalked
# estimates file does not carry.
POPULATION = BUILD / "constituency" / "population_pcon2024_aged_65_plus.csv"

# Parties that stand in England and Wales and that the election file counts
# separately. Everything else falls into "Other".
PARTIES = ("Labour", "Conservative", "Liberal Democrat", "Reform UK",
           "Green Party", "Plaid Cymru", "Scottish National Party", "Other")

# Northern Ireland runs its own party system, so these seats are excluded when
# working out the Great Britain vote shares the Ipsos figures are measured on.
NI_PARTIES = {"Sinn Féin", "Democratic Unionist Party", "Alliance",
              "Ulster Unionist Party", "Traditional Unionist Voice",
              "Social Democratic & Labour Party"}

# Seventeen of the eighteen Northern Ireland seats were won by one of those
# parties in 2024. North Down was won by an independent, so it has to be named.
NI_INDEPENDENT_SEATS = {"North Down"}


def in_northern_ireland(result: dict) -> bool:
    return (result["winner"] in NI_PARTIES
            or result["constituency"] in NI_INDEPENDENT_SEATS)

# Ipsos, How Britain voted in the 2024 election. Vote share and estimated
# turnout by age band, weighted to the actual result. 15,234 GB adults for the
# vote, 17,394 for the turnout, which is expressed as a share of all resident
# adults of that age rather than of those registered.
AGE_BANDS = ((18, 24), (25, 34), (35, 44), (45, 54), (55, 64), (65, 120))
TURNOUT_BY_AGE = (0.37, 0.41, 0.48, 0.55, 0.64, 0.73)
VOTE_BY_AGE = {
    "Conservative":     (5.0, 10.0, 17.0, 22.0, 27.0, 43.0),
    "Labour":          (41.0, 47.0, 41.0, 36.0, 32.0, 23.0),
    "Liberal Democrat": (16.0, 11.0, 13.0, 14.0, 12.0, 12.0),
    "Reform UK":         (8.0, 13.0, 14.0, 17.0, 19.0, 14.0),
    "Green Party":      (19.0, 12.0,  7.0,  6.0,  4.0,  2.0),
}

PENSIONER_VOTE_2024 = {p: v[-1] for p, v in VOTE_BY_AGE.items()}
TURNOUT_65_PLUS = TURNOUT_BY_AGE[-1]


def national_shares(election: list[dict]) -> dict[str, float]:
    """Great Britain vote share by party, from the votes rather than from memory."""
    totals = {p: 0.0 for p in PARTIES}
    everything = 0.0
    for row in election:
        if in_northern_ireland(row):
            continue
        seat_total = float(row["total_votes"])
        everything += seat_total
        counted = 0.0
        for party in PARTIES[:-1]:
            votes = float(row[f"votes_{party}"])
            totals[party] += votes
            counted += votes
        totals["Other"] += seat_total - counted
    return {p: totals[p] / everything * 100.0 for p in PARTIES}


def pensioner_ratios(national: dict[str, float]) -> dict[str, float]:
    """How much better each party does among over-65s than overall.

    Plaid Cymru and "Other" get 1.0, because Ipsos does not publish a figure for
    either and assuming no age skew is sensible.
    """
    ratios = {}
    for party in PARTIES:
        pensioner = PENSIONER_VOTE_2024.get(party)
        ratios[party] = (pensioner / national[party]
                         if pensioner and national[party] > 0 else 1.0)
    return ratios


def child_profile() -> tuple[float, dict[str, float]]:
    """Turnout and vote shares among the adult children of exposed units.

    """
    from model.household_stake import children_by_age
    from model.population import load_population_by_age
    from model.simulate import RESULTS_PATH
    from lib.config import HEADLINE_RULES

    units = pd.read_parquet(UNITS_PATH)
    results = pd.read_parquet(RESULTS_PATH)
    results = results[results["scenario"] == "april_2027"].set_index("unit_id")
    has_children = (results.loc[units["unit_id"], "tax_with_descendants"]
                    .to_numpy() > 0).astype(float)

    population = load_population_by_age()
    liable, _ = children_by_age(units, has_children, len(population))

    weights = np.array([liable[low:high + 1].sum() for low, high in AGE_BANDS])
    if weights.sum() <= 0:
        return TURNOUT_65_PLUS, dict(PENSIONER_VOTE_2024)
    share = weights / weights.sum()

    turnout = float((share * np.array(TURNOUT_BY_AGE)).sum())
    vote = {p: float((share * np.array(v)).sum()) for p, v in VOTE_BY_AGE.items()}
    return turnout, vote


def ratios_from(vote: dict[str, float], national: dict[str, float]) -> dict[str, float]:
    """How much better each party does in a group than across the country."""
    return {p: (vote[p] / national[p] if vote.get(p) and national[p] > 0 else 1.0)
            for p in PARTIES}


def main() -> None:
    election = list(csv.DictReader(ELECTION.open()))
    by_name = {normalise(r["constituency"]): r for r in election}
    grid = pd.read_csv(GRID)
    seats = list(csv.DictReader(ESTIMATES.open()))
    units = pd.read_parquet(UNITS_PATH)
    with POPULATION.open() as handle:
        population = {r["GEOGRAPHY_CODE"]: float(r["OBS_VALUE"])
                      for r in csv.DictReader(handle)}

    # Unit members per person aged 65 and over. Above one, because a unit can
    # hold a partner who is younger than 65.
    members_per_over_65 = float(
        (units["weight"] * units["n_adults"]).sum()
        / (units["weight"] * units["members_65_plus"]).sum())

    national = national_shares(election)
    ratios = pensioner_ratios(national)
    child_turnout, child_vote = child_profile()
    child_ratios = ratios_from(child_vote, national)

    multipliers = grid["multiplier"].to_numpy()
    curves = {t: grid[f"members_over_{t}pc"].to_numpy()
              for t in WEALTH_SHARE_THRESHOLDS}
    child_curves = {t: grid[f"children_over_{t}pc"].to_numpy()
                    for t in WEALTH_SHARE_THRESHOLDS}

    def votes_for(result: dict) -> dict[str, int]:
        votes = {p: int(result[f"votes_{p}"]) for p in PARTIES[:-1]}
        votes["Other"] = int(float(result["total_votes"])) - sum(votes.values())
        return votes

    modelled, unmatched = [], []
    england_wales = set()
    for seat in seats:
        result = by_name.get(normalise(seat["constituency"]))
        if not result:
            unmatched.append(seat["constituency"])
            continue

        multiplier = float(seat["implied_wealth_multiplier"])
        over_65 = population[seat["code"]]
        members = over_65 * members_per_over_65
        exposed = [round(float(np.interp(multiplier, multipliers, curves[t]))
                         * members) for t in WEALTH_SHARE_THRESHOLDS]
        # Their adult children, counted in the parents' seat. See the note at
        # the top of this file about what that assumption costs.
        children = [round(float(np.interp(multiplier, multipliers,
                                          child_curves[t])) * over_65)
                    for t in WEALTH_SHARE_THRESHOLDS]

        england_wales.add(normalise(seat["constituency"]))
        modelled.append({"n": seat["constituency"], "w": result["winner"],
                         "v": votes_for(result), "e": exposed, "c": children})

    # Scotland: swings with the country, but never touched by the inheritance
    # tax sliders, because we have no exposure estimate north of the border.
    zeroes = [0] * len(WEALTH_SHARE_THRESHOLDS)
    for result in election:
        if in_northern_ireland(result):
            continue
        if normalise(result["constituency"]) in england_wales:
            continue
        modelled.append({"n": result["constituency"], "w": result["winner"],
                         "v": votes_for(result), "e": zeroes, "c": zeroes})

    # Every seat in the Commons, so the chamber still adds up to 650.
    baseline: dict[str, int] = {}
    for row in election:
        baseline[row["winner"]] = baseline.get(row["winner"], 0) + 1

    # Northern Ireland only.
    fixed: dict[str, int] = {}
    for row in election:
        if in_northern_ireland(row):
            fixed[row["winner"]] = fixed.get(row["winner"], 0) + 1

    out = {
        "baseline": baseline,
        "fixed": fixed,
        "parties": list(PARTIES),
        "nationalVote": {p: round(v, 2) for p, v in national.items()},
        "pensionerRatio": {p: round(v, 3) for p, v in ratios.items()},
        "turnout65": TURNOUT_65_PLUS,
        "childTurnout": round(child_turnout, 4),
        "childRatio": {p: round(v, 3) for p, v in child_ratios.items()},
        "thresholds": list(WEALTH_SHARE_THRESHOLDS),
        "seats": modelled,
    }
    path = WEB / f"swing-model.v{VERSION}.json"
    path.write_text(json.dumps(out, separators=(",", ":")))

    if unmatched:
        print(f"  {len(unmatched)} seats had no election result: {unmatched[:5]}")
    print(f"  {len(modelled)} seats modelled ({len(england_wales)} with an "
          f"exposure estimate), {sum(fixed.values())} fixed in Northern Ireland")
    print(f"  unit members per over-65: {members_per_over_65:.3f}")
    print(f"  pensioner ratios: "
          f"{ {p: round(r, 2) for p, r in ratios.items()} }")
    print(f"  children: turnout {child_turnout * 100:.0f}%, "
          f"Conservative {child_vote['Conservative']:.1f}% "
          f"(ratio {child_ratios['Conservative']:.2f})")
    print(f"  exposed children at the 2% default: "
          f"{sum(m['c'][1] for m in modelled) / 1e6:.2f}m")
    print(f"  exposed members at the 2% default: "
          f"{sum(m['e'][1] for m in modelled) / 1e6:.2f}m across England and Wales")
    print(f"  total seats: {len(modelled) + sum(fixed.values())}")
    print(f"Written to {path}")


if __name__ == "__main__":
    main()
