"""Compare the article's conditional housing shifts on the rebuilt survey rounds."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd

from compare import RULES_2027_28, VARIANTS, run_variant, write_json
from distribution import death_weights


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    records = []
    for wave in (7, 8):
        units = pd.read_parquet(args.directory / f"round{wave}_2027_28_units.parquet")
        dw = death_weights(units)
        base = run_variant(units, RULES_2027_28, VARIANTS[0])
        reform = run_variant(units, RULES_2027_28, VARIANTS[1])
        baseline_tax = float((base.expected_tax * dw).sum())
        for fraction in (0.075, 0.15):
            amount = np.minimum(units.financial_wealth.clip(lower=0) * fraction,
                                reform.tax_with_descendants / RULES_2027_28.rate)
            amount = amount.where(units.owns_home & (units.residence_value > 0), 0.0)
            moved = units.copy(deep=True)
            moved.financial_wealth -= amount
            moved.property_wealth += amount
            moved.residence_value += amount
            after = run_variant(moved, RULES_2027_28, VARIANTS[1])
            # Shift only the descendant branch, then apply its probability.
            loss = ((reform.tax_with_descendants - after.tax_with_descendants)
                    * units.p_has_descendants)
            if (loss < -1e-6).any():
                raise AssertionError("Moving money into an exempt home increased tax")
            records.append({
                "round": wave, "fraction_of_financial_wealth": fraction,
                "stock_wealth_shift_gbp": float((amount * units.weight * units.p_has_descendants).sum()),
                "annual_loss_at_13_7bn_receipts_gbp": float((loss * dw).sum() / baseline_tax * 13.7e9),
            })
    write_json(args.directory / "portfolio.json", records)
    print(pd.DataFrame(records).to_string(index=False))


if __name__ == "__main__":
    main()
