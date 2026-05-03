from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from robin.backtest.costs.model import CostModel
from robin.contracts.backtest import BacktestRun
from robin.core.ids import stable_hash, stable_id


def run_factor_backtest(frame: pl.DataFrame, strategy_name: str, factor_col: str, cost_model: CostModel | None = None, artifact_dir: Path | None = None) -> BacktestRun:
    cost_model = cost_model or CostModel()
    if frame.is_empty():
        raise ValueError("backtest frame is empty")
    required = {"date", "symbol", factor_col, "forward_return"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"backtest frame missing columns: {sorted(missing)}")
    ranked = frame.with_columns(pl.col(factor_col).rank(descending=True).over("date").alias("rank"))
    selected = ranked.filter(pl.col("rank") <= 1)
    gross_returns = [float(row["forward_return"] or 0.0) for row in selected.to_dicts()]
    turnover = 1.0
    net_returns = [ret - cost_model.cost_fraction(turnover) for ret in gross_returns]
    gross_metrics = metrics(gross_returns)
    net_metrics = metrics(net_returns)
    snapshot_hash = stable_hash(frame.select(["date", "symbol", factor_col, "forward_return"]).sort(["date", "symbol"]).to_dicts())
    cost_config = asdict(cost_model)
    config_hash = stable_hash({"strategy_name": strategy_name, "factor_col": factor_col, "cost_model": cost_config})
    code_hash = stable_hash({"engine": "simple_factor_backtest", "version": 1})
    factor_set_hash = stable_hash({"factors": [factor_col]})
    run_id = stable_id("backtest", {"snapshot_hash": snapshot_hash, "config_hash": config_hash, "code_hash": code_hash})
    artifact_uri = ""
    if artifact_dir:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_uri = str(artifact_dir / f"{run_id}.json")
    return BacktestRun(
        id=run_id,
        strategy_name=strategy_name,
        start_time=datetime.combine(min(row["date"] for row in frame.to_dicts()), datetime.min.time(), tzinfo=timezone.utc),
        end_time=datetime.combine(max(row["date"] for row in frame.to_dicts()), datetime.min.time(), tzinfo=timezone.utc),
        snapshot_hash=snapshot_hash,
        config_hash=config_hash,
        code_hash=code_hash,
        factor_set_hash=factor_set_hash,
        gross_metrics=gross_metrics,
        net_metrics=net_metrics,
        cost_model=cost_config,
        artifact_uri=artifact_uri,
    )


def metrics(returns: list[float]) -> dict[str, float]:
    if not returns:
        return {"total_return": 0.0, "mean_return": 0.0, "hit_rate": 0.0, "max_drawdown": 0.0}
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for ret in returns:
        equity *= 1.0 + ret
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1.0)
    return {
        "total_return": round(equity - 1.0, 6),
        "mean_return": round(sum(returns) / len(returns), 6),
        "hit_rate": round(sum(1 for ret in returns if ret > 0) / len(returns), 6),
        "max_drawdown": round(max_dd, 6),
    }
