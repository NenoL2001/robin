from __future__ import annotations

from robin.contracts.canonical_document import CanonicalDocument
from robin.contracts.claim import ClaimRecord
from robin.contracts.entity import CanonicalEntity
from robin.contracts.event import EventRecord
from robin.contracts.evidence_packet import EvidencePacket
from robin.core.ids import stable_id
from robin.core.types import SourceTier, VerificationStatus
from robin.facts.scoring.evidence import evidence_score


def build_evidence_packet(
    document: CanonicalDocument,
    entities: list[CanonicalEntity],
    events: list[EventRecord],
    claims: list[ClaimRecord],
) -> EvidencePacket:
    tier = SourceTier(str(document.metadata.get("source_tier", "UNSPECIFIED")))
    score, flags = evidence_score(tier, claims)
    statuses = {claim.verification_status for claim in claims}
    if VerificationStatus.CONFLICTED in statuses:
        status = VerificationStatus.CONFLICTED
    elif claims and statuses == {VerificationStatus.VERIFIED}:
        status = VerificationStatus.VERIFIED
    else:
        status = VerificationStatus.INSUFFICIENT_EVIDENCE
    packet_id = stable_id("evidence", {"doc": document.id, "events": [event.id for event in events], "claims": [claim.id for claim in claims]})
    return EvidencePacket(
        id=packet_id,
        canonical_document_id=document.id,
        entity_ids=[entity.security_id for entity in entities],
        event_ids=[event.id for event in events],
        claim_ids=[claim.id for claim in claims],
        evidence_score=score,
        source_tier=tier.value,
        verification_status=status,
        summary=document.text[:500],
        citations=[document.canonical_url] if document.canonical_url else [],
        risk_flags=flags,
        lineage=[document.id, *[entity.id for entity in entities], *[event.id for event in events], *[claim.id for claim in claims]],
    )
