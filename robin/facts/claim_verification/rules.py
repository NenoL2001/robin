from __future__ import annotations

from robin.contracts.canonical_document import CanonicalDocument
from robin.contracts.claim import ClaimRecord
from robin.contracts.event import EventRecord
from robin.core.ids import stable_id
from robin.core.types import SourceTier, VerificationStatus


def verify_claims(document: CanonicalDocument, events: list[EventRecord]) -> list[ClaimRecord]:
    tier = SourceTier(str(document.metadata.get("source_tier", "UNSPECIFIED")))
    claims: list[ClaimRecord] = []
    for event in events:
        status = VerificationStatus.VERIFIED if tier in {SourceTier.P0, SourceTier.P1} else VerificationStatus.UNVERIFIED
        text = concise_statement(document.text, event.event_type)
        if "correction" in document.title.lower() or "corrected" in document.text.lower():
            status = VerificationStatus.VERIFIED
        if "dispute" in document.text.lower() or "conflicting" in document.text.lower():
            status = VerificationStatus.CONFLICTED
        claims.append(
            ClaimRecord(
                id=stable_id("claim", {"event": event.id, "statement": text}),
                event_id=event.id,
                entity_ids=event.entity_ids,
                claim_type=event.event_type,
                statement=text,
                verification_status=status,
                supporting_document_ids=[document.id] if status != VerificationStatus.CONFLICTED else [],
                attributes={"source_tier": tier.value},
                confidence=event.confidence if status == VerificationStatus.VERIFIED else min(0.5, event.confidence),
                lineage=[document.id, event.id],
            )
        )
    return claims


def concise_statement(text: str, event_type: str) -> str:
    normalized = " ".join(text.split())
    return f"{event_type}: {normalized[:240]}"
