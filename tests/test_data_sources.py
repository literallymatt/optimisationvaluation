"""Checks the sample data, the placeholder SQL path and the command line."""

from datetime import date

import polars as pl
import pytest
from sqlalchemy import create_engine, text

import data_sources
import main
from calculations import DataProblem, TurndownRequest, calculate_savings
from tpc_definitions import DATA_POINTS, TPC_DEFINITIONS, render_data_requirements_markdown

REQUEST = TurndownRequest("1234567890123", date(2026, 11, 16), 35, 100.0)
PLACEHOLDER_TABLES = """
CREATE TABLE your_site_table (mpan TEXT, valid_from TEXT, valid_to TEXT, agreed_capacity_kva REAL);
CREATE TABLE your_hh_table (mpan TEXT, settlement_date TEXT, settlement_period INTEGER, import_kwh REAL,
    reactive_import_kvarh REAL, reactive_export_kvarh REAL);
CREATE TABLE your_tpc_rate_table (mpan TEXT, valid_from TEXT, valid_to TEXT, tpc TEXT, rate REAL, unit TEXT,
    rate_source TEXT);
CREATE TABLE your_exemption_table (mpan TEXT, valid_from TEXT, valid_to TEXT, tpc TEXT, exempt_fraction REAL);
CREATE TABLE your_duos_time_band_table (mpan TEXT, valid_from TEXT, valid_to TEXT, day_type TEXT,
    start_time TEXT, end_time TEXT, band TEXT);
CREATE TABLE your_duos_unit_rate_table (mpan TEXT, valid_from TEXT, valid_to TEXT, band TEXT, rate REAL,
    unit TEXT, rate_source TEXT);
CREATE TABLE your_bank_holiday_table (non_working_date TEXT);
"""
TABLE_FOR_SAMPLE_FILE = {
    "site_details.csv": "your_site_table",
    "hh_consumption.csv": "your_hh_table",
    "tpc_rates.csv": "your_tpc_rate_table",
    "exemptions.csv": "your_exemption_table",
    "duos_time_bands.csv": "your_duos_time_band_table",
    "duos_unit_rates.csv": "your_duos_unit_rate_table",
    "non_working_dates.csv": "your_bank_holiday_table",
}


def test_sample_data_gives_the_hand_calculated_total():
    result = calculate_savings(REQUEST, data_sources.load_input_data(REQUEST))
    # Per-kWh: 100 kWh = 0.1 MWh x (10 + 10 + 0.1 + 3 + 0.1 + 30 + 8 + 0.5 + 8 + 0.2) = £6.99.
    # Red DUoS: 100 kWh x 10p = £10. Capacity Market: 0.1 MWh x £50.10 = £5.01.
    # Reactive: 30.9 -> 19.456 kVArh x 0.2p, about £0.0229.
    # Capacity: (590.93 -> 520.10 kVA) x £0.05 x 30 days, about £106.24.
    assert result.firm_saving_gbp == pytest.approx(128.27, abs=0.01)
    assert not result.is_incomplete
    assert result.warnings == []


def test_placeholder_sql_runs_against_a_real_database(tmp_path, monkeypatch):
    """Load the sample data into SQLite, then run through the real SQL path."""
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    engine = create_engine(url)
    with engine.begin() as connection:
        for statement in PLACEHOLDER_TABLES.split(";"):
            if statement.strip():
                connection.execute(text(statement))
        for file_name, table in TABLE_FOR_SAMPLE_FILE.items():
            rows = pl.read_csv(
                data_sources.SAMPLE_DATA_FOLDER / file_name, schema_overrides={"mpan": pl.String}
            )
            columns = ", ".join(f":{column}" for column in rows.columns)
            insert = f"INSERT INTO {table} ({', '.join(rows.columns)}) VALUES ({columns})"
            connection.execute(text(insert), rows.to_dicts())

    expected = calculate_savings(REQUEST, data_sources.load_input_data(REQUEST))

    monkeypatch.setattr(data_sources, "USE_SAMPLE_DATA", False)
    monkeypatch.setattr(data_sources, "DATABASE_URL", url)
    result = calculate_savings(REQUEST, data_sources.load_input_data(REQUEST))

    assert result.firm_saving_gbp == pytest.approx(expected.firm_saving_gbp)
    assert result.lines.equals(expected.lines)


def test_a_missing_column_is_named_in_the_error(monkeypatch):
    monkeypatch.setattr(
        data_sources, "load_exemptions", lambda mpan, day: pl.DataFrame({"tpc": ["bsuos"]})
    )
    with pytest.raises(DataProblem, match=r"load_exemptions is missing columns \['exempt_fraction'\]"):
        data_sources.load_input_data(REQUEST)


def test_dates_are_accepted_as_dates_datetimes_or_text(monkeypatch):
    for value in (date(2026, 12, 25), "2026-12-25", "2026-12-25 00:00:00"):
        monkeypatch.setattr(
            data_sources, "load_non_working_dates", lambda day, v=value: pl.DataFrame({"non_working_date": [v]})
        )
        data = data_sources.load_input_data(REQUEST)
        assert data.non_working_dates["non_working_date"].to_list() == [date(2026, 12, 25)]


def test_arguments_override_the_settings_in_the_script(capsys):
    assert main.main(["--turndown-kwh", "50", "--settlement-period", "20"]) == 0
    output = capsys.readouterr().out
    assert "SP20" in output
    assert "Turn-down 50 kWh" in output


def test_settings_in_the_script_are_the_defaults():
    args = main.parse_arguments([])
    assert args.turndown_kwh == main.TURNDOWN_KWH
    assert args.settlement_date == date.fromisoformat(main.SETTLEMENT_DATE)


def test_output_csv_holds_one_row_per_tpc(tmp_path):
    output = tmp_path / "result.csv"
    assert main.main(["--output", str(output)]) == 0
    assert pl.read_csv(output).height == len(TPC_DEFINITIONS)


def test_a_data_problem_exits_with_a_clear_message(capsys):
    assert main.main(["--turndown-kwh", "9999"]) == 2
    assert "larger than" in capsys.readouterr().err


def test_every_tpc_only_lists_known_datapoints():
    for tpc in TPC_DEFINITIONS:
        assert set(tpc.data_points) <= set(DATA_POINTS), tpc.code


def test_data_requirements_document_is_up_to_date():
    document = main.DATA_REQUIREMENTS_DOC.read_text(encoding="utf-8")
    assert document == render_data_requirements_markdown(), (
        "Run: uv run python main.py --write-datapoints-doc"
    )
