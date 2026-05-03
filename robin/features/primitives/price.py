from __future__ import annotations

import polars as pl


def add_price_primitives(frame: pl.DataFrame) -> pl.DataFrame:
    """Adds deterministic daily price primitives without future-looking data."""

    return (
        frame.sort(["symbol", "date"])
        .with_columns(
            [
                pl.col("close").shift(1).over("symbol").alias("close_lag_1"),
                pl.col("close").shift(5).over("symbol").alias("close_lag_5"),
                pl.col("volume").rolling_mean(20).over("symbol").alias("volume_mean_20d"),
                pl.col("volume").rolling_std(20).over("symbol").alias("volume_std_20d"),
            ]
        )
        .with_columns(
            [
                ((pl.col("close") / pl.col("close_lag_1")) - 1.0).fill_null(0.0).alias("return_1d"),
                ((pl.col("close") / pl.col("close_lag_5")) - 1.0).fill_null(0.0).alias("return_5d"),
                ((pl.col("volume") - pl.col("volume_mean_20d")) / pl.col("volume_std_20d")).fill_nan(0.0).fill_null(0.0).alias("volume_zscore_20d"),
                ((pl.col("close") - pl.col("low")) / (pl.col("high") - pl.col("low"))).fill_nan(0.5).fill_null(0.5).alias("close_location_value"),
            ]
        )
    )
