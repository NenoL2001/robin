from __future__ import annotations

from pathlib import Path

from robin.facts.entity_resolution.security_master import SecurityMaster
from robin.facts.pipeline import build_evidence_packets
from robin.ingest.fetchers.fixture import FixtureFetcher
from robin.ingest.sources.registry import load_source_registry
from robin.core.types import VerificationStatus


FIXTURES = Path("tests/fixtures/vnext")


def test_security_master_entity_resolution_and_event_schema() -> None:
    source = load_source_registry(FIXTURES / "source_registry.yaml").enabled_sources()[0]
    documents, failures = FixtureFetcher().fetch(source)

    packets = build_evidence_packets(documents, SecurityMaster.from_csv(FIXTURES / "security_master.csv"))

    assert not failures
    assert any(packet.verification_status == VerificationStatus.VERIFIED for packet in packets)
    assert any("https://investor.sandisk.com" in citation for packet in packets for citation in packet.citations)
    assert all(packet.id.startswith("evidence_") for packet in packets)


def test_claim_verification_handles_conflict() -> None:
    source = load_source_registry(FIXTURES / "source_registry.yaml").enabled_sources()[0]
    documents, _ = FixtureFetcher().fetch(source)
    packets = build_evidence_packets(documents, SecurityMaster.from_csv(FIXTURES / "security_master.csv"))

    conflict = [packet for packet in packets if "conflicting" in packet.summary.lower()][0]

    assert conflict.verification_status == VerificationStatus.CONFLICTED
    assert "conflicting_claims" in conflict.risk_flags
