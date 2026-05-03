from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl

from robin.features.daily_compute import compute_factor_values_daily, write_factor_values


def backfill_factors(ohlcv: pl.DataFrame, start: date, end: date, output_dir: Path) -> list[Path]:
    paths: list[Path] = []
    current = start
    while current <= end:
        values = compute_factor_values_daily(ohlcv, current)
        paths.append(write_factor_values(values, output_dir))
        current += timedelta(days=1)
    return paths
