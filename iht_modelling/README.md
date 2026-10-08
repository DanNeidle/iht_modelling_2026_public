# Who has a stake in inheritance tax?

The model behind our estimate of how many British households and adults have money riding on inheritance tax, as against the 4.7% of deaths that actually produce a bill. It takes every household in the ONS Wealth and Assets Survey with someone aged 65 or over, values what they own in April 2027 money, and works out what inheritance tax would be due if they died then, under the rules that apply from April 2027 (when unused pension pots come into estates). It then counts the adult children, maps the result onto parliamentary constituencies, and checks itself against HMRC's figures.

The article is [One in five pensioner households could face inheritance tax](https://taxpolicy.org.uk/2026/09/19/inheritance-tax-pensioner-households/), and its methodology section explains the choices in plain English.

## Running it

From the top of the repository:

```bash
python3 iht_modelling/run_all.py
```

That runs the self-tests and then every step in order, each in its own process, so one failure doesn't take the rest down. It takes a few minutes. Every step can also be run on its own.

You need Python 3.12 or later with pandas, numpy, pyreadstat, pyarrow and openpyxl.

A note on paths: in our own project this folder is called `code/`, and some docstrings and messages still say so. Read `code/` as `iht_modelling/`. Generated files go in `.tmp/build/` at the top of the repository, results in `iht_modelling/output/`.

## The data

**None of the input data is in this repository.** [`DATA.md`](DATA.md) lists what you need. Three of the inputs the scripts fetch for themselves; the rest you have to download.

The big one is the **Wealth and Assets Survey** round 8 (study 7215), licensed microdata from the UK Data Service. You'll need to register with them and request the study before you can download it.

Every step stops if its input is missing and tells you which file it wanted.

## Layout

```
iht_modelling/lib/        the tax rules, and every assumption in one config file
iht_modelling/acquire/    fetching and reading the published inputs
iht_modelling/model/      building households, running the tax, validating against HMRC
iht_modelling/analysis/   the headline, the sensitivities, the map, the charts
iht_modelling/web/        the interactive constituency map
iht_modelling/charts/     ECharts JSON for the article's charts
iht_modelling/output/     the model's results
```

Start with `lib/config.py`, which holds every number someone might want to argue with and the reasoning next to it, and `lib/iht_rules.py`, which is the tax calculation. `lib/test_iht_rules.py` checks it against worked examples you can do by hand.

## What it can't tell you

It's a snapshot of the living, not a forecast of what the dead will leave. Run over a year of deaths it finds about 1.6 times as many taxpaying estates as HMRC does, because people spend, give away and pay for care before they die. We report that gap rather than fitting it away; `model/validate.py` measures it. The survey also misses the very wealthiest, which we correct for with a Pareto tail, and it records what people think their homes are worth.

## Licence

MIT. See [`LICENCE`](../LICENCE). Copyright (c) 2026 Tax Policy Associates Ltd.
