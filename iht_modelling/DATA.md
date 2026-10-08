# The data you need, and where to get it

All paths below are relative to the top of the repository. Put the files in a `docs/` folder there.

## 1. The survey

**Wealth and Assets Survey, round 8**, study 7215. Register with the [UK Data Service](https://datacatalogue.ukdataservice.ac.uk/studies/study/7215) and download it.

Unzip it anywhere under `docs/was/`. The model searches that folder for these two files:

```
was_round_8_hhold_eul_may_2025_230525.sav
was_round_8_person_eul_may_2025_230525.sav
```

`iht_modelling/acquire/download_was.py` will fetch them for you once the study is released to your account, or you can just click the link.

## 2. HMRC inheritance tax statistics

Seven tables from the annual release, saved as `.ods` in `docs/hmrc/`:

```
table_12_1_final.ods    numbers of estates and tax, by size of estate
table_12_2_final.ods    exemptions and reliefs claimed
table_12_3_final.ods    assets, by size of estate and by size of tax bill
table_12_4_final.ods    assets, by age, sex and marital status
table_12_5_final.ods    as 12.4, taxpaying estates only
table_12_8_final.ods    tax, by region
table_12_9_final.ods    taxpaying estates, by parliamentary constituency
```

`iht_modelling/acquire/hmrc_tables.py` reads these

Table 12.9 is published on the pre-2024 constituency boundaries. Section 4 below explains how we move it onto today's seats.

## 3. ONS, in `docs/ons/`

| File | What it is | Where |
|---|---|---|
| `uk_population_mid2024.xlsx` | Mid-2024 population estimates | [Population estimates for the UK](https://www.ons.gov.uk/peoplepopulationandcommunity/populationandmigration/populationestimates/datasets/populationestimatesforukenglandandwalesscotlandandnorthernireland) |
| `uk_population_projections_2024based.xlsx` | 2024-based national population projections, which is what the model ages the population to 2027 with | [Zipped population projections data files, UK](https://www.ons.gov.uk/peoplepopulationandcommunity/populationandmigration/populationprojections/datasets/z1zippedpopulationprojectionsdatafilesuk) |
| `nltew_1980_2023_single_year_life_tables.xlsx` | National life tables, single year of age. The model needs single years, not the abridged tables | [Single year life tables, England and Wales](https://www.ons.gov.uk/peoplepopulationandcommunity/birthsdeathsandmarriages/lifeexpectancies/datasets/singleyearlifetablesenglandandwales2023) |
| `mortality_by_marital_status_2010_2019.xlsx` | Deaths at 65 and over split by marital status. This is what the model's marital correction is calibrated against, and it stops at 2019 | [Mortality by marital status](https://www.ons.gov.uk/peoplepopulationandcommunity/birthsdeathsandmarriages/deaths/datasets/mortalitybymaritalstatusinenglandandwales) |
| `population_marital_status_2002_2025.xlsx` | Population by marital status and living arrangements | [Population estimates by marital status](https://www.ons.gov.uk/peoplepopulationandcommunity/populationandmigration/populationestimates/datasets/populationestimatesbymaritalstatusandlivingarrangements) |
| `cohort_fertility_2024_reference_table.xlsx` | Childbearing for women born in different years. This is the fertility table behind every "adult children" figure | [Childbearing for women born in different years](https://www.ons.gov.uk/peoplepopulationandcommunity/birthsdeathsandmarriages/conceptionandfertilityrates/datasets/childbearingforwomenbornindifferentyearsreferencetable) |
| `was_total_wealth_tables_r8.xlsx` | The ONS's own published summaries of the same survey, used to check our wealth figures against theirs | ONS, Wealth and Assets Survey round 8 total wealth tables |
| `uk_hpi_average_prices.csv` | UK House Price Index, average price by region and month. Download the average-price series for Great Britain and the regions | [UK House Price Index](https://landregistry.data.gov.uk/app/ukhpi) |
| `boe_millennium.xlsx` | Bank of England "millennium of macroeconomic data", used for consumer prices before the ONS series starts | [Bank of England research datasets](https://www.bankofengland.co.uk/statistics/research-datasets) |


`iht_modelling/acquire/price_indices.py` downloads the ONS CPI series (D7BT) itself.

## 4. Geography, in `docs/geo/`

From the [ONS Open Geography Portal](https://geoportal.statistics.gov.uk/). Search for each by name and take the generalised (BUC) version, which is small enough to serve in a web map:

```
pcon2024_buc.geojson     Westminster Parliamentary Constituencies, July 2024
pcon_old_buc.geojson     the pre-2024 constituencies, which is what HMRC's data is on
lsoa21_centroids.json    LSOA 2021 population-weighted centroids
lsoa_pop65.csv           LSOA population aged 65 and over
```

The two boundary files are both needed because HMRC publishes on the old seats and we report on the new ones. `iht_modelling/analysis/crosswalk.py` builds a population-weighted crosswalk between them from the 35,672 small areas, which is what the centroids and the LSOA population are for.

## 5. Fetched for you

- **Constituency population by age**, from NOMIS. `iht_modelling/acquire/constituency_population.py`
- **2024 general election results**, from Parliament's Members API. `iht_modelling/acquire/election_results.py`
- **ONS CPI series D7BT**, from the ONS time series API. `iht_modelling/acquire/price_indices.py`

The election script makes two calls per seat across 650 seats, so it is nice and polite, and therefore slow. Please don't be tempted to speed it up. 

## Once you have it

```bash
python3 iht_modelling/run_all.py
```

