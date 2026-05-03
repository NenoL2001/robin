from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from robin.contracts.base import ContractModel


class BacktestRun(ContractModel):
    strategy_name: str
    start_time: datetime
    end_time: datetime
    snapshot_hash: str
    config_hash: str
    code_hash: str
    factor_set_hash: str
    gross_metrics: dict[str, float] = Field(default_factory=dict)
    net_metrics: dict[str, float] = Field(default_factory=dict)
    cost_model: dict[str, Any] = Field(default_factory=dict)
    artifact_uri: str = ""


class ExperimentRun(ContractModel):
    name: str
    backtest_run_ids: list[str] = Field(default_factory=list)
    mlflow_run_id: str = ""
    artifact_uri: str = ""
    config_hash: str
    code_hash: str
    status: str = "created"
