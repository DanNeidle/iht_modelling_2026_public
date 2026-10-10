# Sensitivity to the survey round

These scripts rebuild our Conservative inheritance-tax costing with either Round 7 or Round 8 of the Wealth and Assets Survey. Round 7 covers April 2018 to March 2020; Round 8 covers April 2020 to March 2022. The model, tax rules and target valuation dates are held constant. This is not a replication of Oxford Economics' model.

## Inputs

Set up the household model using [the data instructions](../../iht_modelling/DATA.md) and run it as described in [the proposal README](../README.md). The comparison uses its existing price indices, HMRC tables and saved Round 8 units for validation. Put the Round 7 files alongside the Round 8 files in the extracted UK Data Service SPSS directory under `docs/was/`:

```text
was_round_7_hhold_eul_march_2022.sav
was_round_7_person_eul_june_2022.sav
```

Both rounds are from UK Data Service study 7215. The comparison uses the household cross-sectional weight from each round. All five defined contribution pension components used in the estate calculation have counterparts in Round 7. Four DB and in-payment pension fields require aliases because their valuation method changed, but those fields are excluded from estates.

## Run

Run from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python tory_2026_proposal/round7_comparison/compare.py --round both --output notes/new_round_comparison
PYTHONDONTWRITEBYTECODE=1 python tory_2026_proposal/round7_comparison/diagnostics.py notes/new_round_comparison
PYTHONDONTWRITEBYTECODE=1 python tory_2026_proposal/round7_comparison/portfolio.py notes/new_round_comparison
```

Use `--round 7` or `--round 8` for one round only. The diagnostics and portfolio scripts require both rounds. Omitting `--output` creates a timestamped directory under `notes/`. Existing output directories and files cannot be overwritten. The production model and its saved units are not changed.

## Method

Before applying the existing model, Round 7 wealth is converted to the Round 8 survey-window price basis using ratios of the 24-month mean GB house price and CPI indices. Property increases by 9.164%; other assets by 3.510%. Both rounds then use the same target-date uprating, population controls, descendant assumptions and relief remapping. The Pareto threshold is anchored to the same survey-date money. We also run both rounds without the Pareto correction and test leaving Round 7 physical wealth at its original survey value.

The 2023/24 validation reproduces the existing model's conventions, including its relief-property uprating. This experiment changes the survey input rather than repairing or redesigning the shared model. It does not age respondents longitudinally or reproduce Oxford Economics' drawdown assumptions. The comparison cannot distinguish genuine changes in household wealth and composition from differences in survey quality.

The £13.7bn conversion is the common receipts scale used by the article. It does not forecast the timing of behavioural responses. Regional results from `compare.py` describe household-stock savings; the `annual_regional.csv` files from `diagnostics.py` use the article's death weights.

## Reference results

The [aggregate reference results](reference_results/summary.csv) contain no household-level survey data. [Housing diagnostics](reference_results/diagnostics.json) and [portfolio scenarios](reference_results/portfolio.json) are included separately.

| Calculation | Round 7 | Round 8 |
|---|---:|---:|
| Static cost share, April 2027 rules | 58.54% | 63.60% |
| Static cost on £13.7bn receipts scale | £8.02bn | £8.71bn |
| Tax/HMRC ratio, historical validation | 1.62 | 1.45 |
| Taxable estates/HMRC ratio, historical validation | 1.53 | 1.60 |
| Households facing a selling penalty | 1.491m | 1.484m |
| Households facing a downsizing penalty | 0.838m | 0.839m |

The reference JSON records the source hashes and original project-layout paths from the research run. A fresh run records its own paths. Locally generated Parquet files contain household-level survey-derived data; only the aggregate reference files are included in this public folder.

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tory_2026_proposal/round7_comparison/test_compare.py
```

The tests cover price conversion, preservation of household weights and counts, excluded pension components, and refusal to overwrite files. The full runner also checks that Round 8 reproduces the saved model and that protected model files are unchanged.

## Licence

MIT, as in the [repository licence](../../LICENCE).
