# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Find the household model, wherever this folder has been put.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

for _candidate in (HERE.parent, HERE.parent / "iht_modelling"):
    if (_candidate / "lib" / "iht_rules.py").exists() and (_candidate / "model").is_dir():
        MODEL_CODE = _candidate
        break
else:
    raise SystemExit(
        "Can't find the household model. These scripts need lib/ and model/ either in the folder above this one "
        "or in a sibling folder called iht_modelling/.")

ROOT = MODEL_CODE.parent
for _p in (str(HERE), str(MODEL_CODE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def results_dir() -> Path:
    """Where aggregate results from the behavioural model go."""
    notes = ROOT / "notes"
    return notes / "residence_codex_20261007_results" if notes.is_dir() else HERE / "output"
