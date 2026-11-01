"""Calculate the TPC costs saved by a turn-down in one half-hourly settlement period.

Run with the settings below:           uv run python main.py
Or override them with arguments:       uv run python main.py --turndown-kwh 50
See which datapoints each TPC needs:   uv run python main.py --list-datapoints

To connect your database, edit data_sources.py.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import data_sources
from calculations import DataProblem, SavingsResult, TurndownRequest, calculate_savings
from tpc_definitions import render_data_requirements_markdown

# ---------------------------------------------------------------------------
# EDIT THESE, or override them with command-line arguments
# ---------------------------------------------------------------------------
MPAN = "1234567890123"
SETTLEMENT_DATE = "2026-11-16"  # YYYY-MM-DD
SETTLEMENT_PERIOD = 35  # Period 1 starts at midnight UK time.
TURNDOWN_KWH = 100.0  # kWh at the meter, within this one half hour.
# ---------------------------------------------------------------------------

DATA_REQUIREMENTS_DOC = Path(__file__).parent / "docs" / "tpc-data-requirements.md"


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TPC costs saved by a half-hourly turn-down.")
    parser.add_argument("--mpan", default=MPAN)
    parser.add_argument("--settlement-date", type=date.fromisoformat, default=SETTLEMENT_DATE)
    parser.add_argument("--settlement-period", type=int, default=SETTLEMENT_PERIOD)
    parser.add_argument(
        "--turndown-kwh", type=float, default=TURNDOWN_KWH, help="kWh within the half hour"
    )
    parser.add_argument("--output", type=Path, help="Also save the full breakdown to this CSV")
    parser.add_argument(
        "--list-datapoints", action="store_true", help="Print the datapoints each TPC needs"
    )
    parser.add_argument(
        "--write-datapoints-doc",
        action="store_true",
        help=f"Regenerate docs/{DATA_REQUIREMENTS_DOC.name}",
    )
    return parser.parse_args(arguments)


def print_result(request: TurndownRequest, result: SavingsResult) -> None:
    print(
        f"MPAN {request.mpan} | {request.settlement_date} SP{request.settlement_period} "
        f"| Turn-down {request.turndown_kwh:g} kWh"
    )
    if data_sources.USE_SAMPLE_DATA:
        print("SAMPLE DATA: these rates are fake. See data_sources.py.")
    print()

    label = "INCOMPLETE SAVING (data missing)" if result.is_incomplete else "SAVING"
    print(f"{label}: £{result.firm_saving_gbp:,.2f}")
    if result.possible_saving_gbp:
        print(f"Possible extra, if no later period sets the peak: £{result.possible_saving_gbp:,.2f}")
    print()

    print(f"{'TPC':<34}{'Status':<19}{'Rate':>8} {'Unit':<15}{'Saving':>10}  Note")
    for line in result.lines.iter_rows(named=True):
        saving = line["firm_saving_gbp"] + line["possible_saving_gbp"]
        rate = "" if line["rate"] is None else f"{line['rate']:g}"
        print(
            f"{line['name']:<34}{line['status']:<19}{rate:>8} {line['rate_unit'] or '':<15}"
            f"{'£' + format(saving, ',.2f'):>10}  {line['note']}"
        )

    if result.warnings:
        print("\nWarnings:")
        for warning in result.warnings:
            print(f"  - {warning}")


def main(arguments: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = parse_arguments(arguments)

    if args.list_datapoints:
        print(render_data_requirements_markdown())
        return 0
    if args.write_datapoints_doc:
        DATA_REQUIREMENTS_DOC.write_text(
            render_data_requirements_markdown(), encoding="utf-8", newline="\n"
        )
        print(f"Wrote {DATA_REQUIREMENTS_DOC}")
        return 0

    request = TurndownRequest(
        mpan=args.mpan,
        settlement_date=args.settlement_date,
        settlement_period=args.settlement_period,
        turndown_kwh=args.turndown_kwh,
    )
    try:
        result = calculate_savings(request, data_sources.load_input_data(request))
    except DataProblem as problem:
        print(f"Cannot calculate: {problem}", file=sys.stderr)
        return 2

    print_result(request, result)
    if args.output:
        result.lines.write_csv(args.output)
        print(f"\nSaved the breakdown to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
