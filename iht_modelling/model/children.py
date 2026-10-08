# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Estimate living adult children by maternal birth cohort using ONS family size (Table 3), age-specific fertility (Table 4a) and life tables. Childlessness determines residence-band eligibility; children's ages support the overlap correction."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import ADULT_AGE, BUILD, FERTILITY_XLSX, LIFE_TABLES_XLSX, MODEL_YEAR

# Check the assumed mean of the "4 or more" bucket against Tables 1 and 3 on each run.
FAMILY_SIZE_BUCKETS = (0, 1, 2, 3, 4)
FOUR_OR_MORE_MEAN = 4.6

# Assumed age gap: men are two years older than female partners.
MALE_PARTNER_AGE_GAP = 2


# Loading the ONS tables
def check_four_or_more_mean(family_size: dict[int, np.ndarray],
                            mean_by_cohort: dict[int, float]) -> float:
    """Infer the mean of the "4 or more" bucket from Table 1 means and Table 3 shares."""
    implied = []
    for year, dist in family_size.items():
        mean = mean_by_cohort.get(year)
        if mean is None or dist[4] <= 0:
            continue
        known = dist[1] * 1 + dist[2] * 2 + dist[3] * 3
        implied.append((mean - known) / dist[4])
    return float(np.median(implied)) if implied else FOUR_OR_MORE_MEAN


def load_mean_family_size(path: Path = FERTILITY_XLSX) -> dict[int, float]:
    """Completed family size, the mean, by the woman's year of birth (Table 1a)."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Table 1a"]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header_idx = next(i for i, r in enumerate(rows)
                      if r and isinstance(r[0], str) and r[0].startswith("Year of birth"))
    last_col = max(i for i, c in enumerate(rows[header_idx]) if c is not None)

    out: dict[int, float] = {}
    for row in rows[header_idx + 1:]:
        if not row or not isinstance(row[0], (int, float)):
            continue
        value = row[last_col]
        if isinstance(value, (int, float)):
            out[int(row[0])] = float(value)
    return out


def load_completed_family_size(path: Path = FERTILITY_XLSX) -> dict[int, np.ndarray]:
    """Read Table 3's completed ('Final') cohorts. Return birth year -> probabilities for 0, 1, 2, 3, 4+ children."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Table 3"]

    out: dict[int, np.ndarray] = {}
    for row in ws.iter_rows(values_only=True):
        if not row or not isinstance(row[0], (int, float)):
            continue
        if str(row[1]).strip() != "Final":
            continue

        year = int(row[0])
        pcts = [float(row[i] or 0.0) for i in range(2, 7)]
        total = sum(pcts)
        if total <= 0:
            continue
        out[year] = np.array(pcts) / total

    wb.close()
    if not out:
        raise ValueError(f"no 'Final' rows found in Table 3 of {path}")
    return out


def load_age_at_birth_distribution(path: Path = FERTILITY_XLSX) -> dict[int, np.ndarray]:
    """Normalise Table 4a fertility rates within each cohort. Return birth year -> probabilities for maternal ages 15 to 45."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Table 4a"]

    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    # Find the header row, which starts with 'Year of birth of woman'.
    header_idx = next(
        i for i, r in enumerate(rows)
        if r and isinstance(r[0], str) and r[0].startswith("Year of birth")
    )

    # Treat the final '45 and over' column as age 45.
    n_ages = sum(1 for c in rows[header_idx][1:] if c is not None)
    ages = np.arange(15, 15 + n_ages)

    out: dict[int, np.ndarray] = {}
    for r in rows[header_idx + 1:]:
        if not r or not isinstance(r[0], (int, float)):
            continue
        year = int(r[0])
        rates = np.array([float(r[i + 1] or 0.0) for i in range(n_ages)])
        total = rates.sum()
        if total <= 0:
            continue
        out[year] = rates / total

    if not out:
        raise ValueError(f"no cohort rows found in Table 4a of {path}")
    return out, ages


def load_survival_to_age(path: Path = LIFE_TABLES_XLSX) -> dict[str, np.ndarray]:
    """Survival by age and sex, using lx / 100,000 from the latest ONS period life table.

Period rates overstate survival for older birth cohorts, slightly raising the living-child count. This uncertainty is not in the sensitivity table."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

    # Sheets are named by year; take the most recent numeric one.
    years = sorted(int(n) for n in wb.sheetnames if n.strip().isdigit())
    ws = wb[str(years[-1])]

    rows = [r for r in ws.iter_rows(values_only=True)]
    wb.close()

    # Male and female tables sit side by side: age, mx, qx, lx, dx, ex.
    header_idx = next(
        i for i, r in enumerate(rows)
        if r and str(r[0]).strip().lower() == "age"
    )
    header = [str(c).strip().lower() if c is not None else "" for c in rows[header_idx]]

    male_lx = header.index("lx")
    female_age = header.index("age", male_lx)
    female_lx = header.index("lx", female_age)

    ages, m_surv, f_surv = [], [], []
    for r in rows[header_idx + 1:]:
        if not r or not isinstance(r[0], (int, float)):
            continue
        ages.append(int(r[0]))
        m_surv.append(float(r[male_lx]) / 100_000.0)
        f_surv.append(float(r[female_lx]) / 100_000.0)

    return {
        "age": np.array(ages),
        "male": np.array(m_surv),
        "female": np.array(f_surv),
        # Average male and female survival for children of unknown sex.
        "both": (np.array(m_surv) + np.array(f_surv)) / 2.0,
        "source_year": years[-1],
    }


# Putting it together
@dataclass
class CohortChildren:
    """What we know about the children of women born in one year."""

    mother_birth_year: int
    p_childless: float
    mean_children_born: float
    mean_living_adult_children: float
    # Share of living adult children aged 65-plus, for the overlap correction.
    p_child_aged_65_plus: float
    child_age_distribution: dict[int, float] = field(default_factory=dict)


def build_cohort_table(model_year: int = MODEL_YEAR) -> dict[int, CohortChildren]:
    """Combine cohort family size, birth timing and survival to count children aged 18-plus. Keep childlessness separately for residence-band eligibility."""
    family_size = load_completed_family_size()
    age_at_birth, mother_ages = load_age_at_birth_distribution()
    survival = load_survival_to_age()

    # Check the assumed 4+ mean against published means.
    implied = check_four_or_more_mean(family_size, load_mean_family_size())
    if abs(implied - FOUR_OR_MORE_MEAN) > 0.4:
        raise ValueError(
            f"The '4 or more children' bucket now implies an average of "
            f"{implied:.2f}, against the assumed {FOUR_OR_MORE_MEAN}. Update "
            "the constant deliberately rather than letting it drift."
        )

    surv_by_age = dict(zip(survival["age"].tolist(), survival["both"].tolist()))
    oldest_age = int(survival["age"].max())

    def survives_to(age: int) -> float:
        if age < 0:
            return 0.0
        return surv_by_age.get(min(age, oldest_age), 0.0)

    out: dict[int, CohortChildren] = {}

    for year, dist in sorted(family_size.items()):
        if year not in age_at_birth:
            continue

        p_childless = float(dist[0])
        sizes = np.array([0, 1, 2, 3, FOUR_OR_MORE_MEAN])
        mean_born = float((dist * sizes).sum())

        # Age children to the model year using the mother's birth schedule.
        shares = age_at_birth[year]
        living_adult = 0.0
        living_65_plus = 0.0
        age_hist: dict[int, float] = {}

        for share, mother_age in zip(shares, mother_ages):
            if share <= 0:
                continue
            child_age = model_year - (year + int(mother_age))
            if child_age < ADULT_AGE:
                continue

            births = mean_born * float(share)
            alive = births * survives_to(child_age)
            living_adult += alive
            if child_age >= 65:
                living_65_plus += alive
            age_hist[child_age] = age_hist.get(child_age, 0.0) + alive

        p65 = (living_65_plus / living_adult) if living_adult > 0 else 0.0

        out[year] = CohortChildren(
            mother_birth_year=year,
            p_childless=p_childless,
            mean_children_born=mean_born,
            mean_living_adult_children=living_adult,
            p_child_aged_65_plus=p65,
            child_age_distribution=age_hist,
        )

    return out


def children_for_unit(mother_birth_year: int,
                      cohort_table: dict[int, CohortChildren]) -> CohortChildren:
    """Look up the maternal cohort, using the nearest available year if missing."""
    if mother_birth_year in cohort_table:
        return cohort_table[mother_birth_year]
    years = sorted(cohort_table)
    nearest = min(years, key=lambda y: abs(y - mother_birth_year))
    return cohort_table[nearest]


def female_cohort_for_unit(age: int, sex: str, has_partner: bool) -> int:
    """Use a woman's own cohort; for men, assume a female partner two years younger."""
    if (sex or "").strip().lower().startswith("f"):
        return MODEL_YEAR - age
    return MODEL_YEAR - age + MALE_PARTNER_AGE_GAP


def main() -> None:
    table = build_cohort_table()

    out_path = BUILD / "cohort_children.json"
    serialisable = {
        str(y): {
            "p_childless": c.p_childless,
            "mean_children_born": c.mean_children_born,
            "mean_living_adult_children": c.mean_living_adult_children,
            "p_child_aged_65_plus": c.p_child_aged_65_plus,
        }
        for y, c in table.items()
    }
    out_path.write_text(json.dumps(serialisable, indent=2))

    print(f"Cohorts built: {len(table)} (written to {out_path})\n")
    print("Women born   childless   children born   living adult   of whom 65+")
    print("                    %          (mean)        children             %")
    for year in (1930, 1935, 1940, 1945, 1950, 1955, 1958, 1961):
        if year not in table:
            continue
        c = table[year]
        age_now = MODEL_YEAR - year
        print(f"  {year} (age {age_now:>2})   {c.p_childless * 100:5.1f}"
              f"          {c.mean_children_born:5.2f}"
              f"          {c.mean_living_adult_children:5.2f}"
              f"        {c.p_child_aged_65_plus * 100:5.1f}")


if __name__ == "__main__":
    main()
