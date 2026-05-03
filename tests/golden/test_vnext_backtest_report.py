from __future__ import annotations

from pathlib import Path

import polars as pl

from robin.backtest.analytics.artifacts import write_backtest_report
from robin.backtest.engine.simple import run_factor_backtest
from robin.features.primitives.price import add_price_primitives
from robin.market.loaders.ohlcv import load_ohlcv_csv


FIXTURES = Path("tests/fixtures/vnext")


def test_backtest_report_contains_snapshot_and_costs(tmp_path: Path) -> None:
    frame = add_price_primitives(load_ohlcv_csv(FIXTURES / "ohlcv.csv")).with_columns(
        ((pl.col("close").shift(-1).over("symbol") / pl.col("close")) - 1.0).fill_null(0.0).alias("forward_return")
    )

    run = run_factor_backtest(frame, "fixture_strategy", "return_1d")
    path = write_backtest_report(run, tmp_path)
    text = path.read_text(encoding="utf-8")

    assert run.snapshot_hash in text
    assert "gross_metrics" in text
    assert "net_metrics" in text
