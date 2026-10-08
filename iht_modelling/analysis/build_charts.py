# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Build ECharts JSON from pipeline outputs. Show the England and Wales average as a separate bar and colour seats by winning party. Mark imputed estimates in tooltips."""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.config import OUTPUT
# Import the current data filename.
from analysis.scatter_data import OUT_PATH as SCATTER

ROOT = Path(__file__).resolve().parents[2]
WEB = Path(__file__).resolve().parents[1] / "web"
BUILD = ROOT / ".tmp" / "build" / "constituency"
CHARTS = Path(__file__).resolve().parents[1] / "charts"

# Bump asset versions when output changes; published files are cached for a year.
VERSION = 12

# The summary chart ships on its own version, so adding or revising it does not
# force the six charts already embedded in the article to be re-linked.
KEY_FINDINGS_VERSION = 1

PARTY_COLOURS = {
    "Labour": "#d50000",
    "Conservative": "#0087dc",
    "Liberal Democrat": "#faa61a",
    "Reform UK": "#12b6cf",
    "Green Party": "#6ab023",
    "Plaid Cymru": "#005b54",
    "Independent": "#8d8d8d",
}
AVERAGE_COLOUR = "#4a5254"
# Pixel offsets a dot may take off its row's centre line, so that seats at
# similar exposure spread out instead of stacking into one stripe.
JITTER_OFFSETS = (-12, -8, -4, 0, 4, 8, 12)
HOW_MANY = 50
# The target seats chart runs longer, because the Conservatives need about
# this many to be the largest party again.
HOW_MANY_TARGETS = 75

SHORT_PARTY = {
    "Liberal Democrat": "Lib Dem",
    "Green Party": "Green",
    "Conservative": "Conservative",
    "Reform UK": "Reform",
    "Plaid Cymru": "Plaid Cymru",
    "Labour": "Labour",
    "Independent": "Independent",
}


def load():
    """Load current pipeline outputs from code/output."""
    with (OUTPUT / "constituency_exposure_2024.csv").open() as handle:
        seats = list(csv.DictReader(handle))
    with (BUILD / "population_pcon2024_aged_65_plus.csv").open() as handle:
        population = {r["GEOGRAPHY_CODE"]: float(r["OBS_VALUE"])
                      for r in csv.DictReader(handle)}
    points = json.loads(SCATTER.read_text())["points"]
    party = {p["n"]: p["w"] for p in points}
    conservative = {p["n"]: p["x"] for p in points}
    national = json.loads((OUTPUT / "national_summary.json").read_text())

    gap = {p["n"]: p["g"] for p in points if "g" in p}
    majority = {p["n"]: p["m"] for p in points if "m" in p}

    rows = [{
        "n": s["constituency"],
        "v": float(s["pct_over_65_households_facing_a_bill"]),
        "w": party[s["constituency"]],
        "pop": population[s["code"]],
        "con": conservative[s["constituency"]],
        # How far behind the Conservatives are, where they are the challenger.
        "gap": gap.get(s["constituency"]),
        "majority": majority.get(s["constituency"]),
    } for s in seats]
    rows.sort(key=lambda r: r["v"])
    return rows, round(float(national["h"]), 1)


def bar_chart(items, average, title, estimated: bool) -> dict:
    """A bar per seat, then a gap, then the England and Wales average."""
    # Leave an empty category before the national average bar.
    categories = [i["n"] for i in items] + ["", "England and Wales"]

    data = []
    for item in items:
        note = (" Estimated: HMRC withholds the count for this seat because it "
                "is small enough to identify somebody.") if estimated else ""
        data.append({
            "value": item["v"],
            # Put party and estimation status in the name; the tooltip has no third-field placeholder.
            "name": f"{item['n']} ({item['w']}).{note}",
            "itemStyle": {"color": PARTY_COLOURS[item["w"]], "borderRadius": [3, 3, 0, 0]},
        })
    data.append({"value": None, "name": ""})
    data.append({
        "value": average,
        "name": "England and Wales, all 575 constituencies.",
        "itemStyle": {"color": AVERAGE_COLOUR, "borderRadius": [3, 3, 0, 0]},
    })

    # containLabel reserves tick-label space; left must also allow for the axis name.
    return {
        "title": {"text": title, "left": "50%", "textAlign": "center", "top": 8,
                  "textStyle": {"fontSize": 16}},
        "tooltip": {"trigger": "item"},
        "_tpaTooltip": {"template": "{name}<br>{y:0.0}% of pensioner households"},
        "legend": {"show": False},
        "grid": {"left": 46, "right": 16, "top": 52, "bottom": 32,
                 "containLabel": True},
        "xAxis": {
            "type": "category",
            "data": categories,
            "axisTick": {"show": False},
            "axisLabel": {"interval": 0, "rotate": 90, "fontSize": 9,
                          "color": "#5a6468"},
        },
        "yAxis": {
            "type": "value",
            "name": "% of pensioner households facing a bill",
            "nameLocation": "middle",
            "nameGap": 42,
            "nameTextStyle": {"fontSize": 11, "color": "#5a6468"},
            "axisLabel": {"formatter": "{value}%"},
            "splitLine": {"lineStyle": {"color": "#e6ecec"}},
        },
        "series": [{"type": "bar", "data": data, "barCategoryGap": "18%"}],
    }


def target_seats_chart(rows, average) -> dict:
    """The seats the Conservatives would have to win, and what they hold.

    Every seat where the Conservatives came second in 2024, ranked by how far
    behind they were, closest first. Bars are coloured by whoever holds the seat
    now, so the chart reads as what the Conservatives would be taking, and from
    whom.
    """
    targets = sorted((r for r in rows if r["gap"] is not None),
                     key=lambda r: (r["majority"], -r["v"]))[:HOW_MANY_TARGETS]

    categories = [t["n"] for t in targets] + ["", "England and Wales"]
    data = [{
        "value": t["v"],
        "name": f"{t['n']}. {t['w']} majority {t['majority']:,} ({t['gap']:.1f}%).",
        "itemStyle": {"color": PARTY_COLOURS[t["w"]], "borderRadius": [3, 3, 0, 0]},
    } for t in targets]
    data.append({"value": None, "name": ""})
    data.append({
        "value": average,
        "name": "England and Wales, all 575 constituencies.",
        "itemStyle": {"color": AVERAGE_COLOUR, "borderRadius": [3, 3, 0, 0]},
    })

    return {
        "title": {"text": "The Conservatives' 75 most winnable seats, in target "
                          "order (coloured by current holder)",
                  "left": "50%", "textAlign": "center", "top": 8,
                  "textStyle": {"fontSize": 16}},
        "tooltip": {"trigger": "item"},
        "_tpaTooltip": {"template": "{name}<br>{y:0.0}% of pensioner households"},
        "legend": {"show": False},
        "grid": {"left": 46, "right": 16, "top": 52, "bottom": 32,
                 "containLabel": True},
        "xAxis": {"type": "category", "data": categories,
                  "axisTick": {"show": False},
                  "axisLabel": {"interval": 0, "rotate": 90, "fontSize": 9,
                                "color": "#5a6468"}},
        "yAxis": {"type": "value",
                  "name": "% of pensioner households facing a bill",
                  "nameLocation": "middle", "nameGap": 42,
                  "nameTextStyle": {"fontSize": 11, "color": "#5a6468"},
                  "axisLabel": {"formatter": "{value}%"},
                  "splitLine": {"lineStyle": {"color": "#e6ecec"}}},
        "series": [{"type": "bar", "data": data, "barCategoryGap": "18%"}],
    }


def key_findings_chart(rows, average) -> dict:
    """Every seat as a dot, in a row for the party that holds it.

    This is the figure for the summary box at the top of the article, so it has
    to carry three things at once: the national share, how far the seats spread
    either side of it, and which party's seats sit highest. A bar of party
    averages would hide the spread, and a map would hide the party. A row of
    dots per party shows all three, and it shows the overlap too, which is the
    honest part: the Liberal Democrat average is the highest in the country and
    there are still Labour seats above almost all of theirs.

    Dots are jittered off the centre line of each row so that seats at similar
    exposure do not stack into one blob. The offset is cosmetic and seeded, so
    it is the same every time the chart is built."""
    import random

    totals = defaultdict(lambda: [0.0, 0.0, 0])
    for row in rows:
        entry = totals[row["w"]]
        entry[0] += row["pop"] * row["v"]
        entry[1] += row["pop"]
        entry[2] += 1

    parties = sorted(((name, num / den, seats)
                      for name, (num, den, seats) in totals.items()),
                     key=lambda t: (t[2], t[1]))

    # Bottom to top, because an ECharts category axis counts upwards, so the
    # party with most seats is last in the list and ends up at the top.
    index = {name: i for i, (name, _, _) in enumerate(parties)}
    jitter = random.Random(20260917)

    # A category axis snaps a fractional index back to the row's centre line, so
    # the jitter cannot live in the coordinate. Each party is split into bands
    # instead, one series per band, each nudged a few pixels off centre.
    dots = defaultdict(lambda: defaultdict(list))
    for row in rows:
        band = jitter.choice(JITTER_OFFSETS)
        dots[row["w"]][band].append({
            "value": [round(row["v"], 1), index[row["w"]]],
            "name": f"{row['n']} ({SHORT_PARTY[row['w']]})",
        })

    series = []
    for name, _, _ in parties:
        for band, points in sorted(dots[name].items()):
            series.append({
                "type": "scatter",
                "name": SHORT_PARTY[name],
                "symbolSize": 7,
                "symbolOffset": [0, band],
                "itemStyle": {"color": PARTY_COLOURS[name], "opacity": 0.45},
                "emphasis": {"itemStyle": {"opacity": 1, "borderColor": "#fff",
                                           "borderWidth": 1}},
                "data": points,
                "z": 2,
            })

    # The average sits on top of its own row, ringed in white so it reads as a
    # summary of the dots underneath rather than as another seat.
    series.append({
        "type": "scatter",
        "name": "Average",
        "symbol": "diamond",
        "symbolSize": 15,
        "itemStyle": {"color": "#1b3b41", "borderColor": "#fff", "borderWidth": 2},
        "data": [{"value": [round(value, 1), index[name]],
                  "name": f"{SHORT_PARTY[name]} average"}
                 for name, value, _ in parties],
        "z": 4,
    })

    # The national line, drawn as a two point series rather than a markLine. A
    # markLine puts its label sideways down the plot unless told not to, and at
    # phone widths it stopped drawing the line at all. This renders everywhere.
    # It carries no label: the text beside the chart says what it is.
    series.append({
        "type": "line",
        "name": "England and Wales",
        "data": [[average, -0.6], [average, len(parties) - 0.4]],
        "symbol": "none",
        "lineStyle": {"color": AVERAGE_COLOUR, "width": 1.5, "type": "dashed"},
        "silent": True,
        "z": 1,
    })

    return {
        "title": {
            "text": "Inheritance tax exposure by party",
            "left": "50%", "textAlign": "center", "top": 8,
            "textStyle": {"fontSize": 15},
        },
        "tooltip": {"trigger": "item"},
        "_tpaTooltip": {"template": "{name}<br>{y:0.0}% of pensioner households"},
        "legend": {"show": False},
        "grid": {"left": 12, "right": 24, "top": 52, "bottom": 34,
                 "containLabel": True},
        "xAxis": {
            "type": "value", "min": 5, "max": 55, "interval": 5,
            "axisLabel": {"formatter": "{value}%", "color": "#5a6468"},
            "splitLine": {"lineStyle": {"color": "#e9efef", "width": 1}},
            "axisLine": {"show": False}, "axisTick": {"show": False},
        },
        "yAxis": {
            "type": "category",
            "data": [f"{SHORT_PARTY[name]}  ({seats})" for name, _, seats in parties],
            "axisLabel": {"fontSize": 12, "color": "#3d4a4d"},
            "axisLine": {"show": False}, "axisTick": {"show": False},
            "splitLine": {"show": False},
            # Faint banding, so a row of dots reads as one row.
            "splitArea": {"show": True,
                          "areaStyle": {"color": ["#ffffff", "#f7fafa"]}},
        },
        "series": series,
    }


def party_chart(rows, average) -> dict:
    totals = defaultdict(lambda: [0.0, 0.0, 0])
    for row in rows:
        entry = totals[row["w"]]
        entry[0] += row["pop"] * row["v"]
        entry[1] += row["pop"]
        entry[2] += 1

    parties = sorted(((name, num / den, seats)
                      for name, (num, den, seats) in totals.items()),
                     key=lambda t: -t[1])

    categories = [f"{SHORT_PARTY[p]}\n{n} seat{'' if n == 1 else 's'}"
                  for p, _, n in parties] + ["", "England and Wales"]
    data = [{
        "value": round(value, 1),
        "name": f"{name}, {seats} seat{'' if seats == 1 else 's'}.",
        "itemStyle": {"color": PARTY_COLOURS[name], "borderRadius": [3, 3, 0, 0]},
    } for name, value, seats in parties]
    data.append({"value": None, "name": ""})
    data.append({
        "value": average,
        "name": "England and Wales, all 575 constituencies.",
        "itemStyle": {"color": AVERAGE_COLOUR, "borderRadius": [3, 3, 0, 0]},
    })

    return {
        "title": {"text": "Inheritance tax exposure by the party holding the seat",
                  "left": "50%", "textAlign": "center", "top": 8,
                  "textStyle": {"fontSize": 16}},
        "tooltip": {"trigger": "item"},
        "_tpaTooltip": {"template": "{name}<br>{y:0.0}% of pensioner households"},
        "legend": {"show": False},
        "grid": {"left": 46, "right": 16, "top": 52, "bottom": 32,
                 "containLabel": True},
        "xAxis": {"type": "category", "data": categories,
                  "axisTick": {"show": False},
                  "axisLabel": {"interval": 0, "fontSize": 11, "color": "#5a6468",
                                "lineHeight": 14}},
        "yAxis": {"type": "value",
                  "name": "% of pensioner households facing a bill",
                  "nameLocation": "middle", "nameGap": 45,
                  "nameTextStyle": {"fontSize": 11, "color": "#5a6468"},
                  "axisLabel": {"formatter": "{value}%"},
                  "splitLine": {"lineStyle": {"color": "#e6ecec"}}},
        "series": [{"type": "bar", "data": data, "barCategoryGap": "40%"}],
    }


def scatter_chart(rows) -> dict:
    """One series per party lets the legend filter points."""
    order = ["Labour", "Conservative", "Liberal Democrat", "Reform UK",
             "Green Party", "Plaid Cymru", "Independent"]
    present = [p for p in order if any(r["w"] == p for r in rows)]

    series = [{
        "name": party,
        "type": "scatter",
        "symbolSize": 7,
        "itemStyle": {"color": PARTY_COLOURS[party], "opacity": 0.65,
                      "borderColor": "#fff", "borderWidth": 0.5},
        "data": [{"value": [r["con"], r["v"]], "name": r["n"]}
                 for r in rows if r["w"] == party],
    } for party in present]

    return {
        "title": {"text": "Inheritance tax exposure and the Conservative vote",
                  "left": "50%", "textAlign": "center", "top": 8,
                  "textStyle": {"fontSize": 16}},
        "tooltip": {"trigger": "item"},
        "_tpaTooltip": {"template": "{name}<br>{series}<br>{y:0.0}% of pensioner households<br>{x:0.0}% Conservative vote"},
        "legend": {"show": True, "top": 36, "type": "scroll"},
        "grid": {"left": 46, "right": 16, "top": 80, "bottom": 8,
                 "containLabel": True},
        "xAxis": {"type": "value", "name": "Conservative share of the vote, 2024",
                  "nameLocation": "middle", "nameGap": 30,
                  "nameTextStyle": {"fontSize": 11, "color": "#5a6468"},
                  "axisLabel": {"formatter": "{value}%"},
                  "splitLine": {"lineStyle": {"color": "#e6ecec"}}},
        "yAxis": {"type": "value", "name": "% of pensioner households facing a bill",
                  "nameLocation": "middle", "nameGap": 45,
                  "nameTextStyle": {"fontSize": 11, "color": "#5a6468"},
                  "axisLabel": {"formatter": "{value}%"},
                  "splitLine": {"lineStyle": {"color": "#e6ecec"}}},
        "series": series,
    }


# Vertical pitch of the hand-drawn legend rows, and the size of each swatch.
LEGEND_ROW_HEIGHT = 21
LEGEND_SWATCH = 12


def swatch_legend(entries: list[tuple[str, str]]) -> list[dict]:
    """A legend drawn as graphic elements, because ECharts' own one is unusable here.

    The site's chart loader builds legend entries from the name of each series.
    That is right for a line or bar chart and wrong for a pie, which has one
    unnamed series and puts its names on the slices instead. Left to itself the
    loader invents a single entry called "Series 1", matches it against nothing,
    and draws an empty box. Naming the series gets the entries back but paints
    the first swatch from the default palette rather than from the slice, so the
    key ends up disagreeing with the chart it is a key to.

    Drawing it as graphics sidesteps the whole thing. The group is centred and
    pinned to the bottom, so it stays put at any width. The cost is that the
    entries no longer toggle slices on and off, which a two-slice doughnut in the
    middle of an article has no use for anyway."""
    children = []
    for row, (label, colour) in enumerate(entries):
        top = row * LEGEND_ROW_HEIGHT
        children.append({
            "type": "rect", "left": 0, "top": top,
            "shape": {"width": LEGEND_SWATCH, "height": LEGEND_SWATCH, "r": 3},
            "style": {"fill": colour},
        })
        children.append({
            "type": "text", "left": LEGEND_SWATCH + 8, "top": top - 1,
            "style": {"text": label, "fontSize": 12, "fill": "#3d4a4d"},
        })
    return [{"type": "group", "left": "center", "bottom": 8, "children": children}]


def housing_chart() -> dict:
    """The split between homes that put an estate over the line and everything else.

    This one is published at half width, which is why it is laid out differently
    from the bar charts. Names beside the arcs get ellipsised to nothing in a
    440px column, so the arcs carry the percentage and the names go underneath
    in a key. The title is broken by hand for the same reason: ECharts will not
    wrap a title without being told an exact pixel width, and there isn't one
    that is right at every screen size."""
    # Read the current pipeline output.
    gain = json.loads((OUTPUT / "housing_gain.json").read_text())
    total = gain["actual"]["households_exposed"]
    only = gain["nominal"]["exposed_only_by_gain"]
    rest = total - only

    dark, light = "#1a606d", "#c7d6d8"
    # Contrast against each fill: 7.2:1 on the dark arc, 8.0:1 on the light one.
    on_dark, on_light = "#ffffff", "#1b3b41"
    only_label = "Only because of house price rises"
    rest_label = "Not just house price rises"

    return {
        "title": {"text": "Pensioner households facing\nan inheritance tax bill",
                  "left": "50%", "textAlign": "center", "top": 6,
                  "textStyle": {"fontSize": 15, "lineHeight": 20}},
        "tooltip": {"trigger": "item"},
        "_tpaTooltip": {"template": "{name}<br>{y:0,0} households"},
        # The key is drawn below, so the built-in legend stays out of the way.
        "legend": {"show": False},
        "graphic": swatch_legend([(only_label, dark), (rest_label, light)]),
        "series": [{
            "type": "pie",
            "percentPrecision": 0,
            "radius": ["46%", "70%"],
            "center": ["50%", "48%"],
            "avoidLabelOverlap": False,
            "itemStyle": {"borderColor": "#fff", "borderWidth": 2},
            # Inside the arc, so there is nothing to clip at the container edge.
            "label": {"show": True, "position": "inside", "formatter": "{d}%",
                      "fontSize": 16, "fontWeight": "bold"},
            "labelLine": {"show": False},
            "data": [
                {"value": round(only), "name": only_label,
                 "itemStyle": {"color": dark}, "label": {"color": on_dark}},
                {"value": round(rest), "name": rest_label,
                 "itemStyle": {"color": light}, "label": {"color": on_light}},
            ],
        }],
    }


def main() -> None:
    CHARTS.mkdir(parents=True, exist_ok=True)
    rows, average = load()
    top = list(reversed(rows[-HOW_MANY:]))
    bottom = rows[:HOW_MANY]

    charts = {
        f"iht-top50-constituencies.v{VERSION}.json": bar_chart(
            top, average,
            "The 50 constituencies most exposed to inheritance tax", False),
        f"iht-bottom50-constituencies.v{VERSION}.json": bar_chart(
            bottom, average,
            "The 50 constituencies least exposed to inheritance tax", True),
        f"iht-exposure-by-party.v{VERSION}.json": party_chart(rows, average),
        f"iht-conservative-target-seats.v{VERSION}.json": target_seats_chart(rows, average),
        f"iht-exposure-vs-conservative-vote.v{VERSION}.json": scatter_chart(rows),
        f"iht-housing-gain-share.v{VERSION}.json": housing_chart(),
        f"iht-key-findings.v{KEY_FINDINGS_VERSION}.json": key_findings_chart(rows, average),
    }

    for name, option in charts.items():
        path = CHARTS / name
        path.write_text(json.dumps(option, indent=1))
        print(f"  {path}")

    print(f"\nEngland and Wales average: {average}%")
    print(f"top 50 runs {top[0]['v']}% down to {top[-1]['v']}%")
    print(f"bottom 50 runs {bottom[0]['v']}% up to {bottom[-1]['v']}%")


if __name__ == "__main__":
    main()
