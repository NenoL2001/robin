from __future__ import annotations

from robin.contracts.decision import StrategyDecision
from robin.core.types import DecisionAction


def risk_check(decision: StrategyDecision, *, max_notional: float = 0.0) -> StrategyDecision:
    checks = dict(decision.risk_checks)
    checks.setdefault("live_trading_disabled", True)
    if max_notional <= 0 and decision.action == DecisionAction.BUY:
        checks["blocked_reason"] = "max_notional_zero_or_unspecified"
        return decision.model_copy(update={"action": DecisionAction.BLOCK, "risk_checks": checks})
    return decision.model_copy(update={"risk_checks": checks})
