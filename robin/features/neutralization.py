from __future__ import annotations

import polars as pl


def demean_by_group(frame: pl.DataFrame, value_col: str, group_col: str, output_col: str) -> pl.DataFrame:
    if group_col not in frame.columns:
        return frame.with_columns(pl.col(value_col).alias(output_col))
    partition_cols = ["date", group_col] if "date" in frame.columns else [group_col]
    return frame.with_columns((pl.col(value_col) - pl.col(value_col).mean().over(partition_cols)).alias(output_col))


def market_cap_neutralize(frame: pl.DataFrame, value_col: str, output_col: str) -> pl.DataFrame:
    if "market_cap_bucket" not in frame.columns:
        return frame.with_columns(pl.col(value_col).alias(output_col))
    return demean_by_group(frame, value_col, "market_cap_bucket", output_col)
