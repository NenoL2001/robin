from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..market.news_strategy import FactorCandidateProposal
from .factor_attribution import FactorAttributionSummary
from .factor_specs import FactorSpec


@dataclass(slots=True)
class FactorFlowIssue:
    severity: str
    symbol: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FactorFlowValidationResult:
    dry_run: bool
    checked_signals: int
    factor_count: int
    issues: list[FactorFlowIssue]
    proposed_factors: list[FactorCandidateProposal]
    attribution_summary: list[FactorAttributionSummary]

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "ok": self.ok,
            "checked_signals": self.checked_signals,
            "factor_count": self.factor_count,
            "issues": [item.to_dict() for item in self.issues],
            "proposed_factors": [item.to_dict() for item in self.proposed_factors],
            "attribution_summary": [item.to_dict() for item in self.attribution_summary],
        }

    def summary(self) -> str:
        lines = [
            "策略/因子流程验证",
            f"- dry_run={self.dry_run}; ok={self.ok}; checked_signals={self.checked_signals}; factors={self.factor_count}; issues={len(self.issues)}; proposed_factors={len(self.proposed_factors)}",
        ]
        for issue in self.issues[:12]:
            lines.append(f"- {issue.severity} {issue.symbol}: {issue.message}")
        if self.proposed_factors:
            lines.append("- 建议新增/观察因子: " + ", ".join(item.name for item in self.proposed_factors[:12]))
        if self.attribution_summary:
            best = ", ".join(f"{item.factor_name}:{item.directional_score:g}" for item in self.attribution_summary[:8])
            lines.append(f"- 当前后验较强因子: {best}")
        return "\n".join(lines)


def validate_factor_flow(
    plan: dict[str, Any],
    specs: list[FactorSpec],
    *,
    proposed_factors: list[FactorCandidateProposal],
    attribution_summary: list[FactorAttributionSummary],
    dry_run: bool,
) -> FactorFlowValidationResult:
    spec_names = {item.name for item in specs}
    issues: list[FactorFlowIssue] = []
    signals = [item for item in plan.get("signals", []) if isinstance(item, dict)]
    for signal in signals:
        symbol = str(signal.get("symbol", "")).upper()
        metadata = signal.get("metadata", {}) if isinstance(signal.get("metadata"), dict) else {}
        breakdown = metadata.get("factor_breakdown", []) if isinstance(metadata, dict) else []
        evidence_links = metadata.get("evidence_links", []) if isinstance(metadata, dict) else []
        verdict = metadata.get("risk_gate_verdict", {}) if isinstance(metadata, dict) else {}
        if not breakdown:
            issues.append(FactorFlowIssue("error", symbol, "signal has no factor_breakdown"))
        if not evidence_links and float(signal.get("score") or 0.0) >= 50:
            issues.append(FactorFlowIssue("warn", symbol, "scored signal has no evidence_links"))
        if not isinstance(verdict, dict) or "allowed" not in verdict:
            issues.append(FactorFlowIssue("error", symbol, "risk gate verdict missing from signal metadata"))
        for row in breakdown or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", ""))
            if name and name not in spec_names and not name.startswith(("base_", "portfolio_", "risk_", "valuation_", "price_", "chain_", "catalyst_", "high_impact")):
                issues.append(FactorFlowIssue("warn", symbol, f"factor {name} observed but missing from factor spec store"))
    for spec in specs:
        if spec.evidence_required and not spec.query_terms:
            issues.append(FactorFlowIssue("warn", "", f"factor {spec.name} requires evidence but has no query_terms"))
    return FactorFlowValidationResult(
        dry_run=dry_run,
        checked_signals=len(signals),
        factor_count=len(specs),
        issues=issues,
        proposed_factors=proposed_factors,
        attribution_summary=attribution_summary,
    )
