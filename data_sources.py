"""Where all input data comes from. THIS IS THE FILE TO EDIT.

To connect your database:
  1. Set USE_SAMPLE_DATA to False.
  2. Set the TPC_DATABASE_URL environment variable, or edit DATABASE_URL below.
  3. In each load_... function, replace the placeholder SQL with your own query.

Your query can join anything it likes, as long as it returns the columns listed in
that function's docstring. Use `AS` to rename your columns. Do any filtering for the
MPAN's LLFC, DNO, GSP group or date range inside the query.

The rest of the tool never sees your table names. To see which TPC needs which
datapoint, read docs/tpc-data-requirements.md.
"""

import os
from datetime import date, timedelta
from pathlib import Path

import polars as pl
from sqlalchemy import create_engine, text

from calculations import DataProblem, InputData, TurndownRequest

USE_SAMPLE_DATA = True  # Clearly fake rates, so the tool runs before the DB is connected.
DATABASE_URL = os.environ.get("TPC_DATABASE_URL", "")  # e.g. mssql+pyodbc://... or postgresql://...
SAMPLE_DATA_FOLDER = Path(__file__).parent / "sample_data"


# ---------------------------------------------------------------------------
# EDIT THESE: one function per dataset
# ---------------------------------------------------------------------------


def load_site_details(mpan: str, settlement_date: date) -> pl.DataFrame:
    """The MPAN's fixed details, in zero or one row.

    Returns:
        agreed_capacity_kva (float): Maximum Import Capacity on that date.
    """
    if USE_SAMPLE_DATA:
        return _read_sample("site_details.csv", mpan, settlement_date)

    sql = """
        SELECT agreed_capacity_kva
        FROM your_site_table
        WHERE mpan = :mpan
          AND valid_from <= :settlement_date
          AND valid_to >= :settlement_date
    """
    return _read_sql(sql, mpan=mpan, settlement_date=settlement_date)


def load_hh_consumption(mpan: str, month_start: date, month_end: date) -> pl.DataFrame:
    """Half-hourly consumption for the whole calendar month, EXCLUDING the battery.

    The tool subtracts the turn-down itself. A full month is needed because
    exceeded capacity is charged on the month's highest kVA.

    Returns:
        settlement_date (date)
        settlement_period (int): from 1, up to 46, 48 or 50 depending on the day.
        import_kwh (float): active import.
        reactive_import_kvarh (float, may be null)
        reactive_export_kvarh (float, may be null)
    """
    if USE_SAMPLE_DATA:
        frame = _read_sample("hh_consumption.csv", mpan)
        return frame.filter(_as_date("settlement_date").is_between(month_start, month_end))

    sql = """
        SELECT settlement_date, settlement_period, import_kwh,
               reactive_import_kvarh, reactive_export_kvarh
        FROM your_hh_table
        WHERE mpan = :mpan
          AND settlement_date BETWEEN :month_start AND :month_end
    """
    return _read_sql(sql, mpan=mpan, month_start=month_start, month_end=month_end)


def load_tpc_rates(mpan: str, settlement_date: date) -> pl.DataFrame:
    """One rate per TPC for this MPAN and date. It can be forecast, industry or anything else.

    The MPAN is passed in because any rate can vary by site (for example by LLFC,
    GSP group or contract). Scotland's RO rate, for example, would be selected here.
    DUoS unit rates come from load_duos_unit_rates instead.

    Returns:
        tpc (str): a code from tpc_definitions.py, such as "bsuos".
        rate (float)
        unit (str): gbp_per_mwh, gbp_per_kwh or p_per_kwh for energy charges;
            gbp_per_kva_day or p_per_kva_day for duos_exceeded_capacity;
            gbp_per_kvarh or p_per_kvarh for duos_excess_reactive.
        rate_source (str, may be null): free text shown in the output, such as "forecast".
    """
    if USE_SAMPLE_DATA:
        return _read_sample("tpc_rates.csv", mpan, settlement_date)

    sql = """
        SELECT tpc, rate, unit, rate_source
        FROM your_tpc_rate_table
        WHERE mpan = :mpan
          AND valid_from <= :settlement_date
          AND valid_to >= :settlement_date
    """
    return _read_sql(sql, mpan=mpan, settlement_date=settlement_date)


def load_exemptions(mpan: str, settlement_date: date) -> pl.DataFrame:
    """The fraction of a TPC this MPAN does not pay. Leave out TPCs with no exemption.

    Use it for EII exemptions, and for CCL reliefs such as a CCA reduced rate
    (a 92% discount is 0.92). It applies to per-kWh and Capacity Market TPCs only.

    Returns:
        tpc (str): a code from tpc_definitions.py.
        exempt_fraction (float): from 0 (pays it all) to 1 (pays none).
    """
    if USE_SAMPLE_DATA:
        return _read_sample("exemptions.csv", mpan, settlement_date)

    sql = """
        SELECT tpc, exempt_fraction
        FROM your_exemption_table
        WHERE mpan = :mpan
          AND valid_from <= :settlement_date
          AND valid_to >= :settlement_date
    """
    return _read_sql(sql, mpan=mpan, settlement_date=settlement_date)


def load_duos_time_bands(mpan: str, settlement_date: date) -> pl.DataFrame:
    """The DUoS time windows for this MPAN's DNO and voltage on this date.

    Windows are in UK clock time and must not overlap. Filter any seasonal bands in
    your query. Bank holidays count as weekdays, as in the DNO statements checked so far.

    Returns:
        day_type (str): "weekday" or "weekend".
        start_time (str): "HH:MM", included in the window.
        end_time (str): "HH:MM", not included. Use "24:00" for the end of the day.
        band (str): such as "red", matching load_duos_unit_rates.
    """
    if USE_SAMPLE_DATA:
        return _read_sample("duos_time_bands.csv", mpan, settlement_date)

    sql = """
        SELECT day_type, start_time, end_time, band
        FROM your_duos_time_band_table
        WHERE mpan = :mpan
          AND valid_from <= :settlement_date
          AND valid_to >= :settlement_date
    """
    return _read_sql(sql, mpan=mpan, settlement_date=settlement_date)


def load_duos_unit_rates(mpan: str, settlement_date: date) -> pl.DataFrame:
    """The DUoS unit rate for each band, for this MPAN's LLFC on this date.

    Returns:
        band (str): such as "red".
        rate (float)
        unit (str): gbp_per_mwh, gbp_per_kwh or p_per_kwh.
        rate_source (str, may be null)
    """
    if USE_SAMPLE_DATA:
        return _read_sample("duos_unit_rates.csv", mpan, settlement_date)

    sql = """
        SELECT band, rate, unit, rate_source
        FROM your_duos_unit_rate_table
        WHERE mpan = :mpan
          AND valid_from <= :settlement_date
          AND valid_to >= :settlement_date
    """
    return _read_sql(sql, mpan=mpan, settlement_date=settlement_date)


def load_non_working_dates(settlement_date: date) -> pl.DataFrame:
    """Weekdays that are not Working Days (bank holidays), for the Capacity Market window.

    Only needed for the winter containing settlement_date, but returning more is fine.

    Returns:
        non_working_date (date)
    """
    if USE_SAMPLE_DATA:
        return pl.read_csv(SAMPLE_DATA_FOLDER / "non_working_dates.csv")

    sql = "SELECT non_working_date FROM your_bank_holiday_table"
    return _read_sql(sql)


# ---------------------------------------------------------------------------
# You should not need to edit below here
# ---------------------------------------------------------------------------


def load_input_data(request: TurndownRequest) -> InputData:
    """Call every function above and tidy the results for calculations.py."""
    mpan, settlement_date = request.mpan, request.settlement_date
    month_start = settlement_date.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    month_end = next_month - timedelta(days=1)

    site = _with_columns(
        load_site_details(mpan, settlement_date),
        "load_site_details",
        {"agreed_capacity_kva": pl.Float64},
    )
    if site.height > 1:
        raise DataProblem(f"load_site_details returned {site.height} rows for MPAN {mpan}.")

    return InputData(
        agreed_capacity_kva=site["agreed_capacity_kva"][0] if site.height else None,
        hh_consumption=_with_columns(
            load_hh_consumption(mpan, month_start, month_end),
            "load_hh_consumption",
            {
                "settlement_date": pl.Date,
                "settlement_period": pl.Int64,
                "import_kwh": pl.Float64,
                "reactive_import_kvarh": pl.Float64,
                "reactive_export_kvarh": pl.Float64,
            },
        ),
        tpc_rates=_with_columns(
            load_tpc_rates(mpan, settlement_date),
            "load_tpc_rates",
            {"tpc": pl.String, "rate": pl.Float64, "unit": pl.String, "rate_source": pl.String},
        ),
        exemptions=_with_columns(
            load_exemptions(mpan, settlement_date),
            "load_exemptions",
            {"tpc": pl.String, "exempt_fraction": pl.Float64},
        ),
        duos_time_bands=_with_columns(
            load_duos_time_bands(mpan, settlement_date),
            "load_duos_time_bands",
            {"day_type": pl.String, "start_time": pl.String, "end_time": pl.String, "band": pl.String},
        ),
        duos_unit_rates=_with_columns(
            load_duos_unit_rates(mpan, settlement_date),
            "load_duos_unit_rates",
            {"band": pl.String, "rate": pl.Float64, "unit": pl.String, "rate_source": pl.String},
        ),
        non_working_dates=_with_columns(
            load_non_working_dates(settlement_date),
            "load_non_working_dates",
            {"non_working_date": pl.Date},
        ),
    )


def _with_columns(frame: pl.DataFrame, source: str, schema: dict) -> pl.DataFrame:
    """Keep the expected columns with the expected types, and name any that are missing."""
    missing = [column for column in schema if column not in frame.columns]
    if missing:
        raise DataProblem(f"{source} is missing columns {missing}. It returned {frame.columns}.")
    try:
        return frame.select(
            [
                _as_date(column) if kind == pl.Date else pl.col(column).cast(kind)
                for column, kind in schema.items()
            ]
        )
    except pl.exceptions.PolarsError as error:
        raise DataProblem(f"{source} returned a column of the wrong type: {error}") from error


def _as_date(column: str) -> pl.Expr:
    """Read a date column, whether the source returned text, a date or a datetime."""
    return (
        pl.col(column)
        .cast(pl.String)
        .str.slice(0, 10)
        .str.to_date("%Y-%m-%d")
    )


def _read_sql(sql: str, **parameters) -> pl.DataFrame:
    if not DATABASE_URL:
        raise DataProblem("Set TPC_DATABASE_URL, or DATABASE_URL in data_sources.py.")
    engine = create_engine(DATABASE_URL)
    with engine.connect() as connection:
        return pl.read_database(
            text(sql),
            connection,
            execute_options={"parameters": parameters},
            infer_schema_length=None,
        )


def _read_sample(file_name: str, mpan: str, settlement_date: date | None = None) -> pl.DataFrame:
    frame = pl.read_csv(
        SAMPLE_DATA_FOLDER / file_name,
        schema_overrides={"mpan": pl.String},
        infer_schema_length=None,
    ).filter(pl.col("mpan") == mpan)
    if settlement_date is not None:
        frame = frame.filter(
            _as_date("valid_from") <= settlement_date,
            _as_date("valid_to") >= settlement_date,
        )
    return frame
