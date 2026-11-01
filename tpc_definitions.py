"""The list of TPCs, and exactly which datapoints each one needs.

This file holds no calculations. Read it to answer two questions:
  1. Which TPCs does the tool value?
  2. What information must be supplied to calculate each of them?

To add a TPC charged per kWh, add one entry to TPC_DEFINITIONS and supply its rate
from data_sources.load_tpc_rates. No other code changes.

docs/tpc-data-requirements.md is generated from this file.
Regenerate it with:  uv run python main.py --write-datapoints-doc
"""

from dataclasses import dataclass
from enum import StrEnum


class Rule(StrEnum):
    """How a turn-down becomes a saving. Each rule is a function in calculations.py."""

    PER_KWH = "per_kwh"
    DUOS_UNIT_RATE = "duos_unit_rate"
    CAPACITY_MARKET_PEAK = "capacity_market_peak"
    EXCEEDED_CAPACITY = "exceeded_capacity"
    EXCESS_REACTIVE = "excess_reactive"
    NOT_AFFECTED = "not_affected"
    HANDLED_ELSEWHERE = "handled_elsewhere"


@dataclass(frozen=True)
class DataPoint:
    description: str
    source: str


DATA_POINTS = {
    "turndown_kwh": DataPoint(
        "Turn-down in the settlement period, in kWh at the meter (MSP)",
        "`--turndown-kwh` or the settings at the top of `main.py`",
    ),
    "period_import_kwh": DataPoint(
        "Active import in the settlement period, excluding the battery",
        "`load_hh_consumption`: `import_kwh`",
    ),
    "tpc_rate": DataPoint(
        "The rate for this TPC, MPAN and date, and its unit",
        "`load_tpc_rates`: `rate`, `unit`",
    ),
    "exempt_fraction": DataPoint(
        "Fraction of the charge not payable, from 0 to 1 (for example EII or CCA). "
        "Treated as 0 if there is no row",
        "`load_exemptions`: `exempt_fraction`",
    ),
    "duos_time_bands": DataPoint(
        "Which DUoS band applies at each UK clock time, by day type",
        "`load_duos_time_bands`: `day_type`, `start_time`, `end_time`, `band`",
    ),
    "duos_band_rate": DataPoint(
        "The DUoS unit rate for each band, and its unit",
        "`load_duos_unit_rates`: `band`, `rate`, `unit`",
    ),
    "non_working_dates": DataPoint(
        "Weekdays that are not Working Days under the Capacity Market rules",
        "`load_non_working_dates`: `non_working_date`",
    ),
    "month_import_kwh": DataPoint(
        "Active import for every settlement period in the calendar month",
        "`load_hh_consumption`: `import_kwh`",
    ),
    "month_reactive_kvarh": DataPoint(
        "Reactive import and export for every settlement period in the calendar "
        "month. Estimated at 0.95 power factor, with a warning, if missing",
        "`load_hh_consumption`: `reactive_import_kvarh`, `reactive_export_kvarh`",
    ),
    "period_reactive_kvarh": DataPoint(
        "Reactive import and export in the settlement period. Estimated at 0.95 "
        "power factor, with a warning, if missing",
        "`load_hh_consumption`: `reactive_import_kvarh`, `reactive_export_kvarh`",
    ),
    "agreed_capacity_kva": DataPoint(
        "The MPAN's Maximum Import Capacity (agreed capacity) in kVA",
        "`load_site_details`: `agreed_capacity_kva`",
    ),
}


@dataclass(frozen=True)
class TpcDefinition:
    code: str  # The value of the `tpc` column in load_tpc_rates and load_exemptions.
    name: str
    rule: Rule
    how_saved: str
    varies_by: str
    data_points: tuple[str, ...] = ()
    rate_required: bool = True  # False: no rate means £0, with no "incomplete" warning.
    rate_basis: str | None = None  # "kwh", "kva_day" or "kvarh".


_PER_KWH_DATA = ("turndown_kwh", "tpc_rate", "exempt_fraction")


def _per_kwh(code: str, name: str, varies_by: str, rate_required: bool = True) -> TpcDefinition:
    return TpcDefinition(
        code=code,
        name=name,
        rule=Rule.PER_KWH,
        how_saved="Turn-down kWh x rate x (1 - exempt fraction)",
        varies_by=varies_by,
        data_points=_PER_KWH_DATA,
        rate_required=rate_required,
        rate_basis="kwh",
    )


TPC_DEFINITIONS = (
    _per_kwh("bsuos", "BSUoS", "Date (tariff period)"),
    _per_kwh("cfd_interim_levy", "CfD interim levy", "Date (quarter), EII status"),
    _per_kwh("cfd_operational_levy", "CfD operational levy", "Date (financial year), EII status"),
    _per_kwh("nuclear_rab_interim_levy", "Nuclear RAB interim levy", "Date (quarter), EII status"),
    _per_kwh(
        "nuclear_rab_operational_levy",
        "Nuclear RAB operational levy",
        "Date (financial year), EII status",
    ),
    _per_kwh("renewables_obligation", "Renewables Obligation", "Date (obligation year), EII status"),
    _per_kwh("feed_in_tariff", "Feed-in Tariff", "Date, EII status"),
    _per_kwh("aahedc", "AAHEDC", "Date (financial year)"),
    _per_kwh("climate_change_levy", "Climate Change Levy", "Date (financial year), CCA or other relief"),
    _per_kwh("bsc_charges", "BSC charges", "Date", rate_required=False),
    _per_kwh("ncc_levy", "NCC / EII Support Levy", "Date", rate_required=False),
    TpcDefinition(
        code="duos_unit_rate",
        name="DUoS unit rate",
        rule=Rule.DUOS_UNIT_RATE,
        how_saved="Turn-down kWh x the rate for the band that applies in this period",
        varies_by="DNO, LLFC, voltage, UK clock time, day type, date",
        data_points=("turndown_kwh", "duos_time_bands", "duos_band_rate"),
        rate_basis="kwh",
    ),
    TpcDefinition(
        code="capacity_market_supplier_charge",
        name="Capacity Market supplier charge",
        rule=Rule.CAPACITY_MARKET_PEAK,
        how_saved=(
            "Turn-down kWh x rate x (1 - exempt fraction), only from 16:00 to 19:00 "
            "UK time on Working Days, November to February. Otherwise £0. The rate "
            "must be per unit of peak-period demand"
        ),
        varies_by="Delivery year, UK clock time, Working Day, EII status",
        data_points=(*_PER_KWH_DATA, "non_working_dates"),
        rate_basis="kwh",
    ),
    TpcDefinition(
        code="capacity_market_settlement_levy",
        name="Capacity Market settlement levy",
        rule=Rule.CAPACITY_MARKET_PEAK,
        how_saved="Same rule as the Capacity Market supplier charge",
        varies_by="Financial year, UK clock time, Working Day, EII status",
        data_points=(*_PER_KWH_DATA, "non_working_dates"),
        rate_required=False,
        rate_basis="kwh",
    ),
    TpcDefinition(
        code="duos_exceeded_capacity",
        name="DUoS exceeded capacity",
        rule=Rule.EXCEEDED_CAPACITY,
        how_saved=(
            "Fall in the month's highest kVA above agreed capacity x rate x days in "
            "month. Only counted as firm when the month's HH data is complete"
        ),
        varies_by="DNO, LLFC, agreed capacity, the MPAN's monthly peak kVA, days in month",
        data_points=(
            "turndown_kwh",
            "tpc_rate",
            "agreed_capacity_kva",
            "month_import_kwh",
            "month_reactive_kvarh",
        ),
        rate_basis="kva_day",
    ),
    TpcDefinition(
        code="duos_excess_reactive",
        name="DUoS excess reactive power",
        rule=Rule.EXCESS_REACTIVE,
        how_saved=(
            "Fall in kVArh above 33% of kWh in this period x rate. Assumes the turn-down "
            "keeps the power factor unchanged, so kVArh falls in line with kWh"
        ),
        varies_by="DNO, LLFC, the MPAN's power factor. May not apply to EHV (EDCM) sites",
        data_points=("turndown_kwh", "tpc_rate", "period_import_kwh", "period_reactive_kvarh"),
        rate_required=False,
        rate_basis="kvarh",
    ),
    TpcDefinition(
        code="tnuos_locational",
        name="TNUoS locational (Triad)",
        rule=Rule.HANDLED_ELSEWHERE,
        how_saved="£0. Triad value is accounted for elsewhere",
        varies_by="Not used",
    ),
    TpcDefinition(
        code="tnuos_residual",
        name="TNUoS residual",
        rule=Rule.NOT_AFFECTED,
        how_saved="£0. A daily charge per site, by band",
        varies_by="Not used",
    ),
    TpcDefinition(
        code="duos_fixed",
        name="DUoS fixed charges",
        rule=Rule.NOT_AFFECTED,
        how_saved="£0. A daily charge per site",
        varies_by="Not used",
    ),
    TpcDefinition(
        code="duos_agreed_capacity",
        name="DUoS agreed capacity",
        rule=Rule.NOT_AFFECTED,
        how_saved="£0. Set by the agreed capacity, not by consumption",
        varies_by="Not used",
    ),
)


def render_data_requirements_markdown() -> str:
    """Build the data requirements document from the definitions above."""
    lines = [
        "# TPC data requirements",
        "",
        "Generated from `tpc_definitions.py`. Do not edit by hand. Regenerate with:",
        "`uv run python main.py --write-datapoints-doc`",
        "",
        "## Needed for every run",
        "",
        "- `--mpan`: the meter point. All data can vary by MPAN.",
        "- `--settlement-date` and `--settlement-period`: the period being valued. "
        "Period 1 starts at midnight UK time.",
        f"- `turndown_kwh`: {DATA_POINTS['turndown_kwh'].description}.",
        f"- `period_import_kwh`: {DATA_POINTS['period_import_kwh'].description}. "
        "The turn-down cannot exceed it.",
        "",
        "## Datapoints for each TPC",
        "",
        "| TPC code | TPC | How the saving is calculated | Varies by | Datapoints | Rate needed? |",
        "|---|---|---|---|---|---|",
    ]
    for tpc in TPC_DEFINITIONS:
        data_points = ", ".join(f"`{name}`" for name in tpc.data_points) or "None"
        if tpc.rate_basis is None:
            rate_needed = "No"
        elif tpc.rate_required:
            rate_needed = "Yes"
        else:
            rate_needed = "Optional (£0 if missing)"
        lines.append(
            f"| `{tpc.code}` | {tpc.name} | {tpc.how_saved} | {tpc.varies_by} "
            f"| {data_points} | {rate_needed} |"
        )

    lines += [
        "",
        "## Where each datapoint comes from",
        "",
        "Each `load_...` function is in `data_sources.py`.",
        "",
        "| Datapoint | What it is | Where it comes from |",
        "|---|---|---|",
    ]
    for name, data_point in DATA_POINTS.items():
        lines.append(f"| `{name}` | {data_point.description} | {data_point.source} |")
    return "\n".join(lines) + "\n"
