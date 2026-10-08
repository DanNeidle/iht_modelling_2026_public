#!/usr/bin/env python3
# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Build the tax-cut bang-for-buck chart for the family home inheritance tax article.

Descends from projects/personal_allowance/code/bang_for_buck/build_bang_for_buck_chart.py. Every existing row keeps the cost and GDP range it had there; "Raise the personal allowance" loses its highlight. One row is added and highlighted:

    "Conservatives: no IHT on the family home", cost £7.0bn, GDP range 0 to £0.3bn.

Cost is £7bn, the low end of our £7bn to £9bn static range for 2029-30. The GDP ceiling of £0.3bn is Dan's judgment, retained as instructed on 7 October 2026. The GDP range is illustrative: the OECD's 2021 review finds limited saving responses and evidence that inheritance receipts reduce heirs' labour supply. The top allows for a small saving response by donors; the bottom is floored at zero. Each bar is the upper GDP estimate divided by cost. Sorting uses unrounded ratios, although labels round to pence.
"""

from __future__ import annotations

import json
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import results_dir  # noqa: E402

OUTFILE = results_dir() / "iht-family-home-bang-for-buck.v2.json"

# label, cost (GBP bn), low GDP boost (GBP bn), high GDP boost (GBP bn), group
ITEMS = [
    ("Conservatives: no IHT on the family home", 7.0, 0.0, 0.3, "subject"),
    ("10% hospitality VAT rate", 13.0, 0.0, 0.5, "other"),
    ("Reform's tax-free overtime", 13.5, 0.2, 1.0, "other"),
    ("Universities UK: no employer NI for under-25s", 6.0, 0.3, 1.2, "other"),
    ("Raise the personal allowance", 23.0, 0.0, 6.7, "other"),
    ("Cut basic rate of income tax 0.85p", 6.0, 0.4, 2.0, "other"),
    ("Abolish the apprenticeship levy", 4.0, 0.3, 1.7, "other"),
    ("Cut Universal Credit taper from 55% to ~45%", 6.0, 0.6, 3.0, "other"),
    ("Cut employer NI by 0.55p", 6.0, 1.1, 3.2, "other"),
    ("Cut employee NI by 1.1p", 6.0, 0.9, 3.4, "other"),
    ("Raise employer NI threshold to GBP6,500", 6.0, 1.2, 3.5, "other"),
    ("Abolish High Income Child Benefit Charge", 1.5, 0.1, 0.9, "other"),
    ("Cut residential stamp duty by ~half", 6.0, 1.2, 4.8, "other"),
    ("Extend full expensing to leased assets and structures", 3.0, 1.0, 3.0, "other"),
    ("Abolish GBP100k personal-allowance taper", 3.0, 1.0, 5.0, "other"),
    ("Abolish stamp duty on shares", 3.8, 5.0, 17.0, "other"),
]

COLORS = {"subject": "#D1495B", "other": "#2563EB"}


def ratio_for(cost: float, high: float) -> float:
    return round(high / cost, 2)


def nice_label(label: str) -> str:
    return label.replace("GBP", "£")


def build_chart() -> dict:
    sorted_items = sorted(ITEMS, key=lambda item: item[3] / item[1])
    labels = [nice_label(label) for label, *_ in sorted_items]
    data = []
    for label, cost, low, high, group in sorted_items:
        ratio = ratio_for(cost, high)
        data.append({
            "value": ratio,
            "name": nice_label(label),
            "itemStyle": {"color": COLORS[group]},
            "label": {"formatter": f"£{ratio:.2f}"},
            "cost": round(cost, 2),
            "gdpLow": round(low, 2),
            "gdpHigh": round(high, 2),
        })
    return {
        "backgroundColor": "#ffffff",
        "aria": {"enabled": True, "decal": {"show": False},
                 "label": {"enabled": True, "description": (
                     "A horizontal bar chart comparing the pounds of annual GDP boost per pound of tax cut for different tax cuts. "
                     "The Conservative proposal to exempt the family home from inheritance tax is among the lowest, alongside hospitality VAT, and is highlighted.")}},
        "textStyle": {"fontFamily": "Arial, Helvetica, sans-serif"},
        "title": [
            {"text": "Tax cuts: bang for the buck", "left": "50%", "textAlign": "center", "top": 10,
             "textStyle": {"fontSize": 20, "fontWeight": 700, "color": "#111827"}},
            {"text": "£ of annual GDP boost per £1 cost of tax cut", "left": "50%", "textAlign": "center", "top": 38,
             "textStyle": {"fontSize": 12, "fontWeight": "normal", "color": "#4B5563"}},
        ],
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}, "confine": True},
        "_tpaTooltip": {"header": "{name}", "template": "{series}: £{y:0.00} annual GDP boost per £1", "skipNull": True},
        "grid": {"left": "2%", "right": "10%", "top": 86, "bottom": "6%", "containLabel": True},
        "xAxis": {"type": "value", "min": 0, "max": 5, "interval": 1,
                  "axisLabel": {"formatter": "£{value}.00"}, "splitLine": {"lineStyle": {"color": "#E5E7EB"}}},
        "yAxis": {"type": "category", "axisLabel": {"fontSize": 11, "color": "#111827"}, "axisTick": {"show": False}, "data": labels},
        "series": [{"name": "Annual GDP boost per £1 of tax cut", "type": "bar", "barMaxWidth": 20,
                    "label": {"show": True, "position": "right", "color": "#111827", "fontSize": 11, "formatter": "£{c}"},
                    "emphasis": {"focus": "series"}, "data": data}],
    }


def main() -> None:
    OUTFILE.parent.mkdir(parents=True, exist_ok=True)
    OUTFILE.write_text(json.dumps(build_chart(), indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {OUTFILE}")


if __name__ == "__main__":
    main()
