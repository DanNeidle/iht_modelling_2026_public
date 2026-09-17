# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Inspect WAS variable names and candidate matches for build_units.py. Check matches against the round 8 specification before pinning them. The pension split must distinguish DC pots from DB wealth."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import WAS_DIR
from model.build_units import WAS_VARIABLE_PATTERNS

# Words that mark a variable as pension-related, used for the DB/DC check.
PENSION_HINTS = ("pen", "pension")
DC_HINTS = ("dc", "personal", "draw", "avc", "pot")
DB_HINTS = ("db", "occ", "payment", "inpay")


def find_data_files() -> list[Path]:
    """Any tabular file under docs/was, whatever the Service shipped."""
    if not WAS_DIR.exists():
        raise SystemExit(f"{WAS_DIR} does not exist. Run download_was.py first.")

    extensions = {".sav", ".dta", ".tab", ".csv", ".txt"}
    files = [p for p in WAS_DIR.rglob("*")
             if p.is_file() and p.suffix.lower() in extensions]
    if not files:
        raise SystemExit(
            f"No data files under {WAS_DIR}. Expected .sav, .dta or .tab from "
            "the UK Data Service download."
        )
    return sorted(files, key=lambda p: -p.stat().st_size)


def read_columns(path: Path) -> list[str]:
    """Column names only, without loading the whole file."""
    suffix = path.suffix.lower()

    if suffix == ".sav":
        import pyreadstat
        _, meta = pyreadstat.read_sav(str(path), metadataonly=True)
        return list(meta.column_names)

    if suffix == ".dta":
        import pandas as pd
        with pd.io.stata.StataReader(str(path)) as reader:
            return list(reader.varlist)

    # Tab or comma delimited: the header line is enough.
    with path.open(errors="ignore") as handle:
        header = handle.readline().rstrip("\n")
    delimiter = "\t" if "\t" in header else ","
    return [c.strip().strip('"') for c in header.split(delimiter)]


def score(column: str, patterns: list[str]) -> int:
    """Crude match score: exact beats prefix beats substring."""
    lower = column.lower()
    best = 0
    for pattern in patterns:
        p = pattern.lower()
        if lower == p:
            best = max(best, 3)
        elif lower.startswith(p):
            best = max(best, 2)
        elif p in lower or lower.rstrip("_i") == p:
            best = max(best, 1)
    return best


def main() -> None:
    files = find_data_files()

    print("Files found:\n")
    for path in files:
        print(f"  {path.relative_to(WAS_DIR)}  "
              f"({path.stat().st_size / 1e6:.1f} MB)")

    for path in files[:4]:
        try:
            columns = read_columns(path)
        except Exception as exc:
            print(f"\n  could not read {path.name}: {type(exc).__name__}: {exc}")
            continue

        print(f"\n{'=' * 70}\n{path.name}: {len(columns)} columns\n")

        print("  Candidate matches for the variables we need:\n")
        for field, patterns in WAS_VARIABLE_PATTERNS.items():
            scored = sorted(
                ((score(c, patterns), c) for c in columns),
                key=lambda pair: (-pair[0], len(pair[1])),
            )
            hits = [c for s, c in scored if s > 0][:4]
            marker = " " if hits else "!"
            print(f"  {marker} {field:<24} {', '.join(hits) if hits else 'NOTHING FOUND'}")

        # Check that DC and DB pension components can be separated.
        pension_columns = [c for c in columns
                           if any(h in c.lower() for h in PENSION_HINTS)]
        dc_like = [c for c in pension_columns
                   if any(h in c.lower() for h in DC_HINTS)]
        db_like = [c for c in pension_columns
                   if any(h in c.lower() for h in DB_HINTS)]

        print(f"\n  Pension variables: {len(pension_columns)}")
        print(f"    look like defined contribution: {len(dc_like)}")
        for c in dc_like[:12]:
            print(f"      {c}")
        print(f"    look like defined benefit or in payment: {len(db_like)}")
        for c in db_like[:12]:
            print(f"      {c}")

        if not dc_like or not db_like:
            print("\n  WARNING: the defined benefit and defined contribution")
            print("  components do not look separable in this file. The April")
            print("  2027 scenario cannot be modelled without that")
            print("  split. Check the person-level file, and the round 8 pension")
            print("  derived variable specification, before going further.")

    print("\nNext: pin the confirmed names into WAS_VARIABLE_PATTERNS in")
    print("code/model/build_units.py, then implement load_was_units().")


if __name__ == "__main__":
    main()
