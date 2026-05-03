from __future__ import annotations

import json
from pathlib import Path

from robin.contracts.backtest import BacktestRun, ExperimentRun
from robin.core.ids import stable_id


def write_backtest_report(run: BacktestRun, artifact_dir: Path) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"{run.id}.json"
    path.write_text(json.dumps(run.to_storage_dict(), ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


def experiment_from_backtest(run: BacktestRun, name: str, artifact_uri: str) -> ExperimentRun:
    return ExperimentRun(
        id=stable_id("experiment", {"name": name, "backtest": run.id}),
        name=name,
        backtest_run_ids=[run.id],
        artifact_uri=artifact_uri,
        config_hash=run.config_hash,
        code_hash=run.code_hash,
        status="done",
        lineage=[run.id],
    )
