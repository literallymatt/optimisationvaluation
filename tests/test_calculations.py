"""Each expected value is worked out by hand in the comment beside it."""

from datetime import date, timedelta

import polars as pl
import pytest

from calculations import (
    DataProblem,
    InputData,
    TurndownRequest,
    calculate_savings,
    period_start_uk_time,
    periods_in_settlement_day,
)
from tpc_definitions import TPC_DEFINITIONS

MONDAY = date(2026, 11, 16)
RED_PERIOD = 35  # 17:00 to 17:30.
AMBER_PERIOD = 20  # 09:30 to 10:00.


def hh_month(month_start=date(2026, 11, 1), kwh=100.0, kvarh=10.0, overrides=None, days=30):
    """A complete month of flat consumption, with overrides as {(date, period): (kwh, kvarh)}."""
    overrides = overrides or {}
    rows = []
    for day_number in range(days):
        day = month_start + timedelta(days=day_number)
        for period in range(1, periods_in_settlement_day(day) + 1):
            row_kwh, row_kvarh = overrides.get((day, period), (kwh, kvarh))
            rows.append((day, period, row_kwh, row_kvarh, 0.0 if row_kvarh is not None else None))
    return pl.DataFrame(
        rows,
        schema={
            "settlement_date": pl.Date,
            "settlement_period": pl.Int64,
            "import_kwh": pl.Float64,
            "reactive_import_kvarh": pl.Float64,
            "reactive_export_kvarh": pl.Float64,
        },
        orient="row",
    )


def rates(*rows):
    return pl.DataFrame(
        [(tpc, rate, unit, "test") for tpc, rate, unit in rows],
        schema={"tpc": pl.String, "rate": pl.Float64, "unit": pl.String, "rate_source": pl.String},
        orient="row",
    )


def make_data(**changes):
    values = {
        "agreed_capacity_kva": 500.0,
        "hh_consumption": hh_month(),
        "tpc_rates": rates(),
        "exemptions": pl.DataFrame(schema={"tpc": pl.String, "exempt_fraction": pl.Float64}),
        "duos_time_bands": pl.DataFrame(
            [
                ("weekday", "00:00", "07:30", "green"),
                ("weekday", "07:30", "16:00", "amber"),
                ("weekday", "16:00", "19:00", "red"),
                ("weekday", "19:00", "21:00", "amber"),
                ("weekday", "21:00", "24:00", "green"),
                ("weekend", "00:00", "00:00", "green"),
            ],
            schema=["day_type", "start_time", "end_time", "band"],
            orient="row",
        ),
        "duos_unit_rates": pl.DataFrame(
            [("red", 10.0, "p_per_kwh", "test"), ("amber", 1.0, "p_per_kwh", "test"), ("green", 0.1, "p_per_kwh", "test")],
            schema={"band": pl.String, "rate": pl.Float64, "unit": pl.String, "rate_source": pl.String},
            orient="row",
        ),
        "non_working_dates": pl.DataFrame(
            {"non_working_date": [date(2026, 12, 25)]}, schema={"non_working_date": pl.Date}
        ),
    }
    return InputData(**(values | changes))


def run(data=None, settlement_date=MONDAY, period=RED_PERIOD, turndown=50.0):
    request = TurndownRequest("1", settlement_date, period, turndown)
    return calculate_savings(request, data or make_data())


def line(result, tpc):
    return result.lines.filter(pl.col("tpc") == tpc).row(0, named=True)


# --- Settlement periods ---------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "periods"),
    [(date(2026, 11, 16), 48), (date(2026, 10, 25), 50), (date(2027, 3, 28), 46)],
)
def test_clock_changes_alter_the_number_of_periods(day, periods):
    assert periods_in_settlement_day(day) == periods


def test_period_start_times_follow_the_uk_clock():
    assert period_start_uk_time(MONDAY, 35).strftime("%H:%M") == "17:00"
    # Clocks go back at 02:00 on 25 October 2026, so 01:00 happens twice.
    assert period_start_uk_time(date(2026, 10, 25), 3).strftime("%H:%M") == "01:00"
    assert period_start_uk_time(date(2026, 10, 25), 5).strftime("%H:%M") == "01:00"
    assert period_start_uk_time(date(2026, 10, 25), 7).strftime("%H:%M") == "02:00"
    assert period_start_uk_time(date(2026, 10, 25), 50).strftime("%H:%M") == "23:30"
    # Clocks go forward at 01:00 on 28 March 2027, so period 3 starts at 02:00.
    assert period_start_uk_time(date(2027, 3, 28), 3).strftime("%H:%M") == "02:00"
    assert period_start_uk_time(date(2027, 3, 28), 46).strftime("%H:%M") == "23:30"


# --- Per-kWh charges --------------------------------------------------------------


@pytest.mark.parametrize(
    ("rate", "unit", "expected"),
    [
        (12.49, "gbp_per_mwh", 0.6245),  # 50 kWh = 0.05 MWh x £12.49
        (1.0, "p_per_kwh", 0.50),  # 50 kWh x 1p
        (0.00801, "gbp_per_kwh", 0.4005),  # 50 kWh x £0.00801
    ],
)
def test_per_kwh_saving_handles_each_unit(rate, unit, expected):
    result = run(make_data(tpc_rates=rates(("bsuos", rate, unit))))
    assert line(result, "bsuos")["firm_saving_gbp"] == pytest.approx(expected)


def test_exemption_reduces_the_saving():
    data = make_data(
        tpc_rates=rates(("climate_change_levy", 8.0, "gbp_per_mwh")),
        exemptions=pl.DataFrame({"tpc": ["climate_change_levy"], "exempt_fraction": [0.92]}),
    )
    # 0.05 MWh x £8 x (1 - 0.92) = £0.032
    assert line(run(data), "climate_change_levy")["firm_saving_gbp"] == pytest.approx(0.032)


def test_full_exemption_saves_nothing():
    data = make_data(
        tpc_rates=rates(("renewables_obligation", 30.0, "gbp_per_mwh")),
        exemptions=pl.DataFrame({"tpc": ["renewables_obligation"], "exempt_fraction": [1.0]}),
    )
    assert line(run(data), "renewables_obligation")["firm_saving_gbp"] == 0


# --- DUoS unit rates --------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "period", "band", "expected"),
    [
        (MONDAY, RED_PERIOD, "red", 5.00),  # 50 kWh x 10p
        (MONDAY, AMBER_PERIOD, "amber", 0.50),  # 50 kWh x 1p
        (MONDAY, 1, "green", 0.05),  # 50 kWh x 0.1p
        (date(2026, 11, 15), RED_PERIOD, "green", 0.05),  # A Sunday at 17:00.
    ],
)
def test_duos_uses_the_band_for_that_time_and_day(day, period, band, expected):
    result_line = line(run(settlement_date=day, period=period), "duos_unit_rate")
    assert result_line["firm_saving_gbp"] == pytest.approx(expected)
    assert band in result_line["note"]


def test_a_gap_in_the_duos_time_bands_is_an_error():
    bands = pl.DataFrame(
        [("weekday", "00:00", "07:30", "green")],
        schema=["day_type", "start_time", "end_time", "band"],
        orient="row",
    )
    with pytest.raises(DataProblem, match="expected one DUoS band"):
        run(make_data(duos_time_bands=bands))


# --- Capacity Market --------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "period", "is_saved"),
    [
        (MONDAY, 33, True),  # First period of the window, 16:00.
        (MONDAY, 38, True),  # Last period of the window, 18:30.
        (MONDAY, 32, False),  # 15:30.
        (MONDAY, 39, False),  # 19:00.
        (date(2026, 11, 15), 35, False),  # A Sunday.
        (date(2026, 12, 25), 35, False),  # A bank holiday on a Friday.
    ],
)
def test_capacity_market_is_only_saved_in_the_peak_window(day, period, is_saved):
    tpc_rates = rates(("capacity_market_supplier_charge", 50.0, "gbp_per_mwh"))
    hh = hh_month(month_start=day.replace(day=1), days=31 if day.month == 12 else 30)
    data = make_data(tpc_rates=tpc_rates, hh_consumption=hh)
    saving = line(run(data, day, period), "capacity_market_supplier_charge")["firm_saving_gbp"]
    assert saving == pytest.approx(2.50 if is_saved else 0)  # 0.05 MWh x £50


def test_capacity_market_is_not_saved_in_summer():
    data = make_data(
        tpc_rates=rates(("capacity_market_supplier_charge", 50.0, "gbp_per_mwh")),
        hh_consumption=hh_month(month_start=date(2026, 6, 1)),
    )
    result_line = line(run(data, date(2026, 6, 15), 35), "capacity_market_supplier_charge")
    assert result_line["status"] == "outside_window"


# --- Exceeded capacity ------------------------------------------------------------

CAPACITY_RATE = rates(("duos_exceeded_capacity", 5.0, "p_per_kva_day"))


def test_exceeded_capacity_only_falls_to_the_next_highest_period():
    # kVA = 2 x sqrt(kWh^2 + kVArh^2). Reactive is 0 here, so kVA = 2 x kWh.
    # Before: peak 600 kVA, 100 over agreed. After: SP35 drops, but SP30 still gives 520 kVA.
    # Saving: (100 - 20) kVA x £0.05 x 30 days = £120.
    hh = hh_month(
        kwh=100.0,
        kvarh=0.0,
        overrides={(MONDAY, RED_PERIOD): (300.0, 0.0), (MONDAY, 30): (260.0, 0.0)},
    )
    result = run(make_data(tpc_rates=CAPACITY_RATE, hh_consumption=hh), turndown=100.0)
    assert line(result, "duos_exceeded_capacity")["firm_saving_gbp"] == pytest.approx(120.0)


def test_exceeded_capacity_includes_reactive_power():
    # Before: 2 x sqrt(300^2 + 400^2) = 1000 kVA. After a 50% turn-down: 500 kVA.
    # Saving: 500 kVA excess removed x £0.05 x 30 days = £750.
    hh = hh_month(kwh=10.0, kvarh=0.0, overrides={(MONDAY, RED_PERIOD): (300.0, 400.0)})
    result = run(make_data(tpc_rates=CAPACITY_RATE, hh_consumption=hh), turndown=150.0)
    assert line(result, "duos_exceeded_capacity")["firm_saving_gbp"] == pytest.approx(750.0)


def test_no_exceeded_capacity_saving_when_the_period_is_not_the_peak():
    hh = hh_month(kwh=100.0, kvarh=0.0, overrides={(MONDAY, 30): (300.0, 0.0)})
    result = run(make_data(tpc_rates=CAPACITY_RATE, hh_consumption=hh))
    assert line(result, "duos_exceeded_capacity")["status"] == "no_saving"


def test_no_exceeded_capacity_saving_when_below_agreed_capacity():
    result = run(make_data(tpc_rates=CAPACITY_RATE))
    assert line(result, "duos_exceeded_capacity")["firm_saving_gbp"] == 0


def test_exceeded_capacity_is_only_possible_until_the_month_is_complete():
    hh = hh_month(kwh=100.0, kvarh=0.0, overrides={(MONDAY, RED_PERIOD): (300.0, 0.0)})
    incomplete = hh.filter(pl.col("settlement_date") <= MONDAY)
    result = run(make_data(tpc_rates=CAPACITY_RATE, hh_consumption=incomplete), turndown=100.0)
    result_line = line(result, "duos_exceeded_capacity")
    assert result_line["status"] == "possible"
    assert result_line["firm_saving_gbp"] == 0
    # 600 -> 400 kVA removes all 100 kVA of excess: 100 x £0.05 x 30 = £150.
    assert result.possible_saving_gbp == pytest.approx(150.0)
    assert result.firm_saving_gbp == pytest.approx(10.0)  # Only red DUoS: 100 kWh x 10p.


def test_a_clock_change_month_can_still_be_complete():
    october = date(2026, 10, 1)
    day = date(2026, 10, 15)
    hh = hh_month(month_start=october, days=31, kwh=100.0, kvarh=0.0, overrides={(day, 35): (300.0, 0.0)})
    result = run(make_data(tpc_rates=CAPACITY_RATE, hh_consumption=hh), day, 35, 100.0)
    # 100 kVA of excess x £0.05 x 31 days = £155.
    assert line(result, "duos_exceeded_capacity")["firm_saving_gbp"] == pytest.approx(155.0)


def test_missing_agreed_capacity_marks_the_total_incomplete():
    result = run(make_data(tpc_rates=CAPACITY_RATE, agreed_capacity_kva=None))
    assert line(result, "duos_exceeded_capacity")["status"] == "missing_data"
    assert result.is_incomplete


# --- Excess reactive --------------------------------------------------------------

REACTIVE_RATE = rates(("duos_excess_reactive", 1.0, "p_per_kvarh"))


def test_excess_reactive_keeps_the_power_factor_unchanged():
    # Before: 100 kVArh - 0.33 x 200 kWh = 34 chargeable.
    # After a 50% turn-down: 50 - 0.33 x 100 = 17 chargeable. Saving: 17 x 1p = £0.17.
    hh = hh_month(overrides={(MONDAY, RED_PERIOD): (200.0, 100.0)})
    result = run(make_data(tpc_rates=REACTIVE_RATE, hh_consumption=hh), turndown=100.0)
    assert line(result, "duos_excess_reactive")["firm_saving_gbp"] == pytest.approx(0.17)


def test_no_reactive_saving_below_the_threshold():
    result = run(make_data(tpc_rates=REACTIVE_RATE))  # 10 kVArh on 100 kWh.
    assert line(result, "duos_excess_reactive")["status"] == "no_saving"


def test_reactive_export_counts_when_higher_than_import():
    hh = hh_month(overrides={(MONDAY, RED_PERIOD): (200.0, 10.0)}).with_columns(
        pl.when(pl.col("settlement_date") == MONDAY, pl.col("settlement_period") == RED_PERIOD)
        .then(100.0)
        .otherwise(pl.col("reactive_export_kvarh"))
        .alias("reactive_export_kvarh")
    )
    result = run(make_data(tpc_rates=REACTIVE_RATE, hh_consumption=hh), turndown=100.0)
    assert line(result, "duos_excess_reactive")["firm_saving_gbp"] == pytest.approx(0.17)


def test_missing_reactive_data_is_estimated_with_a_warning():
    hh = hh_month(kwh=100.0, kvarh=None, overrides={(MONDAY, RED_PERIOD): (300.0, None)})
    result = run(make_data(tpc_rates=pl.concat([REACTIVE_RATE, CAPACITY_RATE]), hh_consumption=hh), turndown=100.0)
    assert any("estimated at 0.95" in warning for warning in result.warnings)
    assert line(result, "duos_excess_reactive")["firm_saving_gbp"] == pytest.approx(0, abs=1e-9)
    # Before: 2 x sqrt(300^2 + 99^2) = 631.826 kVA. After: 421.2 kVA. Excess removed: 131.826.
    # Saving: 131.826 x £0.05 x 30 = £197.74.
    assert line(result, "duos_exceeded_capacity")["firm_saving_gbp"] == pytest.approx(197.74, abs=0.01)


# --- Charges a turn-down cannot save, and bad or missing data --------------------


@pytest.mark.parametrize("tpc", ["tnuos_locational", "tnuos_residual", "duos_fixed", "duos_agreed_capacity"])
def test_charges_not_affected_by_turndown_are_zero_even_with_a_rate(tpc):
    data = make_data(tpc_rates=rates((tpc, 999.0, "gbp_per_mwh")))
    assert line(run(data), tpc)["firm_saving_gbp"] == 0


def test_every_tpc_gets_a_row_and_the_total_adds_up():
    tpc_rates = rates(("bsuos", 10.0, "gbp_per_mwh"), ("aahedc", 2.0, "gbp_per_mwh"))
    result = run(make_data(tpc_rates=tpc_rates))
    assert result.lines["tpc"].to_list() == [tpc.code for tpc in TPC_DEFINITIONS]
    # BSUoS £0.50 + AAHEDC £0.10 + red DUoS £5.00.
    assert result.firm_saving_gbp == pytest.approx(5.60)


def test_a_missing_required_rate_marks_the_total_incomplete():
    result = run()
    assert line(result, "bsuos")["status"] == "missing_data"
    assert result.is_incomplete
    assert any(warning.startswith("bsuos:") for warning in result.warnings)


def test_a_missing_optional_rate_does_not_mark_the_total_incomplete():
    required = [
        (tpc.code, 1.0, {"kwh": "gbp_per_mwh", "kva_day": "p_per_kva_day"}[tpc.rate_basis])
        for tpc in TPC_DEFINITIONS
        if tpc.rate_required and tpc.rate_basis and tpc.code != "duos_unit_rate"
    ]
    result = run(make_data(tpc_rates=rates(*required)))
    assert line(result, "ncc_levy")["status"] == "not_supplied"
    assert not result.is_incomplete


def test_turndown_above_consumption_is_an_error():
    with pytest.raises(DataProblem, match="larger than"):
        run(turndown=100.1)


def test_turning_down_all_consumption_is_allowed():
    assert run(turndown=100.0).firm_saving_gbp > 0


@pytest.mark.parametrize("turndown", [0.0, -5.0, float("nan")])
def test_turndown_must_be_positive(turndown):
    with pytest.raises(DataProblem, match="above 0"):
        run(turndown=turndown)


@pytest.mark.parametrize("period", [0, 49])
def test_settlement_period_must_exist_on_that_day(period):
    with pytest.raises(DataProblem, match="settlement periods 1 to 48"):
        run(period=period)


def test_missing_hh_data_for_the_period_is_an_error():
    hh = hh_month().filter(pl.col("settlement_period") != RED_PERIOD)
    with pytest.raises(DataProblem, match="No HH consumption"):
        run(make_data(hh_consumption=hh))


def test_duplicate_hh_rows_are_an_error():
    hh = hh_month()
    with pytest.raises(DataProblem, match="more than one row"):
        run(make_data(hh_consumption=pl.concat([hh, hh.head(1)])))


def test_a_rate_in_the_wrong_unit_type_is_an_error():
    with pytest.raises(DataProblem, match="needs a rate per kwh"):
        run(make_data(tpc_rates=rates(("bsuos", 1.0, "p_per_kva_day"))))


def test_an_unknown_rate_unit_is_an_error():
    with pytest.raises(DataProblem, match="unknown rate unit"):
        run(make_data(tpc_rates=rates(("bsuos", 1.0, "pounds"))))


def test_two_rates_for_one_tpc_is_an_error():
    with pytest.raises(DataProblem, match="expected one rate"):
        run(make_data(tpc_rates=rates(("bsuos", 1.0, "gbp_per_mwh"), ("bsuos", 2.0, "gbp_per_mwh"))))


def test_an_unknown_tpc_code_gives_a_warning():
    result = run(make_data(tpc_rates=rates(("bsuoss", 1.0, "gbp_per_mwh"))))
    assert any("bsuoss" in warning for warning in result.warnings)


def test_an_exempt_fraction_outside_0_to_1_is_an_error():
    data = make_data(
        tpc_rates=rates(("bsuos", 1.0, "gbp_per_mwh")),
        exemptions=pl.DataFrame({"tpc": ["bsuos"], "exempt_fraction": [92.0]}),
    )
    with pytest.raises(DataProblem, match="from 0 to 1"):
        run(data)
