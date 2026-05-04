from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import polars as pl

from robin.contracts.canonical_document import CanonicalDocument
from robin.contracts.claim import ClaimRecord
from robin.contracts.entity import CanonicalEntity
from robin.contracts.event import EventRecord
from robin.contracts.evidence_packet import EvidencePacket
from robin.contracts.raw_document import RawDocument
from robin.core.ids import stable_id
from robin.core.types import SourceTier, VerificationStatus
from robin.facts.entity_resolution.mentions import extract_mentions
from robin.facts.entity_resolution.security_master import SecurityMaster
from robin.facts.evidence_graph.packet_builder import build_evidence_packet
from robin.ingest.normalize.canonicalize import canonicalize_document


EVENT_KEYWORDS = {
    "earnings_guidance_up": (
        "beat",
        "above guidance",
        "raises guidance",
        "raised guidance",
        "outlook improved",
    ),
    "major_contract": (
        "contract",
        "agreement",
        "customer win",
        "supply agreement",
        "multiyear",
    ),
    "product_roadmap_acceleration": (
        "sampling",
        "shipping",
        "roadmap",
        "launch",
        "ramp",
    ),
    "unconfirmed_market_move": (
        "shares rose",
        "stock jumped",
        "market movers",
        "rally",
    ),
}

SOURCE_SCORES = {
    SourceTier.P0: 0.95,
    SourceTier.P1: 0.82,
    SourceTier.P2: 0.58,
    SourceTier.UNSPECIFIED: 0.35,
}


@dataclass(slots=True)
class NewsEventPipelineResult:
    claims: list[ClaimRecord]
    events: list[EventRecord]
    packets: list[EvidencePacket]
    factor_frame: pl.DataFrame


@dataclass(slots=True)
class _DocumentExtraction:
    document: CanonicalDocument
    entities: list[CanonicalEntity]
    events: list[EventRecord]
    claims: list[ClaimRecord]


def build_news_event_pipeline(
    raw_documents: list[RawDocument], security_master: SecurityMaster
) -> NewsEventPipelineResult:
    extractions: list[_DocumentExtraction] = []
    for raw in raw_documents:
        document = canonicalize_document(raw)
        entities = []
        seen: set[str] = set()
        for mention in extract_mentions(document):
            entity = security_master.resolve(mention)
            if entity and entity.security_id not in seen:
                entities.append(entity)
                seen.add(entity.security_id)
        entity_ids = [entity.security_id for entity in entities]
        doc_events = extract_structured_events(raw, document.id, entity_ids)
        doc_claims = extract_claim_records(raw, doc_events, entity_ids)
        extractions.append(
            _DocumentExtraction(
                document=document,
                entities=entities,
                events=doc_events,
                claims=doc_claims,
            )
        )
    claims = apply_corroboration_policy(
        [claim for extraction in extractions for claim in extraction.claims]
    )
    claims_by_event = {claim.event_id: claim for claim in claims}
    events = apply_event_confidence_from_claims(
        [event for extraction in extractions for event in extraction.events],
        claims_by_event,
    )
    events_by_id = {event.id: event for event in events}
    packets: list[EvidencePacket] = []
    for extraction in extractions:
        document_events = [events_by_id[event.id] for event in extraction.events]
        event_ids = {event.id for event in document_events}
        document_claims = [claim for claim in claims if claim.event_id in event_ids]
        packets.append(
            build_evidence_packet(
                extraction.document,
                extraction.entities,
                document_events,
                document_claims,
            )
        )
    packets = enforce_claim_confidence_policy(packets)
    return NewsEventPipelineResult(
        claims=claims,
        events=events,
        packets=packets,
        factor_frame=events_to_factor_frame(events, claims),
    )


def extract_structured_events(
    raw: RawDocument, document_id: str, entity_ids: list[str]
) -> list[EventRecord]:
    text = f"{raw.title} {raw.body}".lower()
    event_types = [
        event_type
        for event_type, terms in EVENT_KEYWORDS.items()
        if any(term in text for term in terms)
    ]
    if not event_types:
        event_types = ["news_observation"]
    source_score = SOURCE_SCORES.get(raw.source_tier, 0.35)
    events: list[EventRecord] = []
    for event_type in event_types:
        confidence = (
            source_score
            if event_type != "unconfirmed_market_move"
            else min(source_score, 0.55)
        )
        events.append(
            EventRecord(
                id=stable_id(
                    "event",
                    {
                        "doc": document_id,
                        "event_type": event_type,
                        "entities": entity_ids,
                    },
                ),
                canonical_document_id=document_id,
                entity_ids=entity_ids,
                event_type=event_type,
                event_time=raw.event_time or raw.published_time,
                confidence=confidence,
                attributes={
                    "source_tier": raw.source_tier.value,
                    "novelty": novelty_score(text),
                    "source_score": source_score,
                },
                lineage=[raw.id, document_id],
            )
        )
    return events


def extract_claim_records(
    raw: RawDocument, events: list[EventRecord], entity_ids: list[str]
) -> list[ClaimRecord]:
    source_score = SOURCE_SCORES.get(raw.source_tier, 0.35)
    claims: list[ClaimRecord] = []
    statement = " ".join((raw.title or raw.body).split())[:500]
    for event in events:
        confidence = claim_confidence(
            source_score=source_score,
            has_entities=bool(entity_ids),
            source_tier=raw.source_tier,
            event_type=event.event_type,
            corroboration_count=1,
        )
        status = (
            VerificationStatus.VERIFIED
            if confidence >= 0.75
            else VerificationStatus.INSUFFICIENT_EVIDENCE
        )
        claims.append(
            ClaimRecord(
                id=stable_id(
                    "claim",
                    {
                        "event": event.id,
                        "statement": statement,
                        "source": raw.source_id,
                    },
                ),
                event_id=event.id,
                entity_ids=entity_ids,
                claim_type=event.event_type,
                statement=statement,
                verification_status=status,
                supporting_document_ids=[raw.id],
                attributes={
                    "source_id": raw.source_id,
                    "source_tier": raw.source_tier.value,
                    "source_score": source_score,
                    "corroboration_count": 1,
                    "entity_link_confidence": 0.9 if entity_ids else 0.3,
                    "numeric_consistency_score": 1.0
                    if raw.source_tier == SourceTier.P0
                    else (0.9 if raw.source_tier == SourceTier.P1 else 0.65),
                    "evidence_span": statement,
                },
                confidence=confidence,
                lineage=[raw.id, event.id],
            )
        )
    return claims


def claim_confidence(
    *,
    source_score: float,
    has_entities: bool,
    source_tier: SourceTier,
    event_type: str,
    corroboration_count: int = 1,
) -> float:
    entity_score = 0.9 if has_entities else 0.3
    numeric_score = (
        1.0
        if source_tier == SourceTier.P0
        else (0.9 if source_tier == SourceTier.P1 else 0.65)
    )
    if source_tier == SourceTier.P0:
        corroboration = 1.0
    elif source_tier == SourceTier.P1:
        corroboration = 1.15 if corroboration_count >= 2 else 0.9
    else:
        corroboration = 1.05 if corroboration_count >= 2 else 0.75
    event_penalty = 0.6 if event_type == "unconfirmed_market_move" else 1.0
    return round(
        source_score * entity_score * numeric_score * corroboration * event_penalty,
        4,
    )


def apply_corroboration_policy(claims: list[ClaimRecord]) -> list[ClaimRecord]:
    grouped: dict[tuple[str, tuple[str, ...]], list[ClaimRecord]] = {}
    for claim in claims:
        key = (claim.claim_type, tuple(sorted(claim.entity_ids)))
        grouped.setdefault(key, []).append(claim)

    adjusted: list[ClaimRecord] = []
    for claim in claims:
        group_key = (claim.claim_type, tuple(sorted(claim.entity_ids)))
        peers = grouped.get(group_key, [])
        independent_sources = {
            str(peer.attributes.get("source_id") or peer.supporting_document_ids[0])
            for peer in peers
            if peer.supporting_document_ids
        }
        event_cluster_id = stable_id(
            "eventcluster",
            {"claim_type": group_key[0], "entities": group_key[1]},
        )
        source_tier = SourceTier(str(claim.attributes.get("source_tier") or SourceTier.UNSPECIFIED.value))
        confidence = claim_confidence(
            source_score=float(claim.attributes.get("source_score") or SOURCE_SCORES.get(source_tier, 0.35)),
            has_entities=bool(claim.entity_ids),
            source_tier=source_tier,
            event_type=claim.claim_type,
            corroboration_count=len(independent_sources),
        )
        status = (
            VerificationStatus.VERIFIED
            if confidence >= 0.75
            else VerificationStatus.INSUFFICIENT_EVIDENCE
        )
        attributes = {
            **dict(claim.attributes),
            "event_cluster_id": event_cluster_id,
            "corroboration_count": len(independent_sources),
            "corroborating_source_ids": sorted(independent_sources),
        }
        adjusted.append(
            claim.model_copy(
                update={
                    "confidence": confidence,
                    "verification_status": status,
                    "attributes": attributes,
                }
            )
        )
    return adjusted


def apply_event_confidence_from_claims(
    events: list[EventRecord], claims_by_event: dict[str, ClaimRecord]
) -> list[EventRecord]:
    adjusted: list[EventRecord] = []
    for event in events:
        claim = claims_by_event.get(event.id)
        if not claim:
            adjusted.append(event)
            continue
        confidence = max(event.confidence, claim.confidence)
        attributes = {
            **dict(event.attributes),
            "event_cluster_id": claim.attributes.get("event_cluster_id", ""),
            "claim_confidence": claim.confidence,
            "corroboration_count": claim.attributes.get("corroboration_count", 1),
        }
        adjusted.append(
            event.model_copy(
                update={
                    "confidence": confidence,
                    "attributes": attributes,
                }
            )
        )
    return adjusted


def enforce_claim_confidence_policy(
    packets: list[EvidencePacket],
) -> list[EvidencePacket]:
    result: list[EvidencePacket] = []
    for packet in packets:
        if packet.evidence_score < 0.5:
            status = VerificationStatus.INSUFFICIENT_EVIDENCE
            risk_flags = [*packet.risk_flags, "low_claim_confidence"]
        elif packet.evidence_score < 0.75:
            status = VerificationStatus.INSUFFICIENT_EVIDENCE
            risk_flags = [*packet.risk_flags, "candidate_event_only"]
        else:
            status = packet.verification_status
            risk_flags = packet.risk_flags
        result.append(
            packet.model_copy(
                update={
                    "verification_status": status,
                    "risk_flags": sorted(set(risk_flags)),
                }
            )
        )
    return result


def events_to_factor_frame(
    events: list[EventRecord], claims: list[ClaimRecord]
) -> pl.DataFrame:
    confidence_by_event = {claim.event_id: claim.confidence for claim in claims}
    rows: list[dict[str, Any]] = []
    for event in events:
        event_date = (event.event_time or event.created_at).date()
        for entity_id in event.entity_ids or ["UNLINKED"]:
            confidence = max(event.confidence, confidence_by_event.get(event.id, 0.0))
            rows.append(
                {
                    "date": event_date,
                    "entity_id": entity_id,
                    "event_type": event.event_type,
                    "event_cluster_id": str(event.attributes.get("event_cluster_id", "")),
                    "event_confidence": confidence,
                    "event_factor_value": confidence if confidence >= 0.75 else 0.0,
                    "candidate_event_value": confidence if confidence < 0.75 else 0.0,
                    "event_id": event.id,
                }
            )
    return (
        pl.DataFrame(rows)
        if rows
        else pl.DataFrame(
            schema={"date": pl.Date, "entity_id": pl.String, "event_type": pl.String}
        )
    )


def novelty_score(text: str) -> float:
    if any(
        term in text for term in ("first", "new", "launch", "record", "raises guidance")
    ):
        return 0.8
    if any(term in text for term in ("rumor", "market movers", "shares rose")):
        return 0.35
    return 0.6
