from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from robin.contracts.base import ContractModel
from robin.core.types import DecisionAction, StrategyState


class StrategyDecision(ContractModel):
    strategy_name: str
    symbol: str
    action: DecisionAction
    state: StrategyState = StrategyState.PAPER
    decision_time: datetime
    evidence_packet_ids: list[str] = Field(default_factory=list)
    factor_value_ids: list[str] = Field(default_factory=list)
    backtest_run_id: str = ""
    risk_checks: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class ExecutionReport(ContractModel):
    decision_id: str
    symbol: str
    state: StrategyState
    requested_action: DecisionAction
    executed: bool = False
    broker_permission: str = "UNSPECIFIED_BROKER_PERMISSION"
    message: str = ""
    fills: list[dict[str, Any]] = Field(default_factory=list)
