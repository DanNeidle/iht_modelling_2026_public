"""Inspect the SPSS fields required for an isolated Round 7 comparison.

Print metadata only. Run after authorisation to read the SPSS files directly.
No project data or model outputs are changed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pyreadstat

CODE = Path(__file__).resolve().parents[2] / "iht_modelling"
sys.path.insert(0, str(CODE))
from model import build_units as bu


def required_columns() -> dict[str, list[str]]:
    return {
        "household": [
            bu.HH_ID, bu.HH_WEIGHT, bu.HH_PROPERTY, bu.HH_RESIDENCE,
            bu.HH_FINANCIAL, bu.HH_PHYSICAL, bu.HH_MARITAL, bu.HH_TENURE,
            bu.HH_REGION, bu.HH_ADULTS,
        ] + bu.HH_DC_COMPONENTS + bu.HH_NON_ESTATE_PENSION
        + bu.HH_LAND_COLUMNS + bu.HOUSING_GAIN_COLUMNS,
        "person": [
            bu.PERSON_ID, bu.PERSON_AGE_BAND, bu.PERSON_SEX,
            bu.PERSON_MARITAL,
        ] + bu.PERSON_BUSINESS_COLUMNS,
    }


def inspect() -> dict:
    folder = bu.was_dir()
    if folder is None:
        raise FileNotFoundError("Existing Round 8 survey directory not found")
    files = {
        "household": {
            7: folder / "was_round_7_hhold_eul_march_2022.sav",
            8: folder / bu.HOUSEHOLD_FILE,
        },
        "person": {
            7: folder / "was_round_7_person_eul_june_2022.sav",
            8: folder / bu.PERSON_FILE,
        },
    }
    output = {}
    for kind, columns in required_columns().items():
        metas = {
            wave: pyreadstat.read_sav(str(path), metadataonly=True)[1]
            for wave, path in files[kind].items()
        }
        lookup = {name.lower(): name for name in metas[7].column_names}
        records = []
        for name in columns:
            proposed = re.sub("r8", "r7", name, flags=re.IGNORECASE)
            candidate = lookup.get(proposed.lower())
            records.append({
                "round8": name,
                "round8_label": metas[8].column_names_to_labels.get(name),
                "round8_values": metas[8].variable_value_labels.get(name, {}),
                "round7_candidate": candidate,
                "round7_label": metas[7].column_names_to_labels.get(candidate),
                "round7_values": metas[7].variable_value_labels.get(candidate, {}),
            })
        relevant = {
            name: label for name, label in metas[7].column_names_to_labels.items()
            if any(term in name.lower() for term in
                   ("valdc", "valdb", "retd", "pinp", "spen", "ppval", "pavc"))
        }
        output[kind] = {
            "files": {str(k): str(v) for k, v in files[kind].items()},
            "rows": {str(k): v.number_rows for k, v in metas.items()},
            "mapping_candidates": records,
            "round7_pension_fields": relevant,
        }
    return output


if __name__ == "__main__":
    print(json.dumps(inspect(), indent=2))
