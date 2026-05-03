from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field


RAW_NEWS_KEYS = {"raw_text", "raw_body", "body", "article_text", "html", "pdf_bytes"}


class PortfolioImplication(BaseModel):
    affected_positions: list[str] = Field(default_factory=list)
    risk_exposure_change: dict[str, float] = Field(default_factory=dict)
    recommended_action: str = "insufficient_evidence"


class AgentAnalysis(BaseModel):
    thesis: str
    evidence_refs: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    alternative_hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    portfolio_implication: PortfolioImplication = Field(
        default_factory=PortfolioImplication
    )
    confidence: float = 0.0
    invalidation_conditions: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


def analyze_local_context(local_context: dict[str, Any]) -> AgentAnalysis:
    assert_no_raw_news(local_context)
    evidence = list(local_context.get("evidence_packets", []) or [])
    factors = list(local_context.get("factor_metrics", []) or [])
    holdings = [
        str(value).upper()
        for value in local_context.get("portfolio_positions", []) or []
    ]
    verified = [
        item
        for item in evidence
        if item.get("verification_status") == "verified"
        and float(item.get("evidence_score", 0.0)) >= 0.75
    ]
    evidence_refs = [
        f"evidence:{item.get('id')}" for item in verified if item.get("id")
    ]
    metric_refs = [
        f"metric:{item.get('factor_name')}"
        for item in factors
        if abs(float(item.get("rank_ic", 0.0))) >= 0.03
    ]
    confidence = min(0.9, 0.25 + 0.18 * len(evidence_refs) + 0.1 * len(metric_refs))
    if not evidence_refs:
        return AgentAnalysis(
            thesis="证据不足：本地 evidence bundle 没有通过 claim confidence 门槛的 verified evidence。",
            evidence_refs=[],
            counter_evidence=["缺少 P0/P1 交叉确认或 claim confidence < 0.75"],
            alternative_hypotheses=[
                {
                    "name": "market_microstructure_or_flow",
                    "supporting_refs": [],
                    "confidence": 0.35,
                },
                {
                    "name": "sector_beta_or_peer_readthrough",
                    "supporting_refs": [],
                    "confidence": 0.25,
                },
            ],
            portfolio_implication=PortfolioImplication(
                affected_positions=holdings, recommended_action="insufficient_evidence"
            ),
            confidence=0.25,
            invalidation_conditions=["出现 P0/P1 官方证据且 claim confidence >= 0.75"],
            missing_data=[
                "verified_evidence_packet",
                "counter_evidence",
                "factor_metrics",
            ],
        )
    return AgentAnalysis(
        thesis="本地证据和因子指标支持一个可检验 thesis，但仍需保留替代解释。",
        evidence_refs=[*evidence_refs, *metric_refs],
        counter_evidence=["检查是否存在低可信单源叙事或事件后反转。"],
        alternative_hypotheses=[
            {
                "name": "verified_event_repricing",
                "supporting_refs": evidence_refs,
                "confidence": confidence,
            },
            {
                "name": "sector_or_peer_beta",
                "supporting_refs": metric_refs,
                "confidence": min(0.55, confidence - 0.1),
            },
        ],
        portfolio_implication=PortfolioImplication(
            affected_positions=holdings,
            recommended_action="candidate_long" if confidence >= 0.65 else "watch",
        ),
        confidence=confidence,
        invalidation_conditions=[
            "verified evidence 被修正或撤回",
            "factor IC 转负且通过 FDR 后仍显著",
            "paper/live divergence 超限",
        ],
        missing_data=[] if metric_refs else ["factor_metrics"],
    )


def render_agent_prompt(local_context: dict[str, Any]) -> str:
    assert_no_raw_news(local_context)
    payload = {
        "role": "robin_local_research_analyzer",
        "instructions": [
            "Only use local_context evidence_refs and metrics_tables.",
            "Do blind market scan before portfolio overlay.",
            "Return JSON matching AgentAnalysis schema.",
            "Use insufficient_evidence when evidence refs are missing or weak.",
        ],
        "local_context": local_context,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def assert_no_raw_news(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) in RAW_NEWS_KEYS:
                raise ValueError(
                    f"raw news field forbidden in agent context: {path}.{key}"
                )
            assert_no_raw_news(child, f"{path}.{key}")
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            assert_no_raw_news(child, f"{path}[{idx}]")
