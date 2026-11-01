"""Regenerates hh_consumption.csv: a fake site with one clear monthly peak.

Run from the project root:  uv run python sample_data/generate_sample_hh.py
"""

import math
from datetime import date, timedelta
from pathlib import Path

PEAK_DATE = date(2026, 11, 16)
rows = ["mpan,settlement_date,settlement_period,import_kwh,reactive_import_kvarh,reactive_export_kvarh"]
for day_number in range(30):
    day = date(2026, 11, 1) + timedelta(days=day_number)
    for period in range(1, 49):
        kwh = round(150 + 60 * math.sin(math.pi * (period - 1) / 47), 1)
        kvarh = round(kwh * 0.2, 1)
        if day == PEAK_DATE and period == 35:
            kwh, kvarh = 270.0, 120.0  # The month's peak, above agreed capacity.
        if day == PEAK_DATE and period == 34:
            kwh, kvarh = 255.0, 51.0  # The next-highest period, also above agreed capacity.
        rows.append(f"1234567890123,{day},{period},{kwh},{kvarh},0")

path = Path(__file__).parent / "hh_consumption.csv"
path.write_text("\n".join(rows) + "\n", newline="\n")
