"""Turns a turn-down in one settlement period into a saving for each TPC.

Nothing here touches the database. The data arrives in an InputData object, and
each rule below is a short function. Volumes are at the meter (MSP), with no loss
adjustment.

The DUoS capacity and reactive formulas follow the DNO charging statements, for
example SSEN's 2026/27 statement, paragraphs 2.41 to 2.53.
"""

import calendar
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import polars as pl

from tpc_definitions import TPC_DEFINITIONS, Rule, TpcDefinition

UK_TIME = ZoneInfo("Europe/London")

# Capacity Market: "Periods of High Demand" (EMRS guidance G15).
CAPACITY_MARKET_MONTHS = {11, 12, 1, 2}
CAPACITY_MARKET_START = time(16, 0)
CAPACITY_MARKET_END = time(19, 0)

# Reactive kVArh allowed per kWh before charges apply: sqrt(1/0.95^2 - 1), which DNO
# statements round to 2 decimal places. It is also used to estimate missing reactive data.
REACTIVE_FREE_RATIO = 0.33

# Savings smaller than this are floating-point noise, not money.
TOLERANCE_GBP = 1e-9

# Each rate unit, the volume it applies to, and its multiplier to convert to pounds.
RATE_UNITS = {
    "gbp_per_mwh": ("kwh", 0.001),
    "gbp_per_kwh": ("kwh", 1.0),
    "p_per_kwh": ("kwh", 0.01),
    "gbp_per_kva_day": ("kva_day", 1.0),
    "p_per_kva_day": ("kva_day", 0.01),
    "gbp_per_kvarh": ("kvarh", 1.0),
    "p_per_kvarh": ("kvarh", 0.01),
}


class DataProblem(ValueError):
    """The request or input data cannot produce a trustworthy answer."""


@dataclass(frozen=True)
class TurndownRequest:
    mpan: str
    settlement_date: date
    settlement_period: int
    turndown_kwh: float


@dataclass(frozen=True)
class InputData:
    """Everything from data_sources.py. See that file for each table's columns."""

    agreed_capacity_kva: float | None
    hh_consumption: pl.DataFrame
    tpc_rates: pl.DataFrame
    exemptions: pl.DataFrame
    duos_time_bands: pl.DataFrame
    duos_unit_rates: pl.DataFrame
    non_working_dates: pl.DataFrame


@dataclass(frozen=True)
class SavingLine:
    tpc: str
    name: str
    status: str
    firm_saving_gbp: float = 0.0
    possible_saving_gbp: float = 0.0
    rate: float | None = None
    rate_unit: str | None = None
    rate_source: str | None = None
    exempt_fraction: float | None = None
    note: str = ""


@dataclass(frozen=True)
class SavingsResult:
    lines: pl.DataFrame
    firm_saving_gbp: float
    possible_saving_gbp: float
    is_incomplete: bool
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Rate:
    value: float
    unit: str
    source: str | None
    gbp_per_volume: float


@dataclass
class _Context:
    request: TurndownRequest
    data: InputData
    period_start: datetime  # UK clock time.
    month: pl.DataFrame  # HH data with kVA before and after the turn-down.
    warnings: list[str]


# ---------------------------------------------------------------------------
# Settlement periods
# ---------------------------------------------------------------------------


def periods_in_settlement_day(settlement_date: date) -> int:
    """48 on a normal day, 46 when clocks go forward, and 50 when they go back."""
    next_date = settlement_date + timedelta(days=1)
    day_start = datetime.combine(settlement_date, time(0), UK_TIME).astimezone(UTC)
    day_end = datetime.combine(next_date, time(0), UK_TIME).astimezone(UTC)
    return int((day_end - day_start) / timedelta(minutes=30))


def period_start_uk_time(settlement_date: date, settlement_period: int) -> datetime:
    """The UK clock time at which a settlement period starts."""
    day_start = datetime.combine(settlement_date, time(0), UK_TIME).astimezone(UTC)
    start = day_start + timedelta(minutes=30) * (settlement_period - 1)
    return start.astimezone(UK_TIME)


def days_in_month(any_date: date) -> int:
    return calendar.monthrange(any_date.year, any_date.month)[1]


# ---------------------------------------------------------------------------
# Main calculation
# ---------------------------------------------------------------------------


def calculate_savings(request: TurndownRequest, data: InputData) -> SavingsResult:
    """Value the turn-down for every TPC and add up the totals."""
    _check_request(request, data)
    warnings = _warnings_about_input_data(data)
    context = _Context(
        request=request,
        data=data,
        period_start=period_start_uk_time(request.settlement_date, request.settlement_period),
        month=_month_with_turndown(request, data.hh_consumption),
        warnings=warnings,
    )

    lines = [_RULES[tpc.rule](tpc, context) for tpc in TPC_DEFINITIONS]

    return SavingsResult(
        lines=_lines_to_dataframe(lines),
        firm_saving_gbp=sum(line.firm_saving_gbp for line in lines),
        possible_saving_gbp=sum(line.possible_saving_gbp for line in lines),
        is_incomplete=any(line.status == "missing_data" for line in lines),
        warnings=context.warnings,
    )


# ---------------------------------------------------------------------------
# The rules: one function for each way a TPC can be saved
# ---------------------------------------------------------------------------


def _per_kwh(tpc: TpcDefinition, context: _Context) -> SavingLine:
    """A charge on every kWh: turn-down x rate x (1 - exempt fraction)."""
    rate = _find_rate(tpc, context.data.tpc_rates)
    if rate is None:
        return _no_rate_line(tpc, context)
    exempt_fraction = _exempt_fraction(tpc, context)
    saving = context.request.turndown_kwh * rate.gbp_per_volume * (1 - exempt_fraction)
    return _line(tpc, rate, firm_saving_gbp=saving, exempt_fraction=exempt_fraction)


def _duos_unit_rate(tpc: TpcDefinition, context: _Context) -> SavingLine:
    """The rate depends on which band (red, amber or green) covers this period."""
    band = _duos_band_for_period(context)
    rates = context.data.duos_unit_rates.filter(pl.col("band").str.to_lowercase() == band.lower())
    rate = _single_rate(tpc, rates, f"band '{band}'")
    if rate is None:
        return _no_rate_line(tpc, context, f"no rate for band '{band}'")
    saving = context.request.turndown_kwh * rate.gbp_per_volume
    return _line(tpc, rate, firm_saving_gbp=saving, note=f"{band} band")


def _capacity_market_peak(tpc: TpcDefinition, context: _Context) -> SavingLine:
    """Charged on demand in the winter peak window, so only saved in that window."""
    if not _is_capacity_market_peak(context):
        return SavingLine(
            tpc.code,
            tpc.name,
            "outside_window",
            note="Only charged 16:00-19:00 on Working Days, November to February",
        )
    return _per_kwh(tpc, context)


def _exceeded_capacity(tpc: TpcDefinition, context: _Context) -> SavingLine:
    """Saved only if the turn-down lowers the month's highest kVA above agreed capacity.

    A breach is charged at the rate x the excess kVA for every day of the month, so
    one half hour can move a whole month of charges. A later period could still set
    a new peak until the month's data is complete, so the saving is only "possible"
    until then.
    """
    rate = _find_rate(tpc, context.data.tpc_rates)
    if rate is None:
        return _no_rate_line(tpc, context)

    agreed_capacity = context.data.agreed_capacity_kva
    if agreed_capacity is None:
        return _no_rate_line(tpc, context, "no agreed capacity")

    peak_before = context.month["kva_before"].max()
    peak_after = context.month["kva_after"].max()
    excess_before = max(peak_before - agreed_capacity, 0.0)
    excess_after = max(peak_after - agreed_capacity, 0.0)

    days = days_in_month(context.request.settlement_date)
    saving = (excess_before - excess_after) * rate.gbp_per_volume * days
    note = (
        f"Monthly peak {peak_before:.1f} -> {peak_after:.1f} kVA, "
        f"agreed capacity {agreed_capacity:.1f} kVA"
    )

    if saving > TOLERANCE_GBP and not _month_is_complete(context):
        context.warnings.append(
            f"{tpc.code}: the month's HH data is incomplete, so a later period could "
            "still set a new peak. This saving is only possible."
        )
        return _line(tpc, rate, possible_saving_gbp=saving, note=note)
    return _line(tpc, rate, firm_saving_gbp=saving, note=note)


def _excess_reactive(tpc: TpcDefinition, context: _Context) -> SavingLine:
    """Saved only if this period's kVArh is above 33% of its kWh."""
    rate = _find_rate(tpc, context.data.tpc_rates)
    if rate is None:
        return _no_rate_line(tpc, context)

    period = context.month.filter(
        pl.col("settlement_date") == context.request.settlement_date,
        pl.col("settlement_period") == context.request.settlement_period,
    ).row(0, named=True)
    before = _chargeable_reactive(period["import_kwh"], period["reactive_kvarh"])
    after = _chargeable_reactive(period["kwh_after"], period["reactive_after"])
    saving = max(before - after, 0.0) * rate.gbp_per_volume
    note = f"Chargeable reactive {before:.1f} -> {after:.1f} kVArh"
    return _line(tpc, rate, firm_saving_gbp=saving, note=note)


def _not_affected(tpc: TpcDefinition, context: _Context) -> SavingLine:
    return SavingLine(tpc.code, tpc.name, "not_affected", note=tpc.how_saved)


def _handled_elsewhere(tpc: TpcDefinition, context: _Context) -> SavingLine:
    return SavingLine(tpc.code, tpc.name, "handled_elsewhere", note=tpc.how_saved)


_RULES = {
    Rule.PER_KWH: _per_kwh,
    Rule.DUOS_UNIT_RATE: _duos_unit_rate,
    Rule.CAPACITY_MARKET_PEAK: _capacity_market_peak,
    Rule.EXCEEDED_CAPACITY: _exceeded_capacity,
    Rule.EXCESS_REACTIVE: _excess_reactive,
    Rule.NOT_AFFECTED: _not_affected,
    Rule.HANDLED_ELSEWHERE: _handled_elsewhere,
}


# ---------------------------------------------------------------------------
# Helpers: rates
# ---------------------------------------------------------------------------


def _find_rate(tpc: TpcDefinition, tpc_rates: pl.DataFrame) -> _Rate | None:
    rows = tpc_rates.filter(pl.col("tpc") == tpc.code)
    return _single_rate(tpc, rows, "this MPAN and date")


def _single_rate(tpc: TpcDefinition, rows: pl.DataFrame, description: str) -> _Rate | None:
    """Return the one rate in rows. Two different answers would be a guess, so refuse."""
    if rows.height == 0:
        return None
    if rows.height > 1:
        raise DataProblem(f"{tpc.code}: expected one rate for {description}, found {rows.height}.")

    row = rows.row(0, named=True)
    if row["rate"] is None:
        return None
    unit = row["unit"]
    if unit not in RATE_UNITS:
        raise DataProblem(f"{tpc.code}: unknown rate unit '{unit}'. Use one of {sorted(RATE_UNITS)}.")
    basis, to_gbp = RATE_UNITS[unit]
    if basis != tpc.rate_basis:
        raise DataProblem(f"{tpc.code}: needs a rate per {tpc.rate_basis}, but got '{unit}'.")
    return _Rate(row["rate"], unit, row["rate_source"], row["rate"] * to_gbp)


def _exempt_fraction(tpc: TpcDefinition, context: _Context) -> float:
    rows = context.data.exemptions.filter(pl.col("tpc") == tpc.code)
    if rows.height == 0:
        return 0.0
    if rows.height > 1:
        raise DataProblem(f"{tpc.code}: expected one exemption, found {rows.height}.")
    fraction = rows["exempt_fraction"][0]
    if fraction is None or not 0 <= fraction <= 1:
        raise DataProblem(f"{tpc.code}: exempt_fraction must be from 0 to 1, got {fraction}.")
    return fraction


def _no_rate_line(tpc: TpcDefinition, context: _Context, reason: str = "no rate") -> SavingLine:
    """A required datapoint missing makes the total incomplete. An optional one is just £0."""
    if tpc.rate_required:
        context.warnings.append(f"{tpc.code}: {reason} supplied, so it was not valued.")
        return SavingLine(tpc.code, tpc.name, "missing_data", note=reason)
    return SavingLine(tpc.code, tpc.name, "not_supplied", note=f"{reason}, so valued at £0")


def _line(
    tpc: TpcDefinition,
    rate: _Rate,
    firm_saving_gbp: float = 0.0,
    possible_saving_gbp: float = 0.0,
    exempt_fraction: float | None = None,
    note: str = "",
) -> SavingLine:
    if possible_saving_gbp > TOLERANCE_GBP:
        status = "possible"
    elif firm_saving_gbp > TOLERANCE_GBP:
        status = "saved"
    else:
        status = "no_saving"
    return SavingLine(
        tpc=tpc.code,
        name=tpc.name,
        status=status,
        firm_saving_gbp=firm_saving_gbp,
        possible_saving_gbp=possible_saving_gbp,
        rate=rate.value,
        rate_unit=rate.unit,
        rate_source=rate.source,
        exempt_fraction=exempt_fraction,
        note=note,
    )


# ---------------------------------------------------------------------------
# Helpers: time windows
# ---------------------------------------------------------------------------


def _duos_band_for_period(context: _Context) -> str:
    """Find the band whose time window contains the start of this period."""
    start = context.period_start
    day_type = "weekend" if start.weekday() >= 5 else "weekday"
    clock_time = start.strftime("%H:%M")

    bands = context.data.duos_time_bands.with_columns(
        pl.col("start_time").str.slice(0, 5),
        # An end time of 00:00 means midnight at the end of the day.
        pl.col("end_time").str.slice(0, 5).replace("00:00", "24:00"),
    ).filter(
        pl.col("day_type").str.to_lowercase() == day_type,
        pl.col("start_time") <= clock_time,
        pl.col("end_time") > clock_time,
    )
    if bands.height != 1:
        raise DataProblem(
            f"duos_unit_rate: expected one DUoS band for a {day_type} at {clock_time}, "
            f"found {bands.height}. Check load_duos_time_bands."
        )
    return bands["band"][0]


def _is_capacity_market_peak(context: _Context) -> bool:
    start = context.period_start
    if start.month not in CAPACITY_MARKET_MONTHS or start.weekday() >= 5:
        return False
    if context.request.settlement_date in context.data.non_working_dates["non_working_date"].to_list():
        return False
    return CAPACITY_MARKET_START <= start.time() < CAPACITY_MARKET_END


# ---------------------------------------------------------------------------
# Helpers: half-hourly consumption
# ---------------------------------------------------------------------------


def _month_with_turndown(request: TurndownRequest, hh: pl.DataFrame) -> pl.DataFrame:
    """Add reactive power and kVA, before and after the turn-down, for each period.

    The turn-down is assumed to keep the power factor unchanged, so reactive power
    in the turned-down period falls in the same proportion as active power.
    Missing reactive data is estimated at 0.95 power factor, as DNOs do.
    """
    is_turned_down = (pl.col("settlement_date") == request.settlement_date) & (
        pl.col("settlement_period") == request.settlement_period
    )
    share_remaining = (
        pl.when(is_turned_down)
        .then((pl.col("import_kwh") - request.turndown_kwh) / pl.col("import_kwh"))
        .otherwise(1.0)
    )
    return (
        hh.filter(pl.col("import_kwh").is_not_null())
        .with_columns(
            pl.max_horizontal("reactive_import_kvarh", "reactive_export_kvarh")
            .fill_null(pl.col("import_kwh") * REACTIVE_FREE_RATIO)
            .alias("reactive_kvarh")
        )
        .with_columns(
            (pl.col("import_kwh") * share_remaining).alias("kwh_after"),
            (pl.col("reactive_kvarh") * share_remaining).alias("reactive_after"),
        )
        .with_columns(
            _kva("import_kwh", "reactive_kvarh").alias("kva_before"),
            _kva("kwh_after", "reactive_after").alias("kva_after"),
        )
    )


def _kva(kwh_column: str, kvarh_column: str) -> pl.Expr:
    """Average kVA in a half hour. Doubling converts half-hourly volume to an hourly rate."""
    apparent = (pl.col(kwh_column).pow(2) + pl.col(kvarh_column).pow(2)).sqrt()
    # DNO rule: reactive power only counts at times of active import.
    return pl.when(pl.col(kwh_column) > 0).then(2 * apparent).otherwise(0.0)


def _chargeable_reactive(kwh: float, kvarh: float) -> float:
    if kwh <= 0:
        return 0.0
    return max(kvarh - REACTIVE_FREE_RATIO * kwh, 0.0)


def _month_is_complete(context: _Context) -> bool:
    """True if the HH data has every settlement period of every day in the month."""
    any_date = context.request.settlement_date
    expected = sum(
        periods_in_settlement_day(any_date.replace(day=day))
        for day in range(1, days_in_month(any_date) + 1)
    )
    return context.month.height == expected


# ---------------------------------------------------------------------------
# Helpers: checks and output
# ---------------------------------------------------------------------------


def _check_request(request: TurndownRequest, data: InputData) -> None:
    if not math.isfinite(request.turndown_kwh) or request.turndown_kwh <= 0:
        raise DataProblem(f"Turn-down must be above 0 kWh, got {request.turndown_kwh}.")

    periods = periods_in_settlement_day(request.settlement_date)
    if not 1 <= request.settlement_period <= periods:
        raise DataProblem(
            f"{request.settlement_date} has settlement periods 1 to {periods}, "
            f"got {request.settlement_period}."
        )

    hh = data.hh_consumption
    if hh.select(pl.struct("settlement_date", "settlement_period").is_duplicated().any()).item():
        raise DataProblem("The HH data has more than one row for the same settlement period.")

    period = hh.filter(
        pl.col("settlement_date") == request.settlement_date,
        pl.col("settlement_period") == request.settlement_period,
    )
    if period.height == 0 or period["import_kwh"][0] is None:
        raise DataProblem(
            f"No HH consumption for MPAN {request.mpan} on {request.settlement_date} "
            f"period {request.settlement_period}."
        )
    consumption = period["import_kwh"][0]
    if request.turndown_kwh > consumption:
        raise DataProblem(
            f"Turn-down of {request.turndown_kwh:g} kWh is larger than that period's "
            f"consumption of {consumption:g} kWh. Export is not valued."
        )


def _warnings_about_input_data(data: InputData) -> list[str]:
    warnings = []

    # A mistyped code would otherwise leave a rate silently unused.
    known_codes = {tpc.code for tpc in TPC_DEFINITIONS}
    for source, frame in (("load_tpc_rates", data.tpc_rates), ("load_exemptions", data.exemptions)):
        for code in sorted(set(frame["tpc"].to_list()) - known_codes):
            warnings.append(f"{source} returned an unknown TPC code, which was ignored: '{code}'.")

    has_reactive = pl.max_horizontal("reactive_import_kvarh", "reactive_export_kvarh").is_not_null()
    if not data.hh_consumption.select(has_reactive.all()).item():
        warnings.append("Some reactive data is missing, so it was estimated at 0.95 power factor.")
    return warnings


def _lines_to_dataframe(lines: list[SavingLine]) -> pl.DataFrame:
    return pl.DataFrame(
        [vars(line) for line in lines],
        schema={
            "tpc": pl.String,
            "name": pl.String,
            "status": pl.String,
            "firm_saving_gbp": pl.Float64,
            "possible_saving_gbp": pl.Float64,
            "rate": pl.Float64,
            "rate_unit": pl.String,
            "rate_source": pl.String,
            "exempt_fraction": pl.Float64,
            "note": pl.String,
        },
    )
