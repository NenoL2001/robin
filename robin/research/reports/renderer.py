from __future__ import annotations

from robin.contracts.backtest import BacktestRun
from robin.contracts.decision import StrategyDecision
from robin.contracts.evidence_packet import EvidencePacket
from robin.contracts.factor import FactorValueDaily


def render_constrained_report(
    evidence_packets: list[EvidencePacket],
    factor_values: list[FactorValueDaily],
    backtests: list[BacktestRun],
    decisions: list[StrategyDecision],
) -> str:
    lines = ["Robin vNext Report", "", "## Evidence"]
    if not evidence_packets:
        lines.append("- 证据不足")
    for packet in evidence_packets:
        cite = packet.citations[0] if packet.citations else packet.id
        lines.append(f"- evidence_id={packet.id}; status={packet.verification_status}; score={packet.evidence_score:.2f}; citation={cite}; summary={packet.summary[:180]}")
    lines.append("")
    lines.append("## Factors")
    if not factor_values:
        lines.append("- 证据不足")
    for value in factor_values[:20]:
        lines.append(f"- factor_eval_id={value.id}; {value.symbol} {value.factor_name}={value.value:.6f}; snapshot={value.snapshot_hash[:12]}")
    lines.append("")
    lines.append("## Backtests")
    for run in backtests:
        lines.append(f"- backtest_id={run.id}; gross={run.gross_metrics}; net={run.net_metrics}; snapshot={run.snapshot_hash[:12]}")
    lines.append("")
    lines.append("## Decisions")
    for decision in decisions:
        refs = decision.evidence_packet_ids or decision.factor_value_ids
        if not refs:
            lines.append(f"- decision_id={decision.id}; {decision.symbol}: 证据不足")
        else:
            lines.append(f"- decision_id={decision.id}; {decision.symbol}: action={decision.action}; refs={','.join(refs[:5])}")
    return "\n".join(lines)
