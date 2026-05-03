from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import polars as pl

from robin.features.primitives.price import add_price_primitives
from robin.market.loaders.ohlcv import load_ohlcv_csv
from robin.metrics.factor_metrics import evaluate_factor_frame


def main() -> None:
    started = time.perf_counter()
    frame = add_price_primitives(
        load_ohlcv_csv(Path("tests/fixtures/vnext/ohlcv.csv"))
    ).with_columns(
        ((pl.col("close").shift(-1).over("symbol") / pl.col("close")) - 1.0)
        .fill_null(0.0)
        .alias("forward_return")
    )
    frame = frame.rename({"symbol": "entity_id"})
    summary = evaluate_factor_frame(frame, "return_1d")
    elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "benchmark": "factor_pipeline_smoke",
                "elapsed_seconds": round(elapsed, 6),
                "as_of": date.today().isoformat(),
                "metrics": summary.to_dict(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
