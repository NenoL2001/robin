from __future__ import annotations

import pytest

from robin.agent.analyzer import analyze_local_context, render_agent_prompt


def test_agent_outputs_insufficient_evidence_without_verified_refs():
    analysis = analyze_local_context(
        {"evidence_packets": [], "factor_metrics": [], "portfolio_positions": ["SNDK"]}
    )

    assert analysis.portfolio_implication.recommended_action == "insufficient_evidence"
    assert analysis.alternative_hypotheses
    assert analysis.counter_evidence


def test_agent_rejects_raw_news_context():
    with pytest.raises(ValueError):
        render_agent_prompt(
            {"evidence_packets": [], "raw_text": "unverified article body"}
        )


def test_agent_uses_only_evidence_and_metric_refs():
    prompt = render_agent_prompt(
        {
            "evidence_packets": [
                {"id": "ev1", "verification_status": "verified", "evidence_score": 0.9}
            ],
            "factor_metrics": [{"factor_name": "alpha", "rank_ic": 0.05}],
            "portfolio_positions": ["SNDK"],
        }
    )
    analysis = analyze_local_context(
        {
            "evidence_packets": [
                {"id": "ev1", "verification_status": "verified", "evidence_score": 0.9}
            ],
            "factor_metrics": [{"factor_name": "alpha", "rank_ic": 0.05}],
            "portfolio_positions": ["SNDK"],
        }
    )

    assert "raw_text" not in prompt
    assert "evidence:ev1" in analysis.evidence_refs
    assert analysis.portfolio_implication.recommended_action in {
        "watch",
        "candidate_long",
    }
