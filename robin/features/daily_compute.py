from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from robin.contracts.factor import FactorDefinition, FactorValueDaily
from robin.core.ids import stable_hash, stable_id
from robin.features.neutralization import demean_by_group, market_cap_neutralize
from robin.features.primitives.price import add_price_primitives
from robin.features.registry.factor_registry import default_factor_definitions
from robin.ingest.lake import parquet_ready_row


def compute_factor_values_daily(ohlcv: pl.DataFrame, as_of_date: date, definitions: list[FactorDefinition] | None = None) -> list[FactorValueDaily]:
    definitions = definitions or default_factor_definitions()
    enriched = add_price_primitives(ohlcv).filter(pl.col("date") <= as_of_date)
    day = enriched.filter(pl.col("date") == as_of_date)
    values: list[FactorValueDaily] = []
    for definition in definitions:
        if definition.name not in day.columns:
            continue
        frame = day.select(["symbol", "date", definition.name, *[col for col in ("industry", "market_cap_bucket") if col in day.columns]])
        frame = demean_by_group(frame, definition.name, "industry", "sector_neutral_value")
        frame = market_cap_neutralize(frame, definition.name, "market_cap_neutral_value")
        snapshot_hash = stable_hash({"factor": definition.id, "date": as_of_date.isoformat(), "rows": frame.select(["symbol", definition.name]).sort("symbol").to_dicts()})
        ranked = frame.with_columns(pl.col(definition.name).rank(method="average").alias("rank"))
        for row in ranked.to_dicts():
            values.append(
                FactorValueDaily(
                    id=stable_id("factorval", {"factor": definition.id, "symbol": row["symbol"], "date": as_of_date.isoformat()}),
                    factor_id=definition.id,
                    factor_name=definition.name,
                    symbol=str(row["symbol"]).upper(),
                    as_of_date=as_of_date,
                    value=float(row[definition.name] or 0.0),
                    rank=float(row["rank"] or 0.0),
                    sector_neutral_value=float(row.get("sector_neutral_value") or row[definition.name] or 0.0),
                    market_cap_neutral_value=float(row.get("market_cap_neutral_value") or row[definition.name] or 0.0),
                    snapshot_hash=snapshot_hash,
                    lineage=[definition.id],
                )
            )
    return values


def write_factor_values(values: list[FactorValueDaily], output_dir: Path) -> Path:
    if not values:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "factor_values_empty.parquet"
        pl.DataFrame([]).write_parquet(path)
        return path
    as_of = values[0].as_of_date.isoformat()
    path = output_dir / f"date={as_of}" / "factor_values.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame([parquet_ready_row(value.to_storage_dict()) for value in values]).write_parquet(path)
    return path
