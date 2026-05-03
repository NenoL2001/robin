from __future__ import annotations

from pathlib import Path

import polars as pl

from robin.backtest.costs.model import CostModel
from robin.backtest.engine.simple import run_factor_backtest
from robin.features.primitives.price import add_price_primitives
from robin.market.loaders.ohlcv import load_ohlcv_csv


FIXTURES = Path("tests/fixtures/vnext")


def test_cost_model_gross_net_split() -> None:
    assert CostModel(fixed_bps=1, spread_bps=2, impact_bps_per_turnover=3).cost_fraction(2.0) == 0.0009


def test_backtest_fixture_metrics_are_stable(tmp_path: Path) -> None:
    frame = add_price_primitives(load_ohlcv_csv(FIXTURES / "ohlcv.csv")).with_columns(
        ((pl.col("close").shift(-1).over("symbol") / pl.col("close")) - 1.0).fill_null(0.0).alias("forward_return")
    )

    run = run_factor_backtest(frame, "fixture_strategy", "return_1d", artifact_dir=tmp_path)

    assert run.id.startswith("backtest_")
    assert run.gross_metrics["hit_rate"] >= run.net_metrics["hit_rate"]
    assert run.net_metrics["total_return"] < run.gross_metrics["total_return"]
