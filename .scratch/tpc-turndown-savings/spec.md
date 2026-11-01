# TPC turn-down savings

Status: ready-for-human (the database queries need connecting)

## Goal

Calculate the TPC cost avoided, per TPC, by a turn-down in one half-hourly settlement period on one MPAN. The usual cause is a battery, but the tool doesn't model it.

## Decisions

- **One rate per TPC.** The rate may be a forecast, industry or contract figure. Pass-through, fixed and reconciled charging don't change the calculation.
- **Inputs:** MPAN, settlement date, settlement period and turn-down kWh, set in the script or through argparse. Everything else comes from the `load_...` functions in `data_sources.py`.
- **HH data excludes the battery.** The tool subtracts the turn-down.
- **Volumes are at MSP,** with no loss adjustment.
- **All data can vary by MPAN.**
- **Reactive power:** the turn-down keeps the power factor unchanged. Missing data is estimated at 0.95 power factor, with a warning.
- **The headline is firm savings.** Exceeded capacity is only "possible" until the month's HH data is complete.

## Out of scope for now

Customer/supplier split, charging methods and reconciliation, band changes, Triad (accounted for elsewhere), battery recharging and rebound, export, loss adjustment, contract volume terms, non-HH sites, gas and Northern Ireland.

## Known limitation

Each run values one period alone. Summing separate runs across periods can overstate exceeded capacity, because each run assumes the other periods stay at their original level. Exceeded capacity for several periods needs a multi-period calculation.

## Rule sources

- Exceeded capacity and reactive formulas, and missing reactive data: [SSEN 2026/27 charging statement](https://www.ssen.co.uk/globalassets/library/charging-statements-sepd/202627/sepd-embedded-networks-duos-charges-2026-2027-v1.2.pdf), paragraphs 2.41 to 2.53 and 2.73. This is one DNO's statement; others should be checked.
- Capacity Market window and levy basis: [EMRS G15](https://www.emrsettlement.co.uk/document/guidance/g15-cm-supplier-payments/).
- CfD and Nuclear RAB chargeable demand: [EMRS G16](https://www.emrsettlement.co.uk/document/guidance/g16-supplier-cfd-and-nuclear-rab-payments/).
- TNUoS residual and Triad: [NESO final tariffs 2026/27](https://www.neso.energy/document/376336/download).
- EII exemption scope: [gov.uk EII guidance](https://assets.publishing.service.gov.uk/media/67ff6d6b393a986ec5cf8df2/energy-intensive-industries-certificate-for-exemption-funding-contracts-for-difference-renewables-obligation-feed-in-tariff.pdf).
