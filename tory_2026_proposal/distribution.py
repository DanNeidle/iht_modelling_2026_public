# Copyright (c) 2026 Tax Policy Associates Ltd
# Released under the MIT Licence. See LICENCE in the project root.
"""Who benefits from the Conservative family home proposal: distributional cuts and chart JSON.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _paths  # noqa: E402,F401  (puts the household model on the path)

from lib.config import RULES_2027_28  # noqa: E402
from model.build_units import UNITS_PATH  # noqa: E402
from model import validate  # noqa: E402
from model.administrative import deaths_65_plus_by_marital_status  # noqa: E402
from static_cost import VARIANTS, run_variant  # noqa: E402
import hmrc_crosscheck  # noqa: E402

OUT = HERE / "output"
REGION = {1: "North East", 2: "North West", 4: "Yorkshire and the Humber", 5: "East Midlands",
          6: "West Midlands", 7: "East of England", 8: "London", 9: "South East",
          10: "South West", 11: "Wales", 12: "Scotland"}

BLUE, RED, GREY = "#2563EB", "#D1495B", "#9CA3AF"


def death_weights(units: pd.DataFrame) -> pd.Series:
    """Per-unit expected deaths producing an estate this year, as in validate.simulate_year_of_deaths."""
    mortality = validate.load_mortality_rates()
    oldest = max(mortality)
    band_rates = validate.band_mortality_rates()
    qx = units["age"].map(band_rates)
    qx = qx.fillna(units["age"].clip(upper=oldest).map(mortality)).astype(float)
    exposure = (units["marital_status"] != "Married").astype(float)
    uncorrected = units["weight"] * qx * exposure
    by_status = deaths_65_plus_by_marital_status()
    observed = sum(v for k, v in by_status.items() if k != "Married or civil partnered") / sum(by_status.values())
    modelled = uncorrected.sum() / validate.deaths_at_65_plus(units)
    return uncorrected * (observed / modelled)


def bar_chart(title: str, categories: list[str], values: list[float], value_fmt: str, highlight: list[bool] | None,
              x_label: str, extra_series=None, description: str = "") -> dict:
    data = []
    for i, v in enumerate(values):
        color = RED if (highlight and highlight[i]) else BLUE
        data.append({"value": round(v, 1), "itemStyle": {"color": color}})
    return {
        "backgroundColor": "#ffffff",
        "aria": {"enabled": True, "decal": {"show": False}, "label": {"enabled": True, "description": description}},
        "textStyle": {"fontFamily": "Arial, Helvetica, sans-serif"},
        "title": [{"text": title, "left": "50%", "textAlign": "center", "top": 10,
                   "textStyle": {"fontSize": 20, "fontWeight": 700, "color": "#111827"}}],
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}, "confine": True},
        "_tpaTooltip": {"header": "{name}", "template": "{series}: " + value_fmt, "skipNull": True},
        "grid": {"left": "2%", "right": "12%", "top": 60, "bottom": "6%", "containLabel": True},
        "xAxis": {"type": "value", "axisLabel": {"formatter": value_fmt.replace("{y:", "{value").replace("}", "}") if False else None},
                  "splitLine": {"lineStyle": {"color": "#E5E7EB"}}},
        "yAxis": {"type": "category", "inverse": True, "data": categories,
                  "axisLabel": {"fontSize": 11, "color": "#111827"}, "axisTick": {"show": False}},
        "series": [{"name": x_label, "type": "bar", "barMaxWidth": 26, "data": data,
                    "label": {"show": True, "position": "right", "color": "#111827", "fontSize": 11}}],
    }


def finish(chart: dict, axis_fmt: str, label_fmt: str) -> dict:
    chart["xAxis"]["axisLabel"] = {"formatter": axis_fmt}
    chart["series"][0]["label"]["formatter"] = label_fmt
    return chart


def main() -> None:
    units = pd.read_parquet(UNITS_PATH)
    base = run_variant(units, RULES_2027_28, VARIANTS[0]).set_index("unit_id")
    cons = run_variant(units, RULES_2027_28, VARIANTS[1]).set_index("unit_id")
    u = units.set_index("unit_id")
    u["deaths"] = death_weights(units).to_numpy()
    u["tax_now"] = base["expected_tax"]
    u["tax_cons"] = cons["expected_tax"]
    u["saving"] = u["tax_now"] - u["tax_cons"]
    u["gross"] = (u.property_wealth + u.financial_wealth + u.physical_wealth + u.business_agricultural + u.dc_pension_wealth)
    total_saving = (u.saving * u.deaths).sum()
    total_tax = (u.tax_now * u.deaths).sum()
    print(f"year of deaths: tax now £{total_tax/1e9:.1f}bn, saving £{total_saving/1e9:.1f}bn ({total_saving/total_tax:.0%})")

    # 1. By estate size
    edges = [0, 0.5e6, 1e6, 1.5e6, 2e6, 3e6, 5e6, 10e6, np.inf]
    labels = ["Under £500k", "£500k to £1m", "£1m to £1.5m", "£1.5m to £2m", "£2m to £3m", "£3m to £5m", "£5m to £10m", "Over £10m"]
    u["band"] = pd.cut(u.gross, edges, labels=labels, right=False)
    g = u.groupby("band", observed=False).apply(lambda d: pd.Series({
        "share_of_cut": (d.saving * d.deaths).sum() / total_saving,
        "estates_now_paying": (d.deaths * base.loc[d.index, "p_over_0"]).sum(),
        "avg_saving_per_paying_estate": (d.saving * d.deaths).sum() / max((d.deaths * base.loc[d.index, "p_over_0"]).sum(), 1e-9),
        "share_of_deaths": d.deaths.sum() / u.deaths.sum(),
        "share_of_tax_now": (d.tax_now * d.deaths).sum() / total_tax,
    }))
    print(g.round(3))
    g.to_csv(OUT / "distribution_by_estate_size.csv")

    # 2. By region
    u["region_name"] = u.region.map(REGION)
    r = u.groupby("region_name").apply(lambda d: pd.Series({
        "share_of_cut": (d.saving * d.deaths).sum() / total_saving,
        "share_of_deaths": d.deaths.sum() / u.deaths.sum(),
        "share_of_65plus_households": d.weight.sum() / u.weight.sum(),
    })).sort_values("share_of_cut", ascending=False)
    print(r.round(3))
    r.to_csv(OUT / "distribution_by_region.csv")

    # 3. By wealth rank of 65-plus households (weighted percentiles of gross estate)
    order = u.sort_values("gross", ascending=False)
    cum = order.weight.cumsum() / order.weight.sum()
    rank_edges = [0, 0.01, 0.05, 0.10, 0.20, 0.50, 1.0001]
    rank_labels = ["Richest 1%", "Next 4% (top 5%)", "Next 5% (top 10%)", "Next 10% (top 20%)", "Next 30% (top half)", "Bottom half"]
    order["rank_band"] = pd.cut(cum, rank_edges, labels=rank_labels, right=True, include_lowest=True)
    w = order.groupby("rank_band", observed=False).apply(lambda d: pd.Series({
        "share_of_cut": (d.saving * d.deaths).sum() / total_saving,
        "wealth_floor": d.gross.min(),
        "avg_saving_per_household_death": (d.saving * d.deaths).sum() / max(d.deaths.sum(), 1e-9),
    }))
    print(w.round(3))
    w.to_csv(OUT / "distribution_by_wealth_rank.csv")

    # 4. HMRC route: home exemption cost by band of current tax bill
    h = hmrc_crosscheck.run(0.6)
    h["share_of_cut"] = h.A_home_only_gbp_m / h.A_home_only_gbp_m.sum()
    h["avg_saving_per_estate"] = h.A_home_only_gbp_m * 1e6 / h.estates
    h["label"] = ["Under £25k", "£25k to £50k", "£50k to £100k", "£100k to £200k", "£200k to £300k", "£300k to £500k", "£500k to £1m", "Over £1m"]
    print(h[["label", "estates", "share_of_cut", "avg_saving_per_estate"]].round(3))
    h.to_csv(OUT / "distribution_hmrc_by_tax_bill.csv", index=False)

    charts = {}
    charts["iht-home-cut-by-estate-size.v3.json"] = finish(bar_chart(
        "Who gets the tax cut?", labels, [100 * x for x in g.share_of_cut], "{y:0.0}%",
        [l in ("£1m to £1.5m", "£1.5m to £2m", "£2m to £3m") for l in labels],
        "Share of the inheritance tax giveaway, by size of estate",
        description="Bar chart of the share of the Conservative inheritance tax cut going to each band of estate size. Estates between £1m and £3m, highlighted, take two thirds of it."),
        "{value}%", "{c}%")
    charts["iht-home-cut-average-saving.v3.json"] = finish(bar_chart(
        "How much each estate saves", labels[1:], [round(x / 1000) for x in g.avg_saving_per_paying_estate.iloc[1:]], "£{y:0}k",
        None, "Average inheritance tax saving per estate that currently pays, by size of estate (£ thousands)",
        description="Bar chart of the average saving per taxpaying estate in each estate size band."),
        "£{value}k", "£{c}k")
    charts["iht-home-cut-by-region.v4.json"] = finish(bar_chart(
        "Where the tax cut goes", list(r.index), [100 * x for x in r.share_of_cut], "{y:0.0}%",
        [n in ("London", "South East") for n in r.index], "Share of the inheritance tax giveaway, by region",
        description="Bar chart of the share of the Conservative inheritance tax cut going to each region of Great Britain. London and the South East, highlighted, take about seven tenths."),
        "{value}%", "{c}%")
    charts["iht-home-cut-by-wealth-rank.v3.json"] = finish(bar_chart(
        "Who gets the tax cut?", rank_labels, [100 * x for x in w.share_of_cut], "{y:0.0}%",
        [l in ("Richest 1%", "Next 4% (top 5%)", "Next 5% (top 10%)", "Next 10% (top 20%)") for l in rank_labels], "Share of the giveaway, by wealth rank of pensioner households",
        description="Bar chart of the share of the Conservative inheritance tax cut by wealth rank of households aged 65 and over."),
        "{value}%", "{c}%")
    charts["iht-home-cut-hmrc-by-tax-bill.v3.json"] = finish(bar_chart(
        "Who gets the tax cut?", list(h.label), [100 * x for x in h.share_of_cut], "{y:0.0}%",
        [l in ("£300k to £500k", "£500k to £1m", "Over £1m") for l in h.label],
        "Share of the home exemption giveaway, by estate's current inheritance tax bill (HMRC data)",
        description="Bar chart from HMRC data of the share of the home exemption's cost going to estates in each band of current tax bill."),
        "{value}%", "{c}%")
    for name, chart in charts.items():
        (OUT / name).write_text(json.dumps(chart, indent=2, ensure_ascii=False) + "\n")
        print("wrote", name)


if __name__ == "__main__":
    main()
