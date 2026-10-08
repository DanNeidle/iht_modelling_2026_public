# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Cross-check exposure using published WAS round 8 wealth percentiles: property Table 3.16, financial Table 5.15 and physical Table 4.13.

Assume a home passing to children and full transferred bands for widows. Exclude pensions because the published tables do not isolate DC pots. Summing component percentiles overstates the spread; 2020-22 values also omit later wealth growth."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

# ONS wealth percentiles, April 2020 to March 2022, in pounds.
WEALTH = {
    "single_over_spa": {
        "property":  {"p25": 0,       "p50": 160_000, "p75": 303_000},
        "financial": {"p25": 3_500,   "p50": 17_000,  "p75": 67_300},
        "physical":  {"p25": 15_000,  "p50": 29_500,  "p75": 50_000},
    },
    "couple_both_over_spa": {
        "property":  {"p25": 160_000, "p50": 300_000, "p75": 475_000},
        "financial": {"p25": 17_900,  "p50": 62_600,  "p75": 185_800},
        "physical":  {"p25": 35_700,  "p50": 60_500,  "p75": 90_200},
    },
}

# Inheritance tax thresholds for an estate with a home passing to children.
THRESHOLD_TWO_SETS_OF_BANDS = 1_000_000    # a couple, or a widow
THRESHOLD_ONE_SET_OF_BANDS = 500_000       # never married or divorced

# Apply the main model's replacement-cost discount to physical wealth.
PHYSICAL_HAIRCUT = 0.35


@dataclass
class Fitted:
    name: str
    median: float
    p75: float
    sigma: float

    def share_above(self, threshold: float) -> float:
        """Lognormal share above the threshold, fitted to the median and 75th percentile."""
        z = (np.log(threshold) - np.log(self.median)) / self.sigma
        return 1.0 - NormalDist().cdf(z)


def estate_base(household: str) -> dict[str, float]:
    """Sum the estate-forming components at each percentile point."""
    parts = WEALTH[household]
    out = {}
    for point in ("p25", "p50", "p75"):
        out[point] = (
            parts["property"][point]
            + parts["financial"][point]
            + parts["physical"][point] * (1.0 - PHYSICAL_HAIRCUT)
        )
    return out


def fit(household: str) -> Fitted:
    base = estate_base(household)
    # The lognormal 75th percentile is 0.6745 log standard deviations above the median.
    sigma = (np.log(base["p75"]) - np.log(base["p50"])) / 0.6744897501960817
    return Fitted(name=household, median=base["p50"], p75=base["p75"], sigma=sigma)


def main() -> None:
    print("\nEstate base by household type, ONS published percentiles")
    print("(property + financial + physical after haircut, no pension wealth)\n")

    fits = {}
    for household in WEALTH:
        base = estate_base(household)
        f = fit(household)
        fits[household] = f
        print(f"  {household}")
        print(f"    25th percentile   £{base['p25']:>10,.0f}")
        print(f"    median            £{base['p50']:>10,.0f}")
        print(f"    75th percentile   £{base['p75']:>10,.0f}")
        print(f"    implied log sd     {f.sigma:>10.3f}\n")

    single = fits["single_over_spa"]
    couple = fits["couple_both_over_spa"]

    print("Share clearing the inheritance tax threshold\n")
    print(f"  widow, home and children, threshold £1,000,000      "
          f"{single.share_above(THRESHOLD_TWO_SETS_OF_BANDS) * 100:5.1f}%")
    print(f"  single never married or divorced, threshold £500,000 "
          f"{single.share_above(THRESHOLD_ONE_SET_OF_BANDS) * 100:5.1f}%")
    print(f"  couple, home and children, threshold £1,000,000     "
          f"{couple.share_above(THRESHOLD_TWO_SETS_OF_BANDS) * 100:5.1f}%")

    # Read the other route rather than quoting it. These numbers were typed in
    # once and went stale: every one of the six was wrong by the time anyone
    # looked, while the sentence underneath still claimed the two routes agreed.
    from model.administrative import build

    results, _ = build()
    singles = {r.band: r.rate_single * 100 for r in results}
    couples = {r.band: r.rate_couple * 100 for r in results}

    print("\nCompare with the administrative method:")
    print("  observed liability among households that ended, by age band:")
    print("    " + "      ".join(f"{b} {v:4.1f}%" for b, v in singles.items()))
    print("  adjusted rate for couples:")
    print("    " + "      ".join(f"{b} {v:4.1f}%" for b, v in couples.items()))

    widow_here = single.share_above(THRESHOLD_TWO_SETS_OF_BANDS) * 100
    couple_here = couple.share_above(THRESHOLD_TWO_SETS_OF_BANDS) * 100
    print(f"\n  The wealth route puts widows at {widow_here:.1f}% and couples "
          f"at {couple_here:.1f}%.")
    print(f"  The tax-return route puts them at {min(singles.values()):.1f}% to "
          f"{max(singles.values()):.1f}% and {min(couples.values()):.1f}% to "
          f"{max(couples.values()):.1f}%.")

    print("\n  How independent these two really are depends which row you read.")
    print("  The single and widow comparison is genuinely independent: that route")
    print("  is HMRC estates over ONS deaths and never looks at wealth. The couple")
    print("  row is not. Its only input is the couple-to-single wealth ratio, which")
    print("  comes from the same Wealth and Assets Survey round 8 that this file and")
    print("  the main model use, so the two sides are partly the same evidence.")

    print("\n  The wealth figures are 2020-22 and exclude pension pots, which enter")
    print("  estates from April 2027, so they sit below the model on that account.")


if __name__ == "__main__":
    main()
