from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from robin.features.daily_compute import compute_factor_values_daily
from robin.features.neutralization import demean_by_group
from robin.market.loaders.ohlcv import load_ohlcv_csv


FIXTURES = Path("tests/fixtures/vnext")


def test_factor_formula_correctness() -> None:
    values = compute_factor_values_daily(load_ohlcv_csv(FIXTURES / "ohlcv.csv"), date(2026, 5, 1))
    sndk_return = next(value for value in values if value.symbol == "SNDK" and value.factor_name == "return_1d")

    assert round(sndk_return.value, 6) == round(117 / 114 - 1, 6)
    assert sndk_return.snapshot_hash


def test_neutralization_demeans_by_group() -> None:
    frame = pl.DataFrame({"industry": ["a", "a", "b"], "value": [1.0, 3.0, 5.0]})
    neutralized = demean_by_group(frame, "value", "industry", "neutral")

    assert neutralized.filter(pl.col("industry") == "a").select(pl.col("neutral").sum()).item() == 0.0
