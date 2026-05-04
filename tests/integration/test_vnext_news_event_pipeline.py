from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from robin.contracts.raw_document import RawDocument
from robin.core.types import SourceTier, VerificationStatus
from robin.facts.entity_resolution.security_master import SecurityMaster
from robin.news.pipeline import build_news_event_pipeline


FIXTURES = Path("tests/fixtures/vnext")


def test_news_event_pipeline_builds_verified_claim_events_and_factor_rows():
    documents = [
        RawDocument(
            source_id="sandisk_ir",
            source_tier=SourceTier.P0,
            url="https://investor.sandisk.com/node/7896/pdf",
            title="SNDK beats earnings and raises guidance",
            body="SNDK revenue was above guidance and outlook improved.",
            event_time=datetime(2026, 5, 1, tzinfo=timezone.utc),
            published_time=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
    ]

    result = build_news_event_pipeline(
        documents, SecurityMaster.from_csv(FIXTURES / "security_master.csv")
    )

    assert result.claims
    assert result.events
    assert result.packets[0].verification_status == VerificationStatus.VERIFIED
    assert result.factor_frame.get_column("event_factor_value").max() > 0


def test_no_official_confirmation_stays_insufficient_evidence():
    documents = [
        RawDocument(
            source_id="generic_media",
            source_tier=SourceTier.P2,
            url="https://example.com/movers",
            title="SNDK shares rose with no confirmed catalyst",
            body="SNDK stock jumped in market movers. No company announcement was cited.",
            event_time=datetime(2026, 5, 1, tzinfo=timezone.utc),
            published_time=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
    ]

    result = build_news_event_pipeline(
        documents, SecurityMaster.from_csv(FIXTURES / "security_master.csv")
    )

    assert (
        result.packets[0].verification_status
        == VerificationStatus.INSUFFICIENT_EVIDENCE
    )
    assert result.factor_frame.get_column("event_factor_value").max() == 0
    assert result.factor_frame.get_column("candidate_event_value").max() > 0


def test_independent_p1_sources_corroborate_same_claim():
    documents = [
        RawDocument(
            source_id="reputable_media_a",
            source_tier=SourceTier.P1,
            url="https://example.com/a",
            title="SNDK wins multiyear customer agreement",
            body="SNDK announced a multiyear customer agreement for data center storage.",
            event_time=datetime(2026, 5, 1, tzinfo=timezone.utc),
            published_time=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ),
        RawDocument(
            source_id="reputable_media_b",
            source_tier=SourceTier.P1,
            url="https://example.com/b",
            title="SNDK customer win expands data center agreement",
            body="SNDK confirmed a customer win and agreement tied to data center demand.",
            event_time=datetime(2026, 5, 1, tzinfo=timezone.utc),
            published_time=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ),
    ]

    result = build_news_event_pipeline(
        documents, SecurityMaster.from_csv(FIXTURES / "security_master.csv")
    )

    assert {packet.verification_status for packet in result.packets} == {
        VerificationStatus.VERIFIED
    }
    assert min(claim.attributes["corroboration_count"] for claim in result.claims) == 2
    assert len({claim.attributes["event_cluster_id"] for claim in result.claims}) == 1
    assert result.factor_frame.get_column("event_cluster_id").n_unique() == 1
    assert result.factor_frame.get_column("event_factor_value").max() > 0.75
