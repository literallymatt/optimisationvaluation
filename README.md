<img src="assets/icon.svg" width="64" height="64" alt="">

# OptimisationValuation

Calculates the TPC costs saved, per TPC, by a turn-down in one half-hourly settlement period on one MPAN.

## Run it

```
uv run python main.py
uv run python main.py --mpan 1234567890123 --settlement-date 2026-11-16 --settlement-period 35 --turndown-kwh 100
uv run python main.py --output result.csv
```

You can also edit the settings at the top of `main.py`. It runs on fake sample data until you connect your database.

## Connect your database

1. Read [docs/tpc-data-requirements.md](docs/tpc-data-requirements.md) to see which datapoints each TPC needs.
2. In `data_sources.py`, set `USE_SAMPLE_DATA = False` and set your database URL.
3. Replace the placeholder SQL in each `load_...` function. Each function's docstring lists the columns it must return.

You'll also need to add your database driver, for example `uv add pyodbc`.

## Files

| File | What it does |
|---|---|
| `main.py` | Settings, command-line arguments and printed output |
| `data_sources.py` | **The file to edit:** one query per dataset |
| `tpc_definitions.py` | The TPC list, and the datapoints each one needs |
| `calculations.py` | The rule for each type of saving |
| `sample_data/` | Fake data for running before the database is connected |
| `tests/` | Run with `uv run pytest` |

The scope and decisions are in [the spec](.scratch/tpc-turndown-savings/spec.md).
