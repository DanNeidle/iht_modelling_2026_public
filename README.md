# Who has a stake in inheritance tax?

The model behind [Tax Policy Associates](https://taxpolicy.org.uk)' estimate of how many British households and adults have money riding on inheritance tax, as against the 4.7% of deaths that actually produce a bill.


## Running it

```bash
python3 code/run_all.py
```

That runs the self-tests and then every step in dependency order, each in its own process, so one failure doesn't take the rest down. It takes a few minutes. Every step can also be run on its own.

You need Python 3.12 or later with pandas, numpy, pyreadstat and openpyxl.

### The data

**None of the input data is in this repository.** Almost all of it is free, but it's not ours.

`DATA.md` lists what yuo need. Three of them the scripts fetch for themselves. The rest, you have to do.

The big one is the **Wealth and Assets Survey** round 8 (study 7215), licensed microdata from the UK Data Service. Register with them and download.

Every step stops if its input is missing and tells you which file it wanted.

## Layout

```
code/lib/          the tax rules, and every assumption in one config file
code/acquire/      fetching the published inputs
code/model/        building units, running the tax, validating against HMRC
code/analysis/     the headline, the sensitivities, the map, the charts
code/web/          the interactive constituency map
code/charts/       ECharts JSON for the article's charts
code/output/       the model's results
```

Start with `code/lib/config.py`, which holds every number someone might want to argue with and the reasoning next to it, and `code/lib/iht_rules.py`, which is the tax calculation. `code/lib/test_iht_rules.py` checks it against worked examples you can do by hand.

## Licence

MIT. See `LICENCE`.

Copyright (c) 2026 Tax Policy Associates Ltd.
