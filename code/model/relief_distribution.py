# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Map reported business and land values onto HMRC's claim distribution, preserving ranks.

Assumes WAS ranks holders correctly and living holders have the same distribution shape as claims at death. All holders retain positive values, including land or businesses that may not qualify. Tied survey values receive equal mapped values. Qualifying passive share portfolios cannot be identified.

calibrate_reliefs.py checks the resulting flow of claims against HMRC."""

from __future__ import annotations

import numpy as np
import pandas as pd


def target_curve(bands: list[tuple[int, int, float]]):
    """Return a value-by-rank function and total claim count from HMRC bands.

Use log-log interpolation inside closed bands. This can miss published means (about 16% high in the £2.5m to £5m band). Fit the bottom band's mean with hi * (1 - s) ** k, where k = hi / mean - 1. Give every top-band claim its published mean; survey weights cannot resolve the extreme tail."""
    lowers = [float(low) for low, _, _ in bands]
    counts = [float(n) for _, n, _ in bands]
    values = [float(v) for _, _, v in bands]

    above = np.cumsum(counts[::-1])[::-1]          # above[i] = claims >= lowers[i]
    total = float(above[0])

    top_count = float(counts[-1])
    top_mean = values[-1] / top_count if top_count else lowers[-1]

    # Shape for the lowest band, pinned to its published mean. See above.
    bottom_hi = lowers[1] if len(lowers) > 1 else 0.0
    bottom_mean = values[0] / counts[0] if counts[0] else bottom_hi
    bottom_k = max(bottom_hi / bottom_mean - 1.0, 0.0) if bottom_mean > 0 else 1.0

    def value_at(rank: np.ndarray) -> np.ndarray:
        """rank counts claims from the largest downwards, 0 at the very top."""
        rank = np.asarray(rank, dtype=float)
        out = np.zeros_like(rank)

        out[rank < top_count] = top_mean

        for i in range(len(lowers) - 1):
            lo, hi = lowers[i], lowers[i + 1]
            n_hi, n_lo = above[i + 1], above[i]
            inside = (rank >= n_hi) & (rank < n_lo)
            if not inside.any() or n_lo <= n_hi:
                continue
            share = (rank[inside] - n_hi) / (n_lo - n_hi)
            if lo <= 0:
                out[inside] = hi * np.power(1.0 - share, bottom_k)
            else:
                out[inside] = hi * np.power(lo / hi, share)

        return np.maximum(out, 0.0)

    return value_at, total


def remap(values: pd.Series, weights: pd.Series,
          bands: list[tuple[int, int, float]]) -> pd.Series:
    """Map positive holders to HMRC's distribution by weighted rank. Scale the curve to the survey's holder count; compare resulting annual claims in calibrate_reliefs.py."""
    value_at, total = target_curve(bands)

    out = pd.Series(0.0, index=values.index)
    holders = values[values > 0]
    if holders.empty:
        return out

    # Use a stable sort for reproducible ties.
    order = holders.sort_values(ascending=False, kind="mergesort").index
    mass = weights.reindex(order).to_numpy(dtype=float)
    if not np.isfinite(mass).all():
        raise ValueError("Non-finite weights reached the relief re-map, which "
                         "means the weights and the values are not aligned.")
    if mass.sum() <= 0:
        return out

    scale = mass.sum() / total

    # Place each household at the midpoint of its survey weight.
    position = (np.cumsum(mass) - mass / 2.0) / scale
    assigned = value_at(position)

    # Give tied holdings their group's weighted mean mapped value. Preserve the total without assigning arbitrary ranks.
    reported = holders.reindex(order).to_numpy(dtype=float)
    frame = pd.DataFrame({"value": reported, "mass": mass, "assigned": assigned})
    frame["weighted"] = frame["mass"] * frame["assigned"]
    totals = frame.groupby("value")[["mass", "weighted"]].transform("sum")
    assigned = np.where(totals["mass"] > 0,
                        totals["weighted"] / totals["mass"], assigned)

    out.loc[order] = assigned
    return out
