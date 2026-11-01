# TPC data requirements

Generated from `tpc_definitions.py`. Do not edit by hand. Regenerate with:
`uv run python main.py --write-datapoints-doc`

## Needed for every run

- `--mpan`: the meter point. All data can vary by MPAN.
- `--settlement-date` and `--settlement-period`: the period being valued. Period 1 starts at midnight UK time.
- `turndown_kwh`: Turn-down in the settlement period, in kWh at the meter (MSP).
- `period_import_kwh`: Active import in the settlement period, excluding the battery. The turn-down cannot exceed it.

## Datapoints for each TPC

| TPC code | TPC | How the saving is calculated | Varies by | Datapoints | Rate needed? |
|---|---|---|---|---|---|
| `bsuos` | BSUoS | Turn-down kWh x rate x (1 - exempt fraction) | Date (tariff period) | `turndown_kwh`, `tpc_rate`, `exempt_fraction` | Yes |
| `cfd_interim_levy` | CfD interim levy | Turn-down kWh x rate x (1 - exempt fraction) | Date (quarter), EII status | `turndown_kwh`, `tpc_rate`, `exempt_fraction` | Yes |
| `cfd_operational_levy` | CfD operational levy | Turn-down kWh x rate x (1 - exempt fraction) | Date (financial year), EII status | `turndown_kwh`, `tpc_rate`, `exempt_fraction` | Yes |
| `nuclear_rab_interim_levy` | Nuclear RAB interim levy | Turn-down kWh x rate x (1 - exempt fraction) | Date (quarter), EII status | `turndown_kwh`, `tpc_rate`, `exempt_fraction` | Yes |
| `nuclear_rab_operational_levy` | Nuclear RAB operational levy | Turn-down kWh x rate x (1 - exempt fraction) | Date (financial year), EII status | `turndown_kwh`, `tpc_rate`, `exempt_fraction` | Yes |
| `renewables_obligation` | Renewables Obligation | Turn-down kWh x rate x (1 - exempt fraction) | Date (obligation year), EII status | `turndown_kwh`, `tpc_rate`, `exempt_fraction` | Yes |
| `feed_in_tariff` | Feed-in Tariff | Turn-down kWh x rate x (1 - exempt fraction) | Date, EII status | `turndown_kwh`, `tpc_rate`, `exempt_fraction` | Yes |
| `aahedc` | AAHEDC | Turn-down kWh x rate x (1 - exempt fraction) | Date (financial year) | `turndown_kwh`, `tpc_rate`, `exempt_fraction` | Yes |
| `climate_change_levy` | Climate Change Levy | Turn-down kWh x rate x (1 - exempt fraction) | Date (financial year), CCA or other relief | `turndown_kwh`, `tpc_rate`, `exempt_fraction` | Yes |
| `bsc_charges` | BSC charges | Turn-down kWh x rate x (1 - exempt fraction) | Date | `turndown_kwh`, `tpc_rate`, `exempt_fraction` | Optional (£0 if missing) |
| `ncc_levy` | NCC / EII Support Levy | Turn-down kWh x rate x (1 - exempt fraction) | Date | `turndown_kwh`, `tpc_rate`, `exempt_fraction` | Optional (£0 if missing) |
| `duos_unit_rate` | DUoS unit rate | Turn-down kWh x the rate for the band that applies in this period | DNO, LLFC, voltage, UK clock time, day type, date | `turndown_kwh`, `duos_time_bands`, `duos_band_rate` | Yes |
| `capacity_market_supplier_charge` | Capacity Market supplier charge | Turn-down kWh x rate x (1 - exempt fraction), only from 16:00 to 19:00 UK time on Working Days, November to February. Otherwise £0. The rate must be per unit of peak-period demand | Delivery year, UK clock time, Working Day, EII status | `turndown_kwh`, `tpc_rate`, `exempt_fraction`, `non_working_dates` | Yes |
| `capacity_market_settlement_levy` | Capacity Market settlement levy | Same rule as the Capacity Market supplier charge | Financial year, UK clock time, Working Day, EII status | `turndown_kwh`, `tpc_rate`, `exempt_fraction`, `non_working_dates` | Optional (£0 if missing) |
| `duos_exceeded_capacity` | DUoS exceeded capacity | Fall in the month's highest kVA above agreed capacity x rate x days in month. Only counted as firm when the month's HH data is complete | DNO, LLFC, agreed capacity, the MPAN's monthly peak kVA, days in month | `turndown_kwh`, `tpc_rate`, `agreed_capacity_kva`, `month_import_kwh`, `month_reactive_kvarh` | Yes |
| `duos_excess_reactive` | DUoS excess reactive power | Fall in kVArh above 33% of kWh in this period x rate. Assumes the turn-down keeps the power factor unchanged, so kVArh falls in line with kWh | DNO, LLFC, the MPAN's power factor. May not apply to EHV (EDCM) sites | `turndown_kwh`, `tpc_rate`, `period_import_kwh`, `period_reactive_kvarh` | Optional (£0 if missing) |
| `tnuos_locational` | TNUoS locational (Triad) | £0. Triad value is accounted for elsewhere | Not used | None | No |
| `tnuos_residual` | TNUoS residual | £0. A daily charge per site, by band | Not used | None | No |
| `duos_fixed` | DUoS fixed charges | £0. A daily charge per site | Not used | None | No |
| `duos_agreed_capacity` | DUoS agreed capacity | £0. Set by the agreed capacity, not by consumption | Not used | None | No |

## Where each datapoint comes from

Each `load_...` function is in `data_sources.py`.

| Datapoint | What it is | Where it comes from |
|---|---|---|
| `turndown_kwh` | Turn-down in the settlement period, in kWh at the meter (MSP) | `--turndown-kwh` or the settings at the top of `main.py` |
| `period_import_kwh` | Active import in the settlement period, excluding the battery | `load_hh_consumption`: `import_kwh` |
| `tpc_rate` | The rate for this TPC, MPAN and date, and its unit | `load_tpc_rates`: `rate`, `unit` |
| `exempt_fraction` | Fraction of the charge not payable, from 0 to 1 (for example EII or CCA). Treated as 0 if there is no row | `load_exemptions`: `exempt_fraction` |
| `duos_time_bands` | Which DUoS band applies at each UK clock time, by day type | `load_duos_time_bands`: `day_type`, `start_time`, `end_time`, `band` |
| `duos_band_rate` | The DUoS unit rate for each band, and its unit | `load_duos_unit_rates`: `band`, `rate`, `unit` |
| `non_working_dates` | Weekdays that are not Working Days under the Capacity Market rules | `load_non_working_dates`: `non_working_date` |
| `month_import_kwh` | Active import for every settlement period in the calendar month | `load_hh_consumption`: `import_kwh` |
| `month_reactive_kvarh` | Reactive import and export for every settlement period in the calendar month. Estimated at 0.95 power factor, with a warning, if missing | `load_hh_consumption`: `reactive_import_kvarh`, `reactive_export_kvarh` |
| `period_reactive_kvarh` | Reactive import and export in the settlement period. Estimated at 0.95 power factor, with a warning, if missing | `load_hh_consumption`: `reactive_import_kvarh`, `reactive_export_kvarh` |
| `agreed_capacity_kva` | The MPAN's Maximum Import Capacity (agreed capacity) in kVA | `load_site_details`: `agreed_capacity_kva` |
