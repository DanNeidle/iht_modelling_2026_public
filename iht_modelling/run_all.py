# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Run the pipeline with python3 code/run_all.py. See DATA.md for required inputs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Run in dependency order, in separate processes. Report failures at the end.
STEPS = [
    ("Extract HMRC tables", "acquire/hmrc_tables.py"),
    ("Long-run price indices", "acquire/price_indices.py"),
    ("Constituency population", "acquire/constituency_population.py"),
    ("Tax rules self-test", "lib/test_iht_rules.py"),
    ("Relief property self-test", "model/test_relief_property.py"),
    ("Housing gain self-test", "model/test_housing_gain.py"),
    ("Household stake self-test", "model/test_household_stake.py"),
    ("Unit definition self-test", "model/test_units.py"),
    ("Uprating self-test", "model/test_uprating.py"),
    ("Population denominators", "model/population.py"),
    ("Children by cohort", "model/children.py"),
    ("Build over-65 units", "model/build_units.py"),
    ("Administrative estimate", "model/administrative.py"),
    ("Wealth cross-check", "model/crosscheck_wealth.py"),
    ("Simulate liabilities", "model/simulate.py"),
    ("Validate against HMRC", "model/validate.py"),
    ("Relief claims against HMRC", "model/calibrate_reliefs.py"),
    ("Headline accounting", "analysis/headline.py"),
    ("Sensitivity table", "analysis/sensitivity.py"),
    ("Constituency estimates", "analysis/constituency.py"),
    ("Crosswalk to 2024 seats", "analysis/crosswalk.py"),
    ("Map data", "analysis/build_map_data.py"),
    ("Scatter data", "analysis/scatter_data.py"),
    ("Swing model data", "analysis/swing_model.py"),
    ("Housing gain counterfactual", "analysis/housing_gain.py"),
    # After the housing counterfactual, because the doughnut reads its output.
    ("Article chart JSON", "analysis/build_charts.py"),
    ("Households with a stake", "analysis/households.py"),
]


def main() -> int:
    failures = []
    for title, script in STEPS:
        print(f"\n{'=' * 72}\n{title}  ({script})\n{'=' * 72}", flush=True)
        result = subprocess.run([sys.executable, str(ROOT / script)],
                                cwd=ROOT.parent)
        if result.returncode != 0:
            failures.append(title)

    print(f"\n{'=' * 72}")
    if failures:
        print("Steps that did not complete cleanly:")
        for title in failures:
            print(f"  {title}")
        # Exit non-zero, so anything reading the status finds out. Validation
        # returns 1 when the gap against HMRC leaves the plausible range.
        return 1
    print("All steps completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
