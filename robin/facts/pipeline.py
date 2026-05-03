from __future__ import annotations

from robin.contracts.entity import CanonicalEntity, EntityMention
from robin.contracts.evidence_packet import EvidencePacket
from robin.contracts.raw_document import RawDocument
from robin.facts.claim_verification.rules import verify_claims
from robin.facts.entity_resolution.mentions import extract_mentions
from robin.facts.entity_resolution.security_master import SecurityMaster
from robin.facts.event_extraction.rules import extract_events
from robin.facts.evidence_graph.packet_builder import build_evidence_packet
from robin.ingest.normalize.canonicalize import canonicalize_document


def build_evidence_packets(raw_documents: list[RawDocument], security_master: SecurityMaster) -> list[EvidencePacket]:
    packets: list[EvidencePacket] = []
    for raw in raw_documents:
        document = canonicalize_document(raw)
        mentions = extract_mentions(document)
        entities = resolve_mentions(mentions, security_master)
        events = extract_events(document, entities)
        claims = verify_claims(document, events)
        packets.append(build_evidence_packet(document, entities, events, claims))
    return packets


def resolve_mentions(mentions: list[EntityMention], security_master: SecurityMaster) -> list[CanonicalEntity]:
    entities: list[CanonicalEntity] = []
    seen: set[str] = set()
    for mention in mentions:
        entity = security_master.resolve(mention)
        if entity and entity.security_id not in seen:
            entities.append(entity)
            seen.add(entity.security_id)
    return entities
