"""Rebuild either WAS round beside the existing IHT model, without overwriting it.

Example: python tory_2026_proposal/round7_comparison/compare.py --round both

Round 7 values first move to the Round 8 survey-window price basis. Both then
use the existing rules, population controls, wealth-tail correction and asset
uprating. This is a survey-round sensitivity, not an Oxford Economics replica.
All generated data go into a new directory under notes/.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import re
import sys
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd
import pyreadstat

PACKAGE = Path(__file__).resolve().parent
PROPOSAL = PACKAGE.parent
CODE = PROPOSAL.parent / "iht_modelling"
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(PROPOSAL))
from lib.config import PROJECT_ROOT as ROOT
from lib.config import ASSUMPTIONS, RULES_2023_24, RULES_2027_28
from model import build_units as bu, housing_gain, validate
from static_cost import VARIANTS, run_variant
from inspect_schema import required_columns


EXCLUDED_PENSION_ALIASES = {
    "dvvaldbt_scaper8_aggr": "DVValDBTR7_aggr",
    "dvretdb_noaccess_scaper8_aggr": "DVRetDB_noaccessR7_aggr",
    "dvpinpval_scaper8_aggr": "DVPInPValR7_aggr",
    "dvspen_scaper8_aggr": "DVSPenR7_aggr",
}
ROUND7_FILES = {
    "household": "was_round_7_hhold_eul_march_2022.sav",
    "person": "was_round_7_person_eul_june_2022.sav",
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def price_bridge() -> dict:
    """Use the same cached monthly series as the existing model, with 24 months per round."""
    from acquire.price_indices import GB, HPI_CSV, fetch_cpi_monthly
    hpi = {}
    with HPI_CSV.open() as stream:
        for row in csv.DictReader(stream):
            if row.get("Region_Name") == GB and row.get("Average_Price"):
                hpi[row["Date"][:7]] = float(row["Average_Price"])
    cpi = fetch_cpi_monthly()
    result = {}
    for label, series in (("property", hpi), ("other", cpi)):
        means = {}
        for wave, start, end in ((7, "2018-04", "2020-03"),
                                 (8, "2020-04", "2022-03")):
            observations = [v for k, v in series.items() if start <= k <= end]
            if len(observations) != 24:
                raise ValueError(f"{label}, Round {wave}: expected 24 monthly observations")
            means[wave] = float(np.mean(observations))
        result[label] = {"round7_mean": means[7], "round8_mean": means[8],
                         "round7_to_round8": means[8] / means[7]}
    return result


def read_raw(round_number: int) -> tuple[bu.UnitsTable, dict]:
    """Reuse the production construction of units, stopping before wealth adjustments."""
    folder = bu.was_dir()
    if folder is None:
        raise FileNotFoundError("WAS files not found")
    original_read = pyreadstat.read_sav
    audit = {}
    tables = {}
    for kind, wanted in required_columns().items():
        r8file = bu.HOUSEHOLD_FILE if kind == "household" else bu.PERSON_FILE
        path = folder / (ROUND7_FILES[kind] if round_number == 7 else r8file)
        _, meta = original_read(str(path), metadataonly=True)
        lookup = {name.lower(): name for name in meta.column_names}
        mapping = {}
        for name in wanted:
            candidate = name
            if round_number == 7:
                candidate = EXCLUDED_PENSION_ALIASES.get(
                    name, re.sub("r8", "r7", name, flags=re.IGNORECASE))
            if candidate.lower() not in lookup:
                raise ValueError(f"No Round {round_number} equivalent for {name}")
            mapping[name] = lookup[candidate.lower()]
        table, _ = original_read(str(path), usecols=list(mapping.values()))
        table = table.rename(columns={v: k for k, v in mapping.items()})
        if kind == "household" and not table[bu.HH_ID].is_unique:
            raise ValueError("Household IDs are not unique")
        tables[r8file] = table
        audit[kind] = {
            "path": str(path.relative_to(ROOT)), "sha256": digest(path),
            "rows": len(table),
            "columns": {k: {"source": v, "label": meta.column_names_to_labels[v],
                             "value_labels": meta.variable_value_labels.get(v, {})}
                        for k, v in mapping.items()},
        }
    if not set(tables[bu.PERSON_FILE][bu.PERSON_ID]).issubset(
            set(tables[bu.HOUSEHOLD_FILE][bu.HH_ID])):
        raise ValueError("Person file contains IDs absent from household file")

    def adapted_read(path, usecols=None, **kwargs):
        if kwargs:
            raise ValueError(f"Unexpected reader options: {kwargs}")
        table = tables[Path(path).name]
        return table.loc[:, usecols].copy(deep=True), None

    with ExitStack() as stack:
        stack.enter_context(patch.object(pyreadstat, "read_sav", adapted_read))
        stack.enter_context(patch.object(bu, "finalise", lambda units, assumptions: units))
        stack.enter_context(patch.object(housing_gain, "REFERENCE_YEAR",
                                         2019 if round_number == 7 else 2021))
        raw = bu.load_was_units(ASSUMPTIONS)
    raw.notes = [f"WAS Round {round_number}; isolated survey-round sensitivity."]
    return raw, audit


def common_price_frame(raw: pd.DataFrame, round_number: int, bridge: dict,
                       bridge_physical: bool = True) -> pd.DataFrame:
    frame = raw.copy(deep=True)
    if round_number == 8:
        return frame
    property_factor = bridge["property"]["round7_to_round8"]
    other_factor = bridge["other"]["round7_to_round8"]
    for col in ("property_wealth", "residence_value", "housing_gain_nominal",
                "housing_gain_real"):
        frame[col] *= property_factor
    for col in ("financial_wealth", "dc_pension_wealth", "dc_pension_cohort_shifted",
                "db_pension_wealth", "business_raw", "agricultural_raw"):
        frame[col] *= other_factor
    if bridge_physical:
        frame["physical_wealth_raw"] *= other_factor
    return frame


def build(raw: bu.UnitsTable, round_number: int, bridge: dict, basis: str,
          pareto: bool = True, bridge_physical: bool = True) -> pd.DataFrame:
    assumptions = dataclasses.replace(ASSUMPTIONS, apply_pareto_tail=pareto)
    if basis == "2023_24":
        factors = bu._uprating_factors()
        assumptions = dataclasses.replace(
            assumptions, uprate_property=factors["uprate_property_validation"],
            uprate_financial=factors["uprate_other_validation"],
            uprate_pension=factors["uprate_other_validation"],
            population_year=validate.VALIDATION_POPULATION_YEAR)
    frame = common_price_frame(raw.frame, round_number, bridge, bridge_physical)
    table = bu.finalise(bu.UnitsTable(frame, "was", list(raw.notes)), assumptions)
    required = ["weight", "residence_value", "financial_wealth", "physical_wealth",
                "property_wealth", "dc_pension_wealth", "business_agricultural"]
    if not np.isfinite(table.frame[required].to_numpy()).all():
        raise ValueError("Non-finite required values in rebuilt units")
    return table.frame


def summarise(units: pd.DataFrame, basis: str) -> tuple[dict, pd.DataFrame]:
    rules = RULES_2023_24 if basis == "2023_24" else RULES_2027_28
    baseline = run_variant(units, rules, VARIANTS[0])
    reform = run_variant(units, rules, VARIANTS[1])
    baseline_flow = validate.simulate_year_of_deaths(units, baseline, scenario="x")
    reform_flow = validate.simulate_year_of_deaths(units, reform, scenario="x")
    tax0, tax1 = (baseline_flow["modelled_tax_gbp"], reform_flow["modelled_tax_gbp"])
    cost_share = 1 - tax1 / tax0
    weight = units["weight"]
    saving = baseline.expected_tax - reform.expected_tax
    if saving.min() < -1e-6:
        raise AssertionError("Reform increases a household's expected tax")
    regional = units[["region", "weight"]].copy()
    regional["stock_tax_saving"] = saving * weight
    regional = regional.groupby("region", as_index=False).sum()
    regional["benefit_share"] = regional.stock_tax_saving / regional.stock_tax_saving.sum()
    wealth_cols = ["residence_value", "property_wealth", "financial_wealth",
                  "physical_wealth", "dc_pension_wealth", "business_agricultural",
                  "pareto_uplift"]
    wealth = {col: float((units[col] * weight).sum()) for col in wealth_cols}
    record = {
        "unweighted_households": len(units), "represented_households": float(weight.sum()),
        "represented_people_65plus": float((weight * units.members_65_plus).sum()),
        "baseline_annual_tax_gbp": tax0, "reform_annual_tax_gbp": tax1,
        "cost_share": cost_share, "cost_at_13_7bn_receipts": cost_share * 13.7e9,
        "baseline_annual_taxpaying_estates": baseline_flow["modelled_taxpaying_estates"],
        "reform_annual_taxpaying_estates": reform_flow["modelled_taxpaying_estates"],
        "stock_baseline_tax_gbp": float((baseline.expected_tax * weight).sum()),
        "stock_reform_tax_gbp": float((reform.expected_tax * weight).sum()),
        "weighted_homeownership": float(np.average(units.owns_home, weights=weight)),
        "wealth_totals_gbp": wealth,
        "london_south_east_benefit_share": float(
            regional.loc[regional.region.isin([8, 9]), "benefit_share"].sum()),
    }
    if basis == "2023_24":
        hmrc_tax = validate.hmrc_tax_aged_65_plus()
        hmrc_estates = validate.hmrc_taxpaying_estates_aged_65_plus()
        record.update(hmrc_comparable_tax_gbp=hmrc_tax,
                      hmrc_comparable_estates=hmrc_estates,
                      tax_to_hmrc_ratio=tax0 / hmrc_tax,
                      estates_to_hmrc_ratio=baseline_flow["modelled_taxpaying_estates"] / hmrc_estates)
    return record, regional


def create_output(value: str | None) -> Path:
    if value:
        path = Path(value).resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = ROOT / "notes" / f"round7_comparison_{stamp}"
    if not path.is_relative_to(ROOT / "notes"):
        raise ValueError("Derived data must be saved under this project's notes/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir(exist_ok=False)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", choices=("7", "8", "both"), default="both")
    parser.add_argument("--output", help="New output directory under notes/; must not exist")
    args = parser.parse_args()
    out = create_output(args.output)
    protected = [bu.UNITS_PATH, bu.UPRATING_JSON,
                 PROPOSAL / "output" / "static_cost.csv"]
    protected += list((CODE / "model").glob("*.py")) + list((CODE / "lib").glob("*.py"))
    before = {str(p.relative_to(ROOT)): digest(p) for p in protected if p.exists()}
    bridge = price_bridge()
    write_json(out / "price_bridge.json", bridge)
    records, checks = [], {}
    rounds = [7, 8] if args.round == "both" else [int(args.round)]
    for round_number in rounds:
        print(f"Reading Round {round_number}", flush=True)
        raw, metadata = read_raw(round_number)
        write_json(out / f"round{round_number}_schema.json", metadata)
        for basis in ("2023_24", "2027_28"):
            print(f"Building Round {round_number}, {basis}", flush=True)
            units = build(raw, round_number, bridge, basis)
            units.to_parquet(out / f"round{round_number}_{basis}_units.parquet", index=False)
            result, regional = summarise(units, basis)
            result.update(round=round_number, basis=basis, scenario="central")
            records.append(result)
            regional.to_csv(out / f"round{round_number}_{basis}_regional.csv", index=False, mode="x")
            if round_number == 8 and basis == "2027_28":
                existing = pd.read_parquet(bu.UNITS_PATH)
                relevant = [c for c in units.columns if c not in (
                    "housing_gain_nominal", "housing_gain_real", "implied_purchase_year",
                    "housing_gain_counted")]
                pd.testing.assert_frame_equal(units[relevant].reset_index(drop=True),
                                              existing[relevant].reset_index(drop=True),
                                              check_exact=False, rtol=1e-10, atol=1e-6)
                checks["round8_rebuild_matches_existing_units"] = True
            no_tail = build(raw, round_number, bridge, basis, pareto=False)
            sensitivity, _ = summarise(no_tail, basis)
            sensitivity.update(round=round_number, basis=basis, scenario="without_pareto")
            records.append(sensitivity)
        # Physical wealth is frozen after the common survey-window date in the
        # current model. Test leaving Round 7 possessions at their original date.
        if round_number == 7:
            units = build(raw, 7, bridge, "2027_28", bridge_physical=False)
            sensitivity, _ = summarise(units, "2027_28")
            sensitivity.update(round=7, basis="2027_28", scenario="physical_not_bridged")
            records.append(sensitivity)
    after = {str(p.relative_to(ROOT)): digest(p) for p in protected if p.exists()}
    if before != after:
        raise AssertionError("A protected model file changed during the comparison")
    checks["existing_model_files_unchanged"] = True
    write_json(out / "results.json", {"results": records, "checks": checks,
                                     "protected_file_hashes": before,
                                     "assumptions": dataclasses.asdict(ASSUMPTIONS),
                                     "runner_sha256": digest(Path(__file__))})
    tabular = pd.DataFrame([{k: v for k, v in rec.items() if not isinstance(v, dict)}
                           for rec in records])
    tabular.to_csv(out / "summary.csv", index=False, mode="x")
    cols = ["round", "basis", "scenario", "cost_share", "cost_at_13_7bn_receipts",
            "tax_to_hmrc_ratio", "estates_to_hmrc_ratio"]
    print(tabular.reindex(columns=cols).to_string(index=False), flush=True)
    print(f"Output: {out}", flush=True)


if __name__ == "__main__":
    main()
