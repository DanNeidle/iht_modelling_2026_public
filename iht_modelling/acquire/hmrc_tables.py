# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Extract HMRC inheritance tax spreadsheets to CSV through tpal. Preserve worksheet markers and treat suppressed cells as missing."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import BUILD, HMRC_TABLE

OUT_DIR = BUILD / "hmrc"

# Keep [c] (suppressed) and [z] (no tax due) as missing, so suppression is never read as zero.
MISSING_MARKERS = {"[c]", "[z]", "[x]", "[w]", ".."}


def read_table_text(project: str, path: Path) -> str:
    """Read all text ranges of a spreadsheet through tpal."""
    chunks: list[str] = []
    index = 1
    while True:
        result = subprocess.run(
            ["tpal", "--json", "open", project, str(path),
             "--chars", "60000", "--range", str(index)],
            capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        if not payload.get("ok"):
            raise RuntimeError(f"tpal failed on {path}: {payload.get('error')}")

        data = payload["data"]
        chunks.append(data["text"])

        rng = data.get("range") or {}
        if index >= int(rng.get("total", 1)):
            break
        index += 1

    return "".join(chunks)


def clean(value: str) -> str:
    value = value.strip()
    if value in MISSING_MARKERS:
        return ""
    # Thousands separators and stray currency symbols.
    return value.replace(",", "").replace("£", "")


def text_to_rows(text: str) -> list[list[str]]:
    """Keep tab-delimited rows and [Sheet] markers; discard cover-sheet prose."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith("[Sheet]"):
            rows.append([line.strip()])
            continue
        if "\t" not in line:
            continue
        rows.append([clean(cell) for cell in line.split("\t")])
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for table_id, path in HMRC_TABLE.items():
        if not path.exists():
            print(f"  missing: {path}")
            continue

        relative = path.relative_to(path.parents[2])
        text = read_table_text("iht", relative)
        rows = text_to_rows(text)

        out_path = OUT_DIR / f"table_{table_id.replace('.', '_')}.csv"
        with out_path.open("w", newline="") as handle:
            csv.writer(handle).writerows(rows)

        print(f"  table {table_id}: {len(rows)} rows -> {out_path.name}")


if __name__ == "__main__":
    main()
