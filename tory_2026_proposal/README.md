# Costing the Conservatives' family home inheritance tax proposal

In October 2026 the Conservative Party proposed two changes to inheritance tax:

1. No inheritance tax at all on a main home left to children or grandchildren, with no cap on its value.
2. The ordinary nil-rate band rising from £325,000 to £500,000 per person, for any asset and any heir, with the £175,000 residence nil-rate band abolished.

The party says this would cost about £6bn in 2029-30, on modelling by Oxford Economics for the think tank Onward. The scripts in this folder produce our own costing: the static cost, the likely behavioural responses, who would benefit, and what else the same money could buy.

## Before you start

These scripts reuse the household model in [`../iht_modelling/`](../iht_modelling/README.md), so run that first. In particular, run

```bash
python3 iht_modelling/run_all.py
```

which builds `.tmp/build/units.parquet`, the file of pensioner households valued in April 2027 money that almost everything here reads. That needs the Wealth and Assets Survey and the other inputs in [`../iht_modelling/DATA.md`](../iht_modelling/DATA.md).

The HMRC cross-check, downsizing calculation and `review_calculations.py --hmrc-only` don't need the survey. Their published inputs and assumptions are included in the scripts. The full housing, investment and fixed-cost planning calculations need the household model.

Run everything from the top of the repository, for example `python3 tory_2026_proposal/static_cost.py`. Results go in `tory_2026_proposal/output/`.

To reproduce the article's additional calculations and the £7.7bn comparison budget:

```bash
python3 tory_2026_proposal/review_calculations.py --budget-bn 7.7 --output tory_2026_proposal/output/article_calculations.json
python3 tory_2026_proposal/behaviour.py
```

The first command refuses to overwrite an existing JSON file; choose a new filename for a subsequent run. To check the HMRC retention, downsizing and headline arithmetic without survey files:

```bash
python3 tory_2026_proposal/review_calculations.py --hmrc-only
```

## The scripts, in the order the article uses them

| Script | What it does | Article figure |
|---|---|---|
| `static_cost.py` | Runs the household model under current law and under the proposal (and some variants: caps on the home's value, the threshold change on its own, the home exempt whoever inherits), and turns each into a year of deaths. Reports the share of inheritance tax the policy removes. | Static cost, survey route: about two thirds of the yield |
| `hmrc_crosscheck.py` | The same question answered from HMRC's table 12.3b alone, with no survey. Nets off the residence band each estate already uses. | Static cost, HMRC route: 50% to 53% |
| `sensitivity.py` | Re-runs the survey route with house values marked down, savings marked up, and Oxford Economics' own drawdown assumptions applied. Slow: it rebuilds the households at 2023-24 prices. | The 55% to 67% range in the methodology |
| `distribution.py` | Who gets the money: by size of estate, by region, by wealth rank of pensioner households, and (from HMRC data) by size of current tax bill. Writes the chart files. | "Who benefits?" charts |
| `downsizing_channel.py` | How much tax rides on downsizing by older homeowners, from published figures on how often they move. | Lock-in, downsizing channel |
| `housing_and_savings.py` | Counts the households that would face a tax penalty for selling or downsizing, the homes HMRC's data suggests are sold before death, and the savings that could move into housing. | Housing market and savings sections; lock-in, homes sold before death |
| `review_calculations.py` | Caps retention savings at remaining tax after the £500,000 bands, allows for descendants and existing downsizing relief, calculates the fixed-cost planning sensitivity, and reconciles the total. Also reproduces housing counts, investment shifts and the alternative allowance and rate. | £7.7bn to £10.9bn total; X-marked planning section; corrected housing and investment figures |
| `dynamic_inputs.py` and `behaviour.py` | The behavioural model: how much tax is lost if people move savings into their homes, and how much comes back because people with smaller bills plan less. `dynamic_inputs.py` just runs `behaviour.py`; `test_behaviour.py` checks it against worked examples. | Upsizing and "less avoidance" |
| `alternatives.py` | What else the same money buys: the nil-rate band you could have with no residence band, and the rate you could cut to. | "What if the money was spent on a simple IHT cut?" |
| `bang_for_buck_chart.py` | Our chart comparing the growth effect per pound of sixteen tax cuts, with this one added. The other rows come from earlier articles. | "How does it compare with other tax cuts?" |

`_paths.py` is plumbing: it finds the household model whether this folder sits next to `iht_modelling/` (as here) or inside our own project.

## Figures in the current article

Annual revenue figures below use the OBR's £13.7bn receipts scale for 2029-30. Historical retention and downsizing amounts are multiplied by 13.7 / 7.03. This is a scaling assumption, not an estimate of inflation or the speed of the behavioural response.

| Component | Article estimate |
|---|---|
| Static cost | £7bn to £9bn |
| Retaining homes before death | £250m to £700m |
| Reduced downsizing | £250m to £650m |
| Moving investments into homes | £300m to £600m |
| Revenue recovered through the marginal-rate response | £20m to £70m |
| Total, after subtracting the marginal-rate recovery | £7.7bn to £10.9bn |

Adding the rounded components gives £7.73bn to £10.93bn before final rounding. The fixed-cost planning calculation is a separate sensitivity: £0.9m to £32.5m, depending on assumed costs and take-up. Including it leaves the rounded headline range unchanged. The average-rate scenarios in `behaviour.py` are alternative sensitivities, not additional offsets in this total.

The housing calculation gives 1.48m pensioner households with a tax penalty for selling up and 0.84m for moving to a home worth half as much. About 459,000 eligible homeowners still pay after reform. Moving 7.5% to 15% of their financial wealth into homes, capped at each household's taxable excess, shifts £57bn to £113bn. These are stocks of wealth, not annual revenue losses. The 30% scenario shifts £219bn. The article's £900,000 allowance and 17.5% rate alternatives use a £7.7bn budget.

The GDP comparison deliberately uses £7bn, the low end of the static range, and a £0.3bn GDP ceiling. Its current chart is `iht-family-home-bang-for-buck.v2.json`; the regional chart is `iht-home-cut-by-region.v4.json` and the wealth-rank chart is `iht-home-cut-by-wealth-rank.v3.json`.

Run the worked examples with:

```bash
python3 -m pytest iht_modelling/lib/test_iht_rules.py tory_2026_proposal/test_behaviour.py tory_2026_proposal/test_review_calculations.py -q
```

## How to read the results

For annual revenue estimates, we use the proportion of modelled tax affected and apply it to the OBR's forecast. The household model measures what the living own, not what the dead leave. On the historical validation basis it finds about 1.6 times as many taxpaying estates as HMRC. Spending, gifts and planning are possible explanations, alongside survey and modelling errors. Scaling works only to the extent that those errors affect baseline and reform similarly; `sensitivity.py` explores that assumption. Housing counts and stocks of financial wealth are reported as modelled levels.

The two static routes disagree. The survey says the proposal removes about two thirds of the tax; HMRC's tables say about half. Asset composition, survey errors and differences between living households and estates at death may explain the gap. Neither route identifies the exact cost, so our range covers both.

**Wealth ranks and the very top.** The survey misses the very wealthiest households, and the household model fills the gap with a statistical (Pareto) tail. Anything said about the richest 1% is therefore rather unreliable.

If you find an error, or think an assumption is wrong, please tell us. The most useful thing anyone could do is rerun the HMRC route on HMRC's estate-level data, which would narrow our static range considerably.

## Licence

MIT. See [`LICENCE`](../LICENCE). Copyright (c) 2026 Tax Policy Associates Ltd.
