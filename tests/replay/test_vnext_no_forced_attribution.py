from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from robin.agent.analyzer import analyze_local_context
from robin.contracts.raw_document import RawDocument
from robin.core.types import SourceTier, VerificationStatus
from robin.facts.entity_resolution.security_master import SecurityMaster
from robin.news.pipeline import build_news_event_pipeline


FIXTURES = Path("tests/fixtures/vnext")


def test_unconfirmed_price_move_replay_does_not_force_attribution():
    documents = [
        RawDocument(
            source_id="broad_recall",
            source_tier=SourceTier.P2,
            url="https://example.com/unconfirmed-sndk-move",
            title="SNDK shares rose sharply with no official confirmation",
            body="The stock jumped in a broad market movers list. No official filing or IR release confirmed the cause.",
            event_time=datetime(2026, 5, 1, tzinfo=timezone.utc),
            published_time=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
    ]
    result = build_news_event_pipeline(
        documents, SecurityMaster.from_csv(FIXTURES / "security_master.csv")
    )
    packet = result.packets[0]
    analysis = analyze_local_context(
        {
            "evidence_packets": [packet.to_storage_dict()],
            "factor_metrics": [],
            "portfolio_positions": ["SNDK"],
        }
    )

    assert packet.verification_status == VerificationStatus.INSUFFICIENT_EVIDENCE
    assert analysis.portfolio_implication.recommended_action == "insufficient_evidence"
    assert "证据不足" in analysis.thesis
