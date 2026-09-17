# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Fetch 2024 constituency vote shares from Parliament's Members API for the scatter chart."""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import BUILD

API = "https://members-api.parliament.uk/api"
OUTPUT_PATH = BUILD / "election_2024.csv"

# Group unlisted parties as "Other", keeping vote shares exhaustive.
PARTIES = ["Labour", "Conservative", "Liberal Democrat", "Reform UK",
           "Green Party", "Scottish National Party", "Plaid Cymru"]


def fetch(url: str, attempts: int = 4):
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url, headers={"Accept": "application/json",
                              "User-Agent": "Tax Policy Associates research"})
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return None


def list_constituencies() -> list[dict]:
    out, skip = [], 0
    while True:
        payload = fetch(f"{API}/Location/Constituency/Search?take=20&skip={skip}")
        items = payload.get("items", [])
        if not items:
            break
        for item in items:
            value = item["value"]
            out.append({"id": value["id"], "name": value["name"],
                        "ons_code": value.get("onsCode") or ""})
        skip += 20
        if skip >= payload.get("totalResults", 0):
            break
    return out


def result_for(constituency_id: int) -> dict | None:
    """Fetch the 2024 general election result, checking history if a by-election is more recent."""
    payload = fetch(f"{API}/Location/Constituency/{constituency_id}"
                    f"/ElectionResult/Latest")
    value = (payload or {}).get("value")

    if not value or "2024 General Election" not in str(value.get("electionTitle", "")):
        # History omits candidates; fetch the selected contest by ID.
        history = fetch(f"{API}/Location/Constituency/{constituency_id}"
                        f"/ElectionResults")
        entry = next((item for item in (history or {}).get("value", [])
                      if "2024 General Election" in str(item.get("electionTitle", ""))),
                     None)
        value = None
        if entry and entry.get("electionId"):
            detail = fetch(f"{API}/Location/Constituency/{constituency_id}"
                           f"/ElectionResult/{entry['electionId']}")
            value = (detail or {}).get("value")

    if not value or not value.get("candidates"):
        return None

    votes: dict[str, float] = {}
    total = 0.0
    for candidate in value.get("candidates", []):
        party = candidate.get("party")
        name = party.get("name") if isinstance(party, dict) else party
        count = float(candidate.get("votes") or 0)
        total += count
        votes[name] = votes.get(name, 0.0) + count

    if total <= 0:
        return None

    shares = {p: round(votes.get(p, 0.0) / total * 100, 2) for p in PARTIES}
    named = sum(votes.get(p, 0.0) for p in PARTIES)
    shares["Other"] = round((total - named) / total * 100, 2)
    shares["total_votes"] = int(total)
    shares["winner"] = max(votes, key=votes.get)
    # Counts as well as shares. A majority worked back from two percentages
    # rounded to two places can be out by a few votes either way, which does not
    # matter in most seats and matters a great deal in Hendon, where the real
    # majority was 15.
    for party in PARTIES:
        shares[f"votes_{party}"] = int(votes.get(party, 0.0))
    shares["votes_winner"] = int(votes[shares["winner"]])
    return shares


def main() -> None:
    print("\nFetching 2024 results from the Parliament Members API")
    constituencies = list_constituencies()
    print(f"  {len(constituencies)} constituencies listed")

    rows, missing = [], 0
    for index, seat in enumerate(constituencies, start=1):
        result = result_for(seat["id"])
        if result is None:
            missing += 1
            continue
        rows.append({"constituency": seat["name"],
                     "ons_code": seat["ons_code"], **result})
        if index % 100 == 0:
            print(f"  {index}/{len(constituencies)}")
        time.sleep(0.05)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  {len(rows)} seats written to {OUTPUT_PATH}")
    if missing:
        print(f"  {missing} had no 2024 result and were skipped")

    con = sorted(rows, key=lambda r: -r["Conservative"])
    print("\n  Highest Conservative share:")
    for row in con[:5]:
        print(f"    {row['Conservative']:>5.1f}%  {row['constituency']}")


if __name__ == "__main__":
    main()
