from __future__ import annotations

from robin.contracts.canonical_document import CanonicalDocument
from robin.contracts.entity import CanonicalEntity
from robin.contracts.event import EventRecord
from robin.core.ids import stable_id


EVENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "earnings_surprise": ("earnings beat", "above guidance", "exceed", "outperformance", "revenue"),
    "guidance_revision": ("guidance", "outlook", "raises guidance", "guide"),
    "contract": ("contract", "agreement", "customer", "commitment", "purchase order"),
    "product_roadmap": ("sample", "ramp", "roadmap", "launch", "hbf", "1.6t", "cpo"),
    "capital_raise": ("share issue", "offering", "private placement", "dilution"),
    "regulatory": ("clearance", "approval", "sec", "antitrust"),
}


def extract_events(document: CanonicalDocument, entities: list[CanonicalEntity]) -> list[EventRecord]:
    text = f"{document.title} {document.text}".lower()
    entity_ids = [entity.id for entity in entities]
    events: list[EventRecord] = []
    for event_type, keywords in EVENT_KEYWORDS.items():
        hits = [keyword for keyword in keywords if keyword in text]
        if not hits:
            continue
        event_id = stable_id("event", {"doc": document.id, "type": event_type, "entities": entity_ids})
        events.append(
            EventRecord(
                id=event_id,
                canonical_document_id=document.id,
                entity_ids=entity_ids,
                event_type=event_type,
                event_time=document.event_time or document.published_time,
                attributes={"matched_keywords": hits},
                confidence=min(0.9, 0.45 + len(hits) * 0.1),
                lineage=[document.id, *entity_ids],
            )
        )
    return events
