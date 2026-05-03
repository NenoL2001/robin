from __future__ import annotations

from datetime import datetime, timezone

from robin.contracts.decision import StrategyDecision
from robin.contracts.evidence_packet import EvidencePacket
from robin.contracts.factor import FactorValueDaily
from robin.core.ids import stable_id
from robin.core.types import DecisionAction, StrategyState, VerificationStatus


def select_decision(strategy_name: str, symbol: str, evidence_packets: list[EvidencePacket], factor_values: list[FactorValueDaily]) -> StrategyDecision:
    verified = [packet for packet in evidence_packets if packet.verification_status == VerificationStatus.VERIFIED]
    score = sum(packet.evidence_score for packet in verified) + sum(value.value for value in factor_values if value.symbol == symbol)
    action = DecisionAction.WATCH
    if verified and score > 0.8:
        action = DecisionAction.BUY
    decision_id = stable_id("decision", {"strategy": strategy_name, "symbol": symbol, "evidence": [p.id for p in evidence_packets], "factors": [f.id for f in factor_values]})
    return StrategyDecision(
        id=decision_id,
        strategy_name=strategy_name,
        symbol=symbol.upper(),
        action=action,
        state=StrategyState.PAPER,
        decision_time=datetime.now(timezone.utc),
        evidence_packet_ids=[packet.id for packet in evidence_packets],
        factor_value_ids=[value.id for value in factor_values],
        rationale="selector consumed verified evidence packets and factor snapshots only",
        lineage=[*[packet.id for packet in evidence_packets], *[value.id for value in factor_values]],
    )
