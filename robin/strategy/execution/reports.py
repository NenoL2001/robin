from __future__ import annotations

from robin.contracts.decision import ExecutionReport, StrategyDecision
from robin.core.ids import stable_id


def execution_report_for_decision(decision: StrategyDecision, broker_permission: str = "UNSPECIFIED_BROKER_PERMISSION") -> ExecutionReport:
    executed = broker_permission != "UNSPECIFIED_BROKER_PERMISSION" and decision.state.value == "live"
    return ExecutionReport(
        id=stable_id("execution", {"decision": decision.id, "permission": broker_permission}),
        decision_id=decision.id,
        symbol=decision.symbol,
        state=decision.state,
        requested_action=decision.action,
        executed=executed,
        broker_permission=broker_permission,
        message="live execution disabled unless broker permission is explicitly configured",
        lineage=[decision.id],
    )
