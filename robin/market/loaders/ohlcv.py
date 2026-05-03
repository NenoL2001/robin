from __future__ import annotations

from pathlib import Path

import polars as pl


REQUIRED_OHLCV_COLUMNS = {"date", "symbol", "open", "high", "low", "close", "volume"}


def load_ohlcv_csv(path: Path) -> pl.DataFrame:
    frame = pl.read_csv(path, try_parse_dates=True)
    missing = REQUIRED_OHLCV_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"OHLCV file missing columns: {sorted(missing)}")
    return frame.sort(["symbol", "date"])
